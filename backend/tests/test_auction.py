"""Аукцион показов: кампании на всю сеть, ставки за клик, выбор по «ставка × CTR», подпись площадки в клике."""
import os
import sqlite3
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from app.auth import create_access_token
from app.config import settings
from app.models import (
    Campaign, CampaignStatus, PartnerEarning, Placement, PlacementDailyStat, Site, SiteStatus, User,
)
from app.routers.ads import click_signature

SERVE = "/api/v1/ad/serve"
CAMPAIGNS = "/api/v1/campaigns"
BACKEND = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def no_exploration(monkeypatch):
    # Детерминированный аукцион: всегда лучшая кампания (случайный показ проверяется отдельно)
    monkeypatch.setattr(settings, "auction_explore_rate", 0.0)


@pytest.fixture
def world(db):
    adv = User(email="adv@example.com", hashed_password="x", balance=Decimal("100"))
    cheap = Placement(name="Дешёвая", code_identifier="cheap", price_per_click=Decimal("0.10"))
    pricey = Placement(name="Дорогая", code_identifier="pricey", price_per_click=Decimal("1.00"))
    db.add_all([adv, cheap, pricey])
    db.commit()
    return adv, cheap, pricey


def _campaign(db, user, placement=None, bid=None, title="Акция", **kw):
    c = Campaign(user_id=user.id, placement_id=placement.id if placement else None,
                 cpc_bid=Decimal(bid) if bid is not None else None, title=title,
                 target_url=f"https://shop.example.com/{title}", status=CampaignStatus.ACTIVE, **kw)
    db.add(c)
    db.commit()
    return c


def _serve(client, code):
    return client.get(SERVE, params={"placement_code": code})


def _headers(user):
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


# ---------- Выбор объявления ----------
def test_network_campaign_shown_where_bid_reaches_floor(client, db, world):
    adv, _, _ = world
    c = _campaign(db, adv, bid="0.50")
    assert _serve(client, "cheap").json()["campaign_id"] == c.id
    assert _serve(client, "pricey").status_code == 404  # ставка 0.50 < цены площадки 1.00


def test_network_campaign_without_bid_pays_floor_everywhere(client, db, world):
    adv, _, _ = world
    c = _campaign(db, adv)
    assert _serve(client, "cheap").json()["campaign_id"] == c.id
    assert _serve(client, "pricey").json()["campaign_id"] == c.id


def test_higher_bid_wins(client, db, world):
    adv, cheap, _ = world
    _campaign(db, adv, cheap, title="low")
    high = _campaign(db, adv, bid="0.30", title="high")
    _campaign(db, adv, bid="0.20", title="mid")
    assert {_serve(client, "cheap").json()["campaign_id"] for _ in range(5)} == {high.id}


def test_ctr_beats_a_slightly_higher_bid(client, db, world):
    adv, _, _ = world
    # 1.00 × (50+1)/(100+100) = 0.255  >  1.50 × (0+1)/(0+100) = 0.015
    good = _campaign(db, adv, bid="1.00", title="good", impressions_count=100, clicks_count=50)
    _campaign(db, adv, bid="1.50", title="new")
    assert _serve(client, "cheap").json()["campaign_id"] == good.id


def test_equal_campaigns_rotate(client, db, world):
    adv, cheap, _ = world
    ids = {_campaign(db, adv, cheap, title=t).id for t in ("a", "b", "c")}
    # Каждый показ снижает оценку CTR показанной кампании — следующая уходит другой
    assert {_serve(client, "cheap").json()["campaign_id"] for _ in range(6)} == ids


def test_exploration_shows_weaker_campaign(client, db, world, monkeypatch):
    adv, _, _ = world
    _campaign(db, adv, bid="5.00", title="strong")
    weak = _campaign(db, adv, bid="0.10", title="weak")
    monkeypatch.setattr(settings, "auction_explore_rate", 1.0)
    seen = {_serve(client, "cheap").json()["campaign_id"] for _ in range(40)}
    assert weak.id in seen


def test_owner_must_afford_the_bid(client, db, world):
    adv, _, _ = world
    adv.balance = Decimal("0.40")
    db.commit()
    _campaign(db, adv, bid="0.50")
    assert _serve(client, "cheap").status_code == 404


def test_targeted_campaign_stays_on_its_placement(client, db, world):
    adv, cheap, _ = world
    _campaign(db, adv, cheap)
    assert _serve(client, "pricey").status_code == 404


