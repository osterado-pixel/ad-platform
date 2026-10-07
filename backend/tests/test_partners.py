"""Партнёрская программа: сайты, доля от кликов, созревание заработка, выплаты и перевод на баланс."""
import time
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.auth import create_access_token
from app.config import settings
from app.models import (
    Campaign, CampaignStatus, PartnerEarning, PartnerTransaction, PartnerTxType, Payout, Site,
    SiteDailyStat, SiteStatus, Transaction, TransactionType, User,
)
from app.services import partners

P = "/api/v1/partner"
A = "/api/v1/admin/partner"
SERVE = "/api/v1/ad/serve"
DAY = 24 * 3600


def _user(db, email, balance="0"):
    u = User(email=email, hashed_password="x", balance=Decimal(balance))
    db.add(u)
    db.commit()
    return u, {"Authorization": f"Bearer {create_access_token(u.id)}"}


@pytest.fixture
def publisher(db):
    return _user(db, "pub@example.com")


@pytest.fixture
def advertiser(db):
    return _user(db, "adv@example.com", balance="100.00")


def _site(client, headers, url="https://blog.example.com", name="Блог"):
    r = client.post(f"{P}/sites", json={"name": name, "url": url}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _approve(client, auth_headers, site_id, **extra):
    r = client.post(f"{A}/sites/{site_id}/moderate", json={"status": "approved", **extra},
                    headers=auth_headers)
    assert r.status_code == 200, r.text
    return r.json()


def _placement(client, headers, site_id):
    r = client.post(f"{P}/sites/{site_id}/placements", json={"name": "Под статьёй"}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def live(client, db, auth_headers, publisher, advertiser):
    """Одобренный сайт партнёра с площадкой (цена клика 1.00) и активной кампанией рекламодателя на ней."""
    _, pub_h = publisher
    adv, _ = advertiser
    site = _site(client, pub_h)
    _approve(client, auth_headers, site["id"])
    pl = _placement(client, pub_h, site["id"])
    client.patch(f"/api/v1/placements/{pl['id']}", json={"price_per_click": "1.00"}, headers=auth_headers)
    c = Campaign(user_id=adv.id, placement_id=pl["id"], title="Акция", target_url="https://shop.example.com",
                 status=CampaignStatus.ACTIVE)
    db.add(c)
    db.commit()
    return site, pl, c


def _click(client, campaign_id):
    r = client.get(f"/api/v1/ad/click/{campaign_id}", follow_redirects=False)
    assert r.status_code == 302


def _shift_days(monkeypatch, days):
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + days * DAY)


def _journal_balance(db, user_id) -> Decimal:
    sign = {PartnerTxType.EARNING: 1, PartnerTxType.PAYOUT_RETURN: 1,
            PartnerTxType.PAYOUT: -1, PartnerTxType.TO_BALANCE: -1}
    rows = db.scalars(select(PartnerTransaction).where(PartnerTransaction.user_id == user_id)).all()
    return sum((sign[t.type] * t.amount for t in rows), Decimal("0"))


# ---------- Сайты ----------
def test_new_site_is_pending_with_default_share(client, publisher):
    _, h = publisher
    site = _site(client, h, url="https://WWW.Blog.Example.com/path")
    assert site["status"] == "pending"
    assert site["domain"] == "blog.example.com"
    assert site["revenue_share"] == 0.6
    assert [s["id"] for s in client.get(f"{P}/sites", headers=h).json()] == [site["id"]]


def test_same_domain_cannot_be_added_twice(client, db, publisher):
    _, h = publisher
    _site(client, h)
    _, other = _user(db, "other@example.com")
    r = client.post(f"{P}/sites", json={"name": "Копия", "url": "http://www.blog.example.com"}, headers=other)
    assert r.status_code == 409


def test_cannot_touch_someone_elses_site(client, db, publisher):
    _, h = publisher
    site = _site(client, h)
    _, other = _user(db, "other@example.com")
    assert client.post(f"{P}/sites/{site['id']}/placements", json={"name": "x"}, headers=other).status_code == 404
    assert client.get(f"{P}/sites/{site['id']}/placements", headers=other).status_code == 404
    assert client.get(f"{P}/sites", headers=other).json() == []


def test_placement_code_is_generated(client, publisher):
    _, h = publisher
    site = _site(client, h)
    pl = _placement(client, h, site["id"])
    assert pl["code_identifier"].startswith(f"s{site['id']}_")
    assert pl["site_id"] == site["id"]
    assert pl["price_per_click"] == float(settings.partner_default_cpc)
    assert client.get(f"{P}/sites/{site['id']}/placements", headers=h).json()[0]["id"] == pl["id"]


def test_placement_limit_per_site(client, publisher, monkeypatch):
    monkeypatch.setattr(settings, "partner_max_placements_per_site", 1)
    _, h = publisher
    site = _site(client, h)
    _placement(client, h, site["id"])
    assert client.post(f"{P}/sites/{site['id']}/placements", json={"name": "2"}, headers=h).status_code == 409


def test_ads_shown_only_on_approved_sites(client, db, auth_headers, publisher, advertiser):
    _, pub_h = publisher
    adv, _ = advertiser
    site = _site(client, pub_h)
    pl = _placement(client, pub_h, site["id"])
    db.add(Campaign(user_id=adv.id, placement_id=pl["id"], title="Акция",
                    target_url="https://shop.example.com", status=CampaignStatus.ACTIVE))
    db.commit()
    code = {"placement_code": pl["code_identifier"]}
    assert client.get(SERVE, params=code).status_code == 404  # сайт ещё на проверке

    _approve(client, auth_headers, site["id"])
    assert client.get(SERVE, params=code).status_code == 200

    r = client.post(f"{A}/sites/{site['id']}/moderate", json={"status": "blocked", "reason": "Накрутка"},
                    headers=auth_headers)
    assert r.json()["rejection_reason"] == "Накрутка"
    assert client.get(SERVE, params=code).status_code == 404
    # Клик по уже показанному баннеру заблокированного сайта не оплачивается
    c = db.scalar(select(Campaign))
    assert client.get(f"/api/v1/ad/click/{c.id}", follow_redirects=False).status_code == 404


def test_rejected_site_cannot_get_placements(client, auth_headers, publisher):
    _, h = publisher
    site = _site(client, h)
    r = client.post(f"{A}/sites/{site['id']}/moderate", json={"status": "rejected"}, headers=auth_headers)
    assert r.status_code == 422  # без причины нельзя
    client.post(f"{A}/sites/{site['id']}/moderate", json={"status": "rejected", "reason": "Пустой сайт"},
                headers=auth_headers)
    assert client.post(f"{P}/sites/{site['id']}/placements", json={"name": "x"}, headers=h).status_code == 409


def test_admin_endpoints_need_admin(client, publisher):
    _, h = publisher
    assert client.get(f"{A}/sites", headers=h).status_code == 403
    assert client.get(f"{A}/payouts", headers=h).status_code == 403
    assert client.post(f"{A}/mature", headers=h).status_code == 403


def test_admin_lists_pending_sites_with_owner(client, auth_headers, publisher):
    _, h = publisher
    site = _site(client, h)
    rows = client.get(f"{A}/sites", params={"status": "pending"}, headers=auth_headers).json()
    assert [(s["id"], s["owner_email"], s["custom_share"]) for s in rows] == [(site["id"], "pub@example.com", False)]
    assert client.get(f"{A}/sites", params={"status": "approved"}, headers=auth_headers).json() == []


# ---------- Доля от кликов ----------
def test_click_on_partner_site_shares_revenue(client, db, live, publisher, advertiser):
    site, _, c = live
    pub, pub_h = publisher
    adv, _ = advertiser
    client.get(SERVE, params={"placement_code": live[1]["code_identifier"]})
    _click(client, c.id)

    db.refresh(adv)
    assert adv.balance == Decimal("99.00")
    earning = db.scalar(select(PartnerEarning))
    assert (earning.user_id, earning.amount, earning.matured) == (pub.id, Decimal("0.60"), False)
    stat = db.scalar(select(SiteDailyStat))
    assert (stat.site_id, stat.impressions, stat.clicks, stat.revenue, stat.earnings) == (
        site["id"], 1, 1, Decimal("1.00"), Decimal("0.60"))

    s = client.get(f"{P}/summary", headers=pub_h).json()
    assert (s["earnings_balance"], s["pending"], s["hold_days"]) == (0.0, 0.6, 14)
    assert s["totals"] == {"impressions": 1, "clicks": 1, "revenue": 1.0, "earnings": 0.6, "ctr": 100.0}
    assert s["sites"][0]["earnings"] == 0.6
    assert len(s["days"]) == 30 and s["days"][-1]["clicks"] == 1


def test_share_is_rounded_down(client, db, auth_headers, live, publisher):
    _, pl, c = live
    client.patch(f"/api/v1/placements/{pl['id']}", json={"price_per_click": "0.15"}, headers=auth_headers)
    _click(client, c.id)
    assert db.scalar(select(PartnerEarning.amount)) == Decimal("0.09")  # 0.15 × 0.6 = 0.09


def test_custom_site_share(client, db, auth_headers, live):
    site, _, c = live
    r = _approve(client, auth_headers, site["id"], revenue_share=0.7)
    assert (r["revenue_share"], r["custom_share"]) == (0.7, True)
    _click(client, c.id)
    assert db.scalar(select(PartnerEarning.amount)) == Decimal("0.70")

    r = _approve(client, auth_headers, site["id"], reset_share=True)
    assert (r["revenue_share"], r["custom_share"]) == (0.6, False)


def test_own_ads_on_own_site_earn_nothing(client, db, live, publisher):
    _, pl, _ = live
    pub, _ = publisher
    pub.balance = Decimal("10.00")
    own = Campaign(user_id=pub.id, placement_id=pl["id"], title="Своё", target_url="https://me.example.com",
                   status=CampaignStatus.ACTIVE)
    db.add(own)
    db.commit()
    _click(client, own.id)
    db.refresh(pub)
    assert pub.balance == Decimal("9.00")
    assert db.scalar(select(func.count()).select_from(PartnerEarning)) == 0
    assert db.scalar(select(SiteDailyStat.clicks)) == 1


def test_platform_placement_has_no_partner(client, db):
    # Площадка без сайта (site_id = NULL): клик оплачивается, партнёрских начислений нет
    from app.models import Placement
    adv = User(email="a@example.com", hashed_password="x", balance=Decimal("5"))
    pl = Placement(name="Шапка", code_identifier="header", price_per_click=Decimal("1"))
    db.add_all([adv, pl])
    db.commit()
    c = Campaign(user_id=adv.id, placement_id=pl.id, title="A", target_url="https://a.example.com",
                 status=CampaignStatus.ACTIVE)
    db.add(c)
    db.commit()
    assert client.get(SERVE, params={"placement_code": "header"}).status_code == 200
    _click(client, c.id)
    db.refresh(adv)
    assert adv.balance == Decimal("4.00")
    assert db.scalar(select(func.count()).select_from(PartnerEarning)) == 0


# ---------- Созревание ----------
def test_earnings_mature_after_hold(client, db, live, publisher, monkeypatch):
    _, _, c = live
    pub, pub_h = publisher
    _click(client, c.id)

    _shift_days(monkeypatch, 13)
    s = client.get(f"{P}/summary", headers=pub_h).json()
    assert (s["earnings_balance"], s["pending"]) == (0.0, 0.6)

    _shift_days(monkeypatch, 1)  # всего 14 дней
    s = client.get(f"{P}/summary", headers=pub_h).json()
    assert (s["earnings_balance"], s["pending"]) == (0.6, 0.0)
    assert partners.mature(db) == 0  # повторно не зачисляется

    tx = client.get(f"{P}/transactions", headers=pub_h).json()
    assert [(t["type"], t["amount"]) for t in tx] == [("earning", 0.6)]
    db.refresh(pub)
    assert _journal_balance(db, pub.id) == pub.earnings_balance


def test_daily_job_matures_for_everyone(client, db, auth_headers, live, publisher, monkeypatch):
    _, _, c = live
    pub, _ = publisher
    _click(client, c.id)
    _shift_days(monkeypatch, 14)
    assert client.post(f"{A}/mature", headers=auth_headers).json() == {"matured": 0.6}
    db.refresh(pub)
    assert pub.earnings_balance == Decimal("0.60")


def _earn(db, user, amount):
    """Сразу созревший заработок (без кликов) — для тестов выплат."""
    user.earnings_balance = Decimal(amount)
    db.add(PartnerTransaction(user_id=user.id, amount=Decimal(amount), type=PartnerTxType.EARNING))
    db.commit()


# ---------- Выплаты ----------
def test_payout_request_and_paid(client, db, auth_headers, publisher):
    pub, h = publisher
    _earn(db, pub, "50.00")
    r = client.post(f"{P}/payouts", json={"amount": 30, "method": "paypal", "details": " pub@pay.example "},
                    headers=h)
    assert r.status_code == 201, r.text
    payout = r.json()
    assert (payout["status"], payout["amount"], payout["details"]) == ("pending", 30.0, "pub@pay.example")
    db.refresh(pub)
    assert pub.earnings_balance == Decimal("20.00")

    queue = client.get(f"{A}/payouts", params={"status": "pending"}, headers=auth_headers).json()
    assert [(p["id"], p["owner_email"]) for p in queue] == [(payout["id"], "pub@example.com")]

    r = client.post(f"{A}/payouts/{payout['id']}/paid", json={"note": "PayPal tx 123"}, headers=auth_headers)
    assert (r.json()["status"], r.json()["admin_note"]) == ("paid", "PayPal tx 123")
    assert r.json()["processed_at"]
    assert client.post(f"{A}/payouts/{payout['id']}/reject", json={}, headers=auth_headers).status_code == 409
    assert client.get(f"{P}/payouts", headers=h).json()[0]["status"] == "paid"
    assert _journal_balance(db, pub.id) == Decimal("20.00")


def test_rejected_payout_returns_money(client, db, auth_headers, publisher):
    pub, h = publisher
    _earn(db, pub, "25.00")
    pid = client.post(f"{P}/payouts", json={"amount": 25, "method": "bank", "details": "DE00 1234"},
                      headers=h).json()["id"]
    r = client.post(f"{A}/payouts/{pid}/reject", json={"note": "Неверный IBAN"}, headers=auth_headers)
    assert r.json()["status"] == "rejected"
    db.refresh(pub)
    assert pub.earnings_balance == Decimal("25.00")
    types = [t["type"] for t in client.get(f"{P}/transactions", headers=h).json()]
    assert types == ["payout_return", "payout", "earning"]
    assert _journal_balance(db, pub.id) == Decimal("25.00")


@pytest.mark.parametrize("amount, status_code", [(10, 400), (60, 400)])
def test_payout_below_minimum_or_above_balance(client, db, publisher, amount, status_code):
    pub, h = publisher
    _earn(db, pub, "50.00")
    r = client.post(f"{P}/payouts", json={"amount": amount, "method": "card", "details": "4242"}, headers=h)
    assert r.status_code == status_code
    db.refresh(pub)
    assert pub.earnings_balance == Decimal("50.00")
    assert db.scalar(select(func.count()).select_from(Payout)) == 0


def test_payout_needs_details(client, db, publisher):
    pub, h = publisher
    _earn(db, pub, "50.00")
    r = client.post(f"{P}/payouts", json={"amount": 20, "method": "card", "details": "   "}, headers=h)
    assert r.status_code == 422


def test_unknown_payout_is_404(client, auth_headers):
    assert client.post(f"{A}/payouts/999/paid", json={}, headers=auth_headers).status_code == 404


def test_transfer_to_ad_balance(client, db, publisher):
    pub, h = publisher
    _earn(db, pub, "5.00")
    r = client.post(f"{P}/transfer", json={"amount": "3.50"}, headers=h)
    assert r.json()["balance"] == 3.5
    db.refresh(pub)
    assert (pub.balance, pub.earnings_balance) == (Decimal("3.50"), Decimal("1.50"))
    deposit = db.scalar(select(Transaction).where(Transaction.user_id == pub.id))
    assert (deposit.type, deposit.amount) == (TransactionType.DEPOSIT, Decimal("3.50"))
    assert client.post(f"{P}/transfer", json={"amount": 2}, headers=h).status_code == 400
    assert _journal_balance(db, pub.id) == Decimal("1.50")


def test_transfer_message_translated(client, db, publisher):
    pub, h = publisher
    _earn(db, pub, "5.00")
    client.post(f"{P}/transfer", json={"amount": 1}, headers=h)
    r = client.get(f"{P}/transactions", headers={**h, "Accept-Language": "en"})
    assert r.json()[0]["description"] == "Transfer to ad balance"


def test_site_model_effective_share(db, monkeypatch):
    monkeypatch.setattr(settings, "publisher_revenue_share", Decimal("0.5"))
    assert Site(revenue_share=None).effective_share == Decimal("0.5")
    assert Site(revenue_share=Decimal("0.75")).effective_share == Decimal("0.75")
    assert partners.share_of(Decimal("0.03"), None) == Decimal("0.01")
    assert SiteStatus.APPROVED.value == "approved"
