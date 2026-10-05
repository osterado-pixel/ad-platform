import pytest
from fastapi.testclient import TestClient

from app.auth import create_access_token
from app.database import get_db
from app.main import app
from app.models import Campaign, CampaignStatus, Placement, User, UserRole

URL = "/api/v1/campaigns"


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def placement(db):
    p = Placement(name="Баннер", code_identifier="banner")
    db.add(p)
    db.commit()
    return p


def headers_for(db, email):
    u = User(email=email, hashed_password="x")
    db.add(u)
    db.commit()
    return u, {"Authorization": f"Bearer {create_access_token(u.id)}"}


def payload(placement_id, **kw):
    return {"placement_id": placement_id, "title": "Распродажа",
            "target_url": "https://shop.ru/sale", **kw}


def test_create_campaign(client, db, placement):
    user, h = headers_for(db, "a@mail.ru")
    r = client.post(URL, json=payload(placement.id), headers=h)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "draft"
    assert body["user_id"] == user.id
    assert body["placement_id"] == placement.id


def test_trailing_slash(client, db, placement):
    _, h = headers_for(db, "a@mail.ru")
    r = client.post(URL + "/", json=payload(placement.id), headers=h, follow_redirects=False)
    assert r.status_code == 201


def test_client_cannot_set_status_or_owner(client, db, placement):
    user, h = headers_for(db, "a@mail.ru")
    other, _ = headers_for(db, "b@mail.ru")
    r = client.post(URL, json=payload(placement.id, status="active", user_id=other.id), headers=h)
    assert r.status_code == 201
    assert r.json()["status"] == "draft"
    assert r.json()["user_id"] == user.id


def test_unknown_or_inactive_placement(client, db, placement):
    _, h = headers_for(db, "a@mail.ru")
    assert client.post(URL, json=payload(999), headers=h).status_code == 404
    placement.is_active = False
    db.commit()
    assert client.post(URL, json=payload(placement.id), headers=h).status_code == 404
    assert db.query(Campaign).count() == 0


def test_requires_auth(client, placement):
    assert client.post(URL, json=payload(placement.id)).status_code == 401
    assert client.get(URL + "/my").status_code == 401


def test_bad_url_rejected(client, db, placement):
    _, h = headers_for(db, "a@mail.ru")
    r = client.post(URL, json=payload(placement.id, target_url="javascript:alert(1)"), headers=h)
    assert r.status_code == 422


def test_my_campaigns_only_own_newest_first(client, db, placement):
    _, ha = headers_for(db, "a@mail.ru")
    _, hb = headers_for(db, "b@mail.ru")
    for title in ["Первая", "Вторая"]:
        client.post(URL, json=payload(placement.id, title=title), headers=ha)
    client.post(URL, json=payload(placement.id, title="Чужая"), headers=hb)

    r = client.get(URL + "/my", headers=ha)
    assert r.status_code == 200
    assert [c["title"] for c in r.json()["items"]] == ["Вторая", "Первая"]
    assert r.json()["total"] == 2


def admin_headers(db):
    u = User(email="admin@mail.ru", hashed_password="x", role=UserRole.ADMIN)
    db.add(u)
    db.commit()
    return {"Authorization": f"Bearer {create_access_token(u.id)}"}


def create(client, h, placement):
    r = client.post(URL, json=payload(placement.id), headers=h)
    assert r.status_code == 201
    return r.json()["id"]


def test_full_moderation_flow_approve(client, db, placement):
    _, h = headers_for(db, "a@mail.ru")
    ha = admin_headers(db)
    cid = create(client, h, placement)

    r = client.post(f"{URL}/{cid}/submit", headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "moderation"

    r = client.patch(f"{URL}/{cid}/moderate", json={"status": "active"}, headers=ha)
    assert r.status_code == 200
    assert r.json()["status"] == "active"
    assert r.json()["rejection_reason"] is None


def test_reject_then_resubmit(client, db, placement):
    _, h = headers_for(db, "a@mail.ru")
    ha = admin_headers(db)
    cid = create(client, h, placement)
    client.post(f"{URL}/{cid}/submit", headers=h)

    r = client.patch(f"{URL}/{cid}/moderate",
                     json={"status": "rejected", "rejection_reason": "Плохая картинка"}, headers=ha)
    assert r.json()["status"] == "rejected"
    assert r.json()["rejection_reason"] == "Плохая картинка"

    r = client.post(f"{URL}/{cid}/submit", headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "moderation"
    assert r.json()["rejection_reason"] is None


def test_advertiser_cannot_moderate_own_campaign(client, db, placement):
    _, h = headers_for(db, "a@mail.ru")
    cid = create(client, h, placement)
    client.post(f"{URL}/{cid}/submit", headers=h)
    r = client.patch(f"{URL}/{cid}/moderate", json={"status": "active"}, headers=h)
    assert r.status_code == 403
    assert db.get(Campaign, cid).status is CampaignStatus.MODERATION


def test_cannot_moderate_draft(client, db, placement):
    _, h = headers_for(db, "a@mail.ru")
    cid = create(client, h, placement)
    r = client.patch(f"{URL}/{cid}/moderate", json={"status": "active"}, headers=admin_headers(db))
    assert r.status_code == 409


def test_cannot_submit_twice_or_active(client, db, placement):
    _, h = headers_for(db, "a@mail.ru")
    ha = admin_headers(db)
    cid = create(client, h, placement)
    assert client.post(f"{URL}/{cid}/submit", headers=h).status_code == 200
    assert client.post(f"{URL}/{cid}/submit", headers=h).status_code == 409
    client.patch(f"{URL}/{cid}/moderate", json={"status": "active"}, headers=ha)
    assert client.post(f"{URL}/{cid}/submit", headers=h).status_code == 409


def test_cannot_submit_foreign_campaign(client, db, placement):
    _, ha = headers_for(db, "a@mail.ru")
    _, hb = headers_for(db, "b@mail.ru")
    cid = create(client, ha, placement)
    assert client.post(f"{URL}/{cid}/submit", headers=hb).status_code == 404
    assert client.post(f"{URL}/99999/submit", headers=ha).status_code == 404


def test_moderate_validation(client, db, placement):
    _, h = headers_for(db, "a@mail.ru")
    ha = admin_headers(db)
    cid = create(client, h, placement)
    client.post(f"{URL}/{cid}/submit", headers=h)
    assert client.patch(f"{URL}/{cid}/moderate", json={"status": "rejected"}, headers=ha).status_code == 422
    assert client.patch(f"{URL}/{cid}/moderate", json={"status": "draft"}, headers=ha).status_code == 422
    assert client.patch(f"{URL}/99999/moderate", json={"status": "active"}, headers=ha).status_code == 404
