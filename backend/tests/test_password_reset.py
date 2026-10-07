"""Восстановление пароля по email: письмо со ссылкой, смена пароля, защита от перебора и подмены."""
import smtplib
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from sqlalchemy import select

from app.auth import create_access_token, get_password_hash, verify_password
from app.config import settings
from app.models import PasswordResetToken, User
from app.services import mailer

FORGOT = "/api/v1/auth/forgot-password"
RESET = "/api/v1/auth/reset-password"


@pytest.fixture
def user(db):
    u = User(email="user@example.com", hashed_password=get_password_hash("old-password"))
    db.add(u)
    db.commit()
    return u


@pytest.fixture
def sent(monkeypatch):
    """Письма вместо отправки складываются в список."""
    box = []
    monkeypatch.setattr(mailer, "send_email", lambda to, subject, body: box.append((to, subject, body)))
    return box


def _token(body: str) -> str:
    return body.split("/app#/reset/")[1].split()[0]


def test_reset_flow(client, db, user, sent):
    old_session = create_access_token(user.id, token_version=user.token_version)
    r = client.post(FORGOT, json={"email": "USER@example.com"})
    assert r.status_code == 202
    [(to, subject, body)] = sent
    assert (to, subject) == ("user@example.com", "Смена пароля в Ad Platform")
    assert "http://testserver/app#/reset/" in body
    token = _token(body)
    assert token not in str(db.scalar(select(PasswordResetToken.token_hash)))  # хранится только HMAC

    r = client.post(RESET, json={"token": token, "new_password": "new-password-1"})
    assert r.status_code == 200
    db.refresh(user)
    assert verify_password("new-password-1", user.hashed_password)
    # Новый токен действует, прежние сеансы завершены
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).status_code == 200
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {old_session}"}).status_code == 401
    # Ссылка одноразовая
    assert client.post(RESET, json={"token": token, "new_password": "another-pass"}).status_code == 400


def test_same_answer_for_unknown_email(client, user, sent):
    known = client.post(FORGOT, json={"email": "user@example.com"})
    unknown = client.post(FORGOT, json={"email": "nobody@example.com"})
    assert (known.status_code, known.json()) == (unknown.status_code, unknown.json())
    assert len(sent) == 1


def test_email_and_answer_in_request_language(client, user, sent):
    r = client.post(FORGOT, json={"email": "user@example.com"}, headers={"Accept-Language": "de"})
    assert r.json()["detail"].startswith("Falls diese E-Mail-Adresse registriert ist")
    assert sent[0][1] == "Passwort für Ad Platform zurücksetzen"
    assert "gültig für 30 Min." in sent[0][2]


def test_link_uses_public_url(client, user, sent, monkeypatch):
    monkeypatch.setattr(settings, "public_url", "https://ads.example.com/")
    client.post(FORGOT, json={"email": "user@example.com"})
    assert "https://ads.example.com/app#/reset/" in sent[0][2]


def test_new_link_replaces_old(client, user, sent):
    client.post(FORGOT, json={"email": "user@example.com"})
    client.post(FORGOT, json={"email": "user@example.com"})
    first, second = _token(sent[0][2]), _token(sent[1][2])
    assert client.post(RESET, json={"token": first, "new_password": "new-password-1"}).status_code == 400
    assert client.post(RESET, json={"token": second, "new_password": "new-password-1"}).status_code == 200


def test_expired_link(client, db, user, sent):
    client.post(FORGOT, json={"email": "user@example.com"})
    row = db.scalar(select(PasswordResetToken))
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    r = client.post(RESET, json={"token": _token(sent[0][2]), "new_password": "new-password-1"})
    assert r.status_code == 400
    assert "устарела" in r.json()["detail"]


def test_wrong_token_and_weak_password(client, user, sent):
    client.post(FORGOT, json={"email": "user@example.com"})
    assert client.post(RESET, json={"token": "x" * 43, "new_password": "new-password-1"}).status_code == 400
    assert client.post(RESET, json={"token": _token(sent[0][2]), "new_password": "short"}).status_code == 422


