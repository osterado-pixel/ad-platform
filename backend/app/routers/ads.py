import hashlib
import hmac
import re
import time
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db, write_lock
from app.ledger import add_transaction
from app.models import (
    Campaign, CampaignStatus, Click, EarningSource, Placement, Site, SiteStatus, TransactionType, User,
)
from app.services import partners
from app.schemas import AdResponse
from app.stats import bump_daily

router = APIRouter(prefix="/api/v1/ad", tags=["Выдача рекламы (Ad Serving)"])

# Ротация и учёт кликов: ни браузер, ни прокси не должны кешировать ответы
NO_STORE = {"Cache-Control": "no-store"}

# Поисковики и боты предпросмотра ссылок (Telegram, Slack, WhatsApp...) — клик не оплачивается
BOT_UA = re.compile(r"bot|crawl|spider|slurp|preview|facebookexternalhit|whatsapp", re.IGNORECASE)


def _active_campaigns():
    """Кампании, которые сейчас активны: статус ACTIVE, в периоде показа, на активной площадке
    (площадка платформы или сайта партнёра, одобренного администратором)."""
    now = func.now()
    return (
        select(Campaign)
        .join(Campaign.placement)
        .outerjoin(Placement.site)
        .where(
            Campaign.status == CampaignStatus.ACTIVE,
            Placement.is_active.is_(True),
            or_(Placement.site_id.is_(None), Site.status == SiteStatus.APPROVED),
            or_(Campaign.start_date.is_(None), Campaign.start_date <= now),
            or_(Campaign.end_date.is_(None), Campaign.end_date >= now),
        )
    )


# --- 1. Эндпоинт получения рекламного объявления ---
@router.get("/serve", response_model=AdResponse)
def serve_ad(
    placement_code: str,
    request: Request,
    response: Response,
    # int, а не Literal[404, 204]: из адреса приходит строка "204", а Literal её не приводит к числу
    empty_status: int = Query(
        default=404,
        description="Код ответа, если рекламы для площадки сейчас нет: 404 или 204. Виджет передаёт 204: "
                    "ответ 404 браузер пишет в консоль сайта-партнёра как ошибку",
    ),
    db: Session = Depends(get_db),
):
    """
    Публичный эндпоинт для сайтов-партнеров.
    Принимает `placement_code` (например, header_banner_1)
    и возвращает случайную активную кампанию, владелец которой может оплатить клик.
    """
    if empty_status not in (404, 204):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="empty_status: допустимо 404 или 204")

    # Шаг A: Ищем активную рекламную площадку по ее коду
    placement = db.execute(
        select(Placement.id, Placement.site_id)
        .outerjoin(Placement.site)
        .where(
            Placement.code_identifier == placement_code,
            Placement.is_active.is_(True),
            or_(Placement.site_id.is_(None), Site.status == SiteStatus.APPROVED),
        )
    ).one_or_none()
    if placement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Рекламная площадка не найдена или деактивирована",
            headers=NO_STORE,
        )

    # Шаги B и C: случайная кампания площадки, владельцу которой хватает денег на клик.
    # Баланс общий на все кампании пользователя: при пополнении показ возобновится сам.
    campaign = db.scalar(
        _active_campaigns()
        .join(Campaign.owner)
        .where(
            Campaign.placement_id == placement.id,
            User.balance >= Placement.price_per_click,
        )
        .order_by(func.random())
        .limit(1)
    )
    if campaign is None:
        if empty_status == 204:
            return Response(status_code=status.HTTP_204_NO_CONTENT, headers=NO_STORE)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Для данной площадки нет активных рекламных кампаний",
            headers=NO_STORE,
        )

    ad = AdResponse(
        campaign_id=campaign.id,
        title=campaign.title,
        description=campaign.description,
        image_url=campaign.image_url,
        click_url=str(request.url_for("track_click", campaign_id=campaign.id)),
    )
    db.rollback()  # закрываем чтение: транзакция записи должна начаться с записи

    # Учёт показа (боты и предпросмотры ссылок не считаются)
    if not _is_bot(request):
        with write_lock():
            db.execute(
                update(Campaign)
                .where(Campaign.id == ad.campaign_id)
                .values(impressions_count=Campaign.impressions_count + 1)
            )
            bump_daily(db, ad.campaign_id, impressions=1)
            if placement.site_id is not None:
                partners.bump_site_daily(db, placement.site_id, impressions=1)
            db.commit()

    response.headers.update(NO_STORE)
    return ad


def _is_bot(request: Request) -> bool:
    return bool(BOT_UA.search(request.headers.get("user-agent", "")))


def _ip_hash(request: Request) -> str:
    ip = request.client.host if request.client else "unknown"
    return hmac.new(settings.secret_key.encode(), ip.encode(), hashlib.sha256).hexdigest()


