from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, StatementError

from app.database import get_db
from app.main import app
from app.models import Campaign, CampaignStatus, Placement, User, UserRole


def make_user(db, **kw):
    u = User(email=kw.pop("email", "a@b.c"), hashed_password="x", **kw)
    db.add(u)
    db.flush()
    return u


def make_placement(db):
    p = Placement(name="Top", code_identifier="top")
    db.add(p)
    db.flush()
    return p


def test_defaults_and_types(db):
    u = make_user(db, telegram_id=7_000_000_000)
    db.commit()
    db.expire_all()
    u = db.get(User, u.id)
    assert u.role is UserRole.ADVERTISER
    assert u.balance == Decimal("0")
    assert u.telegram_id == 7_000_000_000
    assert u.created_at is not None


def test_fk_enforced(db):
    db.add(Campaign(user_id=999, placement_id=999, title="t", target_url="u"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_invalid_status_rejected(db):
    u, p = make_user(db), make_placement(db)
    db.add(Campaign(user_id=u.id, placement_id=p.id, title="t", target_url="u", status="actvie"))
    with pytest.raises(StatementError):
        db.flush()


def test_invalid_status_rejected_by_db(db):
    u, p = make_user(db), make_placement(db)
    with pytest.raises(IntegrityError):
        db.execute(text(
            "INSERT INTO campaigns (user_id, placement_id, title, target_url, status) "
            "VALUES (:u, :p, 't', 'u', 'actvie')"), {"u": u.id, "p": p.id})


def test_negative_balance_rejected(db):
    with pytest.raises(IntegrityError):
        make_user(db, balance=Decimal("-1"))


def test_dates_check(db):
    u, p = make_user(db), make_placement(db)
    now = datetime.now(timezone.utc)
    db.add(Campaign(user_id=u.id, placement_id=p.id, title="t", target_url="u",
                    start_date=now, end_date=now - timedelta(days=1)))
    with pytest.raises(IntegrityError):
        db.flush()


def test_role_not_nullable(db):
    # ORM подставляет значение по умолчанию вместо None
    assert make_user(db, role=None).role is UserRole.ADVERTISER
    # а прямой NULL отклоняет сама БД
    with pytest.raises(IntegrityError):
        db.execute(text(
            "INSERT INTO users (email, hashed_password, role) VALUES ('n@n.n', 'x', NULL)"))


def test_user_delete_cascades_campaigns(db):
    u, p = make_user(db), make_placement(db)
    db.add(Campaign(user_id=u.id, placement_id=p.id, title="t", target_url="u"))
    db.commit()
    db.delete(u)
    db.commit()
    assert db.query(Campaign).count() == 0


def test_placement_delete_restricted(db):
    u, p = make_user(db), make_placement(db)
    db.add(Campaign(user_id=u.id, placement_id=p.id, title="t", target_url="u",
                    status=CampaignStatus.ACTIVE))
    db.commit()
    db.delete(p)
    with pytest.raises(IntegrityError):
        db.commit()


def test_health(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        r = TestClient(app).get("/api/v1/health")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok"}