# ---------- Клик ----------
def _click_url(client, code):
    return _serve(client, code).json()["click_url"]


def test_click_charges_bid_and_counts_placement(client, db, world):
    adv, cheap, _ = world
    c = _campaign(db, adv, bid="0.40")
    url = _click_url(client, "cheap")
    q = parse_qs(urlsplit(url).query)
    assert q == {"p": [str(cheap.id)], "s": [click_signature(c.id, cheap.id)]}
    assert client.get(url, follow_redirects=False).status_code == 302
    db.refresh(adv)
    assert adv.balance == Decimal("99.60")
    stat = db.scalar(select(PlacementDailyStat))
    assert (stat.placement_id, stat.impressions, stat.clicks, stat.spend) == (cheap.id, 1, 1, Decimal("0.40"))


def test_targeted_campaign_with_bid_pays_bid(client, db, world):
    adv, cheap, _ = world
    c = _campaign(db, adv, cheap, bid="0.25")
    client.get(f"/api/v1/ad/click/{c.id}", follow_redirects=False)  # старая ссылка, без p и s
    db.refresh(adv)
    assert adv.balance == Decimal("99.75")


@pytest.mark.parametrize("query", ["", "?p={p}", "?p={p}&s=forged", "?p={other}&s={sig}"])
def test_network_click_without_valid_signature_is_free(client, db, world, query):
    adv, cheap, pricey = world
    c = _campaign(db, adv, bid="2.00")
    q = query.format(p=cheap.id, other=pricey.id, sig=click_signature(c.id, cheap.id))
    r = client.get(f"/api/v1/ad/click/{c.id}{q}", follow_redirects=False)
    assert r.status_code == 302  # посетитель всё равно попадает на сайт
    db.refresh(adv)
    assert adv.balance == Decimal("100")


def test_bid_lowered_below_floor_after_show(client, db, world):
    adv, _, pricey = world
    c = _campaign(db, adv, bid="1.50")
    url = _click_url(client, "pricey")
    c.cpc_bid = Decimal("0.50")
    db.commit()
    client.get(url, follow_redirects=False)
    db.refresh(adv)
    assert adv.balance == Decimal("100")


def test_network_click_on_partner_site_shares_the_bid(client, db, world):
    adv, _, _ = world
    pub = User(email="pub@example.com", hashed_password="x")
    db.add(pub)
    db.commit()
    site = Site(user_id=pub.id, name="Блог", url="https://blog.example.com", domain="blog.example.com",
                status=SiteStatus.APPROVED)
    db.add(site)
    db.commit()
    db.add(Placement(name="Под статьёй", code_identifier="s1_x", price_per_click=Decimal("0.10"), site_id=site.id))
    db.commit()
    _campaign(db, adv, bid="0.50")
    client.get(_click_url(client, "s1_x"), follow_redirects=False)
    assert db.scalar(select(PartnerEarning.amount)) == Decimal("0.30")  # 60% от ставки 0.50


# ---------- API кампаний ----------
BODY = {"title": "Курсы", "target_url": "https://school.example.com"}


def test_create_network_campaign(client, db, world, auth_headers):
    adv, _, _ = world
    r = client.post(CAMPAIGNS, json={**BODY, "cpc_bid": "0.35"}, headers=_headers(adv))
    assert r.status_code == 201, r.text
    assert (r.json()["placement_id"], r.json()["cpc_bid"]) == (None, 0.35)
    admin_view = client.get(CAMPAIGNS, headers=auth_headers).json()["items"][0]
    assert admin_view["placement_name"] is None


def test_bid_below_floor_rejected(client, world):
    adv, _, pricey = world
    r = client.post(CAMPAIGNS, json={**BODY, "placement_id": pricey.id, "cpc_bid": "0.50"}, headers=_headers(adv))
    assert r.status_code == 422
    assert "1.00" in r.json()["detail"]


@pytest.mark.parametrize("bid", ["0", "-1", "1000.01", "0.001"])
def test_bid_validation(client, world, bid):
    adv, _, _ = world
    assert client.post(CAMPAIGNS, json={**BODY, "cpc_bid": bid}, headers=_headers(adv)).status_code == 422


