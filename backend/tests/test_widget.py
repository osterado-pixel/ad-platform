from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Campaign, CampaignDailyStat, CampaignStatus, Placement, User
from app.routers import ads

SERVE = "/api/v1/ad/serve"


@pytest.fixture
def world(db):
    user = User(email="a@mail.ru", hashed_password="x", balance=Decimal("100"))
    placement = Placement(name="Шапка", code_identifier="header", price_per_click=Decimal("2.50"))
    db.add_all([user, placement])
    db.commit()
    campaign = Campaign(user_id=user.id, placement_id=placement.id, title="Ad",
                        target_url="https://shop.ru/", status=CampaignStatus.ACTIVE)
    db.add(campaign)
    db.commit()
    return user, placement, campaign


@pytest.fixture
def visitor(client):
    """Посетитель сайта со своим IP и браузером; работает с тестовой БД (через фикстуру client)."""
    def _make(ip="1.1.1.1", ua="Mozilla/5.0"):
        return TestClient(app, client=(ip, 50000), headers={"User-Agent": ua})
    return _make


def daily(db, campaign):
    db.expire_all()
    return db.get(CampaignDailyStat, (campaign.id, date(2026, 10, 5)))


@pytest.fixture
def fixed_day(monkeypatch):
    # 2026-10-05 12:00 UTC — и для статистики, и для окна повторных кликов
    monkeypatch.setattr(ads.time, "time", lambda: 1_791_201_600)


# --- CORS ---

def test_cors_allows_any_site_for_serve(client, world):
    r = client.get(SERVE, params={"placement_code": "header"},
                   headers={"Origin": "https://partner-site.ru"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in r.headers


def test_cors_preflight(client):
    r = client.options(SERVE, headers={
        "Origin": "https://partner-site.ru",
        "Access-Control-Request-Method": "GET",
    })
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == "*"


# --- widget.js и демо ---

def test_widget_js_served(client):
    r = client.get("/widget.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/javascript")
    assert "max-age=300" in r.headers["cache-control"]
    assert "/api/v1/ad/serve" in r.text
    assert ".innerHTML" not in r.text  # текст рекламодателя только через textContent


def test_demo_page(client):
    r = client.get("/demo")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert '/widget.js' in r.text


# --- показы ---

def test_serve_counts_impression(visitor, db, world, fixed_day):
    _, _, campaign = world
    for ip in ["1.1.1.1", "2.2.2.2", "1.1.1.1"]:
        assert visitor(ip).get(SERVE, params={"placement_code": "header"}).status_code == 200
    db.expire_all()
    assert db.get(Campaign, campaign.id).impressions_count == 3
    assert daily(db, campaign).impressions == 3


def test_bot_impression_not_counted(visitor, db, world, fixed_day):
    _, _, campaign = world
    visitor(ua="Googlebot/2.1").get(SERVE, params={"placement_code": "header"})
    db.expire_all()
    assert db.get(Campaign, campaign.id).impressions_count == 0
    assert daily(db, campaign) is None


def test_paid_click_updates_daily_stats(visitor, db, world, fixed_day):
    _, _, campaign = world
    visitor("1.1.1.1").get(f"/api/v1/ad/click/{campaign.id}", follow_redirects=False)
    visitor("2.2.2.2").get(f"/api/v1/ad/click/{campaign.id}", follow_redirects=False)
    visitor("2.2.2.2").get(f"/api/v1/ad/click/{campaign.id}", follow_redirects=False)  # повтор
    row = daily(db, campaign)
    assert (row.clicks, row.spend) == (2, Decimal("5.00"))


def test_stats_split_by_day(visitor, db, world, monkeypatch):
    _, _, campaign = world
    monkeypatch.setattr(ads.time, "time", lambda: 1_791_201_600)          # 2026-10-05
    visitor().get(SERVE, params={"placement_code": "header"})
    monkeypatch.setattr(ads.time, "time", lambda: 1_791_201_600 + 86400)  # 2026-10-06
    visitor().get(SERVE, params={"placement_code": "header"})
    visitor().get(SERVE, params={"placement_code": "header"})
    db.expire_all()
    rows = {r.day: r.impressions for r in db.query(CampaignDailyStat)}
    assert rows == {date(2026, 10, 5): 1, date(2026, 10, 6): 2}


def test_campaign_response_has_impressions(visitor, client, auth_headers, db, world):
    _, _, campaign = world
    visitor().get(SERVE, params={"placement_code": "header"})
    r = client.get(f"/api/v1/campaigns/{campaign.id}", headers=auth_headers)
    assert r.json()["impressions_count"] == 1


def test_serve_empty_status_for_widget(client, db, world):
    _, _, campaign = world
    campaign.status = CampaignStatus.PAUSED
    db.commit()
    assert client.get(SERVE, params={"placement_code": "header"}).status_code == 404
    r = client.get(SERVE, params={"placement_code": "header", "empty_status": 204})
    assert (r.status_code, r.content, r.headers["cache-control"]) == (204, b"", "no-store")
    # Неверный код площадки — всё равно 404: партнёр увидит, что вставил код с ошибкой
    assert client.get(SERVE, params={"placement_code": "nope", "empty_status": 204}).status_code == 404
    assert client.get(SERVE, params={"placement_code": "header", "empty_status": 500}).status_code == 422
