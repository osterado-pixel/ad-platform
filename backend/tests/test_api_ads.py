from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import Campaign, CampaignStatus, Placement, User

SERVE = "/api/v1/ad/serve"


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def setup(db):
    user = User(email="a@mail.ru", hashed_password="x")
    placement = Placement(name="Шапка", code_identifier="header")
    db.add_all([user, placement])
    db.commit()

    def add(title, status=CampaignStatus.ACTIVE, **kw):
        c = Campaign(user_id=user.id, placement_id=placement.id, title=title,
                     target_url=f"https://shop.ru/{title}", status=status, **kw)
        db.add(c)
        db.commit()
        return c

    return placement, add


def test_serve_returns_public_fields_only(client, setup):
    _, add = setup
    c = add("one", description="Скидки", image_url="https://cdn.ru/b.png")
    r = client.get(SERVE, params={"placement_code": "header"})
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    assert r.json() == {
        "campaign_id": c.id, "title": "one", "description": "Скидки",
        "image_url": "https://cdn.ru/b.png",
        "click_url": f"http://testserver/api/v1/ad/click/{c.id}",
    }


def test_serve_rotates_only_servable(client, setup):
    _, add = setup
    now = datetime.now(timezone.utc)
    add("a")
    add("b", start_date=now - timedelta(days=1), end_date=now + timedelta(days=1))
    add("draft", status=CampaignStatus.DRAFT)
    add("moderation", status=CampaignStatus.MODERATION)
    add("rejected", status=CampaignStatus.REJECTED, rejection_reason="x")
    add("expired", end_date=now - timedelta(minutes=5))
    add("future", start_date=now + timedelta(minutes=5))

    seen = {client.get(SERVE, params={"placement_code": "header"}).json()["title"] for _ in range(60)}
    assert seen == {"a", "b"}


def test_serve_unknown_or_inactive_placement(client, db, setup):
    placement, add = setup
    add("a")
    assert client.get(SERVE, params={"placement_code": "nope"}).status_code == 404
    placement.is_active = False
    db.commit()
    assert client.get(SERVE, params={"placement_code": "header"}).status_code == 404


def test_serve_no_campaigns(client, setup):
    r = client.get(SERVE, params={"placement_code": "header"})
    assert r.status_code == 404
    assert r.json()["detail"] == "Для данной площадки нет активных рекламных кампаний"


def test_serve_requires_code(client):
    assert client.get(SERVE).status_code == 422


def test_click_redirects_active(client, setup):
    _, add = setup
    c = add("one")
    r = client.get(f"/api/v1/ad/click/{c.id}", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "https://shop.ru/one"
    assert r.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("status,kw", [
    (CampaignStatus.DRAFT, {}),
    (CampaignStatus.MODERATION, {}),
    (CampaignStatus.REJECTED, {"rejection_reason": "x"}),
    (CampaignStatus.PAUSED, {}),
    (CampaignStatus.ACTIVE, {"end_date": datetime.now(timezone.utc) - timedelta(days=1)}),
])
def test_click_not_servable(client, setup, status, kw):
    _, add = setup
    c = add("x", status=status, **kw)
    assert client.get(f"/api/v1/ad/click/{c.id}", follow_redirects=False).status_code == 404


def test_click_unknown(client):
    assert client.get("/api/v1/ad/click/999", follow_redirects=False).status_code == 404
