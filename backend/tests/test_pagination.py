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
        body = r.json()
        ids += [x["id"] for x in (body["items"] if isinstance(body, dict) else body)]
        if r.headers["x-has-more"] == "false":
            assert "x-next-before-id" not in r.headers
            return ids
        params = {"limit": limit, "before_id": r.headers["x-next-before-id"]}


@pytest.mark.parametrize("url", ["/api/v1/campaigns/my", "/api/v1/campaigns"])
def test_my_campaigns_pages(client, owner, campaigns, url):
    ids, offset = [], 0
    while True:  # 3 + 3 + 1
        body = client.get(url, params={"limit": 3, "offset": offset}, headers=bearer(owner)).json()
        assert (body["total"], body["limit"], body["offset"]) == (7, 3, offset)
        ids += [c["id"] for c in body["items"]]
        offset += len(body["items"])
        if offset >= body["total"]:
            break
    assert ids == campaigns  # новые сверху, без пропусков и повторов


def test_default_limit_is_bounded(client, db, owner):
    p = Placement(name="P", code_identifier="p")
    db.add(p)
    db.commit()
    db.add_all([Campaign(user_id=owner.id, placement_id=p.id, title=f"C{i}", target_url="https://a.ru/")
                for i in range(55)])
    db.commit()
    body = client.get("/api/v1/campaigns/my", headers=bearer(owner)).json()
    assert (len(body["items"]), body["total"], body["limit"]) == (10, 55, 10)


def test_wallet_history_cursor(client, db, owner):
    from app.ledger import add_transaction
    for i in range(5):
        add_transaction(db, user_id=owner.id, amount=Decimal(i + 1), type=TransactionType.DEPOSIT)
    db.commit()
    ids = walk(client, "/api/v1/wallet/history", bearer(owner), limit=2)
    assert ids == sorted(ids, reverse=True) and len(ids) == 5 and len(set(ids)) == 5
    # total — во всех страницах общий, и с курсором тоже
    r = client.get("/api/v1/wallet/history", params={"limit": 2, "before_id": ids[1]}, headers=bearer(owner))
    assert [t["id"] for t in r.json()["items"]] == ids[2:4] and r.json()["total"] == 5


@pytest.mark.parametrize("url", ["/api/v1/campaigns/my", "/api/v1/wallet/history", "/api/v1/users",
                                 "/api/v1/campaigns", "/api/v1/placements", "/api/v1/placements/all"])
@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 501}, {"offset": -1}, {"offset": MAX_OFFSET + 1}])
def test_limits_enforced(client, auth_headers, url, params):
    assert client.get(url, params=params, headers=auth_headers).status_code == 422


def test_cursor_validation(client, owner):
    r = client.get("/api/v1/wallet/history", params={"before_id": 0}, headers=bearer(owner))
    assert r.status_code == 422


def test_admin_lists_have_more(client, auth_headers, db, owner, campaigns):
    body = client.get("/api/v1/campaigns", params={"limit": 5}, headers=auth_headers).json()
    assert len(body["items"]) == 5 and body["total"] == 7
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


def test_wallet_history_pagination(client, auth_headers):
    # Пополняем кошелек 5 раз, чтобы создать 5 транзакций
    for _ in range(5):
        client.post("/api/v1/wallet/deposit", json={"amount": 10.0}, headers=auth_headers)

    # Запрашиваем страницу 1 (limit=2, offset=0)
    page1 = client.get("/api/v1/wallet/history?limit=2&offset=0", headers=auth_headers)
    assert page1.status_code == 200
    data1 = page1.json()
    assert data1["total"] == 5
    assert len(data1["items"]) == 2
    assert data1["limit"] == 2
    assert data1["offset"] == 0

    # Запрашиваем страницу 2 (limit=2, offset=2)
    page2 = client.get("/api/v1/wallet/history?limit=2&offset=2", headers=auth_headers)
    assert page2.status_code == 200
    data2 = page2.json()
    assert len(data2["items"]) == 2
    assert data2["offset"] == 2

    # Страница 3 — оставшаяся запись; страницы не пересекаются и идут новыми сверху
    data3 = client.get("/api/v1/wallet/history?limit=2&offset=4", headers=auth_headers).json()
    assert len(data3["items"]) == 1
    ids = [t["id"] for d in (data1, data2, data3) for t in d["items"]]
    assert len(set(ids)) == 5 and ids == sorted(ids, reverse=True)
