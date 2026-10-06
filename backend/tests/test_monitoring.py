"""app/monitoring.py: Sentry. В интернет ничего не уходит — события перехватывает транспорт-заглушка."""
from decimal import Decimal

import pytest
import sentry_sdk
from sentry_sdk.transport import Transport

from app import monitoring
from app.ai import AIUnavailable
from app.config import settings
from tests.test_ai_background import factory, new_task, run  # noqa: F401
from tests.test_ai_copy import gemini, user_with_balance  # noqa: F401


class CaptureTransport(Transport):
    """Вместо отправки в Sentry — складывает события в список."""

    def __init__(self, options=None):
        super().__init__(options)
        self.events = []

    def capture_envelope(self, envelope):
        for item in envelope.items:
            if item.type == "event":
                self.events.append(item.payload.json)


@pytest.fixture
def sentry(monkeypatch):
    """Sentry включён с тестовым DSN; события — в transport.events."""
    transport = CaptureTransport()
    real_init = sentry_sdk.init
    calls = []

    def init(**kwargs):
        calls.append(kwargs)
        return real_init(transport=transport, **kwargs)
    monkeypatch.setattr(sentry_sdk, "init", init)
    monkeypatch.setattr(settings, "sentry_dsn", "https://public@o0.ingest.sentry.io/0")
    monkeypatch.setattr(settings, "sentry_environment", "test")
    assert monitoring.init_sentry("worker") is True
    transport.calls = calls
    yield transport
    sentry_sdk.get_client().close()
    real_init()  # выключить: без DSN клиент ничего не отправляет


def test_disabled_without_dsn(monkeypatch):
    monkeypatch.setattr(settings, "sentry_dsn", "")
    called = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: called.append(kw))
    assert monitoring.init_sentry("api") is False
    assert called == []


def test_options_no_personal_data(sentry):
    [options] = sentry.calls
    assert options["send_default_pii"] is False
    assert options["environment"] == "test" and options["traces_sample_rate"] == 0.0


def test_unexpected_background_error_reported(db, gemini, factory, sentry):
    """Непредвиденная ошибка фоновой AI-задачи — событие в Sentry (оповещение) с тегом воркера."""
    gemini.error = RuntimeError("сломался разбор ответа")
    user, _ = user_with_balance(db, "10")
    run(new_task(db, user.id), user.id, factory)
    sentry_sdk.flush()
    [event] = sentry.events
    assert event["level"] == "error" and event["tags"]["component"] == "worker"
    assert event["exception"]["values"][-1]["type"] == "RuntimeError"
    assert "непредвиденная ошибка" in event["logentry"]["message"]


def test_expected_failure_not_reported(db, gemini, factory, sentry):
    """Ожидаемый сбой (лимит Gemini) — не оповещение: задача failed, деньги вернулись, Sentry молчит."""
    gemini.error = AIUnavailable("превышен лимит запросов к Gemini API")
    user, _ = user_with_balance(db, "10")
    run(new_task(db, user.id), user.id, factory)
    sentry_sdk.flush()
    assert sentry.events == []


def test_request_error_without_token_or_ip(client, db, sentry, monkeypatch):
    """Падение запроса: событие уходит, но без токена (ни в заголовках, ни в переменных стека) и без IP."""
    from app.auth import create_access_token
    from app.models import User
    from app.routers import wallet

    def broken(*_args, **_kwargs):
        raise RuntimeError("сбой в обработчике")
    monkeypatch.setattr(wallet, "WalletBalanceResponse", broken)
    user = User(email="s@mail.ru", hashed_password="x", balance=Decimal("1"))
    db.add(user)
    db.commit()
    token = create_access_token(user.id)

    with pytest.raises(RuntimeError):
        client.get("/api/v1/wallet/balance", headers={"Authorization": f"Bearer {token}"})
    sentry_sdk.flush()
    event = next(e for e in sentry.events if e.get("exception"))
    assert event["exception"]["values"][-1]["type"] == "RuntimeError"
    assert token not in repr(event)
    assert "ip_address" not in event.get("user", {})
