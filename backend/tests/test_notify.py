"""Уведомления по email: события, язык письма, «заканчиваются деньги», сводка администратору."""
import time
from datetime import timedelta
from decimal import Decimal

import pytest

from app.auth import create_access_token
from app.config import settings
from app.models import (
    Campaign, CampaignDailyStat, CampaignStatus, Click, Payout, Placement, Site, SiteDailyStat, SiteStatus, User,
    UserRole,
)
from app.services import fraud, mailer, notify
from app.stats import utc_today


@pytest.fixture
def mails(monkeypatch):
    """Уведомления включены, отправка — сразу и в список (вместо почтового сервера)."""
    box = []
    monkeypatch.setattr(settings, "notifications", True)
    monkeypatch.setattr(notify, "_dispatch", lambda fn, *args: fn(*args))
    monkeypatch.setattr(mailer, "send_email", lambda to, subject, body: box.append((to, subject, body)))
    return box


def _user(db, email, **kw):
    u = User(email=email, hashed_password="x", **kw)
    db.add(u)
    db.commit()
    return u


def test_campaign_approved_by_admin(client, db, auth_headers, mails, monkeypatch):
    monkeypatch.setattr(settings, "public_url", "https://ads.example.com")
    adv = _user(db, "adv@example.com", language="en")
    c = Campaign(user_id=adv.id, title="Sale", target_url="https://a.example.com", status=CampaignStatus.MODERATION)
    db.add(c)
    db.commit()
    client.patch(f"/api/v1/campaigns/{c.id}/moderate", json={"status": "active"}, headers=auth_headers)
    [(to, subject, body)] = mails
    assert (to, subject) == ("adv@example.com", "Campaign “Sale” approved")
    assert "showing across the network" in body and f"https://ads.example.com/app#/campaigns/{c.id}" in body


def test_campaign_rejected_in_russian_with_admin_reason(client, db, auth_headers, mails):
    adv = _user(db, "adv@example.com", language="ru")
    c = Campaign(user_id=adv.id, title="Акция", target_url="https://a.example.com", status=CampaignStatus.MODERATION)
    db.add(c)
    db.commit()
    client.patch(f"/api/v1/campaigns/{c.id}/moderate", json={"status": "rejected", "rejection_reason": "Нет лицензии"},
                 headers=auth_headers)
    [(_, subject, body)] = mails
    assert subject == "Кампания «Акция» отклонена" and "Причина: Нет лицензии" in body
    assert "app#" not in body  # PUBLIC_URL не задан — письмо без ссылки


def test_off_switch(db, mails, monkeypatch):
    u = _user(db, "u@example.com")
    monkeypatch.setattr(settings, "notifications", False)
    notify.send(u, "Тема", "Текст")
    assert mails == []


def test_site_decisions(client, db, auth_headers, mails):
    pub = _user(db, "pub@example.com", language="de")
    site = Site(user_id=pub.id, name="Blog", url="https://blog.example.com", domain="blog.example.com")
    db.add(site)
    db.commit()
    url = f"/api/v1/admin/partner/sites/{site.id}/moderate"
    client.post(url, json={"status": "approved"}, headers=auth_headers)
    client.post(url, json={"status": "approved", "revenue_share": 0.7}, headers=auth_headers)  # статус тот же
    client.post(url, json={"status": "blocked", "reason": "Betrug"}, headers=auth_headers)
    assert [m[1] for m in mails] == ["Website blog.example.com freigegeben", "Website blog.example.com gesperrt"]
    assert "Grund: Betrug" in mails[1][2]


