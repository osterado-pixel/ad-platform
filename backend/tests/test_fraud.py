"""Накрутка: отчёт по сайтам партнёров и аннулирование созревающего заработка."""
import time
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.auth import create_access_token
from app.config import settings
from app.models import (
    Campaign, CampaignStatus, Click, EarningSource, PartnerEarning, Placement, Site, SiteDailyStat, SiteStatus, User,
)
from app.services import fraud, partners
from app.stats import utc_today

A = "/api/v1/admin/partner"


@pytest.fixture
def world(db):
    pub = User(email="pub@example.com", hashed_password="x")
    honest = User(email="honest@example.com", hashed_password="x")
    adv = User(email="adv@example.com", hashed_password="x")
    db.add_all([pub, honest, adv])
    db.commit()
    bad = Site(user_id=pub.id, name="Накрутка", url="https://bad.example.com", domain="bad.example.com",
               status=SiteStatus.APPROVED)
    good = Site(user_id=honest.id, name="Честный", url="https://good.example.com", domain="good.example.com",
                status=SiteStatus.APPROVED)
    db.add_all([bad, good])
    db.commit()
    p_bad = Placement(name="b", code_identifier="b", site_id=bad.id)
    p_good = Placement(name="g", code_identifier="g", site_id=good.id)
    db.add_all([p_bad, p_good])
    db.commit()
    c = Campaign(user_id=adv.id, title="A", target_url="https://a.example.com", status=CampaignStatus.ACTIVE)
    db.add(c)
    db.commit()
    today, minute = utc_today(), int(time.time() // 60)
    db.add_all([
        # 200 показов, 30 кликов (CTR 15%), все клики с 3 адресов
        SiteDailyStat(site_id=bad.id, day=today, impressions=200, clicks=30, revenue=Decimal("30"),
                      earnings=Decimal("18")),
        # 1000 показов, 10 кликов (CTR 1%), 10 адресов
        SiteDailyStat(site_id=good.id, day=today, impressions=1000, clicks=10, revenue=Decimal("10"),
                      earnings=Decimal("6")),
        PartnerEarning(user_id=pub.id, day=today, source=EarningSource.SITE, amount=Decimal("18")),
        PartnerEarning(user_id=pub.id, day=today, source=EarningSource.REFERRAL, amount=Decimal("1")),
        PartnerEarning(user_id=pub.id, day=date(2026, 1, 1), source=EarningSource.SITE, amount=Decimal("5"),
                       matured=True),
        PartnerEarning(user_id=honest.id, day=today, source=EarningSource.SITE, amount=Decimal("6")),
    ])
    db.add_all([Click(campaign_id=c.id, ip_hash=f"ip{i % 3}", time_window=minute - i, cost=Decimal("1"),
                      placement_id=p_bad.id) for i in range(30)])
    db.add_all([Click(campaign_id=c.id, ip_hash=f"good{i}", time_window=minute - i, cost=Decimal("1"),
                      placement_id=p_good.id) for i in range(10)])
    db.commit()
    return pub, honest, bad, good


def test_report_flags_suspicious_site_first(client, auth_headers, world):
    pub, _, bad, good = world
    rows = client.get(f"{A}/fraud", headers=auth_headers).json()
    assert [r["site_id"] for r in rows] == [bad.id, good.id]
    top = rows[0]
    assert (top["clicks"], top["impressions"], top["ctr"], top["unique_ips"]) == (30, 200, 15.0, 3)
    assert set(top["flags"]) == {"high_ctr", "few_ips"}
    assert (top["owner_email"], top["earnings"], top["pending"]) == ("pub@example.com", 18.0, 19.0)
    assert rows[1]["flags"] == []


@pytest.mark.parametrize("impressions, clicks, ips, expected", [
    (99, 50, 50, []),                                               # мало показов для вывода о CTR
    (100, 6, 6, ["high_ctr"]),
    (100, 5, 5, []),
    (1000, 20, 9, ["few_ips"]),
    (1000, 19, 1, []),                                              # мало кликов для вывода об адресах
    (3, 5, 5, ["clicks_over_impressions"]),
])
def test_flags(impressions, clicks, ips, expected):
    assert fraud.site_flags(impressions, clicks, ips) == expected


def test_forfeit_on_block_keeps_matured_and_referral(client, db, auth_headers, world, monkeypatch):
    pub, honest, bad, _ = world
    r = client.post(f"{A}/sites/{bad.id}/moderate", headers=auth_headers,
                    json={"status": "blocked", "reason": "Накрутка кликов", "forfeit_pending": True})
    assert r.json()["status"] == "blocked"
    rows = {(e.source, e.matured): e for e in db.scalars(select(PartnerEarning).where(PartnerEarning.user_id == pub.id))}
    assert (rows[(EarningSource.SITE, False)].amount, rows[(EarningSource.SITE, False)].forfeited) == (0, Decimal("18"))
    assert rows[(EarningSource.REFERRAL, False)].amount == Decimal("1")   # реферальное не трогаем
    assert rows[(EarningSource.SITE, True)].amount == Decimal("5")        # созревшее — уже не аннулировать
    assert partners.pending_amount(db, pub.id) == Decimal("1")
    assert partners.pending_amount(db, honest.id) == Decimal("6")         # другие партнёры не затронуты

    # Через срок созревания аннулированное не зачисляется
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + settings.earnings_hold_days * 24 * 3600)
    partners.mature(db, pub.id)
    db.refresh(pub)
    assert pub.earnings_balance == Decimal("1.00")


