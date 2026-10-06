"""POST /api/v1/ai/generate-copy: резерв, генерация, расчёт по факту, возврат при ошибке.

Gemini не вызывается: gemini_service.generate_ad подменяется.
"""
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.ai import AIUnavailable
from app.auth import create_access_token
from app.config import settings
from app.ledger import add_transaction
from app.models import AILog, AITask, AITaskStatus, Transaction, TransactionType, User
from app.services import gemini_service, moderation_service

URL = "/api/v1/ai/generate-copy"
BODY = {"product_description": "Онлайн-курс Python с нуля", "target_audience": "новички"}
VARIANTS = {"variants": [{"title": f"Заголовок {i}", "text": "Текст", "cta": "Купить"} for i in range(3)]}


def user_with_balance(db, balance, email="adv@mail.ru"):
    u = User(email=email, hashed_password="x", balance=Decimal(balance))
    db.add(u)
    db.flush()
    if Decimal(balance) > 0:
        add_transaction(db, user_id=u.id, amount=Decimal(balance), type=TransactionType.DEPOSIT)
    db.commit()
    return u, {"Authorization": f"Bearer {create_access_token(u.id)}"}


def balance_of(db, user_id) -> Decimal:
    db.expire_all()
    return db.get(User, user_id).balance


def assert_ledger_matches(db, user_id):
    """Баланс = пополнения + возвраты − списания: деньги не теряются и не появляются из ниоткуда."""
    def total(*types):
        return db.scalar(select(func.coalesce(func.sum(Transaction.amount), 0))
                         .where(Transaction.user_id == user_id, Transaction.type.in_(types)))
    income = total(TransactionType.DEPOSIT, TransactionType.REFUND)
    spend = total(TransactionType.CLICK_SPEND, TransactionType.AI_SPEND)
    assert Decimal(income) - Decimal(spend) == balance_of(db, user_id)
    assert db.get(User, user_id).transactions_count == db.scalar(
        select(func.count()).where(Transaction.user_id == user_id))
    # Заморожено ровно столько, сколько зарезервировано под ещё выполняющиеся задачи
    held = db.scalar(select(func.coalesce(func.sum(Transaction.amount), 0))
                     .join(AITask, AITask.transaction_id == Transaction.id)
                     .where(AITask.user_id == user_id, AITask.status == AITaskStatus.PROCESSING))
    assert db.get(User, user_id).held_balance == Decimal(held)


def held_of(db, user_id) -> Decimal:
    db.expire_all()
    return db.get(User, user_id).held_balance


@pytest.fixture
def gemini(monkeypatch):
    """Включает копирайтер и подменяет модель. state.cost — себестоимость в $, state.error — исключение."""
    monkeypatch.setattr(settings, "gemini_api_key", "AIza-test")
    monkeypatch.setattr(settings, "usd_rate", Decimal("1"))

    class State:
        cost = Decimal("0.004")
        error = None
        during = None  # колбэк «пока модель думает»
        calls = []

    def generate_ad(product_description, target_audience):
        State.calls.append((product_description, target_audience))
        if State.during:
            State.during()
        if State.error:
            raise State.error
        return {"content": VARIANTS, "usage": {
            "model": "gemini-test", "prompt_tokens": 600, "completion_tokens": 400, "total_tokens": 1000,
            "cost": State.cost}}

    monkeypatch.setattr(gemini_service, "generate_ad", generate_ad)
    return State


