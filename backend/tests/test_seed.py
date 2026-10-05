"""seed.py: данные согласованы, повторный запуск ничего не дублирует, сбой — откат."""
from decimal import Decimal

import pytest
from sqlalchemy import func, select

import seed
from app.models import Campaign, CampaignDailyStat, Placement, Transaction, TransactionType, User


def test_seed_creates_consistent_data(client, db):
    assert seed.seed(db) is True
    db.commit()

    user = db.scalar(select(User).where(User.email == seed.EMAIL))
    campaign = db.scalar(select(Campaign).where(Campaign.user_id == user.id))
    assert user.balance == Decimal("500.00")
    assert (campaign.status.value, campaign.impressions_count, campaign.clicks_count) == ("active", 120, 8)

    # Журнал сходится с балансом, счётчик — с журналом
    txs = db.scalars(select(Transaction).where(Transaction.user_id == user.id)).all()
    ledger = sum((-t.amount if t.type is TransactionType.CLICK_SPEND else t.amount) for t in txs)
    assert ledger == user.balance and user.transactions_count == len(txs) == 2

    # Статистика по дням совпадает с карточкой кампании
    stat = db.scalar(select(CampaignDailyStat).where(CampaignDailyStat.campaign_id == campaign.id))
    assert (stat.impressions, stat.clicks, stat.spend) == (120, 8, Decimal("120.00"))

    # Через API: вход, история, обзор, показ на площадке
    token = client.post("/api/v1/auth/login",
                        data={"username": seed.EMAIL, "password": seed.PASSWORD}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/wallet/history", headers=h).json()["total"] == 2
    assert client.get("/api/v1/stats/me", headers=h).json()["totals"]["clicks"] == 8
    assert client.get("/api/v1/ad/serve", params={"placement_code": seed.PLACEMENT_CODE}).status_code == 200


def test_seed_is_idempotent(db):
    assert seed.seed(db) is True
    db.commit()
    assert seed.seed(db) is False
    db.commit()
    assert db.scalar(select(func.count()).select_from(User)) == 1
    assert db.scalar(select(func.count()).select_from(Transaction)) == 2


def test_seed_reuses_existing_placement(db):
    db.add(Placement(name="Создана вручную", code_identifier=seed.PLACEMENT_CODE))
    db.commit()
    assert seed.seed(db) is True
    db.commit()
    assert db.scalar(select(func.count()).select_from(Placement)) == 1


def test_main_rolls_back_and_fails_on_error(db, monkeypatch, capsys):
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(seed, "SessionLocal", sessionmaker(bind=db.get_bind()))
    monkeypatch.setattr(seed, "inspect", lambda _: type("I", (), {"get_table_names": lambda self: ["users"]})())

    def broken(session):
        session.add(User(email="half@example.com", hashed_password="x"))
        session.flush()
        raise RuntimeError("сбой посередине")
    monkeypatch.setattr(seed, "seed", broken)
    assert seed.main() == 1
    assert "сбой посередине" in capsys.readouterr().err
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(User)) == 0  # ничего не осталось наполовину
