from datetime import timedelta

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth import (
    authenticate_user, create_access_token, get_current_user,
    get_password_hash, verify_password,
)
from app.config import settings
from app.database import get_db
from app.models import User


def test_hash_and_verify():
    h = get_password_hash("пароль123")
    assert h != "пароль123"
    assert verify_password("пароль123", h)
    assert not verify_password("wrong", h)


def test_long_password():
    with pytest.raises(ValueError):
        get_password_hash("я" * 37)  # 74 байта
    assert not verify_password("x" * 100, get_password_hash("x" * 72))


def test_verify_corrupted_hash():
    assert not verify_password("x", "not-a-hash")


def test_authenticate_user(db):
    db.add(User(email="a@b.ru", hashed_password=get_password_hash("12345678")))
    db.commit()
    assert authenticate_user(db, "A@B.ru", "12345678").email == "a@b.ru"
    assert authenticate_user(db, "a@b.ru", "wrong") is None
    assert authenticate_user(db, "none@b.ru", "12345678") is None


def _client(db):
    app = FastAPI()

    @app.get("/me")
    def me(user: User = Depends(get_current_user)):
        return {"email": user.email}

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_get_current_user(db):
    u = User(email="a@b.ru", hashed_password="x")
    db.add(u)
    db.commit()
    client = _client(db)

    ok = client.get("/me", headers={"Authorization": f"Bearer {create_access_token(u.id)}"})
    assert ok.json() == {"email": "a@b.ru"}

    expired = create_access_token(u.id, timedelta(seconds=-1))
    forged = jwt.encode({"sub": str(u.id), "exp": 9999999999}, "x" * 32, algorithm="HS256")
    unsigned = jwt.encode({"sub": str(u.id), "exp": 9999999999}, None, algorithm="none")
    no_exp = jwt.encode({"sub": str(u.id)}, settings.secret_key, algorithm="HS256")
    deleted = create_access_token(999)
    for token in [expired, forged, unsigned, no_exp, deleted, "garbage"]:
        r = client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401, token
    assert client.get("/me").status_code == 401


def test_bcrypt_rounds_from_settings():
    assert get_password_hash("12345678").startswith(f"$2b${settings.bcrypt_rounds:02d}$")
