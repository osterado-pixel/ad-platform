from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app import cli
from app.database import get_db
from app.main import app
from app.models import (
    Campaign, CampaignStatus, Click, Placement, Transaction, TransactionType, User,
)
from app.routers import ads

SERVE = "/api/v1/ad/serve"


@pytest.fixture
def make_client(db):
    app.dependency_overrides[get_db] = lambda: db

    def _make(ip="1.1.1.1", ua="Mozilla/5.0"):
        return TestClient(app, client=(ip, 50000), headers={"User-Agent": ua})

    yield _make
    app.dependency_overrides.clear()


@pytest.fixture
def world(db):
    """Владелец с балансом 10.00, площадка с ценой клика 3.00, активная кампания."""
    user = User(email="a@mail.ru", hashed_password="x", balance=Decimal("10.00"))
    placement = Placement(name="Шапка", code_identifier="header", price_per_click=Decimal("3.00"))
    db.add_all([user, placement])
    db.commit()
    campaign = Campaign(user_id=user.id, placement_id=placement.id, title="Ad",
                        target_url="https://shop.ru/", status=CampaignStatus.ACTIVE)
    db.add(campaign)
    db.commit()
    return user, placement, campaign


def click(client, campaign):
    r = client.get(f"/api/v1/ad/click/{campaign.id}", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "https://shop.ru/"


def state(db, user, campaign):
    db.expire_all()
    return db.get(User, user.id).balance, db.get(Campaign, campaign.id).clicks_count


def test_click_charges_owner(make_client, db, world):
    user, _, campaign = world
    click(make_client(), campaign)
    assert state(db, user, campaign) == (Decimal("7.00"), 1)
    c = db.query(Click).one()
    assert c.cost == Decimal("3.00")
    assert len(c.ip_hash) == 64 and "1.1.1.1" not in c.ip_hash


def test_repeat_click_same_ip_not_charged(make_client, db, world):
    user, _, campaign = world
    client = make_client()
    click(client, campaign)
    click(client, campaign)
    click(client, campaign)
    assert state(db, user, campaign) == (Decimal("7.00"), 1)


def test_repeat_click_after_window_charged(make_client, db, world, monkeypatch):
    user, _, campaign = world
    client = make_client()
    click(client, campaign)
    real_time = ads.time.time
    monkeypatch.setattr(ads.time, "time", lambda: real_time() + 11 * 60)
    click(client, campaign)
    assert state(db, user, campaign) == (Decimal("4.00"), 2)


def test_different_ips_charged(make_client, db, world):
    user, _, campaign = world
    click(make_client("1.1.1.1"), campaign)
    click(make_client("2.2.2.2"), campaign)
    assert state(db, user, campaign) == (Decimal("4.00"), 2)


def test_balance_never_negative(make_client, db, world):
    user, _, campaign = world  # 10.00 / 3.00 = 3 оплаченных клика
    for i in range(5):
        click(make_client(f"10.0.0.{i}"), campaign)  # редирект всегда
    assert state(db, user, campaign) == (Decimal("1.00"), 3)
    assert db.query(Click).count() == 3


@pytest.mark.parametrize("ua", [
    "TelegramBot (like TwitterBot)", "Slackbot-LinkExpanding 1.0", "facebookexternalhit/1.1",
    "Mozilla/5.0 (compatible; Googlebot/2.1)", "WhatsApp/2.23",
])
def test_bots_not_charged(make_client, db, world, ua):
    user, _, campaign = world
    click(make_client(ua=ua), campaign)
    assert state(db, user, campaign) == (Decimal("10.00"), 0)


def test_serve_hides_when_owner_cannot_pay_and_resumes(make_client, db, world):
    user, _, _ = world
    client = make_client()
    assert client.get(SERVE, params={"placement_code": "header"}).status_code == 200

    db.get(User, user.id).balance = Decimal("2.99")
    db.commit()
    assert client.get(SERVE, params={"placement_code": "header"}).status_code == 404

    db.get(User, user.id).balance = Decimal("3.00")
    db.commit()
    assert client.get(SERVE, params={"placement_code": "header"}).status_code == 200


def test_free_placement_serves_with_zero_balance(make_client, db, world):
    user, placement, campaign = world
    placement.price_per_click = Decimal("0")
    db.get(User, user.id).balance = Decimal("0")
    db.commit()
    client = make_client()
    assert client.get(SERVE, params={"placement_code": "header"}).status_code == 200
    click(client, campaign)
    assert state(db, user, campaign) == (Decimal("0.00"), 1)


def test_clicks_count_in_my_campaigns(make_client, db, world):
    from app.auth import create_access_token
    user, _, campaign = world
    client = make_client()
    click(client, campaign)
    r = client.get("/api/v1/campaigns/my",
                   headers={"Authorization": f"Bearer {create_access_token(user.id)}"})
    assert r.json()[0]["clicks_count"] == 1


def test_cli_add_balance(db, world, monkeypatch):
    user, _, _ = world
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=db.get_bind()))
    assert cli.add_balance("A@Mail.ru", "100.50") == 0
    db.expire_all()
    assert db.get(User, user.id).balance == Decimal("110.50")
    for bad in ["-5", "0", "abc", "1.001"]:
        assert cli.add_balance("a@mail.ru", bad) == 1
    assert cli.add_balance("nobody@mail.ru", "10") == 1


