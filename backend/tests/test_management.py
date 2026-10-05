from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.auth import create_access_token
from app.models import Campaign, CampaignStatus, Placement, User, UserRole

C = "/api/v1/campaigns"
SERVE = "/api/v1/ad/serve"


def bearer(user):
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.fixture
def owner(db):
    u = User(email="owner@mail.ru", hashed_password="x", balance=Decimal("100"))
    db.add(u)
    db.commit()
    return u


@pytest.fixture
def placements(db):
    a = Placement(name="Шапка", code_identifier="header")
    b = Placement(name="Подвал", code_identifier="footer")
    off = Placement(name="Отключена", code_identifier="off", is_active=False)
    db.add_all([a, b, off])
    db.commit()
    return a, b, off


def make(db, owner, placement, status=CampaignStatus.DRAFT, **kw):
    c = Campaign(user_id=owner.id, placement_id=placement.id, title=kw.pop("title", "Ad"),
                 target_url="https://shop.ru/", status=status, **kw)
    db.add(c)
    db.commit()
    return c


def status_of(db, campaign):
    db.expire_all()
    return db.get(Campaign, campaign.id).status


# --- Список кампаний для админа ---

def test_admin_list_with_filter_and_owner(client, auth_headers, db, owner, placements):
    a, _, _ = placements
    first = make(db, owner, a, CampaignStatus.MODERATION, title="Первая")
    make(db, owner, a, CampaignStatus.DRAFT)
    second = make(db, owner, a, CampaignStatus.MODERATION, title="Вторая")

    r = client.get(C, params={"status": "moderation"}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert [c["id"] for c in body] == [first.id, second.id]  # очередь — по порядку поступления
    assert body[0]["owner_email"] == "owner@mail.ru"
    assert body[0]["placement_name"] == "Шапка"
    assert len(client.get(C, headers=auth_headers).json()) == 3


def test_advertiser_cannot_list_all(client, owner):
    assert client.get(C, headers=bearer(owner)).status_code == 403


# --- Создание с датами ---

def test_create_with_dates_converted_to_utc(client, db, owner, placements):
    a, _, _ = placements
    r = client.post(C, headers=bearer(owner), json={
        "placement_id": a.id, "title": "T", "target_url": "https://a.ru",
        "start_date": "2030-01-01T03:00:00+03:00", "end_date": "2030-02-01T00:00:00Z",
    })
    assert r.status_code == 201
    assert r.json()["start_date"] == "2030-01-01T00:00:00Z"


def test_create_end_before_start_rejected(client, owner, placements):
    a, _, _ = placements
    r = client.post(C, headers=bearer(owner), json={
        "placement_id": a.id, "title": "T", "target_url": "https://a.ru",
        "start_date": "2030-02-01T00:00:00Z", "end_date": "2030-01-01T00:00:00Z",
    })
    assert r.status_code == 422


def test_future_campaign_not_served_yet(client, db, owner, placements):
    a, _, _ = placements
    make(db, owner, a, CampaignStatus.ACTIVE,
         start_date=datetime.now(timezone.utc) + timedelta(days=1))
    assert client.get(SERVE, params={"placement_code": "header"}).status_code == 404


# --- Редактирование ---

def test_edit_draft_stays_draft(client, db, owner, placements):
    a, b, _ = placements
    c = make(db, owner, a)
    r = client.patch(f"{C}/{c.id}", headers=bearer(owner),
                     json={"title": "Новый", "placement_id": b.id, "description": "Текст"})
    assert r.status_code == 200
    assert (r.json()["title"], r.json()["placement_id"], r.json()["status"]) == ("Новый", b.id, "draft")


def test_edit_active_content_requires_new_moderation(client, db, owner, placements):
    a, _, _ = placements
    c = make(db, owner, a, CampaignStatus.ACTIVE)
    assert client.get(SERVE, params={"placement_code": "header"}).status_code == 200

    r = client.patch(f"{C}/{c.id}", headers=bearer(owner), json={"target_url": "https://evil.example"})
    assert r.json()["status"] == "moderation"
    # до повторного одобрения подменённая реклама не показывается
    assert client.get(SERVE, params={"placement_code": "header"}).status_code == 404


def test_edit_active_dates_only_stays_active(client, db, owner, placements):
    a, _, _ = placements
    c = make(db, owner, a, CampaignStatus.ACTIVE)
    r = client.patch(f"{C}/{c.id}", headers=bearer(owner), json={"end_date": "2099-01-01T00:00:00Z"})
    assert r.json()["status"] == "active"
    assert r.json()["end_date"] == "2099-01-01T00:00:00Z"


def test_edit_same_value_is_not_a_change(client, db, owner, placements):
    a, _, _ = placements
    c = make(db, owner, a, CampaignStatus.ACTIVE)
    r = client.patch(f"{C}/{c.id}", headers=bearer(owner), json={"title": "Ad"})
    assert r.json()["status"] == "active"


def test_edit_clear_optional_field(client, db, owner, placements):
    a, _, _ = placements
    c = make(db, owner, a, description="Старое")
    r = client.patch(f"{C}/{c.id}", headers=bearer(owner), json={"description": None})
    assert r.json()["description"] is None


@pytest.mark.parametrize("payload", [{"title": None}, {"target_url": None}, {"title": ""},
                                     {"target_url": "javascript:alert(1)"}])
def test_edit_invalid(client, db, owner, placements, payload):
    a, _, _ = placements
    c = make(db, owner, a)
    assert client.patch(f"{C}/{c.id}", headers=bearer(owner), json=payload).status_code == 422


def test_edit_end_before_existing_start(client, db, owner, placements):
    a, _, _ = placements
    c = make(db, owner, a, start_date=datetime(2030, 6, 1, tzinfo=timezone.utc))
    r = client.patch(f"{C}/{c.id}", headers=bearer(owner), json={"end_date": "2030-05-01T00:00:00Z"})
    assert r.status_code == 422


def test_edit_to_inactive_placement(client, db, owner, placements):
    a, _, off = placements
    c = make(db, owner, a)
    assert client.patch(f"{C}/{c.id}", headers=bearer(owner),
                        json={"placement_id": off.id}).status_code == 404


@pytest.mark.parametrize("status", [CampaignStatus.MODERATION, CampaignStatus.COMPLETED])
def test_edit_locked_statuses(client, db, owner, placements, status):
    a, _, _ = placements
    c = make(db, owner, a, status)
    assert client.patch(f"{C}/{c.id}", headers=bearer(owner), json={"title": "X"}).status_code == 409


def test_edit_foreign_campaign(client, db, owner, placements):
    a, _, _ = placements
    c = make(db, owner, a)
    other = User(email="other@mail.ru", hashed_password="x")
    db.add(other)
    db.commit()
    assert client.patch(f"{C}/{c.id}", headers=bearer(other), json={"title": "X"}).status_code == 404


# --- Пауза / возобновление / завершение / удаление ---

def test_pause_and_resume(client, db, owner, placements):
    a, _, _ = placements
    c = make(db, owner, a, CampaignStatus.ACTIVE)
    h = bearer(owner)
    assert client.post(f"{C}/{c.id}/pause", headers=h).json()["status"] == "paused"
    assert client.get(SERVE, params={"placement_code": "header"}).status_code == 404
    assert client.post(f"{C}/{c.id}/pause", headers=h).status_code == 409
    assert client.post(f"{C}/{c.id}/resume", headers=h).json()["status"] == "active"
    assert client.get(SERVE, params={"placement_code": "header"}).status_code == 200
    assert client.post(f"{C}/{c.id}/resume", headers=h).status_code == 409


def test_pause_draft_conflict(client, db, owner, placements):
    a, _, _ = placements
    c = make(db, owner, a)
    assert client.post(f"{C}/{c.id}/pause", headers=bearer(owner)).status_code == 409


def test_complete_is_final(client, db, owner, placements):
    a, _, _ = placements
    c = make(db, owner, a, CampaignStatus.ACTIVE)
    h = bearer(owner)
    assert client.post(f"{C}/{c.id}/complete", headers=h).json()["status"] == "completed"
    assert client.post(f"{C}/{c.id}/complete", headers=h).status_code == 409
    assert client.post(f"{C}/{c.id}/resume", headers=h).status_code == 409
    assert client.post(f"{C}/{c.id}/submit", headers=h).status_code == 409


@pytest.mark.parametrize("status", [CampaignStatus.DRAFT, CampaignStatus.REJECTED])
def test_delete_unshown(client, db, owner, placements, status):
    a, _, _ = placements
    c = make(db, owner, a, status)
    assert client.delete(f"{C}/{c.id}", headers=bearer(owner)).status_code == 204
    db.expire_all()
    assert db.get(Campaign, c.id) is None


@pytest.mark.parametrize("status", [CampaignStatus.ACTIVE, CampaignStatus.PAUSED,
                                    CampaignStatus.MODERATION, CampaignStatus.COMPLETED])
def test_delete_shown_forbidden(client, db, owner, placements, status):
    a, _, _ = placements
    c = make(db, owner, a, status)
    assert client.delete(f"{C}/{c.id}", headers=bearer(owner)).status_code == 409


def test_delete_rejected_with_impressions_forbidden(client, db, owner, placements):
    a, _, _ = placements
    c = make(db, owner, a, CampaignStatus.REJECTED, impressions_count=5)
    assert client.delete(f"{C}/{c.id}", headers=bearer(owner)).status_code == 409


# --- Площадки ---

def test_admin_updates_placement(client, auth_headers, db, placements):
    a, _, _ = placements
    r = client.patch(f"/api/v1/placements/{a.id}", headers=auth_headers,
                     json={"price_per_click": 7.5, "name": "Новая шапка", "code_identifier": "hacked"})
    assert r.status_code == 200
    body = r.json()
    assert (body["price_per_click"], body["name"], body["code_identifier"]) == (7.5, "Новая шапка", "header")


def test_deactivate_placement_stops_serving(client, auth_headers, db, owner, placements):
    a, _, _ = placements
    make(db, owner, a, CampaignStatus.ACTIVE)
    assert client.get(SERVE, params={"placement_code": "header"}).status_code == 200
    client.patch(f"/api/v1/placements/{a.id}", headers=auth_headers, json={"is_active": False})
    assert client.get(SERVE, params={"placement_code": "header"}).status_code == 404
    public = [p["code_identifier"] for p in client.get("/api/v1/placements").json()["items"]]
    assert "header" not in public
    every = [p["code_identifier"] for p in client.get("/api/v1/placements/all", headers=auth_headers).json()["items"]]
    assert every == ["header", "footer", "off"]


def test_placement_update_validation_and_access(client, auth_headers, owner, placements):
    a, _, _ = placements
    url = f"/api/v1/placements/{a.id}"
    assert client.patch(url, headers=auth_headers, json={"price_per_click": -1}).status_code == 422
    assert client.patch(url, headers=bearer(owner), json={"name": "X"}).status_code == 403
    assert client.get("/api/v1/placements/all", headers=bearer(owner)).status_code == 403
    assert client.patch("/api/v1/placements/999", headers=auth_headers, json={"name": "X"}).status_code == 404


# --- Пользователи ---

def test_admin_lists_and_searches_users(client, auth_headers, owner):
    emails = [u["email"] for u in client.get("/api/v1/users", headers=auth_headers).json()]
    assert set(emails) == {"owner@mail.ru", "admin@example.com"}
    found = client.get("/api/v1/users", params={"q": "OWNER"}, headers=auth_headers).json()
    assert [u["email"] for u in found] == ["owner@mail.ru"]
    assert client.get("/api/v1/users", params={"q": "%"}, headers=auth_headers).json() == []
    assert client.get(f"/api/v1/users/{owner.id}", headers=auth_headers).json()["email"] == "owner@mail.ru"


def test_admin_changes_role(client, auth_headers, db, owner):
    r = client.patch(f"/api/v1/users/{owner.id}/role", headers=auth_headers, json={"role": "admin"})
    assert r.json()["role"] == "admin"
    db.expire_all()
    assert db.get(User, owner.id).role is UserRole.ADMIN


def test_admin_cannot_change_own_role(client, auth_headers, db):
    admin = db.query(User).filter_by(email="admin@example.com").one()
    r = client.patch(f"/api/v1/users/{admin.id}/role", headers=auth_headers, json={"role": "advertiser"})
    assert r.status_code == 409


def test_users_admin_only(client, owner):
    assert client.get("/api/v1/users", headers=bearer(owner)).status_code == 403
    assert client.patch(f"/api/v1/users/{owner.id}/role", headers=bearer(owner),
                        json={"role": "admin"}).status_code == 403
