"""Постраничная выдача: limit, offset (с потолком), курсор before_id, признак X-Has-More."""
from decimal import Decimal

import pytest

from app.auth import create_access_token
from app.models import Campaign, CampaignStatus, Placement, Transaction, TransactionType, User
from app.pagination import MAX_OFFSET


def bearer(user):
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.fixture
def owner(db):
    u = User(email="owner@mail.ru", hashed_password="x")
    db.add(u)
    db.commit()
    return u


@pytest.fixture
def campaigns(db, owner):
    p = Placement(name="P", code_identifier="p")
    db.add(p)
    db.commit()
    db.add_all([Campaign(user_id=owner.id, placement_id=p.id, title=f"C{i}", target_url="https://a.ru/",
                         status=CampaignStatus.DRAFT) for i in range(7)])
    db.commit()
    return [c.id for c in db.query(Campaign).order_by(Campaign.id.desc())]


def walk(client, url, headers, limit):
    """Проходит все страницы по курсору X-Next-Before-Id."""
    ids, params = [], {"limit": limit}
    while True:
        r = client.get(url, params=params, headers=headers)
        assert r.status_code == 200
        ids += [x["id"] for x in r.json()]
        if r.headers["x-has-more"] == "false":
            assert "x-next-before-id" not in r.headers
            return ids
        params = {"limit": limit, "before_id": r.headers["x-next-before-id"]}


def test_my_campaigns_cursor_walks_all_pages(client, owner, campaigns):
    assert walk(client, "/api/v1/campaigns/my", bearer(owner), limit=3) == campaigns  # 3 + 3 + 1


def test_my_campaigns_offset_and_has_more(client, owner, campaigns):
    r = client.get("/api/v1/campaigns/my", params={"limit": 7}, headers=bearer(owner))
    assert len(r.json()) == 7 and r.headers["x-has-more"] == "false"
    r = client.get("/api/v1/campaigns/my", params={"limit": 2, "offset": 2}, headers=bearer(owner))
    assert [c["id"] for c in r.json()] == campaigns[2:4]
    assert r.headers["x-has-more"] == "true"


def test_default_limit_is_bounded(client, db, owner):
    p = Placement(name="P", code_identifier="p")
    db.add(p)
    db.commit()
    db.add_all([Campaign(user_id=owner.id, placement_id=p.id, title=f"C{i}", target_url="https://a.ru/")
                for i in range(55)])
    db.commit()
    r = client.get("/api/v1/campaigns/my", headers=bearer(owner))
    assert len(r.json()) == 50 and r.headers["x-has-more"] == "true"


def test_wallet_history_cursor(client, db, owner):
    db.add_all([Transaction(user_id=owner.id, amount=Decimal(i + 1), type=TransactionType.DEPOSIT)
                for i in range(5)])
    db.commit()
    ids = walk(client, "/api/v1/wallet/history", bearer(owner), limit=2)
    assert ids == sorted(ids, reverse=True) and len(ids) == 5 and len(set(ids)) == 5


@pytest.mark.parametrize("url", ["/api/v1/campaigns/my", "/api/v1/wallet/history", "/api/v1/users",
                                 "/api/v1/campaigns", "/api/v1/placements", "/api/v1/placements/all"])
@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 501}, {"offset": -1}, {"offset": MAX_OFFSET + 1}])
def test_limits_enforced(client, auth_headers, url, params):
    assert client.get(url, params=params, headers=auth_headers).status_code == 422


def test_cursor_validation(client, owner):
    r = client.get("/api/v1/wallet/history", params={"before_id": 0}, headers=bearer(owner))
    assert r.status_code == 422


def test_admin_lists_have_more(client, auth_headers, db, owner, campaigns):
    r = client.get("/api/v1/campaigns", params={"limit": 5}, headers=auth_headers)
    assert len(r.json()) == 5 and r.headers["x-has-more"] == "true"
    r = client.get("/api/v1/users", params={"limit": 1}, headers=auth_headers)
    assert len(r.json()) == 1 and r.headers["x-has-more"] == "true"


def test_placements_paginated(client, auth_headers, db):
    db.add_all([Placement(name=f"P{i}", code_identifier=f"p{i}", is_active=i % 2 == 0) for i in range(6)])
    db.commit()
    # Публичный список: только активные (p0, p2, p4), по порядку, с общим числом
    body = client.get("/api/v1/placements", params={"limit": 2}).json()
    assert [p["code_identifier"] for p in body["items"]] == ["p0", "p2"]
    assert (body["total"], body["limit"], body["offset"]) == (3, 2, 0)
    body = client.get("/api/v1/placements", params={"limit": 2, "offset": 2}).json()
    assert [p["code_identifier"] for p in body["items"]] == ["p4"] and body["total"] == 3
    # Админский — все 6, включая отключённые
    body = client.get("/api/v1/placements/all", params={"limit": 4, "offset": 4}, headers=auth_headers).json()
    assert [p["code_identifier"] for p in body["items"]] == ["p4", "p5"] and body["total"] == 6


def test_placements_default_page_size(client, db):
    db.add_all([Placement(name=f"P{i}", code_identifier=f"p{i}") for i in range(12)])
    db.commit()
    body = client.get("/api/v1/placements").json()
    assert (len(body["items"]), body["total"], body["limit"]) == (10, 12, 10)
    # Деньги в элементах — числом, как во всём API
    assert body["items"][0]["price_per_click"] == 0.0


def test_stats_lists_bounded(client, auth_headers, owner, campaigns):
    body = client.get("/api/v1/stats/me", params={"campaigns_limit": 3}, headers=bearer(owner)).json()
    assert [c["campaign_id"] for c in body["campaigns"]] == campaigns[:3]
    assert body["campaigns_has_more"] is True
    body = client.get("/api/v1/stats/me", headers=bearer(owner)).json()
    assert len(body["campaigns"]) == 7 and body["campaigns_has_more"] is False
    assert client.get("/api/v1/stats/me", params={"campaigns_limit": 501}, headers=bearer(owner)).status_code == 422
    body = client.get("/api/v1/stats/platform", params={"placements_limit": 1}, headers=auth_headers).json()
    assert len(body["placements"]) == 1 and body["placements_has_more"] is False