def _charge_click(db: Session, request: Request, campaign) -> bool:
    """Списывает цену клика с владельца. True — клик оплачен и засчитан.

    campaign — строка (id, user_id, title, price, site_id, publisher_id, revenue_share) из track_click.
    Клик на сайте партнёра: его доля начисляется в той же транзакции БД, что и списание.
    """
    if _is_bot(request):
        return False

    price = campaign.price
    ip_hash = _ip_hash(request)
    minute = int(time.time() // 60)

    # 1. Повтор: этот IP уже кликал по кампании за последние CLICK_DEDUP_MINUTES минут.
    #    Окно считается от прошлого клика, а не по фиксированным отрезкам
    #    (иначе клики в 12:09 и 12:11 попали бы в разные отрезки и оба оплатились).
    #    Чтение — вне очереди записей: в WAL оно идёт параллельно с записью других кликов
    recent = db.scalar(
        select(Click.id).where(
            Click.campaign_id == campaign.id,
            Click.ip_hash == ip_hash,
            Click.time_window > minute - settings.click_dedup_minutes,
        ).limit(1)
    )
    db.rollback()  # закрываем чтение: транзакция записи должна начаться с записи
    if recent is not None:
        return False

    with write_lock():
        # 2. Фиксируем клик. Два одновременных повтора в одну минуту нарушат уникальный
        #    индекс — второй отсекается атомарно (важно для PostgreSQL, где нет write_lock)
        db.add(Click(campaign_id=campaign.id, ip_hash=ip_hash, time_window=minute, cost=price))
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            return False

        # 3. Списываем атомарно: условие balance >= price проверяется в том же UPDATE,
        #    поэтому параллельные клики не уведут баланс в минус
        charged = db.execute(
            update(User)
            .where(User.id == campaign.user_id, User.balance >= price)
            .values(balance=User.balance - price)
        ).rowcount
        if not charged:
            db.rollback()  # денег не хватило: клик не засчитываем
            return False

        # 4. Счётчик тоже через UPDATE, а не += в Python: иначе параллельные клики потеряются
        db.execute(
            update(Campaign)
            .where(Campaign.id == campaign.id)
            .values(clicks_count=Campaign.clicks_count + 1)
        )
        # 5. Дневная статистика и запись в журнал — в той же транзакции БД, что и списание
        bump_daily(db, campaign.id, clicks=1, spend=price)
        if price > 0:
            add_transaction(
                db, user_id=campaign.user_id, amount=price, type=TransactionType.CLICK_SPEND,
                campaign_id=campaign.id,
                # description — String(255), а title может быть до 255 символов: обрезаем
                description=f"Списание за клик по кампании #{campaign.id} ({campaign.title})"[:255],
            )
        if campaign.site_id is not None:
            # Свои объявления на своём сайте: партнёр заплатил бы сам себе — доли нет
            share = (partners.share_of(price, campaign.revenue_share)
                     if campaign.publisher_id != campaign.user_id else Decimal("0"))
            partners.bump_site_daily(db, campaign.site_id, clicks=1, revenue=price, earnings=share)
            partners.accrue(db, campaign.publisher_id, share, EarningSource.SITE)
        db.commit()
        return True


# --- 2. Эндпоинт обработки клика (Редирект) ---
@router.get("/click/{campaign_id}", response_class=RedirectResponse,
            status_code=status.HTTP_302_FOUND)
def track_click(
    campaign_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Оплачивает клик (price_per_click площадки списывается с баланса владельца)
    и перенаправляет пользователя на target_url.
    Боты, повторные клики и клики без денег на балансе не оплачиваются, но редирект выполняется.
    """
    # Всё нужное одним запросом (без отдельной подгрузки площадки)
    campaign = db.execute(
        _active_campaigns()
        .with_only_columns(
            Campaign.id, Campaign.user_id, Campaign.title, Campaign.target_url,
            Placement.price_per_click.label("price"), Placement.site_id,
            Site.user_id.label("publisher_id"), Site.revenue_share,
        )
        .where(Campaign.id == campaign_id)
    ).one_or_none()
    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Рекламная кампания не найдена",
            headers=NO_STORE,
        )
    # Закрываем читающую транзакцию до записи: в SQLite транзакция «чтение → запись»
    # при конкуренции сразу получает "database is locked", не дожидаясь timeout
    db.rollback()

    _charge_click(db, request, campaign)

    # 302: обычный переход по ссылке; no-store — каждый клик доходит до сервера.
    # Посетитель уже кликнул, поэтому переводим его на сайт в любом случае
    return RedirectResponse(url=campaign.target_url, status_code=status.HTTP_302_FOUND,
                            headers=NO_STORE)