def ledger_balance(db, user_id):
    """Баланс по журналу: пополнения и возвраты минус списания."""
    total = Decimal("0")
    for t in db.query(Transaction).filter_by(user_id=user_id):
        total += -t.amount if t.type is TransactionType.CLICK_SPEND else t.amount
    return total


def test_click_writes_transaction(make_client, db, world):
    user, _, campaign = world
    click(make_client(), campaign)
    t = db.query(Transaction).one()
    assert (t.user_id, t.amount, t.type, t.campaign_id) == (
        user.id, Decimal("3.00"), TransactionType.CLICK_SPEND, campaign.id)


def test_unpaid_clicks_write_no_transaction(make_client, db, world):
    _, _, campaign = world
    click(make_client(ua="TelegramBot"), campaign)
    client = make_client()
    click(client, campaign)
    click(client, campaign)  # повтор
    assert db.query(Transaction).count() == 1


def test_free_click_writes_no_transaction(make_client, db, world):
    _, placement, campaign = world
    placement.price_per_click = Decimal("0")
    db.commit()
    click(make_client(), campaign)
    assert db.query(Transaction).count() == 0


def test_ledger_matches_balance(make_client, db, world, monkeypatch):
    user, _, campaign = world
    user.balance = Decimal("0")
    db.commit()
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=db.get_bind()))
    assert cli.add_balance("a@mail.ru", "10") == 0
    for i in range(5):
        click(make_client(f"10.0.0.{i}"), campaign)
    assert cli.add_balance("a@mail.ru", "2.5") == 0
    click(make_client("10.0.0.99"), campaign)

    db.expire_all()
    balance = db.get(User, user.id).balance
    assert balance == Decimal("0.50")  # 10 − 3·3 + 2.5 − 3
    assert ledger_balance(db, user.id) == balance


def test_transaction_amount_must_be_positive(db, world):
    user, _, _ = world
    db.add(Transaction(user_id=user.id, amount=Decimal("0"), type=TransactionType.REFUND))
    with pytest.raises(IntegrityError):
        db.flush()


def test_user_with_transactions_cannot_be_deleted(db, world):
    user, _, _ = world
    db.add(Transaction(user_id=user.id, amount=Decimal("1"), type=TransactionType.DEPOSIT))
    db.commit()
    db.delete(user)
    with pytest.raises(IntegrityError):
        db.commit()


def test_click_transaction_description(make_client, db, world):
    _, _, campaign = world
    click(make_client(), campaign)
    assert db.query(Transaction).one().description == f"Списание за клик по кампании #{campaign.id} (Ad)"


def test_click_transaction_description_long_title(make_client, db, world):
    _, _, campaign = world
    campaign.title = "Я" * 255
    db.commit()
    click(make_client(), campaign)
    assert len(db.query(Transaction).one().description) == 255


def _at(monkeypatch, minute):
    """Подменяет время: unix-минута `minute` (+30 секунд)."""
    monkeypatch.setattr(ads.time, "time", lambda: minute * 60 + 30)


BASE_MINUTE = 29_000_009  # 9-я минута 10-минутного отрезка: прежняя схема отрезков ломалась здесь


@pytest.mark.parametrize("gap,charged", [
    (1, False), (2, False), (9, False),  # в пределах 10 минут от прошлого клика
    (10, True), (11, True),              # 10 минут прошло
])
def test_dedup_window_counts_from_previous_click(make_client, db, world, monkeypatch, gap, charged):
    user, _, campaign = world
    client = make_client()
    _at(monkeypatch, BASE_MINUTE)
    click(client, campaign)
    _at(monkeypatch, BASE_MINUTE + gap)
    click(client, campaign)
    expected = (Decimal("4.00"), 2) if charged else (Decimal("7.00"), 1)
    assert state(db, user, campaign) == expected


