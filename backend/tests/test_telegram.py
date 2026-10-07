"""Telegram: код привязки в кабинете, эндпоинты бота /api/v1/bot/* (секрет, привязка, генерация)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import update

from app.config import Settings, settings
from app.models import AITask, TelegramLinkCode, User
from app.routers import telegram
from tests.conftest import GEMINI_VARIANTS

SECRET = "bot-secret-0123456789abcdefghijklmnopqrstuv"
BOT = {"X-Bot-Secret": SECRET}
TG = 777000111


@pytest.fixture
def bot_on(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_secret", SECRET)
    monkeypatch.setattr(settings, "telegram_bot_username", "AdPlatformBot")


def get_code(client, headers) -> str:
    r = client.post("/api/v1/telegram/link-code", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["code"]


def link(client, code, telegram_id=TG):
    return client.post("/api/v1/bot/link", json={"code": code, "telegram_id": telegram_id}, headers=BOT)


# --- Код привязки в кабинете ---
def test_link_code(client, test_user, user_headers, bot_on, db):
    body = client.post("/api/v1/telegram/link-code", headers=user_headers).json()
    code = body["code"]
    assert len(code) == 8 and set(code) <= set(telegram.CODE_ALPHABET)
    assert body["deep_link"] == f"https://t.me/AdPlatformBot?start={code}"
    assert datetime.fromisoformat(body["expires_at"]) > datetime.now(timezone.utc) + timedelta(minutes=9)
    # Хранится только HMAC кода
    stored = db.query(TelegramLinkCode).one()
    assert stored.code_hash != code and len(stored.code_hash) == 64


def test_new_code_replaces_old(client, test_user, user_headers, bot_on, db):
    old = get_code(client, user_headers)
    new = get_code(client, user_headers)
    assert db.query(TelegramLinkCode).count() == 1
    assert link(client, old).status_code == 400
    assert link(client, new).status_code == 200


def test_disabled_bot(client, test_user, user_headers, monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_secret", "")
    assert client.post("/api/v1/telegram/link-code", headers=user_headers).status_code == 503
    # Эндпоинты бота как будто не существуют
    assert client.get(f"/api/v1/bot/me?telegram_id={TG}", headers=BOT).status_code == 404


def test_bot_secret_required(client, bot_on):
    assert client.get(f"/api/v1/bot/me?telegram_id={TG}").status_code == 401
    assert client.get(f"/api/v1/bot/me?telegram_id={TG}", headers={"X-Bot-Secret": "x" * 40}).status_code == 401
    assert client.post("/api/v1/bot/link", json={"code": "ABCDEFGH", "telegram_id": TG}).status_code == 401


def test_short_secret_rejected_at_startup(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_SECRET", "short")
    with pytest.raises(ValueError, match="TELEGRAM_BOT_SECRET"):
        Settings()


# --- Привязка ---
def test_link_flow(client, test_user, user_headers, bot_on, db):
    code = get_code(client, user_headers)
    r = link(client, f"  {code[:4].lower()}-{code[4:].lower()} ")  # регистр, пробелы и дефис — неважны
    assert r.status_code == 200
    assert r.json() == {"email": "test@example.com", "balance": 10.0, "held_balance": 0.0}
    db.expire_all()
    assert db.get(User, test_user.id).telegram_id == TG
    me = client.get("/api/v1/auth/me", headers=user_headers).json()
    assert me["telegram_linked"] is True and "telegram_id" not in me  # сам ID наружу не отдаём
    assert link(client, code).status_code == 400  # код одноразовый
    assert db.query(TelegramLinkCode).count() == 0


def test_expired_code(client, test_user, user_headers, bot_on, db):
    code = get_code(client, user_headers)
    db.execute(update(TelegramLinkCode).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    db.commit()
    assert link(client, code).status_code == 400


def test_bruteforce_limited(client, test_user, user_headers, bot_on):
    good = get_code(client, user_headers)
    for _ in range(telegram.LINK_ATTEMPTS):
        assert link(client, "WRONGCOD").status_code == 400
    # Дальше — отказ даже с верным кодом: перебор за 15 минут — не больше 10 попыток
    r = link(client, good)
    assert r.status_code == 429 and "15 минут" in r.json()["detail"]
    assert link(client, good, telegram_id=TG + 1).status_code == 200  # лимит — на один Telegram


def test_telegram_already_linked_elsewhere(client, db, test_user, user_headers, bot_on):
    from app.auth import create_access_token
    other = User(email="other@example.com", hashed_password="x", telegram_id=TG)
    db.add(other)
    db.commit()
    r = link(client, get_code(client, user_headers))
    assert r.status_code == 409 and "/unlink" in r.json()["detail"]
    # Отвязали у другого аккаунта — теперь можно
    client.delete("/api/v1/telegram/link", headers={"Authorization": f"Bearer {create_access_token(other.id)}"})
    assert link(client, get_code(client, user_headers)).status_code == 200


# --- Действия бота ---
def linked(client, user_headers):
    assert link(client, get_code(client, user_headers)).status_code == 200


def test_bot_me_and_unlink(client, test_user, user_headers, bot_on, db):
    assert client.get(f"/api/v1/bot/me?telegram_id={TG}", headers=BOT).status_code == 404
    linked(client, user_headers)
    assert client.get(f"/api/v1/bot/me?telegram_id={TG}", headers=BOT).json()["balance"] == 10.0
    assert client.post("/api/v1/bot/unlink", json={"telegram_id": TG}, headers=BOT).status_code == 204
    assert client.get(f"/api/v1/bot/me?telegram_id={TG}", headers=BOT).status_code == 404
    assert client.get("/api/v1/auth/me", headers=user_headers).json()["telegram_linked"] is False


def test_bot_generate(client, test_user, user_headers, bot_on, mock_gemini, db):
    linked(client, user_headers)
    r = client.post("/api/v1/bot/generate", headers=BOT, json={
        "telegram_id": TG, "product_description": "Онлайн-курс Python с нуля", "target_audience": "новички"})
    assert r.status_code == 202, r.text
    task_id = r.json()["task_id"]
    assert r.json()["check_status_url"] == f"/api/v1/bot/tasks/{task_id}"
    # TestClient выполняет фоновую задачу сразу после ответа
    t = client.get(f"/api/v1/bot/tasks/{task_id}?telegram_id={TG}", headers=BOT).json()
    assert t["status"] == "completed" and t["result"] == GEMINI_VARIANTS
    mock_gemini.assert_called_once_with("Онлайн-курс Python с нуля", "новички")
    db.expire_all()
    assert db.get(User, test_user.id).balance == Decimal("9.99")  # та же оплата, что у сайта


def test_bot_generate_same_checks_as_site(client, test_user, user_headers, bot_on, mock_gemini):
    linked(client, user_headers)
    r = client.post("/api/v1/bot/generate", headers=BOT,
                    json={"telegram_id": TG, "product_description": "Лучшее онлайн-казино с бонусом"})
    assert r.status_code == 422 and "азартные игры" in r.json()["detail"]
    mock_gemini.assert_not_called()


def test_bot_cannot_read_foreign_task(client, db, test_user, user_headers, bot_on):
    linked(client, user_headers)
    other = User(email="o@example.com", hashed_password="x")
    db.add(other)
    db.flush()
    task = AITask(user_id=other.id)
    db.add(task)
    db.commit()
    assert client.get(f"/api/v1/bot/tasks/{task.id}?telegram_id={TG}", headers=BOT).status_code == 404


def test_status(client, test_user, user_headers, bot_on, monkeypatch):
    assert client.get("/api/v1/telegram/status", headers=user_headers).json() == {
        "enabled": True, "bot_username": "AdPlatformBot", "linked": False}
    linked(client, user_headers)
    assert client.get("/api/v1/telegram/status", headers=user_headers).json()["linked"] is True
    monkeypatch.setattr(settings, "telegram_bot_secret", "")
    assert client.get("/api/v1/telegram/status", headers=user_headers).json()["enabled"] is False