def test_new_clicks_after_forfeit_still_count(db, world):
    pub, _, _, _ = world
    assert fraud.forfeit_pending(db, pub.id) == Decimal("18.00")
    partners.accrue(db, pub.id, Decimal("0.60"), EarningSource.SITE)  # клик на другом (честном) сайте
    db.commit()
    row = db.scalar(select(PartnerEarning).where(PartnerEarning.user_id == pub.id,
                                                 PartnerEarning.source == EarningSource.SITE,
                                                 PartnerEarning.matured.is_(False)))
    assert (row.amount, row.forfeited) == (Decimal("0.60"), Decimal("18"))


def test_block_without_forfeit_keeps_pending(client, db, auth_headers, world):
    pub, _, bad, _ = world
    client.post(f"{A}/sites/{bad.id}/moderate", headers=auth_headers, json={"status": "blocked", "reason": "x"})
    assert partners.pending_amount(db, pub.id) == Decimal("19")


def test_forfeit_endpoint(client, auth_headers, world):
    pub, _, _, _ = world
    assert client.post(f"{A}/users/{pub.id}/forfeit", headers=auth_headers).json() == {"forfeited": 18.0}
    assert client.post(f"{A}/users/{pub.id}/forfeit", headers=auth_headers).json() == {"forfeited": 0.0}
    assert client.post(f"{A}/users/9999/forfeit", headers=auth_headers).status_code == 404


def test_fraud_endpoints_admin_only(client, db, world):
    pub, _, _, _ = world
    h = {"Authorization": f"Bearer {create_access_token(pub.id)}"}
    assert client.get(f"{A}/fraud", headers=h).status_code == 403
    assert client.post(f"{A}/users/{pub.id}/forfeit", headers=h).status_code == 403


def test_click_remembers_placement(client, db):
    adv = User(email="a@example.com", hashed_password="x", balance=Decimal("5"))
    pl = Placement(name="Шапка", code_identifier="header", price_per_click=Decimal("1"))
    db.add_all([adv, pl])
    db.commit()
    c = Campaign(user_id=adv.id, placement_id=pl.id, title="A", target_url="https://a.example.com",
                 status=CampaignStatus.ACTIVE)
    db.add(c)
    db.commit()
    client.get(f"/api/v1/ad/click/{c.id}", follow_redirects=False)
    assert db.scalar(select(Click.placement_id).where(Click.campaign_id == c.id)) == pl.id


