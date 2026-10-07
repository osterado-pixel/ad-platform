"""Платежи и тарифы: тестовый провайдер, уведомления (подпись, повторы, сумма), подписки, возможности."""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.models import Payment, PaymentStatus, Subscription, SubscriptionStatus, Transaction, User
from app.payments import PaymentProviderError, test_provider
from app.services import entitlements
from tests.test_ai_copy import assert_ledger_matches

PAY = "/api/v1/payments"
PLANS = "/api/v1/plans"


@pytest.fixture
def test_mode(monkeypatch):
    monkeypatch.setattr(settings, "payments_provider", "test")


def webhook(client, provider_payment_id, status, amount, signature=None, provider="test"):
    body = test_provider.webhook_body(provider_payment_id, status, Decimal(amount))
    sig = test_provider.sign(body) if signature is None else signature
    return client.post(f"{PAY}/webhook/{provider}", content=body,
                       headers={"X-Test-Signature": sig, "Content-Type": "application/json"})


def top_up(client, headers, amount="100.00"):
    r = client.post(f"{PAY}/top-up", json={"amount": amount}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def balance(db, user):
    db.expire_all()
    return db.get(User, user.id).balance


# --- Выключено по умолчанию ---
def test_disabled_by_default(client, test_user, user_headers, auth_headers):
    cfg = client.get(f"{PAY}/config", headers=user_headers).json()
    assert cfg["enabled"] is False and cfg["provider"] is None and cfg["test_mode"] is False
    assert client.post(f"{PAY}/top-up", json={"amount": "100"}, headers=user_headers).status_code == 503
    assert webhook(client, "x", "succeeded", "1").status_code == 404
    assert client.get(f"{PAY}/test-checkout/x").status_code == 404
    # Платный тариф без платежей купить нельзя
    client.post(PLANS, json={"code": "pro", "name": "Pro", "price": "19"}, headers=auth_headers)
    assert client.post(f"{PLANS}/pro/buy", headers=user_headers).status_code == 503


def test_unknown_provider_is_disabled_and_logged(monkeypatch, caplog):
    from app import payments
    monkeypatch.setattr(settings, "payments_provider", "yookassa_typo")
    assert payments.get_provider() is None
    payments.startup_check(logging.getLogger("t"))
    assert "не поддерживается" in caplog.text


# --- Пополнение баланса ---
def test_top_up_full_cycle(client, db, test_user, user_headers, test_mode):
    p = top_up(client, user_headers, "250.50")
    assert p["status"] == "pending" and p["purpose"] == "top_up" and p["amount"] == 250.5
    assert p["confirmation_url"] == f"/api/v1/payments/test-checkout/{p['id']}"
    assert balance(db, test_user) == Decimal("10.00")  # до оплаты — ничего

    stored = db.get(Payment, p["id"])
    assert webhook(client, stored.provider_payment_id, "succeeded", "250.50").status_code == 200
    assert balance(db, test_user) == Decimal("260.50")
    db.expire_all()
    stored = db.get(Payment, p["id"])
    assert stored.status == PaymentStatus.SUCCEEDED and stored.paid_at is not None
    tx = db.get(Transaction, stored.transaction_id)
    assert tx.amount == Decimal("250.50") and "Оплата картой" in tx.description
    assert client.get(f"{PAY}/{p['id']}", headers=user_headers).json()["status"] == "succeeded"
    assert_ledger_matches(db, test_user.id)


def test_repeated_webhook_credits_once(client, db, test_user, user_headers, test_mode):
    p = db.get(Payment, top_up(client, user_headers)["id"])
    for _ in range(3):
        assert webhook(client, p.provider_payment_id, "succeeded", "100.00").status_code == 200
    assert balance(db, test_user) == Decimal("110.00")
    assert_ledger_matches(db, test_user.id)


@pytest.mark.parametrize("signature", ["", "0" * 64, "forged-signature"])
def test_forged_webhook_rejected(client, db, test_user, user_headers, test_mode, signature):
    p = db.get(Payment, top_up(client, user_headers)["id"])
    r = webhook(client, p.provider_payment_id, "succeeded", "100.00", signature=signature)
    assert r.status_code == 400 and "подпись" in r.json()["detail"]
    assert balance(db, test_user) == Decimal("10.00")


def test_amount_mismatch_rejected(client, db, test_user, user_headers, test_mode, caplog):
    p = db.get(Payment, top_up(client, user_headers, "1.00")["id"])
    r = webhook(client, p.provider_payment_id, "succeeded", "1000.00")  # подписано, но сумма другая
    assert r.status_code == 400 and "сумма" in r.json()["detail"]
    assert balance(db, test_user) == Decimal("10.00")
    assert any(rec.levelname == "ERROR" for rec in caplog.records)  # оповещение администратору


def test_unknown_payment_ignored(client, test_mode):
    assert webhook(client, "test_no_such", "succeeded", "1").status_code == 200


def test_canceled_then_late_success_does_not_credit(client, db, test_user, user_headers, test_mode):
    p = db.get(Payment, top_up(client, user_headers)["id"])
    webhook(client, p.provider_payment_id, "canceled", "100.00")
    webhook(client, p.provider_payment_id, "succeeded", "100.00")  # опоздавшее уведомление
    db.expire_all()
    assert db.get(Payment, p.id).status == PaymentStatus.CANCELED
    assert balance(db, test_user) == Decimal("10.00")


def test_refund_marked_and_alerted_balance_untouched(client, db, test_user, user_headers, test_mode, caplog):
    p = db.get(Payment, top_up(client, user_headers)["id"])
    webhook(client, p.provider_payment_id, "succeeded", "100.00")
    webhook(client, p.provider_payment_id, "refunded", "100.00")
    db.expire_all()
    assert db.get(Payment, p.id).status == PaymentStatus.REFUNDED
    assert balance(db, test_user) == Decimal("110.00")  # списание при возврате — вручную (место доработки)
    assert "возвращён плательщику" in caplog.text


def test_amount_limits(client, test_user, user_headers, test_mode):
    for bad in ("0", "0.5", "100000.01"):
        assert client.post(f"{PAY}/top-up", json={"amount": bad}, headers=user_headers).status_code == 422


def test_provider_error_marks_failed(client, db, test_user, user_headers, test_mode, monkeypatch):
    def broken(self, *a, **kw):
        raise PaymentProviderError("банк недоступен")
    monkeypatch.setattr(test_provider.TestProvider, "create_payment", broken)
    r = client.post(f"{PAY}/top-up", json={"amount": "100"}, headers=user_headers)
    assert r.status_code == 502
    assert db.scalar(select(Payment.status)) == PaymentStatus.FAILED


def test_payments_private(client, db, test_user, user_headers, auth_headers, test_mode):
    pid = top_up(client, user_headers)["id"]
    assert client.get(f"{PAY}/{pid}", headers=auth_headers).status_code == 404  # чужой (даже админу)
    assert client.get(f"{PAY}/{pid}").status_code == 401
    page = client.get(PAY, headers=user_headers).json()
    assert page["total"] == 1 and page["items"][0]["id"] == pid
    assert client.get(PAY, headers=auth_headers).json()["total"] == 0


# --- Тестовая страница оплаты ---
def test_test_checkout_page(client, db, test_user, user_headers, test_mode):
    pid = top_up(client, user_headers, "42.00")["id"]
    page = client.get(f"{PAY}/test-checkout/{pid}")
    assert page.status_code == 200 and "Деньги ненастоящие" in page.text and "42.00 RUB" in page.text
    r = client.post(f"{PAY}/test-checkout/{pid}", data={"result": "succeeded"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/app#/wallet"
    assert balance(db, test_user) == Decimal("52.00")
    client.post(f"{PAY}/test-checkout/{pid}", data={"result": "succeeded"}, follow_redirects=False)
    assert balance(db, test_user) == Decimal("52.00")  # повтор — без второго зачисления
    assert "уже обработан" in client.get(f"{PAY}/test-checkout/{pid}").text
    assert_ledger_matches(db, test_user.id)


def test_test_checkout_cancel(client, db, test_user, user_headers, test_mode):
    pid = top_up(client, user_headers)["id"]
    client.post(f"{PAY}/test-checkout/{pid}", data={"result": "canceled"}, follow_redirects=False)
    db.expire_all()
    assert db.get(Payment, pid).status == PaymentStatus.CANCELED and balance(db, test_user) == Decimal("10.00")
    assert client.post(f"{PAY}/test-checkout/{pid}", data={"result": "hack"}).status_code == 422


# --- Тарифы ---
def make_plan(client, auth_headers, **kw):
    body = {"code": "pro", "name": "Pro", "price": "19.00", "period_days": 30,
            "features": {"ai_generations": 300}, **kw}
    r = client.post(PLANS, json=body, headers=auth_headers)
    assert r.status_code == 201, r.text
    return r.json()


def test_plan_admin(client, test_user, user_headers, auth_headers):
    plan = make_plan(client, auth_headers)
    assert client.post(PLANS, json={"code": "pro", "name": "X", "price": "1"}, headers=auth_headers).status_code == 409
    assert client.post(PLANS, json={"code": "Bad Code", "name": "X", "price": "1"}, headers=auth_headers).status_code == 422
    assert client.post(PLANS, json={"code": "x2", "name": "X", "price": "1"}, headers=user_headers).status_code == 403
    make_plan(client, auth_headers, code="hidden", is_active=False)
    assert [p["code"] for p in client.get(PLANS).json()] == ["pro"]  # без входа, только действующие
    r = client.patch(f"{PLANS}/{plan['id']}", json={"price": "25.00"}, headers=auth_headers)
    assert r.json()["price"] == 25.0
    assert client.patch(f"{PLANS}/{plan['id']}", json={"price": "1"}, headers=user_headers).status_code == 403


def test_free_plan_activates_immediately(client, db, test_user, user_headers, auth_headers):
    make_plan(client, auth_headers, code="free", price="0", period_days=None, features={"ai_generations": 5})
    r = client.post(f"{PLANS}/free/buy", headers=user_headers)
    assert r.status_code == 200 and r.json()["payment"] is None
    assert r.json()["subscription"]["status"] == "active" and r.json()["subscription"]["ends_at"] is None
    my = client.get(f"{PLANS}/my", headers=user_headers).json()
    assert my["entitlements"] == {"plan": "free", "ai_generations": 5} and my["plan"]["code"] == "free"


def test_paid_plan_via_payment(client, db, test_user, user_headers, auth_headers, test_mode):
    make_plan(client, auth_headers)
    assert client.get(f"{PLANS}/my", headers=user_headers).json()["entitlements"] == {"plan": None}
    r = client.post(f"{PLANS}/pro/buy", headers=user_headers).json()
    payment = db.get(Payment, r["payment"]["id"])
    assert payment.purpose.value == "plan" and payment.amount == Decimal("19.00")
    webhook(client, payment.provider_payment_id, "succeeded", "19.00")
    my = client.get(f"{PLANS}/my", headers=user_headers).json()
    assert my["entitlements"] == {"plan": "pro", "ai_generations": 300}
    ends = datetime.fromisoformat(my["subscription"]["ends_at"])
    assert abs(ends - (datetime.now(timezone.utc) + timedelta(days=30))) < timedelta(minutes=1)
    assert balance(db, test_user) == Decimal("10.00")  # тариф оплачен картой, баланс не тронут


def buy_and_pay(client, db, user_headers, code, price):
    pid = client.post(f"{PLANS}/{code}/buy", headers=user_headers).json()["payment"]["id"]
    webhook(client, db.get(Payment, pid).provider_payment_id, "succeeded", price)


def test_extend_same_plan_no_gap(client, db, test_user, user_headers, auth_headers, test_mode):
    make_plan(client, auth_headers)
    buy_and_pay(client, db, user_headers, "pro", "19.00")
    buy_and_pay(client, db, user_headers, "pro", "19.00")
    subs = db.scalars(select(Subscription).order_by(Subscription.id)).all()
    assert [s.status for s in subs] == [SubscriptionStatus.ACTIVE, SubscriptionStatus.ACTIVE]
    assert subs[1].starts_at == subs[0].ends_at  # продление — сразу за текущей, без перерыва
    # Сейчас действует первая; после её окончания — вторая
    assert entitlements.active_subscription(db, test_user.id).id == subs[0].id
    later = subs[0].ends_at.replace(tzinfo=timezone.utc) + timedelta(seconds=1)
    assert entitlements.active_subscription(db, test_user.id, now=later).id == subs[1].id


def test_switch_plan_cancels_previous(client, db, test_user, user_headers, auth_headers, test_mode):
    make_plan(client, auth_headers)
    make_plan(client, auth_headers, code="team", name="Team", price="49.00", features={"ai_generations": 2000})
    buy_and_pay(client, db, user_headers, "pro", "19.00")
    buy_and_pay(client, db, user_headers, "team", "49.00")
    db.expire_all()
    statuses = [s.status for s in db.scalars(select(Subscription).order_by(Subscription.id))]
    assert statuses == [SubscriptionStatus.CANCELED, SubscriptionStatus.ACTIVE]
    assert entitlements.entitlements(db, test_user.id) == {"plan": "team", "ai_generations": 2000}


def test_plan_edit_keeps_paid_features(client, db, test_user, user_headers, auth_headers, test_mode):
    plan = make_plan(client, auth_headers)
    buy_and_pay(client, db, user_headers, "pro", "19.00")
    client.patch(f"{PLANS}/{plan['id']}", json={"features": {"ai_generations": 1}}, headers=auth_headers)
    assert entitlements.entitlements(db, test_user.id)["ai_generations"] == 300  # оплаченное не меняется


def test_expired_subscription_gives_nothing(client, db, test_user, user_headers, auth_headers, test_mode):
    make_plan(client, auth_headers)
    buy_and_pay(client, db, user_headers, "pro", "19.00")
    in_40_days = datetime.now(timezone.utc) + timedelta(days=40)
    assert entitlements.entitlements(db, test_user.id, now=in_40_days) == {"plan": None}


def test_unknown_or_inactive_plan(client, test_user, user_headers, auth_headers, test_mode):
    make_plan(client, auth_headers, code="old", is_active=False)
    assert client.post(f"{PLANS}/old/buy", headers=user_headers).status_code == 404
    assert client.post(f"{PLANS}/nope/buy", headers=user_headers).status_code == 404


def test_one_row_per_provider_payment_id(db, test_user):
    """Один платёж провайдера — одна запись: база не даст создать дубль."""
    from sqlalchemy.exc import IntegrityError
    for _ in range(2):
        db.add(Payment(user_id=test_user.id, provider="test", provider_payment_id="test_same",
                       purpose="top_up", amount=Decimal("1"), currency="RUB"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert db.scalar(select(func.count()).select_from(Payment)) == 0


def test_test_checkout_page_csp(client, test_user, user_headers, test_mode):
    pid = top_up(client, user_headers)["id"]
    csp = client.get(f"{PAY}/test-checkout/{pid}").headers["content-security-policy"]
    assert "default-src 'none'" in csp and "form-action 'self'" in csp and "script-src" not in csp
