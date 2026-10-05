"""User.is_admin — производный от role флаг: один источник правды о правах."""
from sqlalchemy import select

from app.auth import create_access_token
from app.models import User, UserRole


def test_read_and_write(db):
    u = User(email="a@mail.ru", hashed_password="x")
    db.add(u)
    db.commit()
    assert u.is_admin is False and u.role is UserRole.ADVERTISER
    u.is_admin = True
    db.commit()
    db.expire_all()
    assert db.get(User, u.id).role is UserRole.ADMIN and db.get(User, u.id).is_admin is True
    u.is_admin = False
    assert u.role is UserRole.ADVERTISER


def test_constructor_and_query(db):
    db.add_all([User(email="boss@mail.ru", hashed_password="x", is_admin=True),
                User(email="plain@mail.ru", hashed_password="x")])
    db.commit()
    admins = db.scalars(select(User.email).where(User.is_admin == True)).all()  # noqa: E712
    assert admins == ["boss@mail.ru"]
    assert db.query(User).filter(User.is_admin.is_(False)).one().email == "plain@mail.ru"


def test_cannot_diverge_from_role(db):
    u = User(email="a@mail.ru", hashed_password="x", role=UserRole.ADMIN)
    db.add(u)
    db.commit()
    u.role = UserRole.ADVERTISER  # смена роли сразу меняет флаг — разойтись им негде
    assert u.is_admin is False


def test_api_exposes_is_admin_and_access_matches(client, db):
    admin = User(email="boss@mail.ru", hashed_password="x", is_admin=True)
    plain = User(email="plain@mail.ru", hashed_password="x")
    db.add_all([admin, plain])
    db.commit()
    h = lambda u: {"Authorization": f"Bearer {create_access_token(u.id)}"}
    assert client.get("/api/v1/auth/me", headers=h(admin)).json()["is_admin"] is True
    assert client.get("/api/v1/auth/me", headers=h(plain)).json()["is_admin"] is False
    # Права в API определяются тем же признаком
    assert client.get("/api/v1/users", headers=h(admin)).status_code == 200
    assert client.get("/api/v1/users", headers=h(plain)).status_code == 403


def test_get_current_admin_is_same_dependency(client, db):
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from app.auth import get_current_admin, require_admin
    from app.database import get_db
    assert get_current_admin is require_admin

    probe = FastAPI()

    @probe.get("/admin-only")
    def admin_only(admin: User = Depends(get_current_admin)):
        return {"email": admin.email}

    probe.dependency_overrides[get_db] = lambda: db
    c = TestClient(probe)
    admin = User(email="boss@mail.ru", hashed_password="x", is_admin=True)
    plain = User(email="plain@mail.ru", hashed_password="x")
    db.add_all([admin, plain])
    db.commit()
    h = lambda u: {"Authorization": f"Bearer {create_access_token(u.id)}"}
    assert c.get("/admin-only", headers=h(admin)).json() == {"email": "boss@mail.ru"}
    r = c.get("/admin-only", headers=h(plain))
    assert r.status_code == 403 and r.json()["detail"] == "Недостаточно прав. Требуются права администратора."
    assert c.get("/admin-only").status_code == 401
