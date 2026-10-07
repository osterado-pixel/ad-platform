import hashlib
import hmac
import random
import re
import time
from dataclasses import dataclass
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import Float, Numeric, cast, func, literal, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db, write_lock
from app.ledger import add_transaction
from app.models import (
    Campaign, CampaignStatus, Click, EarningSource, Placement, Site, SiteStatus, TransactionType, User,
)
from app.schemas import AdResponse
from app.services import partners
from app.stats import bump_daily, bump_placement_daily

router = APIRouter(prefix="/api/v1/ad", tags=["Выдача рекламы (Ad Serving)"])

# Ротация и учёт кликов: ни браузер, ни прокси не должны кешировать ответы
NO_STORE = {"Cache-Control": "no-store"}

# Поисковики и боты предпросмотра ссылок (Telegram, Slack, WhatsApp...) — клик не оплачивается
BOT_UA = re.compile(r"bot|crawl|spider|slurp|preview|facebookexternalhit|whatsapp", re.IGNORECASE)

# Сглаживание CTR для аукциона: (клики + 1) / (показы + 100) — у новой кампании без статистики
# оценка 1%, по мере показов она сходится к настоящему CTR. Без сглаживания кампания с 1 кликом
# на 1 показ (CTR 100%) обгоняла бы всех
CTR_PRIOR_CLICKS = 1
CTR_PRIOR_IMPRESSIONS = 100


@dataclass(frozen=True)
class Charge:
    """Что и с кого списать за клик: кампания, цена, площадка показа и её партнёр (если есть)."""
    id: int                       # кампания
    user_id: int                  # рекламодатель
    title: str
    price: Decimal                # ставка кампании, без ставки — цена клика площадки
    floor: Decimal                # цена клика площадки (минимальная ставка)
    placement_id: int
    site_id: int | None = None
    publisher_id: int | None = None
    revenue_share: Decimal | None = None


def _live_campaigns():
    """Кампании, которые сейчас можно показывать: статус ACTIVE и в периоде показа."""
    now = func.now()
    return select(Campaign).where(
        Campaign.status == CampaignStatus.ACTIVE,
        or_(Campaign.start_date.is_(None), Campaign.start_date <= now),
        or_(Campaign.end_date.is_(None), Campaign.end_date >= now),
    )


def _live_placement():
    """Площадка, на которой можно показывать рекламу: активна и (если это сайт партнёра) сайт одобрен."""
    return (
        select(Placement.id, Placement.site_id, Placement.price_per_click.label("floor"),
               Site.user_id.label("publisher_id"), Site.revenue_share)
        .outerjoin(Placement.site)
        .where(Placement.is_active.is_(True),
               or_(Placement.site_id.is_(None), Site.status == SiteStatus.APPROVED))
    )


