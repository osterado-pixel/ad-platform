"""Проверка сайтов партнёров на накрутку кликов — отчёт для администратора.

Признаки (коды — для перевода в кабинете):
- high_ctr — кликабельность выше HIGH_CTR_PERCENT при заметном числе показов: у честного баннера CTR
  обычно 0.1–2%, а накрутчик кликает по своей рекламе;
- few_ips — мало разных адресов на клики (клики с одних и тех же IP), при заметном числе кликов;
- clicks_over_impressions — кликов больше, чем показов: ссылку клика открывают напрямую, без баннера.

Отчёт лишь подсказывает: решение (заблокировать сайт и аннулировать созревающий заработок) — за администратором.
Пока заработок созревает (EARNINGS_HOLD_DAYS), его можно аннулировать — forfeit_pending().
"""
import time
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.database import write_lock
from app.models import Click, EarningSource, PartnerEarning, Placement, Site, SiteDailyStat, User
from app.stats import utc_today

HIGH_CTR_PERCENT = 5.0
HIGH_CTR_MIN_IMPRESSIONS = 100
FEW_IPS_RATIO = 0.5          # разных адресов меньше половины кликов
FEW_IPS_MIN_CLICKS = 20


def site_flags(impressions: int, clicks: int, unique_ips: int) -> list[str]:
    flags = []
    if impressions >= HIGH_CTR_MIN_IMPRESSIONS and clicks * 100 > HIGH_CTR_PERCENT * impressions:
        flags.append("high_ctr")
    if clicks >= FEW_IPS_MIN_CLICKS and unique_ips < clicks * FEW_IPS_RATIO:
        flags.append("few_ips")
    if clicks > impressions:
        flags.append("clicks_over_impressions")
    return flags


def fraud_report(db: Session, days: int) -> list[dict]:
    """Сайты с кликами за последние days дней: показатели и признаки накрутки; подозрительные — сверху."""
    start = utc_today() - timedelta(days=days - 1)
    d = SiteDailyStat
    stats = {r.site_id: r for r in db.execute(
        select(d.site_id, func.sum(d.impressions).label("impressions"), func.sum(d.clicks).label("clicks"),
               func.sum(d.earnings).label("earnings"))
        .where(d.day >= start).group_by(d.site_id))}
    since_minute = int(time.time() // 60) - days * 24 * 60
    ips = {r.site_id: r.unique_ips for r in db.execute(
        select(Placement.site_id, func.count(func.distinct(Click.ip_hash)).label("unique_ips"))
        .join(Placement, Placement.id == Click.placement_id)
        .where(Placement.site_id.is_not(None), Click.time_window >= since_minute)
        .group_by(Placement.site_id))}
    pending = dict(db.execute(
        select(PartnerEarning.user_id, func.sum(PartnerEarning.amount))
        .where(PartnerEarning.matured.is_(False)).group_by(PartnerEarning.user_id)).all())

    rows = []
    sites = db.execute(select(Site, User.email).join(User, User.id == Site.user_id)
                       .where(Site.id.in_([sid for sid, r in stats.items() if r.clicks]))).all()
    for site, email in sites:
        s = stats[site.id]
        impressions, clicks = int(s.impressions or 0), int(s.clicks or 0)
        unique_ips = int(ips.get(site.id, 0))
        rows.append({
            "site_id": site.id, "name": site.name, "domain": site.domain, "status": site.status,
            "user_id": site.user_id, "owner_email": email,
            "impressions": impressions, "clicks": clicks,
            "ctr": round(clicks * 100 / impressions, 2) if impressions else 0.0,
            "unique_ips": unique_ips,
            "earnings": Decimal(s.earnings or 0).quantize(Decimal("0.01")),
            "pending": Decimal(pending.get(site.user_id) or 0).quantize(Decimal("0.01")),
            "flags": site_flags(impressions, clicks, unique_ips),
        })
    rows.sort(key=lambda r: (-len(r["flags"]), -r["clicks"]))
    return rows


def forfeit_pending(db: Session, user_id: int) -> Decimal:
    """Аннулирует созревающий заработок партнёра с сайтов (не реферальный): сумма переходит
    из amount в forfeited и не будет выплачена. Уже созревшее (доступное к выводу) не трогается."""
    where = (PartnerEarning.user_id == user_id, PartnerEarning.matured.is_(False),
             PartnerEarning.source == EarningSource.SITE, PartnerEarning.amount > 0)
    with write_lock():
        total = db.scalar(select(func.coalesce(func.sum(PartnerEarning.amount), 0)).where(*where))
        # Одна команда на строку: amount и forfeited меняются вместе, параллельный клик не потеряется
        db.execute(update(PartnerEarning).where(*where).values(
            forfeited=PartnerEarning.forfeited + PartnerEarning.amount, amount=0))
        db.commit()
    return Decimal(total).quantize(Decimal("0.01"))