def test_rate_limit_per_email(client, user, sent):
    for _ in range(3):
        assert client.post(FORGOT, json={"email": "user@example.com"}).status_code == 202
    r = client.post(FORGOT, json={"email": "user@example.com"})
    assert r.status_code == 429 and "Retry-After" in r.headers
    assert len(sent) == 3


def test_reset_clears_login_lockout(client, db, user, sent):
    for _ in range(settings.login_max_failures_per_email):
        client.post("/api/v1/auth/login", data={"username": "user@example.com", "password": "wrong-pass"})
    assert client.post("/api/v1/auth/login", data={"username": "user@example.com",
                                                   "password": "old-password"}).status_code == 429
    client.post(FORGOT, json={"email": "user@example.com"})
    client.post(RESET, json={"token": _token(sent[0][2]), "new_password": "new-password-1"})
    assert client.post("/api/v1/auth/login", data={"username": "user@example.com",
                                                   "password": "new-password-1"}).status_code == 200


# ---------- Отправка письма ----------
def test_without_smtp_letter_goes_to_log(monkeypatch, caplog):
    monkeypatch.setattr(settings, "smtp_host", "")
    with caplog.at_level("INFO", logger="app.services.mailer"):
        assert mailer.send_email("a@example.com", "Тема", "Ссылка") is False
    assert "Ссылка" in caplog.text


@pytest.mark.parametrize("port, cls_name, starttls", [(587, "SMTP", True), (465, "SMTP_SSL", False)])
def test_smtp_send(monkeypatch, port, cls_name, starttls):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_port", port)
    monkeypatch.setattr(settings, "smtp_user", "robot@example.com")
    monkeypatch.setattr(settings, "smtp_password", "secret")
    monkeypatch.setattr(settings, "smtp_from", "")
    with mock.patch.object(smtplib, cls_name) as smtp_cls:
        assert mailer.send_email("a@example.com", "Тема", "Текст") is True
    smtp = smtp_cls.return_value.__enter__.return_value
    assert smtp.starttls.called is starttls
    smtp.login.assert_called_once_with("robot@example.com", "secret")
    msg = smtp.send_message.call_args.args[0]
    assert (msg["From"], msg["To"], msg["Subject"]) == ("robot@example.com", "a@example.com", "Тема")


def test_smtp_error_is_logged_not_raised(monkeypatch, caplog):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_port", 587)
    with mock.patch.object(smtplib, "SMTP", side_effect=OSError("connection refused")):
        assert mailer.send_email("a@example.com", "Тема", "Текст") is False
    assert "Не удалось отправить письмо" in caplog.text


# ---------- Подмена адреса в ссылке (заголовок Host) ----------
def test_real_email_needs_public_url(client, user, sent, monkeypatch, caplog):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "public_url", "")
    r = client.post(FORGOT, json={"email": "user@example.com"}, headers={"Host": "evil.example.com"})
    assert r.status_code == 202          # ответ тот же — по нему не узнать, есть ли такой email
    assert sent == []                     # но письмо со ссылкой на чужой домен не уходит
    assert "PUBLIC_URL" in caplog.text


def test_host_header_ignored_with_public_url(client, user, sent, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "public_url", "https://ads.example.com")
    client.post(FORGOT, json={"email": "user@example.com"}, headers={"Host": "evil.example.com"})
    assert "https://ads.example.com/app#/reset/" in sent[0][2] and "evil" not in sent[0][2]


def test_password_change_voids_reset_link(client, db, user, sent):
    client.post(FORGOT, json={"email": "user@example.com"})
    token = _token(sent[0][2])
    h = {"Authorization": f"Bearer {create_access_token(user.id, token_version=user.token_version)}"}
    r = client.post("/api/v1/auth/change-password", headers=h,
                    json={"current_password": "old-password", "new_password": "changed-password"})
    assert r.status_code == 200
    assert client.post(RESET, json={"token": token, "new_password": "attacker-pass"}).status_code == 400