def click_signature(campaign_id: int, placement_id: int) -> str:
    """Подпись пары (кампания, площадка) в ссылке клика: без SECRET_KEY нельзя подставить
    в ссылку другую площадку и получить долю за чужой клик."""
    msg = f"click:{campaign_id}:{placement_id}".encode()
    return hmac.new(settings.secret_key.encode(), msg, hashlib.sha256).hexdigest()[:20]


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
    Принимает `placement_code` (например, header_banner_1) и проводит аукцион среди кампаний
    этой площадки и кампаний на всю сеть: побеждает наибольший ожидаемый доход с показа
    (ставка × сглаженный CTR), доля AUCTION_EXPLORE_RATE показов — случайной кампании.
    Участвуют кампании со ставкой не ниже цены клика площадки, владельцу которых хватает денег на клик.
    """
    if empty_status not in (404, 204):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="empty_status: допустимо 404 или 204")

    # Шаг A: Ищем активную рекламную площадку по ее коду
    placement = db.execute(
        _live_placement().where(Placement.code_identifier == placement_code)
    ).one_or_none()
    if placement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Рекламная площадка не найдена или деактивирована",
            headers=NO_STORE,
        )

    # Шаги B и C: аукцион. Цена клика кампании — её ставка, без ставки — цена площадки.
    # Баланс общий на все кампании пользователя: при пополнении показ возобновится сам.
    price = func.coalesce(Campaign.cpc_bid, literal(placement.floor, Numeric(12, 2)))
    ctr = (cast(Campaign.clicks_count + CTR_PRIOR_CLICKS, Float)
           / cast(Campaign.impressions_count + CTR_PRIOR_IMPRESSIONS, Float))
    if random.random() < settings.auction_explore_rate:
        order = (func.random(),)
    else:
        order = ((cast(price, Float) * ctr).desc(), func.random())  # равные — по очереди, случайно
    campaign = db.scalar(
        _live_campaigns()
        .join(Campaign.owner)
        .where(
            or_(Campaign.placement_id == placement.id, Campaign.placement_id.is_(None)),
            price >= placement.floor,
            User.balance >= price,
        )
        .order_by(*order)
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

    click_url = request.url_for("track_click", campaign_id=campaign.id).include_query_params(
        p=placement.id, s=click_signature(campaign.id, placement.id))
    ad = AdResponse(
        campaign_id=campaign.id,
        title=campaign.title,
        description=campaign.description,
        image_url=campaign.image_url,
        click_url=str(click_url),
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
            bump_placement_daily(db, placement.id, impressions=1)
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


def _charge_click(db: Session, request: Request, campaign: Charge) -> bool:
    """Списывает цену клика с владельца. True — клик оплачен и засчитан.

    Клик на сайте партнёра: его доля начисляется в той же транзакции БД, что и списание.
    """
    if _is_bot(request) or campaign.price < campaign.floor:
        return False  # ставку снизили ниже цены площадки уже после показа — клик не оплачивается

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
        bump_placement_daily(db, campaign.placement_id, clicks=1, spend=price)
        if price > 0:
            add_transaction(
                db, user_id=campaign.user_id, amount=price, type=TransactionType.CLICK_SPEND,
                campaign_id=campaign.id,
                # description — String(255), а title может быть до 255 символов: обрезаем
                description=f"Списание за клик по кампании #{campaign.id} ({campaign.title})"[:255],
            )
        share = Decimal("0")
        if campaign.site_id is not None:
            # Свои объявления на своём сайте: партнёр заплатил бы сам себе — доли нет
            if campaign.publisher_id != campaign.user_id:
                share = partners.share_of(price, campaign.revenue_share)
            partners.bump_site_daily(db, campaign.site_id, clicks=1, revenue=price, earnings=share)
            partners.accrue(db, campaign.publisher_id, share, EarningSource.SITE)
        # Пригласившим рекламодателя и партнёра — доля от того, что осталось платформе
        partners.accrue_referrals(db, (campaign.user_id, campaign.publisher_id), price - share)
        db.commit()
        return True


# --- 2. Эндпоинт обработки клика (Редирект) ---
@router.get("/click/{campaign_id}", response_class=RedirectResponse,
            status_code=status.HTTP_302_FOUND)
def track_click(
    campaign_id: int,
    request: Request,
    p: int | None = Query(default=None, description="Площадка показа (из ссылки, выданной /serve)"),
    s: str | None = Query(default=None, max_length=64, description="Подпись площадки"),
    db: Session = Depends(get_db),
):
    """
    Оплачивает клик и перенаправляет пользователя на target_url. Цена — ставка кампании
    (без ставки — цена клика площадки); на сайте партнёра его доля начисляется сразу.
    Боты, повторные клики и клики без денег на балансе не оплачиваются, но редирект выполняется.
    """
    campaign = db.execute(
        _live_campaigns()
        .with_only_columns(Campaign.id, Campaign.user_id, Campaign.title, Campaign.target_url,
                           Campaign.placement_id, Campaign.cpc_bid)
        .where(Campaign.id == campaign_id)
    ).one_or_none()
    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Рекламная кампания не найдена",
            headers=NO_STORE,
        )

    # Площадка, за клик на которой платим. У кампании с площадкой — всегда её площадка
    # (ссылки, выданные до аукциона, — без p и s). У кампании на всю сеть — из подписанной ссылки
    placement_id = campaign.placement_id
    if placement_id is None and p is not None and s is not None \
            and hmac.compare_digest(s, click_signature(campaign.id, p)):
        placement_id = p
    placement = None
    if placement_id is not None:
        placement = db.execute(_live_placement().where(Placement.id == placement_id)).one_or_none()
    # Закрываем читающую транзакцию до записи: в SQLite транзакция «чтение → запись»
    # при конкуренции сразу получает "database is locked", не дожидаясь timeout
    db.rollback()

    if placement is None and campaign.placement_id is not None:
        # Площадку кампании отключили (или сайт партнёра заблокирован): как кампания не на показе
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Рекламная кампания не найдена",
            headers=NO_STORE,
        )
    if placement is not None:
        _charge_click(db, request, Charge(
            id=campaign.id, user_id=campaign.user_id, title=campaign.title,
            price=placement.floor if campaign.cpc_bid is None else campaign.cpc_bid,
            floor=placement.floor, placement_id=placement.id, site_id=placement.site_id,
            publisher_id=placement.publisher_id, revenue_share=placement.revenue_share,
        ))
    # Кампания на всю сеть без верной подписи площадки: не знаем, кому и сколько, — без оплаты

    # 302: обычный переход по ссылке; no-store — каждый клик доходит до сервера.
    # Посетитель уже кликнул, поэтому переводим его на сайт в любом случае
    return RedirectResponse(url=campaign.target_url, status_code=status.HTTP_302_FOUND,
                            headers=NO_STORE)
