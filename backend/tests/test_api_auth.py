import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def register(client, email="User@Mail.ru", password="secret123"):
    return client.post("/api/v1/auth/register", json={"email": email, "password": password})


def login(client, email="user@mail.ru", password="secret123"):
    return client.post("/api/v1/auth/login", data={"username": email, "password": password})


def test_full_flow(client):
    r = register(client)
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "user@mail.ru"
    assert body["role"] == "advertiser"
    assert body["balance"] == 0.0
    assert "password" not in body and "hashed_password" not in body

    r = login(client, email="USER@mail.ru")
    assert r.status_code == 200
    token = r.json()
    assert token["token_type"] == "bearer"

    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token['access_token']}"})
    assert r.status_code == 200
    assert r.json()["email"] == "user@mail.ru"


def test_register_duplicate_email(client):
    assert register(client).status_code == 201
    r = register(client, email="user@MAIL.ru")
    assert r.status_code == 409


@pytest.mark.parametrize("payload", [
    {"email": "bad", "password": "secret123"},
    {"email": "a@b.ru", "password": "short"},
    {"email": "a@b.ru", "password": "я" * 40},
    {"email": "a@b.ru"},
])
def test_register_validation(client, payload):
    assert client.post("/api/v1/auth/register", json=payload).status_code == 422


def test_register_ignores_role(client):
    r = client.post("/api/v1/auth/register",
                    json={"email": "a@b.ru", "password": "secret123", "role": "admin"})
    assert r.status_code == 201
    assert r.json()["role"] == "advertiser"


def test_login_wrong_password(client):
    register(client)
    r = login(client, password="wrong-pass")
    assert r.status_code == 401
    assert r.json()["detail"] == "Неверный email или пароль"


def test_login_unknown_email(client):
    r = login(client, email="nobody@mail.ru")
    assert r.status_code == 401
    assert r.json()["detail"] == "Неверный email или пароль"


def test_me_requires_token(client):
    assert client.get("/api/v1/auth/me").status_code == 401


def test_json_has_utf8_charset(client):
    assert client.get("/").headers["content-type"] == "application/json; charset=utf-8"
    r = login(client, email="nobody@mail.ru")
    assert r.headers["content-type"] == "application/json; charset=utf-8"