def test_dedup_window_from_last_paid_click_not_first(make_client, db, world, monkeypatch):
    user, _, campaign = world
    client = make_client()
    for minute, in [(BASE_MINUTE,), (BASE_MINUTE + 5,), (BASE_MINUTE + 10,), (BASE_MINUTE + 15,)]:
        _at(monkeypatch, minute)
        click(client, campaign)
    # оплачены 0-я и 10-я минуты; 5-я и 15-я — повторы в окне оплаченных
    assert state(db, user, campaign) == (Decimal("4.00"), 2)


def test_unpaid_click_does_not_block_ip(make_client, db, world, monkeypatch):
    user, _, campaign = world
    db.get(User, user.id).balance = Decimal("0")
    db.commit()
    client = make_client()
    _at(monkeypatch, BASE_MINUTE)
    click(client, campaign)  # денег нет — не оплачен и не записан
    db.get(User, user.id).balance = Decimal("10.00")
    db.commit()
    _at(monkeypatch, BASE_MINUTE + 1)
    click(client, campaign)  # после пополнения тот же посетитель — оплачивается
    assert state(db, user, campaign) == (Decimal("7.00"), 1)


def test_cli_create_admin(db, monkeypatch):
    from app.auth import verify_password
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=db.get_bind()))
    assert cli.create_admin("Boss@Mail.ru", "short") == 1  # пароль короче 8
    assert cli.create_admin("not-an-email", "password123") == 1
    assert cli.create_admin("Boss@Mail.ru", "password123") == 0
    db.expire_all()
    boss = db.query(User).filter_by(email="boss@mail.ru").one()
    assert boss.role.value == "admin" and verify_password("password123", boss.hashed_password)
    # Повторно — тот же пользователь, новый пароль
    assert cli.create_admin("boss@mail.ru", "newpassword1") == 0
    db.expire_all()
    assert db.query(User).count() == 1
    assert verify_password("newpassword1", db.query(User).one().hashed_password)


def test_cli_set_password(db, monkeypatch):
    from app.auth import verify_password
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=db.get_bind()))
    db.add(User(email="u@mail.ru", hashed_password="x"))
    db.commit()
    assert cli.set_password("U@mail.ru", "short") == 1
    assert cli.set_password("nobody@mail.ru", "password123") == 1
    assert cli.set_password("U@mail.ru", "password123") == 0
    db.expire_all()
    u = db.query(User).one()
    assert verify_password("password123", u.hashed_password) and u.token_version == 1


def test_cli_purge(db, world, monkeypatch):
    import time
    from app.models import AuthAttempt
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=db.get_bind()))
    _, _, campaign = world
    now_min = int(time.time()) // 60
    db.add_all([
        Click(campaign_id=campaign.id, ip_hash="a" * 64, time_window=now_min - 31 * 24 * 60, cost=Decimal("1")),
        Click(campaign_id=campaign.id, ip_hash="b" * 64, time_window=now_min - 5, cost=Decimal("1")),
        AuthAttempt(kind="login_ip", key="k" * 64, ts=int(time.time()) - 2 * 86400),
        AuthAttempt(kind="login_ip", key="k" * 64, ts=int(time.time())),
    ])
    db.commit()
    assert cli.purge(0) == 1  # меньше окна защиты от повторов нельзя
    assert cli.purge(30) == 0
    db.expire_all()
    assert [c.ip_hash[0] for c in db.query(Click)] == ["b"]
    assert db.query(AuthAttempt).count() == 1


def test_cli_backup_db(tmp_path, monkeypatch):
    import sqlite3
    from sqlalchemy import create_engine
    import app.database as database
    src = tmp_path / "live.db"
    con = sqlite3.connect(src)
    con.execute("create table t (x)")
    con.execute("insert into t values (42)")
    con.commit()
    con.close()
    monkeypatch.setattr(database, "engine", create_engine(f"sqlite:///{src.as_posix()}"))
    monkeypatch.setattr(database, "is_sqlite", True)
    assert cli.backup_db(str(tmp_path / "backups")) == 0
    [copy] = list((tmp_path / "backups").glob("app-*.db"))
    con = sqlite3.connect(copy)
    try:
        assert con.execute("select x from t").fetchone() == (42,)
    finally:
        con.close()