def test_bid_change_does_not_need_moderation(client, db, world):
    adv, cheap, _ = world
    c = _campaign(db, adv, cheap)
    r = client.patch(f"{CAMPAIGNS}/{c.id}", json={"cpc_bid": "0.80"}, headers=_headers(adv))
    assert (r.json()["status"], r.json()["cpc_bid"]) == ("active", 0.8)
    r = client.patch(f"{CAMPAIGNS}/{c.id}", json={"cpc_bid": None}, headers=_headers(adv))
    assert r.json()["cpc_bid"] is None


def test_switch_to_network_needs_moderation(client, db, world):
    adv, cheap, _ = world
    c = _campaign(db, adv, cheap)
    r = client.patch(f"{CAMPAIGNS}/{c.id}", json={"placement_id": None}, headers=_headers(adv))
    assert (r.json()["status"], r.json()["placement_id"]) == ("moderation", None)


def test_bid_checked_against_current_placement_on_update(client, db, world):
    adv, _, pricey = world
    c = _campaign(db, adv, pricey)
    r = client.patch(f"{CAMPAIGNS}/{c.id}", json={"cpc_bid": "0.20"}, headers=_headers(adv))
    assert r.status_code == 422


def test_unapproved_partner_placements_hidden_from_advertisers(client, db, world):
    adv, _, _ = world
    site = Site(user_id=adv.id, name="Новый", url="https://new.example.com", domain="new.example.com")
    db.add(site)
    db.commit()
    hidden = Placement(name="На проверке", code_identifier="s9_y", site_id=site.id)
    db.add(hidden)
    db.commit()
    codes = [p["code_identifier"] for p in client.get("/api/v1/placements").json()["items"]]
    assert codes == ["cheap", "pricey"]
    r = client.post(CAMPAIGNS, json={**BODY, "placement_id": hidden.id}, headers=_headers(adv))
    assert r.status_code == 404


def test_platform_stats_count_network_campaigns_per_placement(client, db, world, auth_headers):
    adv, cheap, _ = world
    _campaign(db, adv, bid="0.40")
    client.get(_click_url(client, "cheap"), follow_redirects=False)
    body = client.get("/api/v1/stats/platform", headers=auth_headers).json()
    row = next(p for p in body["placements"] if p["placement_id"] == cheap.id)
    assert (row["impressions"], row["clicks"], row["spend"]) == (1, 1, 0.4)


# ---------- Миграция ----------
def _alembic(db_path, *args):
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path.as_posix()}"}
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env,
                          capture_output=True, text=True)


def test_migration_backfills_placement_stats_and_guards_downgrade(tmp_path):
    db_path = tmp_path / "old.db"
    assert _alembic(db_path, "upgrade", "f699f89dbf7e").returncode == 0  # версия до аукциона
    con = sqlite3.connect(db_path)
    con.executescript("""
        INSERT INTO users (id, email, hashed_password) VALUES (1, 'a@example.com', 'x');
        INSERT INTO placements (id, name, code_identifier) VALUES (1, 'Шапка', 'header'), (2, 'Подвал', 'footer');
        INSERT INTO campaigns (id, user_id, placement_id, title, target_url)
            VALUES (1, 1, 1, 'A', 'https://a'), (2, 1, 1, 'B', 'https://b'), (3, 1, 2, 'C', 'https://c');
        INSERT INTO campaign_daily_stats (campaign_id, day, impressions, clicks, spend) VALUES
            (1, '2026-10-01', 10, 2, 4.00), (2, '2026-10-01', 5, 1, 2.00), (3, '2026-10-02', 7, 0, 0);
    """)
    con.commit()
    con.close()

    assert _alembic(db_path, "upgrade", "head").returncode == 0
    con = sqlite3.connect(db_path)
    rows = con.execute("SELECT placement_id, day, impressions, clicks, CAST(spend AS TEXT) "
                       "FROM placement_daily_stats ORDER BY placement_id").fetchall()
    assert rows == [(1, "2026-10-01", 15, 3, "6"), (2, "2026-10-02", 7, 0, "0")]
    con.execute("INSERT INTO campaigns (id, user_id, title, target_url) VALUES (4, 1, 'Net', 'https://n')")
    con.commit()
    con.close()

    failed = _alembic(db_path, "downgrade", "f699f89dbf7e")
    assert failed.returncode != 0 and "всю сеть" in failed.stderr  # кампанию на всю сеть не потерять молча
    con = sqlite3.connect(db_path)
    con.execute("DELETE FROM campaigns WHERE id = 4")
    con.commit()
    con.close()
    assert _alembic(db_path, "downgrade", "f699f89dbf7e").returncode == 0