def test_auto_block_notifies(db, mails):
    pub = _user(db, "pub@example.com", language="en")
    adv = _user(db, "adv@example.com")
    site = Site(user_id=pub.id, name="B", url="https://b.example.com", domain="b.example.com",
                status=SiteStatus.APPROVED)
    db.add(site)
    db.commit()
    pl = Placement(name="p", code_identifier="p", site_id=site.id)
    c = Campaign(user_id=adv.id, title="A", target_url="https://a.example.com", status=CampaignStatus.ACTIVE)
    db.add_all([pl, c])
    db.commit()
    # 100 показов, 30 кликов с одного адреса: высокий CTR и мало адресов — два признака
    db.add(SiteDailyStat(site_id=site.id, day=utc_today(), impressions=100, clicks=30, revenue=Decimal("30"),
                         earnings=Decimal("18")))
    minute = int(time.time() // 60)
    db.add_all([Click(campaign_id=c.id, ip_hash="same", time_window=minute - i, cost=Decimal("1"),
                      placement_id=pl.id) for i in range(30)])
    db.commit()
    assert fraud.auto_block(db) == [site.id]
    [(to, subject, body)] = mails
    assert (to, subject) == ("pub@example.com", "Site b.example.com suspended for review")
    assert "Suspended automatically, signs of click fraud" in body and "resume after the review" in body


def test_payout_processed(client, db, auth_headers, mails):
    pub = _user(db, "pub@example.com", language="ru")
    p1 = Payout(user_id=pub.id, amount=Decimal("25"), method="paypal", details="pub@pay.example")
    p2 = Payout(user_id=pub.id, amount=Decimal("30"), method="paypal", details="pub@pay.example")
    db.add_all([p1, p2])
    db.commit()
    client.post(f"/api/v1/admin/partner/payouts/{p1.id}/paid", json={}, headers=auth_headers)
    client.post(f"/api/v1/admin/partner/payouts/{p2.id}/reject", json={}, headers=auth_headers)
    assert [m[1] for m in mails] == [f"Выплата #{p1.id} отправлена", f"Заявка на выплату #{p2.id} отклонена"]
    assert "Сумма: 25.00" in mails[0][2]


def test_language_remembered_on_register_and_login(client, db):
    client.post("/api/v1/auth/register", json={"email": "x@example.com", "password": "password123"},
                headers={"Accept-Language": "de"})
    u = db.query(User).filter_by(email="x@example.com").one()
    assert u.language == "de"
    client.post("/api/v1/auth/login", data={"username": "x@example.com", "password": "password123"},
                headers={"Accept-Language": "en"})
    db.refresh(u)
    assert u.language == "en"


# ---------- Заканчиваются деньги ----------
@pytest.fixture
def advertiser(db):
    adv = _user(db, "adv@example.com", balance=Decimal("3.00"), language="en")
    db.add(Campaign(user_id=adv.id, title="A", target_url="https://a.example.com", status=CampaignStatus.ACTIVE))
    db.commit()
    return adv


def test_low_balance_once_until_topped_up(db, advertiser, mails):
    assert notify.low_balance(db) == 1
    assert mails[0][1] == "Your ad balance is running low" and "Your balance is 3.00" in mails[0][2]
    assert notify.low_balance(db) == 0          # повторно не пишем
    advertiser.balance = Decimal("50")
    db.commit()
    notify.low_balance(db)                      # баланс поднялся — флаг сброшен
    advertiser.balance = Decimal("1")
    db.commit()
    assert notify.low_balance(db) == 1          # снова опустился — снова письмо


def test_low_balance_only_with_active_campaigns(db, mails):
    _user(db, "idle@example.com", balance=Decimal("0"))
    assert notify.low_balance(db) == 0


# ---------- Сводка администратору ----------
def test_admin_digest_only_when_needed(db, mails):
    admin = _user(db, "boss@example.com", role=UserRole.ADMIN, language="en")
    assert notify.admin_digest(db) is False and mails == []
    adv = _user(db, "adv@example.com")
    c = Campaign(user_id=adv.id, title="A", target_url="https://a.example.com", status=CampaignStatus.MODERATION)
    db.add_all([c, Payout(user_id=adv.id, amount=Decimal("20"), method="bank", details="DE00")])
    db.commit()
    db.add(CampaignDailyStat(campaign_id=c.id, day=utc_today() - timedelta(days=1), impressions=1, clicks=1,
                             spend=Decimal("7.50")))
    db.commit()
    assert notify.admin_digest(db) is True
    [(to, subject, body)] = mails
    assert (to, subject) == (admin.email, "Ad Platform: waiting for your decision")
    for line in ("Campaigns in review: 1", "Partner sites in review: 0", "Payout requests: 1 totaling 20.00",
                 "Yesterday's turnover: 7.50"):
        assert line in body


def test_daily_job_sends_emails(db, advertiser, mails):
    from app import maintenance
    from app.database import background_session_factory
    maintenance.daily_emails_sync(background_session_factory(db))
    assert [m[0] for m in mails] == ["adv@example.com"]


def test_real_dispatch_uses_thread_pool(db, monkeypatch):
    import threading
    sent, done = [], threading.Event()
    monkeypatch.setattr(settings, "notifications", True)
    monkeypatch.setattr(mailer, "send_email", lambda *a: (sent.append(a), done.set()))
    notify.send(_user(db, "u@example.com"), "Тема", "Текст")
    assert done.wait(5)  # письмо ушло в отдельном потоке
    assert sent[0][0] == "u@example.com"
