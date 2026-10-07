"""Удаление старых служебных записей — чтобы таблицы не росли бесконечно.

Что удаляется:
- клики старше CLICKS_RETENTION_DAYS — нужны только для защиты от повторных кликов
  (статистика хранится по дням в campaign_daily_stats, деньги — в журнале transactions);
- попытки входа старше суток — нужны только для ограничения частоты входа;
- завершённые AI-задачи (completed / failed) старше AI_TASKS_RETENTION_DAYS — с результатом генерации.

Что НЕ удаляется никогда: незавершённые AI-задачи (за ними может стоять замороженный резерв денег),
журнал денег (transactions) и журнал AI-запросов (ai_logs) — это финансовая история.

Запускается раз в сутки внутри сервера (schedule_daily_purge, см. app/main.py) и вручную:
python -m app.cli purge.
"""
import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, write_lock
from app.models import AITask, AITaskStatus, AuthAttempt, Click

log = logging.getLogger(__name__)

PURGE_INTERVAL_SECONDS = 24 * 3600
AUTH_ATTEMPTS_KEEP_SECONDS = 24 * 3600  # самое длинное окно ограничений — час (регистрации с IP)


@dataclass
class PurgeResult:
    clicks: int
    auth_attempts: int
    ai_tasks: int


def min_clicks_days() -> int:
    """Клики нужны для защиты от повторов: хранить не меньше окна CLICK_DEDUP_MINUTES (в днях, вверх)."""
    return max(1, -(-settings.click_dedup_minutes // (24 * 60)))


def purge_old_records(db: Session, clicks_days: int, ai_tasks_days: int) -> PurgeResult:
    if clicks_days < min_clicks_days():
        raise ValueError(f"Клики нельзя хранить меньше {min_clicks_days()} дн.: "
                         "они нужны для защиты от повторных кликов")
    if ai_tasks_days < 1:
        raise ValueError("AI-задачи нельзя хранить меньше 1 дня")
    now = int(time.time())
    ai_cutoff = datetime.now(timezone.utc) - timedelta(days=ai_tasks_days)
    with write_lock():
        clicks = db.execute(delete(Click).where(Click.time_window < now // 60 - clicks_days * 24 * 60)).rowcount
        attempts = db.execute(delete(AuthAttempt).where(AuthAttempt.ts < now - AUTH_ATTEMPTS_KEEP_SECONDS)).rowcount
        # Только завершённые: у pending/processing может быть замороженный резерв (его вернёт очистка зависших)
        tasks = db.execute(delete(AITask).where(
            AITask.status.in_([AITaskStatus.COMPLETED, AITaskStatus.FAILED]),
            AITask.updated_at < ai_cutoff,
        )).rowcount
        db.commit()
    return PurgeResult(clicks=clicks, auth_attempts=attempts, ai_tasks=tasks)


def purge_sync(session_factory: Callable[[], Session] = SessionLocal) -> PurgeResult | None:
    """Очистка со сроками из настроек. Ошибка БД — в лог (с подробностями), а не наружу."""
    try:
        with session_factory() as db:
            result = purge_old_records(db, settings.clicks_retention_days, settings.ai_tasks_retention_days)
    except SQLAlchemyError:
        log.exception("Ошибка при удалении старых записей")
        return None
    if result.clicks or result.auth_attempts or result.ai_tasks:
        log.info("Удалены старые записи: кликов %s, попыток входа %s, AI-задач %s",
                 result.clicks, result.auth_attempts, result.ai_tasks)
    return result


def mature_sync(session_factory: Callable[[], Session] = SessionLocal) -> None:
    """Зачисление созревшего заработка партнёров (партнёру оно происходит и при открытии кабинета)."""
    from app.services.partners import mature
    try:
        with session_factory() as db:
            amount = mature(db)
    except SQLAlchemyError:
        log.exception("Ошибка при зачислении заработка партнёров")
        return
    if amount:
        log.info("Партнёрам зачислен созревший заработок: %s", amount)


async def schedule_daily_purge(interval_seconds: float = PURGE_INTERVAL_SECONDS) -> None:
    """Раз в сутки, пока работает сервер (первый раз — сразу после запуска).

    При нескольких процессах сервера очистка идёт в каждом — безопасно: удаление повторяемо.
    """
    while True:
        try:
            if settings.auto_purge:
                await asyncio.to_thread(purge_sync)
            # Созревший заработок партнёров — в «доступно к выводу» (не удаление, поэтому без AUTO_PURGE)
            await asyncio.to_thread(mature_sync)
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            raise  # остановка сервера
        except Exception:
            log.exception("Ошибка в цикле удаления старых записей")
            await asyncio.sleep(interval_seconds)
