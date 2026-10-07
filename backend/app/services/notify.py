"""Уведомления по email: пользователю — о решениях по его кампаниям, сайтам и выплатам, о заканчивающихся
деньгах; администратору — ежедневная сводка того, что ждёт человека.

Письмо — на языке пользователя (users.language: язык кабинета при регистрации и последнем входе).
Тексты — русские шаблоны из каталога app/messages.py, переводятся при отправке.
Отправка — в отдельном потоке: ни запрос API, ни фоновая задача не ждут почтовый сервер.
NOTIFICATIONS=false — письма не отправляются (восстановление пароля работает всегда).
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.database import write_lock
from app.i18n import localize
from app.models import (
    Campaign, CampaignDailyStat, CampaignStatus, Payout, PayoutStatus, Site, SiteStatus, User, UserRole,
)
from app.services import mailer
from app.stats import utc_today

log = logging.getLogger(__name__)

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="notify")


def _dispatch(fn, *args) -> None:
    """Отправка в отдельном потоке (в тестах подменяется на немедленный вызов)."""
    _pool.submit(fn, *args)


def _link(path: str) -> str | None:
    base = settings.public_url.rstrip("/")
    return f"{base}/app#{path}" if base else None


def send(user: User, subject: str, *lines: str, path: str | None = None) -> None:
    """subject и lines — готовые русские тексты (уже с подстановками); переводятся на язык пользователя."""
    if not settings.notifications:
        return
    lang = user.language
    body = "\n\n".join(localize(line, lang) for line in lines)
    link = _link(path) if path else None
    if link:
        body += f"\n\n{link}"
    body += "\n\n— Ad Platform"
    _dispatch(mailer.send_email, user.email, localize(subject, lang), body)


# ---------- События ----------
def campaign_decided(campaign: Campaign, owner: User) -> None:
    if campaign.status == CampaignStatus.ACTIVE:
        send(owner, f"Кампания «{campaign.title}» одобрена",
             "Кампания одобрена и показывается на площадках сети.", path=f"/campaigns/{campaign.id}")
    elif campaign.status == CampaignStatus.REJECTED:
        send(owner, f"Кампания «{campaign.title}» отклонена",
             f"Причина: {campaign.rejection_reason}",
             "Исправьте объявление и отправьте его на модерацию снова.", path=f"/campaigns/{campaign.id}")


def site_decided(site: Site, owner: User) -> None:
    if site.status == SiteStatus.APPROVED:
        send(owner, f"Сайт {site.domain} одобрен",
             "Сайт принят в рекламную сеть: на ваших площадках показывается реклама.", path="/partner")
    elif site.status == SiteStatus.REJECTED:
        send(owner, f"Сайт {site.domain} отклонён", f"Причина: {site.rejection_reason}", path="/partner")
    elif site.status == SiteStatus.PENDING and site.fraud_hold:
        send(owner, f"Сайт {site.domain} приостановлен для проверки", f"Причина: {site.rejection_reason}",
             "Показ рекламы и вывод заработка возобновятся после проверки.", path="/partner")
    elif site.status == SiteStatus.BLOCKED:
        send(owner, f"Сайт {site.domain} заблокирован", f"Причина: {site.rejection_reason}", path="/partner")


def payout_processed(payout: Payout, owner: User) -> None:
    if payout.status == PayoutStatus.PAID:
        send(owner, f"Выплата #{payout.id} отправлена", f"Сумма: {payout.amount:.2f}", path="/partner")
    elif payout.status == PayoutStatus.REJECTED:
        send(owner, f"Заявка на выплату #{payout.id} отклонена",
             "Сумма вернулась в «Доступно к выводу».", path="/partner")


# ---------- Ежедневно ----------
def low_balance(db: Session) -> int:
    """Рекламодателям с активными кампаниями и балансом ниже LOW_BALANCE_THRESHOLD — одно письмо,
    пока баланс снова не поднимется выше порога. Возвращает число писем."""
    threshold = settings.low_balance_threshold
    has_active = select(Campaign.id).where(Campaign.user_id == User.id,
                                           Campaign.status == CampaignStatus.ACTIVE).exists()
    with write_lock():
        db.execute(update(User).where(User.low_balance_notified.is_(True), User.balance >= threshold)
                   .values(low_balance_notified=False))
        users = db.scalars(select(User).where(User.low_balance_notified.is_(False), User.balance < threshold,
                                              has_active)).all()
        for user in users:
            user.low_balance_notified = True
        db.commit()
    for user in users:
        send(user, "Заканчиваются деньги на рекламу",
             f"На балансе {user.balance:.2f} — когда денег не хватит на клик, показ ваших кампаний остановится.",
             "Пополните баланс, чтобы реклама не прерывалась.", path="/wallet")
    return len(users)


def admin_digest(db: Session) -> bool:
    """Сводка администраторам — только если что-то ждёт человека. True — письмо отправлено."""
    moderation = db.scalar(select(func.count()).select_from(Campaign)
                           .where(Campaign.status == CampaignStatus.MODERATION))
    sites = db.scalar(select(func.count()).select_from(Site).where(Site.status == SiteStatus.PENDING))
    payouts, payouts_sum = db.execute(select(func.count(), func.coalesce(func.sum(Payout.amount), 0))
                                      .where(Payout.status == PayoutStatus.PENDING)).one()
    if not (moderation or sites or payouts):
        return False
    yesterday = utc_today() - timedelta(days=1)
    revenue = db.scalar(select(func.coalesce(func.sum(CampaignDailyStat.spend), 0))
                        .where(CampaignDailyStat.day == yesterday)) or Decimal("0")
    for admin in db.scalars(select(User).where(User.role == UserRole.ADMIN)):
        send(admin, "Ad Platform: ждёт решения",
             f"Кампаний на модерации: {moderation}",
             f"Сайтов партнёров на проверке: {sites}",
             f"Заявок на выплату: {payouts} на сумму {Decimal(payouts_sum):.2f}",
             f"Оборот за вчера: {Decimal(revenue):.2f}",
             path="/admin/moderation")
    return True
