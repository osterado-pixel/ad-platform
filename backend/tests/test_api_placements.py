import pytest
from fastapi.testclient import TestClient

from app.auth import create_access_token, get_password_hash
from app.database import get_db
from app.main import app
from app.models import Placement, User, UserRole

URL = "/api/v1/placements"
BANNER = {"name": "Главный баннер", "code_identifier": "main_banner",
          "price_per_day": 100, "price_per_click": "2.5"}


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def auth_headers(db, role):
    u = User(email=f"{role.value}@mail.ru", hashed_password=get_password_hash("secret123"), role=role)
    db.add(u)
    db.commit()
    return {"Authorization": f"Bearer {create_access_token(u.id)}"}


def test_admin_creates_placement(client, db):
    r = client.post(URL, json=BANNER, headers=auth_headers(db, UserRole.ADMIN))
    assert r.status_code == 201
    body = r.json()
    assert body["code_identifier"] == "main_banner"
    assert body["price_per_day"] == 100.0
    assert body["price_per_click"] == 2.5
    assert body["is_active"] is True


def test_trailing_slash_no_redirect(client, db):
    r = client.post(URL + "/", json=BANNER, headers=auth_headers(db, UserRole.ADMIN),
                    follow_redirects=False)
    assert r.status_code == 201


def test_advertiser_cannot_create(client, db):
    r = client.post(URL, json=BANNER, headers=auth_headers(db, UserRole.ADVERTISER))
    assert r.status_code == 403
    assert db.query(Placement).count() == 0


def test_anonymous_cannot_create(client):
    assert client.post(URL, json=BANNER).status_code == 401


def test_duplicate_code(client, db):
    headers = auth_headers(db, UserRole.ADMIN)
    assert client.post(URL, json=BANNER, headers=headers).status_code == 201
    r = client.post(URL, json={**BANNER, "name": "Другой"}, headers=headers)
    assert r.status_code == 409


def test_invalid_payload(client, db):
    r = client.post(URL, json={**BANNER, "price_per_day": -5}, headers=auth_headers(db, UserRole.ADMIN))
    assert r.status_code == 422


def test_public_list_only_active(client, db):
    db.add_all([
        Placement(name="B", code_identifier="b"),
        Placement(name="Off", code_identifier="off", is_active=False),
        Placement(name="A", code_identifier="a"),
    ])
    db.commit()
    for url in (URL, URL + "/"):
        r = client.get(url, follow_redirects=False)
        assert r.status_code == 200
        assert [p["code_identifier"] for p in r.json()] == ["b", "a"]


def test_make_admin_cli(db, monkeypatch, capsys):
    from app import cli
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=db.get_bind()))
    db.add(User(email="boss@mail.ru", hashed_password="x"))
    db.commit()
    assert cli.make_admin("Boss@Mail.ru") == 0
    db.expire_all()
    assert db.query(User).one().role is UserRole.ADMIN
    assert cli.make_admin("nobody@mail.ru") == 1
