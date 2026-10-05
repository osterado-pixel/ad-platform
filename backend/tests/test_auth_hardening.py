"""Ограничение попыток входа/регистрации, смена пароля и отзыв токенов."""
import pytest
from fastapi.testclient import TestClient

from app import ratelimit
from app.auth import get_password_hash
from app.main import app
from app.models import AuthAttempt, User

LOGIN = "/api/v1/auth/login"


@pytest.fixture
def user(db):
    u = User(email="user@mail.ru", hashed_password=get_password_hash("password123"))
    db.add(u)
    db.commit()
    return u


@pytest.fixture
def from_ip(client):
    """Клиент с заданным IP (работает с тестовой БД через фикстуру client)."""
    return lambda ip: TestClient(app, client=(ip, 50000))


def login(c, email="user@mail.ru", password="password123"):
    return c.post(LOGIN, data={"username": email, "password": password})


# --- Перебор паролей ---

def test_email_locked_after_5_failures(client, user, from_ip):
    for i in range(5):
        assert login(from_ip(f"1.1.1.{i}"), password="wrong-pass").status_code == 401
    r = login(from_ip("9.9.9.9"), password="wrong-pass")  # даже с другого IP
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0
    assert "email" in r.json()["detail"]
    # и верный пароль не проверяется, пока окно не истекло: перебор нельзя продолжить
    assert login(from_ip("9.9.9.9")).status_code == 429


def test_lock_expires(client, user, from_ip, monkeypatch):
    for _ in range(5):
        login(from_ip("1.1.1.1"), password="wrong-pass")
    assert login(from_ip("2.2.2.2")).status_code == 429
    real = ratelimit.time.time
    monkeypatch.setattr(ratelimit.time, "time", lambda: real() + 15 * 60 + 1)
    assert login(from_ip("2.2.2.2")).status_code == 200


def test_success_resets_email_counter(client, user, from_ip):
    for _ in range(4):
        login(from_ip("1.1.1.1"), password="wrong-pass")
    assert login(from_ip("1.1.1.1")).status_code == 200
    for _ in range(4):  # счётчик начался заново
        assert login(from_ip("1.1.1.1"), password="wrong-pass").status_code == 401


def test_ip_locked_after_20_failures(client, user, from_ip):
    c = from_ip("6.6.6.6")
    for i in range(20):  # перебор разных email с одного IP
        assert login(c, email=f"victim{i}@mail.ru", password="wrong-pass").status_code == 401
    r = login(c)
    assert r.status_code == 429 and "адреса" in r.json()["detail"]
    assert login(from_ip("7.7.7.7")).status_code == 200  # другим IP — можно


def test_email_case_does_not_bypass_limit(client, user, from_ip):
    for i in range(5):
        login(from_ip(f"1.1.1.{i}"), email="USER@mail.ru" if i % 2 else "user@MAIL.ru", password="wrong-pass")
    assert login(from_ip("8.8.8.8")).status_code == 429


def test_attempts_store_no_plain_email_or_ip(client, db, user, from_ip):
    login(from_ip("5.5.5.5"), password="wrong-pass")
    keys = [a.key for a in db.query(AuthAttempt)]
    assert len(keys) == 2 and all(len(k) == 64 for k in keys)
    assert not any("5.5.5.5" in k or "user@" in k for k in keys)


# --- Массовая регистрация ---

def test_register_limited_per_ip(client, from_ip):
    c = from_ip("4.4.4.4")
    for i in range(10):
        assert c.post("/api/v1/auth/register", json={"email": f"u{i}@mail.ru", "password": "password123"}).status_code == 201
    r = c.post("/api/v1/auth/register", json={"email": "u10@mail.ru", "password": "password123"})
    assert r.status_code == 429 and "retry-after" in r.headers
    assert from_ip("4.4.4.5").post("/api/v1/auth/register",
                                   json={"email": "other@mail.ru", "password": "password123"}).status_code == 201


# --- Смена пароля и отзыв токенов ---

def test_change_password_revokes_old_tokens(client, user, from_ip):
    phone = login(from_ip("1.1.1.1")).json()["access_token"]
    laptop = login(from_ip("2.2.2.2")).json()["access_token"]
    me = lambda t: client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {t}"})
    assert me(phone).status_code == 200 and me(laptop).status_code == 200

    r = client.post("/api/v1/auth/change-password", headers={"Authorization": f"Bearer {laptop}"},
                    json={"current_password": "password123", "new_password": "newpassword456"})
    assert r.status_code == 200
    new_token = r.json()["access_token"]

    assert me(new_token).status_code == 200       # текущий сеанс продолжается с новым токеном
    assert me(laptop).status_code == 401           # старые токены отозваны
    assert me(phone).status_code == 401
    assert login(from_ip("3.3.3.3")).status_code == 401
    assert login(from_ip("3.3.3.3"), password="newpassword456").status_code == 200


@pytest.mark.parametrize("payload,code", [
    ({"current_password": "wrong", "new_password": "newpassword456"}, 400),
    ({"current_password": "password123", "new_password": "password123"}, 400),
    ({"current_password": "password123", "new_password": "short"}, 422),
])
def test_change_password_validation(client, user, from_ip, payload, code):
    token = login(from_ip("1.1.1.1")).json()["access_token"]
    r = client.post("/api/v1/auth/change-password", headers={"Authorization": f"Bearer {token}"}, json=payload)
    assert r.status_code == code
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_change_password_requires_auth(client):
    r = client.post("/api/v1/auth/change-password",
                    json={"current_password": "password123", "new_password": "newpassword456"})
    assert r.status_code == 401


def test_old_tokens_without_version_still_valid(client, user):
    # Токены, выданные до появления версий (без поля ver), продолжают работать
    import jwt
    from datetime import datetime, timedelta, timezone
    from app.config import settings
    legacy = jwt.encode({"sub": str(user.id), "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                        settings.secret_key, algorithm="HS256")
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {legacy}"}).status_code == 200
