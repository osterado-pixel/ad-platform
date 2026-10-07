"""Очистка «зависших» AI-задач: давно не менявшие статус → failed, резерв денег — обратно на баланс.

Сама логика — fail_stale_tasks в app/services/ai_background.py (её же вызывает запуск сервера).
Отличия от учебной инструкции:
- зарезервированные за задачу деньги возвращаются (массовый UPDATE статуса их бы «потерял»);
- давность считается по updated_at, а не по created_at: задача, долго ждавшая в очереди и только
  что взятая в работу, не считается зависшей;
- закрываются и pending-задачи, которые так и не начали выполняться;
- каждая задача закрывается атомарно и только если всё ещё зависла — работающую не тронет.
"""
import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.services.ai_background import fail_stale_tasks

logger = logging.getLogger(__name__)

# Генерация занимает до ~1.5 мин (таймаут Gemini 30 с, до 2 повторов): меньший порог закрывал бы
# ещё работающие задачи
MIN_TIMEOUT_MINUTES = 2


def cleanup_stuck_ai_tasks_sync(timeout_minutes: int = 10,
                                session_factory: Callable[[], Session] = SessionLocal) -> int:
    """Закрывает зависшие задачи и возвращает их резерв. Возвращает число закрытых задач."""
    if timeout_minutes < MIN_TIMEOUT_MINUTES:
        raise ValueError(f"timeout_minutes должен быть не меньше {MIN_TIMEOUT_MINUTES}")
    try:
        return fail_stale_tasks(session_factory, timedelta(minutes=timeout_minutes))
    except SQLAlchemyError:
        # Очистка не должна ронять то, что её вызвало (запуск сервера, периодическую задачу)
        logger.exception("Ошибка при очистке зависших AI-задач")
        return 0


async def cleanup_stuck_ai_tasks(timeout_minutes: int = 10) -> int:
    """Имя и async-вызов из инструкции. Работа с БД — в отдельном потоке, сервер не блокируется."""
    return await asyncio.to_thread(cleanup_stuck_ai_tasks_sync, timeout_minutes)


def retry_ai_reviews_sync(session_factory=None) -> None:
    from app.database import SessionLocal
    from app.moderation import retry_failed_reviews
    from app.services.site_check import retry_errors
    factory = session_factory or SessionLocal
    try:
        campaigns, sites = retry_failed_reviews(factory), retry_errors(factory)
    except Exception:
        logger.exception("Ошибка повторной AI-проверки")
        return
    if campaigns or sites:
        logger.info("Повторная AI-проверка: кампаний %s, сайтов %s", campaigns, sites)


async def schedule_task_cleanup(interval_seconds: int | None = None, timeout_minutes: int | None = None) -> None:
    """Периодическая очистка внутри процесса сервера (без отдельного Celery Beat).

    Запускается из lifespan в app/main.py и останавливается при остановке сервера (CancelledError).
    При нескольких процессах сервера (WEB_WORKERS) цикл идёт в каждом — это безопасно: задача
    закрывается атомарно и только один раз, резерв возвращается один раз.
    """
    interval = interval_seconds if interval_seconds is not None else settings.ai_cleanup_interval_seconds
    timeout = timeout_minutes if timeout_minutes is not None else settings.ai_task_timeout_minutes
    while True:
        try:
            await asyncio.sleep(interval)
            await cleanup_stuck_ai_tasks(timeout_minutes=timeout)
            # Модель не ответила (перегрузка, лимиты) — повторить проверку кампаний и сайтов
            await asyncio.to_thread(retry_ai_reviews_sync)
        except asyncio.CancelledError:
            logger.info("Фоновая очистка AI-задач остановлена")
            raise  # отмена должна дойти до вызвавшего, иначе остановка сервера «зависнет» на ожидании
        except Exception:
            # Любая ошибка — в лог с подробностями, цикл продолжает работу
            logger.exception("Ошибка в цикле очистки AI-задач")
