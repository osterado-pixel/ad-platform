from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import stats as stats_module
from app.auth import create_access_token
from app.main import app
from app.models import Campaign, CampaignDailyStat, CampaignStatus, Placement, User
from app.routers import ads

TODAY_TS = 1_791_201_600  # 2026-10-05 12:00 UTC
TODAY = date(2026, 10, 5)


def bearer(user):
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.fixture(autouse=True)
def fixed_time(monkeypatch):
    monkeypatch.setattr(ads.time, "time", lambda: TODAY_TS)
    monkeypatch.setattr(stats_module.time, "time", lambda: TODAY_TS)


@pytest.fixture
def world(db):
    owner = User(email="owner@mail.ru", hashed_password="x", balance=Decimal("100"))
    other = User(email="other@mail.ru", hashed_password="x", balance=Decimal("50"))
    p1 = Placement(name="Шапка", code_identifier="header", price_per_click=Decimal("2.00"))
    p2 = Placement(name="Подвал", code_identifier="footer", price_per_click=Decimal("1.00"))
    db.add_all([owner, other, p1, p2])
    db.commit()
    c1 = Campaign(user_id=owner.id, placement_id=p1.id, title="Первая",
                  target_url="https://a.ru/", status=CampaignStatus.ACTIVE)
    c2 = Campaign(user_id=owner.id, placement_id=p2.id, title="Вторая",
                  target_url="https://b.ru/", status=CampaignStatus.DRAFT)
    c3 = Campaign(user_id=other.id, placement_id=p2.id, title="Чужая",
                  target_url="https://c.ru/", status=CampaignStatus.ACTIVE)
    db.add_all([c1, c2, c3])
    db.commit()
    # Прошлые дни
    db.add_all([
        CampaignDailyStat(campaign_id=c1.id, day=date(2026, 10, 3), impressions=10, clicks=2, spend=Decimal("4.00")),
        CampaignDailyStat(campaign_id=c3.id, day=date(2026, 10, 4), impressions=4, clicks=1, spend=Decimal("1.00")),
        CampaignDailyStat(campaign_id=c1.id, day=date(2026, 9, 1), impressions=99, clicks=9, spend=Decimal("18.00")),
    ])
    db.commit()
    return owner, other, (p1, p2), (c1, c2, c3)


@pytest.fixture
def traffic(client, world):
    """Сегодня: 4 показа и 2 оплаченных клика по первой кампании."""
    _, _, _, (c1, _, _) = world
    for ip in ["1.1.1.1", "2.2.2.2", "3.3.3.3", "4.4.4.4"]:
        v = TestClient(app, client=(ip, 1))
        assert v.get("/api/v1/ad/serve", params={"placement_code": "header"}).status_code == 200
    for ip in ["1.1.1.1", "2.2.2.2"]:
        TestClient(app, client=(ip, 1)).get(f"/api/v1/ad/click/{c1.id}", follow_redirects=False)
    return world


def test_campaign_stats_days_filled_with_zeros(client, traffic):
    owner, _, _, (c1, _, _) = traffic
    r = client.get(f"/api/v1/stats/campaigns/{c1.id}", params={"days": 4}, headers=bearer(owner))
    assert r.status_code == 200
    body = r.json()
    assert (body["period_start"], body["period_end"]) == ("2026-10-02", "2026-10-05")
    assert [(d["day"], d["impressions"], d["clicks"], d["spend"], d["ctr"]) for d in body["days"]] == [
        ("2026-10-02", 0, 0, 0.0, 0.0),
        ("2026-10-03", 10, 2, 4.0, 20.0),
        ("2026-10-04", 0, 0, 0.0, 0.0),
        ("2026-10-05", 4, 2, 4.0, 50.0),
    ]
    assert body["totals"] == {"impressions": 14, "clicks": 4, "spend": 8.0, "ctr": 28.57}


def test_campaign_stats_default_30_days_excludes_old(client, traffic):
    owner, _, _, (c1, _, _) = traffic
    body = client.get(f"/api/v1/stats/campaigns/{c1.id}", headers=bearer(owner)).json()
    assert len(body["days"]) == 30
    assert body["totals"]["impressions"] == 14  # 99 показов от 1 сентября вне периода


def test_campaign_stats_access(client, auth_headers, traffic):
    _, other, _, (c1, _, _) = traffic
    assert client.get(f"/api/v1/stats/campaigns/{c1.id}", headers=bearer(other)).status_code == 404
    assert client.get(f"/api/v1/stats/campaigns/{c1.id}", headers=auth_headers).status_code == 200
    assert client.get(f"/api/v1/stats/campaigns/{c1.id}").status_code == 401
    assert client.get("/api/v1/stats/campaigns/999", headers=auth_headers).status_code == 404


@pytest.mark.parametrize("days", [0, 366])
def test_days_limits(client, traffic, days):
    owner, _, _, (c1, _, _) = traffic
    assert client.get(f"/api/v1/stats/campaigns/{c1.id}", params={"days": days},
                      headers=bearer(owner)).status_code == 422


def test_my_stats(client, traffic):
    owner, _, _, (c1, c2, _) = traffic
    body = client.get("/api/v1/stats/me", params={"days": 7}, headers=bearer(owner)).json()
    assert body["balance"] == 96.0  # 100 − 2 клика × 2.00
    assert body["campaigns_by_status"]["active"] == 1
    assert body["campaigns_by_status"]["draft"] == 1
    assert body["campaigns_by_status"]["completed"] == 0
    assert body["totals"] == {"impressions": 14, "clicks": 4, "spend": 8.0, "ctr": 28.57}
    assert [(c["campaign_id"], c["impressions"], c["clicks"]) for c in body["campaigns"]] == [
        (c2.id, 0, 0), (c1.id, 14, 4),
    ]
    assert len(body["days"]) == 7


def test_my_stats_only_own(client, traffic):
    _, other, _, (_, _, c3) = traffic
    body = client.get("/api/v1/stats/me", params={"days": 7}, headers=bearer(other)).json()
    assert body["totals"]["impressions"] == 4
    assert [c["campaign_id"] for c in body["campaigns"]] == [c3.id]


def test_my_stats_new_user_empty(client, db):
    u = User(email="new@mail.ru", hashed_password="x")
    db.add(u)
    db.commit()
    body = client.get("/api/v1/stats/me", headers=bearer(u)).json()
    assert body["totals"] == {"impressions": 0, "clicks": 0, "spend": 0.0, "ctr": 0.0}
    assert body["campaigns"] == []


def test_platform_stats(client, auth_headers, traffic):
    _, _, (p1, p2), _ = traffic
    body = client.get("/api/v1/stats/platform", params={"days": 7}, headers=auth_headers).json()
    assert body["totals"] == {"impressions": 18, "clicks": 5, "spend": 9.0, "ctr": 27.78}
    assert body["users_count"] == 3  # owner, other, admin
    assert body["active_campaigns"] == 2
    assert body["moderation_queue"] == 0
    assert body["advertisers_balance"] == 146.0
    assert [(p["placement_id"], p["impressions"], p["clicks"], p["spend"]) for p in body["placements"]] == [
        (p1.id, 14, 4, 8.0), (p2.id, 4, 1, 1.0),
    ]


def test_platform_stats_admin_only(client, traffic):
    owner, _, _, _ = traffic
    assert client.get("/api/v1/stats/platform", headers=bearer(owner)).status_code == 403
