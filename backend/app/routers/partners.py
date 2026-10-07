"""Партнёрская программа: сайты партнёров, их площадки, заработок и выплаты (+ разделы администратора).

Деньги — в app/services/partners.py; здесь только проверки доступа и ответы API.
"""
import secrets
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.auth import get_current_user, require_admin
from app.config import settings
from app.database import get_db, write_lock
from app.models import (
    EarningSource, PartnerEarning, PartnerTransaction, Payout, PayoutStatus, Placement, Site, SiteDailyStat, SiteStatus, User,
)
from app.pagination import before_id_param, fetch_page, limit_param, offset_param
from app.routers.stats import Days, _ctr, _period
from app.schemas import (
    PartnerDayStat, PartnerPlacementCreate, PartnerSiteTotals, PartnerSummary, PartnerTotals,
    PartnerTransactionResponse, PayoutAdminResponse, PayoutCreate, PayoutProcess, PayoutResponse,
    PlacementResponse, ReferralInfo, SiteAdminResponse, SiteCreate, SiteModerate, SiteResponse, TransferRequest,
    WalletBalanceResponse,
)
from app.services import partners

router = APIRouter(prefix="/api/v1/partner", tags=["Партнёрская программа"])
admin_router = APIRouter(prefix="/api/v1/admin/partner", tags=["Партнёрская программа (для админа)"])

SITE_TAKEN = "Этот сайт уже добавлен в партнёрскую программу"
MAX_SITES_PER_USER = 50


def _domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _my_site(db: Session, site_id: int, user: User) -> Site:
    site = db.get(Site, site_id)
    if site is None or site.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сайт не найден")
    return site


