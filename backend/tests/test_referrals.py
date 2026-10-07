"""Реферальная программа: код и ссылка, привязка при регистрации, вознаграждение с кликов."""
import time
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.auth import create_access_token
from app.config import settings
from app.models import (
    Campaign, CampaignStatus, EarningSource, PartnerEarning, Placement, Site, SiteStatus, User,
)
from app.services import partners

REG = "/api/v1/auth/register"
REF = "/api/v1/partner/referral"


@pytest.fixture(autouse=True)
def no_exploration(monkeypatch):
    monkeypatch.setattr(settings, "auction_explore_rate", 0.0)


def _user(db, email, **kw):
    u = User(email=email, hashed_password="x", **kw)
    db.add(u)
    db.commit()
    return u


def _h(user):
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _register(client, email, ref=None):
    body = {"email": email, "password": "password123", **({"ref": ref} if ref is not None else {})}
    r = client.post(REG, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_referral_link_is_stable(client, db):
    u = _user(db, "inviter@example.com")
    first = client.get(REF, headers=_h(u)).json()
    assert len(first["code"]) == 8 and set(first["code"]) <= set(partners.REFERRAL_ALPHABET)
    assert first["link"] == f"http://testserver/app?ref={first['code']}"
    assert (first["share"], first["days"], first["invited"], first["earned_total"]) == (0.1, 365, 0, 0.0)
    assert client.get(REF, headers=_h(u)).json()["code"] == first["code"]


def test_link_uses_public_url(client, db, monkeypatch):
    monkeypatch.setattr(settings, "public_url", "https://ads.example.com/")
    u = _user(db, "inviter@example.com")
    assert client.get(REF, headers=_h(u)).json()["link"].startswith("https://ads.example.com/app?ref=")


def test_registration_with_code_links_users(client, db):
    inviter = _user(db, "inviter@example.com")
    code = client.get(REF, headers=_h(inviter)).json()["code"]
    new_id = _register(client, "friend@example.com", ref=f"  {code.lower()} ")  # регистр и пробелы не важны
    assert db.get(User, new_id).referred_by_id == inviter.id
    info = client.get(REF, headers=_h(inviter)).json()
    assert (info["invited"], info["active"]) == (1, 1)


@pytest.mark.parametrize("ref", ["", "NOSUCHCODE", "x" * 64])
def test_unknown_code_does_not_block_registration(client, db, ref):
    new_id = _register(client, "friend@example.com", ref=ref)
    assert db.get(User, new_id).referred_by_id is None


def test_code_too_long_is_rejected(client):
    r = client.post(REG, json={"email": "f@example.com", "password": "password123", "ref": "x" * 65})
    assert r.status_code == 422


@pytest.fixture
def network(db):
    """Пригласивший → рекламодатель (ставка 1.00 на всю сеть) и пригласивший → партнёр с сайтом."""
    inviter = _user(db, "inviter@example.com")
    adv = _user(db, "adv@example.com", balance=Decimal("100"), referred_by_id=inviter.id)
    pub = _user(db, "pub@example.com", referred_by_id=inviter.id)
    site = Site(user_id=pub.id, name="Блог", url="https://blog.example.com", domain="blog.example.com",
                status=SiteStatus.APPROVED)
    own = Placement(name="Своя", code_identifier="own", price_per_click=Decimal("0.10"))
    db.add_all([site, own])
    db.commit()
    db.add(Placement(name="Партнёр", code_identifier="partner", price_per_click=Decimal("0.10"), site_id=site.id))
    db.add(Campaign(user_id=adv.id, cpc_bid=Decimal("1.00"), title="A", target_url="https://a.example.com",
                    status=CampaignStatus.ACTIVE))
    db.commit()
    return inviter, adv, pub


def _click(client, code):
    url = client.get("/api/v1/ad/serve", params={"placement_code": code}).json()["click_url"]
    assert client.get(url, follow_redirects=False).status_code == 302


def _earnings(db, user, source):
    return db.scalar(select(PartnerEarning.amount).where(
        PartnerEarning.user_id == user.id, PartnerEarning.source == source))


def test_reward_from_advertiser_on_platform_placement(client, db, network):
    inviter, _, _ = network
    _click(client, "own")  # платформе — весь 1.00, пригласившему 10%
    assert _earnings(db, inviter, EarningSource.REFERRAL) == Decimal("0.10")


def test_reward_from_both_sides_on_partner_site(client, db, network):
    inviter, _, pub = network
    _click(client, "partner")
    # Клик 1.00: партнёру 0.60, платформе 0.40; пригласившему 10% от 0.40 — за рекламодателя и за партнёра
    assert _earnings(db, pub, EarningSource.SITE) == Decimal("0.60")
    assert _earnings(db, inviter, EarningSource.REFERRAL) == Decimal("0.08")
    assert client.get(REF, headers=_h(inviter)).json()["earned_total"] == 0.08


def test_reward_stops_after_referral_period(client, db, network, monkeypatch):
    inviter, _, _ = network
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 366 * 24 * 3600)
    _click(client, "own")
    assert _earnings(db, inviter, EarningSource.REFERRAL) is None
    assert client.get(REF, headers=_h(inviter)).json()["active"] == 0


def test_referral_earnings_mature_like_site_earnings(client, db, network, monkeypatch):
    inviter, _, _ = network
    _click(client, "own")
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + settings.earnings_hold_days * 24 * 3600)
    s = client.get("/api/v1/partner/summary", headers=_h(inviter)).json()
    assert (s["earnings_balance"], s["pending"]) == (0.1, 0.0)


def test_tiny_click_gives_no_reward(db):
    inviter = _user(db, "inviter@example.com")
    adv = _user(db, "adv@example.com", referred_by_id=inviter.id)
    partners.accrue_referrals(db, (adv.id, None), Decimal("0.05"))  # 10% от 0.05 → 0.00
    partners.accrue_referrals(db, (adv.id,), Decimal("0"))
    db.commit()
    assert db.scalar(select(PartnerEarning)) is None


def test_inviter_deleted_link_cleared(db):
    # FK SET NULL: удаление пригласившего (например, вручную в базе) не ломает приглашённого
    inviter = _user(db, "inviter@example.com")
    friend = _user(db, "friend@example.com", referred_by_id=inviter.id)
    db.delete(inviter)
    db.commit()
    db.refresh(friend)
    assert friend.referred_by_id is None
