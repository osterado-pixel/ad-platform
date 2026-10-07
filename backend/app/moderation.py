"""AI-проверка кампании на модерации: вызов модели и сохранение результата.

Вызывается в фоне после отправки на модерацию и по кнопке администратора. Обращение к модели
идёт вне транзакции БД (сетевой запрос может длиться секунды), результат записывается только
если кампания всё ещё на модерации и её содержимое не изменилось.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app import ai
from app.config import settings
from app.database import write_lock
from app.models import Campaign, CampaignStatus
from app.services import notify
from app.services.moderation_service import find_local_violations

log = logging.getLogger(__name__)

AUTO_REJECT_PREFIX = "Автоматическая проверка: "


def _snapshot(c: Campaign) -> tuple:
    return c.title, c.description, c.target_url, c.image_url


def run_ai_review(session_factory: Callable[[], Session], campaign_id: int) -> Campaign | None:
    """Проверяет кампанию и сохраняет ai_* поля. Возвращает кампанию или None, если проверять нечего."""
    with session_factory() as db:
        campaign = db.get(Campaign, campaign_id)
        if campaign is None or campaign.status != CampaignStatus.MODERATION:
            return None
        snapshot = _snapshot(campaign)

    # Сетевой запрос — без открытой транзакции и блокировок
    result, error = None, None
    try:
        result = ai.moderate_ad(*snapshot)
    except ai.AIUnavailable as e:
        error = str(e)
        log.warning("AI-модерация кампании #%s не выполнена: %s", campaign_id, e)

    with write_lock(), session_factory() as db:
        campaign = db.get(Campaign, campaign_id)
        # Пока модель думала, админ мог вынести решение, а кампанию — отправить заново:
        # тогда этот результат устарел и ничего не меняет
        if campaign is None or campaign.status != CampaignStatus.MODERATION or _snapshot(campaign) != snapshot:
            return None
        campaign.ai_checked_at = datetime.now(timezone.utc)
        if result is None:
            campaign.ai_verdict, campaign.ai_risk, campaign.ai_reasons = "error", None, None
            campaign.ai_summary = error
        else:
            campaign.ai_verdict, campaign.ai_risk = result.verdict, result.risk
            campaign.ai_reasons, campaign.ai_summary = result.reasons, result.summary
            if settings.ai_auto_reject and result.verdict == "reject" and result.risk == "high":
                campaign.status = CampaignStatus.REJECTED
                reasons = "; ".join(result.reasons) or result.summary or "нарушение правил площадки"
                campaign.rejection_reason = (AUTO_REJECT_PREFIX + reasons)[:1000]
            elif (settings.ai_auto_approve and result.verdict == "approve" and result.risk == "low"
                  and not find_local_violations(" ".join(filter(None, snapshot[:2])))):
                # Вторая, независимая проверка — локальные стоп-фразы: модель могла пропустить «казино»
                campaign.status = CampaignStatus.ACTIVE
        if campaign.status == CampaignStatus.MODERATION:
            # Результат проверки — не правка кампании: updated_at не сдвигается (иначе окно повтора
            # RETRY_WINDOW, отсчитываемое от отправки, сдвигалось бы каждой неудачной попыткой — бесконечно)
            flag_modified(campaign, "updated_at")
        db.commit()
        db.refresh(campaign)
        if campaign.status != CampaignStatus.MODERATION:  # автопилот принял решение — сообщить владельцу
            notify.campaign_decided(campaign, campaign.owner)
        db.expunge(campaign)
        return campaign


# Повтор, если модель не ответила (перегрузка, лимиты): каждые AI_CLEANUP_INTERVAL_SECONDS (5 мин),
# не чаще раза в RETRY_AFTER и только первые RETRY_WINDOW после отправки — дальше решает человек
RETRY_AFTER = timedelta(minutes=5)
RETRY_WINDOW = timedelta(hours=24)
RETRY_BATCH = 20


def retry_failed_reviews(session_factory: Callable[[], Session]) -> int:
    """Перепроверяет кампании на модерации, по которым модель не ответила или проверки ещё не было
    (например, сервер перезапустился раньше фоновой проверки). Возвращает число проверок."""
    if not ai.is_enabled():
        return 0
    now = datetime.now(timezone.utc)
    with session_factory() as db:
        ids = db.scalars(
            select(Campaign.id).where(
                Campaign.status == CampaignStatus.MODERATION,
                Campaign.updated_at >= now - RETRY_WINDOW,
                or_(Campaign.ai_verdict.is_(None),
                    (Campaign.ai_verdict == "error") & (Campaign.ai_checked_at < now - RETRY_AFTER)),
            ).order_by(Campaign.id).limit(RETRY_BATCH)
        ).all()
    for campaign_id in ids:
        try:
            run_ai_review(session_factory, campaign_id)
        except Exception:  # одна сбойная кампания не останавливает остальные
            log.exception("Ошибка повторной AI-проверки кампании #%s", campaign_id)
    return len(ids)