# ---------- Сайты партнёра ----------
@router.post("/sites", response_model=SiteResponse, status_code=status.HTTP_201_CREATED)
def add_site(data: SiteCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Добавить сайт. Реклама на нём начнёт показываться после проверки администратором."""
    domain = _domain(data.url)
    if not domain:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Укажите адрес сайта")
    count = db.scalar(select(func.count()).select_from(Site).where(Site.user_id == user.id))
    if count >= MAX_SITES_PER_USER:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"Можно добавить не больше {MAX_SITES_PER_USER} сайтов")
    if db.scalar(select(Site.id).where(Site.domain == domain)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=SITE_TAKEN)
    site = Site(user_id=user.id, name=data.name, url=data.url, domain=domain)
    db.add(site)
    try:
        db.commit()
    except IntegrityError:  # параллельное добавление того же домена
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=SITE_TAKEN) from None
    db.refresh(site)
    return site


@router.get("/sites", response_model=list[SiteResponse])
def my_sites(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.scalars(select(Site).where(Site.user_id == user.id).order_by(Site.id)).all()


@router.post("/sites/{site_id}/placements", response_model=PlacementResponse,
             status_code=status.HTTP_201_CREATED)
def add_placement(site_id: int, data: PartnerPlacementCreate, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """Место под баннер на сайте. Код для вставки — code_identifier (виджет: data-placement)."""
    site = _my_site(db, site_id, user)
    if site.status in (SiteStatus.REJECTED, SiteStatus.BLOCKED):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Сайт отклонён или заблокирован — площадки на нём создать нельзя")
    count = db.scalar(select(func.count()).select_from(Placement).where(Placement.site_id == site.id))
    if count >= settings.partner_max_placements_per_site:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"На одном сайте — не больше {settings.partner_max_placements_per_site} площадок")
    # Код генерируем сами: партнёр не займёт чужой или «красивый» код и не угадает чужой
    placement = Placement(name=data.name, code_identifier=f"s{site.id}_{secrets.token_hex(5)}",
                          price_per_click=settings.partner_default_cpc, site_id=site.id)
    db.add(placement)
    db.commit()
    db.refresh(placement)
    return placement


@router.get("/sites/{site_id}/placements", response_model=list[PlacementResponse])
def site_placements(site_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    site = _my_site(db, site_id, user)
    return db.scalars(select(Placement).where(Placement.site_id == site.id).order_by(Placement.id)).all()


# ---------- Заработок ----------
def _partner_totals(impressions, clicks, revenue, earnings) -> dict:
    impressions, clicks = int(impressions or 0), int(clicks or 0)
    cent = Decimal("0.01")
    return {"impressions": impressions, "clicks": clicks,
            "revenue": Decimal(revenue or 0).quantize(cent), "earnings": Decimal(earnings or 0).quantize(cent),
            "ctr": _ctr(impressions, clicks)}


def _site_stats(db: Session, user_id: int, start: date, end: date):
    d = SiteDailyStat
    cols = (func.sum(d.impressions), func.sum(d.clicks), func.sum(d.revenue), func.sum(d.earnings))
    period = (Site.user_id == user_id, d.day >= start, d.day <= end)
    by_day = {r[0]: r[1:] for r in db.execute(
        select(d.day, *cols).join(Site, Site.id == d.site_id).where(*period).group_by(d.day))}
    days, day = [], start
    while day <= end:
        days.append(PartnerDayStat(day=day, **_partner_totals(*by_day.get(day, (0, 0, 0, 0)))))
        day += timedelta(days=1)
    by_site = {r[0]: r[1:] for r in db.execute(
        select(d.site_id, *cols).join(Site, Site.id == d.site_id).where(*period).group_by(d.site_id))}
    sites = [PartnerSiteTotals(site_id=s.id, name=s.name, status=s.status,
                               **_partner_totals(*by_site.get(s.id, (0, 0, 0, 0))))
             for s in db.scalars(select(Site).where(Site.user_id == user_id).order_by(Site.id))]
    return days, sites


@router.get("/summary", response_model=PartnerSummary)
def summary(days: int = Days, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Баланс партнёра (доступно и созревает) и статистика сайтов по дням."""
    partners.mature(db, user.id)  # созревшее к сегодняшнему дню — сразу в «доступно»
    db.refresh(user)
    start, end = _period(days)
    series, sites = _site_stats(db, user.id, start, end)
    totals = _partner_totals(sum(x.impressions for x in series), sum(x.clicks for x in series),
                             sum((x.revenue for x in series), Decimal("0")),
                             sum((x.earnings for x in series), Decimal("0")))
    return PartnerSummary(
        earnings_balance=user.earnings_balance, pending=partners.pending_amount(db, user.id),
        hold_days=settings.earnings_hold_days, payout_min_amount=settings.payout_min_amount,
        revenue_share=float(settings.publisher_revenue_share), period_start=start, period_end=end,
        totals=PartnerTotals(**totals), days=series, sites=sites,
    )


@router.get("/transactions", response_model=list[PartnerTransactionResponse])
def partner_transactions(
    response: Response,
    limit: int = limit_param(default=20),
    before_id: int | None = before_id_param(),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Журнал заработка (зачисления, выплаты, переводы), новые сверху. Курсор — X-Next-Before-Id."""
    query = select(PartnerTransaction).where(PartnerTransaction.user_id == user.id)
    if before_id is not None:
        query = query.where(PartnerTransaction.id < before_id)
    return fetch_page(db, query.order_by(PartnerTransaction.id.desc()), response, limit, cursor_attr="id")


@router.post("/transfer", response_model=WalletBalanceResponse)
def transfer(data: TransferRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Перевести доступный заработок на свой рекламный баланс (сразу, без минимальной суммы)."""
    partners.transfer_to_balance(db, user.id, data.amount)
    db.refresh(user)
    return WalletBalanceResponse(balance=user.balance, held_balance=user.held_balance)


@router.post("/payouts", response_model=PayoutResponse, status_code=status.HTTP_201_CREATED)
def create_payout(data: PayoutCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Заявка на вывод. Сумма сразу списывается с доступного; при отклонении — возвращается."""
    return partners.request_payout(db, user.id, data.amount, data.method, data.details)


@router.get("/payouts", response_model=list[PayoutResponse])
def my_payouts(response: Response, limit: int = limit_param(default=20), offset: int = offset_param(),
               db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = select(Payout).where(Payout.user_id == user.id).order_by(Payout.id.desc())
    return fetch_page(db, query, response, limit, offset)


# ---------- Реферальная программа ----------
@router.get("/referral", response_model=ReferralInfo)
def referral(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Реферальная ссылка и итоги: кого пригласили и сколько начислено."""
    code = partners.referral_code(db, user)
    base = settings.public_url.rstrip("/") or str(request.base_url).rstrip("/")
    invited = db.scalar(select(func.count()).select_from(User).where(User.referred_by_id == user.id))
    active = db.scalar(select(func.count()).select_from(User).where(
        User.referred_by_id == user.id, User.created_at >= partners.referral_since()))
    earned = db.scalar(select(func.coalesce(func.sum(PartnerEarning.amount), 0)).where(
        PartnerEarning.user_id == user.id, PartnerEarning.source == EarningSource.REFERRAL))
    return ReferralInfo(code=code, link=f"{base}/app?ref={code}", share=float(settings.referral_share),
                        days=settings.referral_days, invited=invited, active=active,
                        earned_total=Decimal(earned).quantize(Decimal("0.01")))


# ---------- Администратор ----------
@admin_router.get("/sites", response_model=list[SiteAdminResponse])
def admin_sites(
    response: Response,
    site_status: SiteStatus | None = Query(default=None, alias="status"),
    limit: int = limit_param(),
    offset: int = offset_param(),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Сайты партнёров; ?status=pending — очередь на проверку (старые сверху)."""
    query = select(Site).options(selectinload(Site.owner))
    if site_status is not None:
        query = query.where(Site.status == site_status)
    order = Site.id if site_status == SiteStatus.PENDING else Site.id.desc()
    return fetch_page(db, query.order_by(order), response, limit, offset)


@admin_router.post("/sites/{site_id}/moderate", response_model=SiteAdminResponse)
def moderate_site(site_id: int, data: SiteModerate, db: Session = Depends(get_db),
                  _admin: User = Depends(require_admin)):
    """Одобрить, отклонить или заблокировать сайт; заодно можно задать ему свою долю партнёра."""
    site = db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сайт не найден")
    with write_lock():
        site.status = data.status
        site.rejection_reason = data.reason
        if data.reset_share:
            site.revenue_share = None
        elif data.revenue_share is not None:
            site.revenue_share = data.revenue_share
        db.commit()
    db.refresh(site)
    return site


@admin_router.get("/payouts", response_model=list[PayoutAdminResponse])
def admin_payouts(
    response: Response,
    payout_status: PayoutStatus | None = Query(default=None, alias="status"),
    limit: int = limit_param(),
    offset: int = offset_param(),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Заявки на выплату; ?status=pending — очередь (старые сверху)."""
    query = select(Payout).options(selectinload(Payout.owner))
    if payout_status is not None:
        query = query.where(Payout.status == payout_status)
    order = Payout.id if payout_status == PayoutStatus.PENDING else Payout.id.desc()
    return fetch_page(db, query.order_by(order), response, limit, offset)


@admin_router.post("/payouts/{payout_id}/paid", response_model=PayoutAdminResponse)
def payout_paid(payout_id: int, data: PayoutProcess, db: Session = Depends(get_db),
                _admin: User = Depends(require_admin)):
    """Деньги партнёру отправлены (вручную, по реквизитам заявки)."""
    return partners.mark_paid(db, payout_id, data.note)


@admin_router.post("/payouts/{payout_id}/reject", response_model=PayoutAdminResponse)
def payout_reject(payout_id: int, data: PayoutProcess, db: Session = Depends(get_db),
                  _admin: User = Depends(require_admin)):
    """Отклонить заявку: сумма вернётся партнёру в «доступно к выводу»."""
    return partners.reject_payout(db, payout_id, data.note)


@admin_router.post("/mature")
def mature_now(db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    """Зачислить созревший заработок всем партнёрам сейчас (обычно — раз в сутки автоматически)."""
    return {"matured": float(partners.mature(db))}
