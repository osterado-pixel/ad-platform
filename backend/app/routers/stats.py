from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.database import get_db
from app.models import (
    Campaign, CampaignDailyStat, CampaignStatus, Placement, PlacementDailyStat, User, UserRole,
)
from app.schemas import (
    CampaignStats, CampaignTotals, DayStat, MyStats, PlacementTotals, PlatformStats, Totals,
)
from app.stats import utc_today

router = APIRouter(prefix="/api/v1/stats", tags=["Статистика"])

Days = Query(default=30, ge=1, le=365, description="За сколько последних дней (включая сегодня, UTC)")


def _ctr(impressions: int, clicks: int) -> float:
    # Всегда число (фронтенду не нужна проверка на null); без показов — 0
    return round(clicks * 100 / impressions, 2) if impressions else 0.0


def _totals(impressions, clicks, spend) -> Totals:
    impressions, clicks = int(impressions or 0), int(clicks or 0)
    # Всегда 2 знака: "0.00", а не "0" — одинаковый формат денег во всём API
    spend = Decimal(spend or 0).quantize(Decimal("0.01"))
    return Totals(impressions=impressions, clicks=clicks, spend=spend, ctr=_ctr(impressions, clicks))


def _period(days: int) -> tuple[date, date]:
    end = utc_today()
    return end - timedelta(days=days - 1), end


def _daily(db: Session, start: date, end: date, *where) -> list[DayStat]:
    """Ряд по дням [start..end] с нулями в днях без событий."""
    d = CampaignDailyStat
    rows = db.execute(
        select(d.day, func.sum(d.impressions), func.sum(d.clicks), func.sum(d.spend))
        .join(Campaign, Campaign.id == d.campaign_id)
        .where(d.day >= start, d.day <= end, *where)
        .group_by(d.day)
    ).all()
    by_day = {r[0]: r for r in rows}
    result, day = [], start
    while day <= end:
        _, imp, clk, sp = by_day.get(day, (day, 0, 0, Decimal("0")))
        t = _totals(imp, clk, sp)
        result.append(DayStat(day=day, **t.model_dump()))
        day += timedelta(days=1)
    return result


def _sum_days(days: list[DayStat]) -> Totals:
    return _totals(sum(x.impressions for x in days), sum(x.clicks for x in days),
                   sum((x.spend for x in days), Decimal("0")))


# --- Статистика одной кампании ---
@router.get("/campaigns/{campaign_id}", response_model=CampaignStats)
def campaign_stats(
    campaign_id: int,
    days: int = Days,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    campaign = db.get(Campaign, campaign_id)
    if campaign is None or (campaign.user_id != current_user.id and current_user.role != UserRole.ADMIN):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кампания не найдена")
    start, end = _period(days)
    series = _daily(db, start, end, CampaignDailyStat.campaign_id == campaign_id)
    return CampaignStats(campaign_id=campaign.id, title=campaign.title, status=campaign.status,
                         period_start=start, period_end=end, totals=_sum_days(series), days=series)


# --- Сводка рекламодателя ---
@router.get("/me", response_model=MyStats)
def my_stats(
    days: int = Days,
    campaigns_limit: int = Query(default=50, ge=1, le=500,
                                 description="Сколько кампаний (новые сверху) показать в списке"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    start, end = _period(days)
    series = _daily(db, start, end, Campaign.user_id == current_user.id)

    by_status = dict(db.execute(
        select(Campaign.status, func.count()).where(Campaign.user_id == current_user.id)
        .group_by(Campaign.status)
    ).all())

    d = CampaignDailyStat
    per_campaign = db.execute(
        select(Campaign.id, Campaign.title, Campaign.status,
               func.coalesce(func.sum(d.impressions), 0), func.coalesce(func.sum(d.clicks), 0),
               func.coalesce(func.sum(d.spend), 0))
        .outerjoin(d, (d.campaign_id == Campaign.id) & (d.day >= start) & (d.day <= end))
        .where(Campaign.user_id == current_user.id)
        .group_by(Campaign.id, Campaign.title, Campaign.status)
        .order_by(Campaign.id.desc())
        .limit(campaigns_limit + 1)  # +1: узнать, есть ли ещё
    ).all()

    return MyStats(
        balance=current_user.balance,
        period_start=start, period_end=end,
        campaigns_by_status={s.value: by_status.get(s, 0) for s in CampaignStatus},
        totals=_sum_days(series),
        days=series,
        campaigns=[CampaignTotals(campaign_id=cid, title=title, status=st,
                                  **_totals(imp, clk, Decimal(sp)).model_dump())
                   for cid, title, st, imp, clk, sp in per_campaign[:campaigns_limit]],
        campaigns_has_more=len(per_campaign) > campaigns_limit,
    )


# --- Сводка платформы (для админа) ---
@router.get("/platform", response_model=PlatformStats)
def platform_stats(
    days: int = Days,
    placements_limit: int = Query(default=100, ge=1, le=500,
                                  description="Сколько площадок показать в разбивке"),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    start, end = _period(days)
    series = _daily(db, start, end)

    # По статистике площадок, а не кампаний: у кампаний на всю сеть нет своей площадки
    d = PlacementDailyStat
    per_placement = db.execute(
        select(Placement.id, Placement.name, Placement.code_identifier, Placement.is_active,
               func.coalesce(func.sum(d.impressions), 0), func.coalesce(func.sum(d.clicks), 0),
               func.coalesce(func.sum(d.spend), 0))
        .outerjoin(d, (d.placement_id == Placement.id) & (d.day >= start) & (d.day <= end))
        .group_by(Placement.id, Placement.name, Placement.code_identifier, Placement.is_active)
        .order_by(Placement.id)
        .limit(placements_limit + 1)
    ).all()

    return PlatformStats(
        period_start=start, period_end=end,
        totals=_sum_days(series),  # spend здесь = выручка платформы за период
        days=series,
        users_count=db.scalar(select(func.count()).select_from(User)),
        active_campaigns=db.scalar(select(func.count()).select_from(Campaign)
                                   .where(Campaign.status == CampaignStatus.ACTIVE)),
        moderation_queue=db.scalar(select(func.count()).select_from(Campaign)
                                   .where(Campaign.status == CampaignStatus.MODERATION)),
        advertisers_balance=db.scalar(select(func.coalesce(func.sum(User.balance), 0))),
        placements=[PlacementTotals(placement_id=pid, name=name, code_identifier=code, is_active=active,
                                    **_totals(imp, clk, Decimal(sp)).model_dump())
                    for pid, name, code, active, imp, clk, sp in per_placement[:placements_limit]],
        placements_has_more=len(per_placement) > placements_limit,
    )


# Тот же отчёт по пути из учебной инструкции
analytics_router = APIRouter(prefix="/api/v1/analytics", tags=["Статистика"])
analytics_router.add_api_route("/summary", my_stats, methods=["GET"], response_model=MyStats,
                               summary="Сводка рекламодателя (то же, что /api/v1/stats/me)")