# ---------- Автопилот: явная накрутка — приостановка до решения администратора ----------
def test_auto_suspend_two_flags(db, world):
    pub, honest, bad, good = world
    assert fraud.auto_block(db) == [bad.id]
    db.refresh(bad)
    assert (bad.status, bad.fraud_hold) == (SiteStatus.PENDING, True)  # показа нет
    assert bad.rejection_reason == ("Автоматическая приостановка, признаки накрутки: "
                                    "слишком высокий CTR; клики с малого числа адресов")
    # Ничего не аннулировано: признаки мог подстроить и недоброжелатель
    assert partners.pending_amount(db, pub.id) == Decimal("19")
    assert fraud.auto_block(db) == []                                # уже приостановлен


def test_suspended_owner_earnings_frozen(db, world, monkeypatch):
    pub, honest, _, _ = world
    fraud.auto_block(db)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + settings.earnings_hold_days * 24 * 3600)
    partners.mature(db)
    db.refresh(pub)
    db.refresh(honest)
    assert pub.earnings_balance == 0                    # заморожено до решения
    assert honest.earnings_balance == Decimal("6.00")   # других партнёров не касается


def test_admin_clears_suspension_and_autopilot_respects_it(client, db, auth_headers, world, monkeypatch):
    pub, _, bad, _ = world
    fraud.auto_block(db)
    r = client.post(f"{A}/sites/{bad.id}/moderate", json={"status": "approved"}, headers=auth_headers)
    assert (r.json()["status"], r.json()["fraud_hold"]) == ("approved", False)
    db.refresh(bad)
    assert bad.fraud_reviewed_at is not None
    assert fraud.auto_block(db) == []   # та же статистика прошлых дней — но решение администратора важнее
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + settings.earnings_hold_days * 24 * 3600)
    partners.mature(db, pub.id)
    db.refresh(pub)
    assert pub.earnings_balance == Decimal("19.00")  # заморозка снята — заработок созрел


def test_admin_blocks_suspended_with_forfeit(client, db, auth_headers, world):
    pub, _, bad, _ = world
    fraud.auto_block(db)
    client.post(f"{A}/sites/{bad.id}/moderate", headers=auth_headers,
                json={"status": "blocked", "reason": "Накрутка", "forfeit_pending": True})
    db.refresh(bad)
    assert (bad.status, bad.fraud_hold) == (SiteStatus.BLOCKED, False)
    assert partners.pending_amount(db, pub.id) == Decimal("1")  # аннулирован заработок с сайтов


def test_auto_block_ignores_single_flag(db, world):
    _, _, bad, _ = world
    stat = db.scalar(select(SiteDailyStat).where(SiteDailyStat.site_id == bad.id))
    stat.impressions = 10_000  # CTR 0.3% — остаётся один признак (мало адресов)
    db.commit()
    assert fraud.auto_block(db) == []
    db.refresh(bad)
    assert bad.status == SiteStatus.APPROVED


def test_auto_suspend_reason_translated(client, db, world):
    pub, _, bad, _ = world
    fraud.auto_block(db)
    h = {"Authorization": f"Bearer {create_access_token(pub.id)}", "Accept-Language": "en"}
    site = client.get("/api/v1/partner/sites", headers=h).json()[0]
    assert site["rejection_reason"] == ("Suspended automatically, signs of click fraud: "
                                        "CTR too high; clicks from few addresses")


def test_daily_job_runs_auto_suspend(db, world):
    from app import maintenance
    from app.database import background_session_factory
    _, _, bad, _ = world
    maintenance.fraud_block_sync(background_session_factory(db))
    db.expire_all()
    assert db.get(Site, bad.id).fraud_hold is True


def test_content_check_does_not_lift_suspension(db, world, monkeypatch):
    from app import ai
    from app.database import background_session_factory
    from app.services import site_check
    _, _, bad, _ = world
    fraud.auto_block(db)
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    bad.verified_at = bad.created_at  # владение подтверждено ранее
    db.commit()
    monkeypatch.setattr(site_check, "fetch_page", lambda url, domain, transport=None: site_check.Page(url, "T", "x"))
    monkeypatch.setattr(ai, "moderate_site",
                        lambda *a: ai.AIModerationResult(verdict="approve", risk="low", reasons=[], summary=""))
    site = site_check.check_site(background_session_factory(db), bad.id)
    assert (site.status, site.fraud_hold) == (SiteStatus.PENDING, True)