def test_success_charges_actual_cost(client, db, gemini):
    user, h = user_with_balance(db, "10")
    r = client.post(URL, json=BODY, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True and body["data"] == VARIANTS
    # 0.004 $ при курсе 1 → 0.01 (вверх до копейки)
    assert body["billing"] == {"tokens_used": 1000, "cost_deducted": 0.01, "remaining_balance": 9.99}
    assert balance_of(db, user.id) == Decimal("9.99")
    assert gemini.calls == [("Онлайн-курс Python с нуля", "новички")]

    [tx] = db.scalars(select(Transaction).where(Transaction.type == TransactionType.AI_SPEND)).all()
    assert tx.amount == Decimal("0.01") and "1000 токенов" in tx.description
    [entry] = db.scalars(select(AILog)).all()
    assert (entry.user_id, entry.prompt_type, entry.model, entry.total_tokens) == (user.id, "gemini_ad_copy",
                                                                                  "gemini-test", 1000)
    assert (entry.cost, entry.charged, entry.transaction_id) == (Decimal("0.004"), Decimal("0.01"), tx.id)
    assert_ledger_matches(db, user.id)

    # Операция видна в истории кошелька
    [last, *_] = client.get("/api/v1/wallet/history", headers=h).json()["items"]
    assert (last["type"], last["amount"]) == ("ai_spend", 0.01)


def test_usd_rate_converts_to_balance_currency(client, db, gemini, monkeypatch):
    monkeypatch.setattr(settings, "usd_rate", Decimal("90"))  # баланс в рублях
    user, h = user_with_balance(db, "100")
    r = client.post(URL, json=BODY, headers=h)
    assert r.json()["billing"]["cost_deducted"] == 0.36  # 0.004 $ * 90
    assert_ledger_matches(db, user.id)


def test_insufficient_balance_402(client, db, gemini):
    user, h = user_with_balance(db, "0.02")  # резерв — 0.03
    r = client.post(URL, json=BODY, headers=h)
    assert r.status_code == 402 and "0.03" in r.json()["detail"]
    assert gemini.calls == []  # платный запрос к модели не отправлен
    assert balance_of(db, user.id) == Decimal("0.02")
    assert db.scalar(select(func.count()).select_from(AILog)) == 0
    assert_ledger_matches(db, user.id)


def test_ai_error_refunds_hold(client, db, gemini):
    gemini.error = AIUnavailable("превышен лимит запросов к Gemini API")
    user, h = user_with_balance(db, "5")
    r = client.post(URL, json=BODY, headers=h)
    assert r.status_code == 503
    assert "превышен лимит" in r.json()["detail"] and "не списаны" in r.json()["detail"]
    assert balance_of(db, user.id) == Decimal("5")
    types = db.scalars(select(Transaction.type).where(Transaction.user_id == user.id)
                       .order_by(Transaction.id)).all()
    assert types == [TransactionType.DEPOSIT, TransactionType.AI_SPEND, TransactionType.REFUND]
    assert db.scalar(select(func.count()).select_from(AILog)) == 0
    assert_ledger_matches(db, user.id)


def test_unexpected_error_refunds_without_leaking_details(client, db, gemini):
    gemini.error = RuntimeError("секретная внутренняя деталь")
    user, h = user_with_balance(db, "5")
    r = client.post(URL, json=BODY, headers=h)
    assert r.status_code == 500 and "секретная" not in r.text
    assert balance_of(db, user.id) == Decimal("5")
    assert_ledger_matches(db, user.id)


def test_charge_never_exceeds_hold(client, db, gemini):
    gemini.cost = Decimal("5")  # аномально дорогой ответ
    user, h = user_with_balance(db, "1")
    r = client.post(URL, json=BODY, headers=h)
    assert r.json()["billing"]["cost_deducted"] == 0.03
    assert balance_of(db, user.id) == Decimal("0.97")
    assert_ledger_matches(db, user.id)


def test_parallel_requests_cannot_overspend(client, db, gemini):
    user, h = user_with_balance(db, "0.05")  # хватает на один резерв (0.03), не на два
    second = {}

    def another_request_meanwhile():
        gemini.during = None
        second["r"] = client.post(URL, json=BODY, headers=h)

    gemini.during = another_request_meanwhile
    assert client.post(URL, json=BODY, headers=h).status_code == 200
    assert second["r"].status_code == 402
    assert balance_of(db, user.id) == Decimal("0.04")
    assert_ledger_matches(db, user.id)


def test_disabled_without_key(client, db, gemini, monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    user, h = user_with_balance(db, "5")
    assert client.post(URL, json=BODY, headers=h).status_code == 503
    assert balance_of(db, user.id) == Decimal("5")


@pytest.mark.parametrize("body", [
    {"product_description": "коротко"},
    {"product_description": "   " + " " * 20},
    {"product_description": "x" * 2001},
    {**BODY, "target_audience": "x" * 301},
])
def test_validation(client, db, gemini, body):
    _, h = user_with_balance(db, "5")
    assert client.post(URL, json=body, headers=h).status_code == 422
    assert gemini.calls == []


def test_default_audience_and_auth(client, db, gemini):
    _, h = user_with_balance(db, "5")
    assert client.post(URL, json={"product_description": BODY["product_description"]}).status_code == 401
    assert client.post(URL, json={"product_description": BODY["product_description"]}, headers=h).status_code == 200
    assert gemini.calls[-1][1] == "Общая аудитория"


# --- Модерация до денег и до Gemini ---
@pytest.mark.parametrize("body,reason", [
    ({"product_description": "Лучшее онлайн-казино с бонусом"}, "азартные игры"),
    ({"product_description": "Курс для тех, кто хочет зарабатывать", "target_audience": "кто ищет заработок без вложений"},
     "заработка без вложений"),
])
def test_forbidden_text_rejected_before_billing(client, db, gemini, body, reason):
    user, h = user_with_balance(db, "5")
    r = client.post(URL, json=body, headers=h)
    assert r.status_code == 422 and reason in r.json()["detail"]
    assert gemini.calls == []  # платный запрос не отправлен
    assert balance_of(db, user.id) == Decimal("5")
    assert db.scalar(select(func.count()).where(Transaction.user_id == user.id)) == 1  # только пополнение


def test_moderation_runs_before_balance_check(client, db, gemini):
    # Без денег и с запрещённым текстом — причина в тексте, а не «пополните баланс»
    _, h = user_with_balance(db, "0")
    assert client.post(URL, json={"product_description": "Ставки на спорт онлайн"}, headers=h).status_code == 422


def test_openai_moderation_flag_rejects(client, db, gemini, monkeypatch):
    calls = []
    monkeypatch.setattr(moderation_service, "find_openai_violations",
                        lambda text: calls.append(text) or ["угрозы"])
    user, h = user_with_balance(db, "5")
    r = client.post(URL, json=BODY, headers=h)
    assert r.status_code == 422 and "угрозы" in r.json()["detail"]
    assert calls == [f"{BODY['product_description']}\n{BODY['target_audience']}"]  # одним запросом
    assert gemini.calls == [] and balance_of(db, user.id) == Decimal("5")


# --- Замороженный баланс (held_balance) ---
def test_held_balance_during_generation(client, db, gemini):
    user, h = user_with_balance(db, "10")
    seen = {}

    def check_wallet():
        seen.update(client.get("/api/v1/wallet/balance", headers=h).json())
    gemini.during = check_wallet
    client.post(URL, json=BODY, headers=h)
    # Пока модель отвечает: 0.03 заморожено и в доступный баланс не входит
    assert seen == {"balance": 9.97, "held_balance": 0.03}
    # После: заморозка снята, списано по факту
    assert (balance_of(db, user.id), held_of(db, user.id)) == (Decimal("9.99"), Decimal("0"))
    assert client.get("/api/v1/auth/me", headers=h).json()["held_balance"] == 0.0


def test_held_balance_released_on_error(client, db, gemini):
    gemini.error = AIUnavailable("сбой")
    user, h = user_with_balance(db, "10")
    client.post(URL, json=BODY, headers=h)
    assert (balance_of(db, user.id), held_of(db, user.id)) == (Decimal("10"), Decimal("0"))
