from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.auth import create_access_token
from app.database import get_db
from app.main import app
from app.models import Campaign, CampaignStatus, Placement, Transaction, TransactionType, User, UserRole

W = "/api/v1/wallet"


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def make_user(db, email, role=UserRole.ADVERTISER, balance="0"):
    u = User(email=email, hashed_password="x", role=role, balance=Decimal(balance))
    db.add(u)
    db.commit()
    return u, {"Authorization": f"Bearer {create_access_token(u.id)}"}


def test_admin_deposits_to_self(client, db):
    admin, h = make_user(db, "admin@mail.ru", UserRole.ADMIN, "10")
    r = client.post(f"{W}/deposit", json={"amount": 100.5}, headers=h)
    assert r.status_code == 200
    assert r.json() == {"balance": 110.5, "held_balance": 0.0}
    t = db.query(Transaction).one()
    assert (t.user_id, t.amount, t.type) == (admin.id, Decimal("100.50"), TransactionType.DEPOSIT)


def test_admin_deposits_to_other_user(client, db):
    _, h = make_user(db, "admin@mail.ru", UserRole.ADMIN)
    user, hu = make_user(db, "a@mail.ru")
    r = client.post(f"{W}/deposit", params={"user_id": user.id}, json={"amount": 50}, headers=h)
    assert r.json() == {"balance": 50.0, "held_balance": 0.0}
    assert client.get(f"{W}/balance", headers=hu).json() == {"balance": 50.0, "held_balance": 0.0}
    assert db.query(Transaction).one().user_id == user.id


def test_advertiser_cannot_deposit(client, db):
    user, h = make_user(db, "a@mail.ru")
    r = client.post(f"{W}/deposit", json={"amount": 1000000}, headers=h)
    assert r.status_code == 403
    db.expire_all()
    assert db.get(User, user.id).balance == Decimal("0")
    assert db.query(Transaction).count() == 0


def test_deposit_unknown_user(client, db):
    _, h = make_user(db, "admin@mail.ru", UserRole.ADMIN)
    r = client.post(f"{W}/deposit", params={"user_id": 999}, json={"amount": 5}, headers=h)
    assert r.status_code == 404
    assert db.query(Transaction).count() == 0


@pytest.mark.parametrize("amount", [0, -5, "0.001", 1_000_001])
def test_deposit_invalid_amount(client, db, amount):
    _, h = make_user(db, "admin@mail.ru", UserRole.ADMIN)
    assert client.post(f"{W}/deposit", json={"amount": amount}, headers=h).status_code == 422


def test_deposit_does_not_resume_paused_campaigns(client, db):
    admin, h = make_user(db, "admin@mail.ru", UserRole.ADMIN)
    p = Placement(name="P", code_identifier="p")
    db.add(p)
    db.commit()
    c = Campaign(user_id=admin.id, placement_id=p.id, title="T", target_url="https://a.ru/",
                 status=CampaignStatus.PAUSED)
    db.add(c)
    db.commit()
    client.post(f"{W}/deposit", json={"amount": 10}, headers=h)
    db.expire_all()
    assert db.get(Campaign, c.id).status is CampaignStatus.PAUSED


def test_history_own_only_newest_first_paginated(client, db):
    a, ha = make_user(db, "a@mail.ru")
    b, _ = make_user(db, "b@mail.ru")
    from app.ledger import add_transaction
    for amount in ["1", "2", "3"]:
        add_transaction(db, user_id=a.id, amount=Decimal(amount), type=TransactionType.DEPOSIT)
    add_transaction(db, user_id=b.id, amount=Decimal("99"), type=TransactionType.DEPOSIT)
    db.commit()

    body = client.get(f"{W}/history", headers=ha).json()
    assert [t["amount"] for t in body["items"]] == [3.0, 2.0, 1.0]
    assert (body["total"], body["limit"], body["offset"]) == (3, 20, 0)  # чужая операция не считается
    body = client.get(f"{W}/history", params={"limit": 1, "offset": 1}, headers=ha).json()
    assert [t["amount"] for t in body["items"]] == [2.0] and body["total"] == 3
    assert client.get(f"{W}/history", params={"limit": 0}, headers=ha).status_code == 422


def test_wallet_requires_auth(client):
    assert client.get(f"{W}/balance").status_code == 401
    assert client.get(f"{W}/history").status_code == 401
    assert client.post(f"{W}/deposit", json={"amount": 1}).status_code == 401


@pytest.mark.parametrize("amount", [100.0, 500, "500.5"])
def test_deposit_description_two_decimals(client, db, amount):
    _, h = make_user(db, "admin@mail.ru", UserRole.ADMIN)
    client.post(f"{W}/deposit", json={"amount": amount}, headers=h)
    expected = f"{Decimal(str(amount)):.2f}"
    assert db.query(Transaction).one().description == f"Пополнение баланса на {expected}"
