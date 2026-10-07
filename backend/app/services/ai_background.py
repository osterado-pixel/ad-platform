"""Фоновая AI-генерация: выполняется после ответа на HTTP-запрос, результат — в ai_tasks.

Шаги задачи: pending → processing → completed / failed.
1. Захват: pending → processing одним UPDATE — одну задачу не выполнят дважды (два обработчика,
   повторный вызов).
2. Резерв денег — обычно уже сделан эндпоинтом при создании задачи (ссылка — в задаче); задача без
   резерва резервирует сама. Затем модерация и генерация Gemini.
3. Успех: completed + результат + расчёт по факту — одной транзакцией БД.
   Ошибка: failed + понятная причина + полный возврат резерва — одной транзакцией.
4. Задачи, прерванные перезапуском сервера, при следующем запуске помечаются failed, а их резерв
   возвращается (fail_stale_tasks).

Функция синхронная: FastAPI BackgroundTasks выполняет её в пуле потоков, не блокируя сервер.
"""
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.ai import AIUnavailable
from app.database import SessionLocal, write_lock
from app.i18n import DEFAULT_LANGUAGE
from app.models import AITask, AITaskStatus, Transaction
from app.services import ai_billing, gemini_service
from app.services.moderation_service import ContentRejected, moderate_text_sync

log = logging.getLogger(__name__)

PROMPT_TYPE = "gemini_background_ad"
# Дольше генерация не идёт (таймаут Gemini 30 с с повторами): старше — значит, прервана
STALE_AFTER = timedelta(minutes=10)
INTERRUPTED = ("Задача не завершилась вовремя (сбой или перезапуск сервера). "
               "Деньги не списаны — запустите генерацию ещё раз")


def _move(db: Session, task_id: str, frm: AITaskStatus, to: AITaskStatus, *where, **values) -> bool:
    """Переводит задачу из статуса frm в to. False — статус уже другой (задачу обработал кто-то ещё)."""
    return db.execute(
        update(AITask).where(AITask.id == task_id, AITask.status == frm, *where).values(status=to, **values)
    ).rowcount == 1


def _fail(db: Session, task_id: str, hold: ai_billing.Hold | None, message: str) -> None:
    with write_lock():
        if not _move(db, task_id, AITaskStatus.PROCESSING, AITaskStatus.FAILED, error_message=message[:1000]):
            db.rollback()  # задачу уже закрыло восстановление после перезапуска (и вернуло резерв)
            return
        if hold is not None:
            ai_billing.refund(db, hold)
        db.commit()


def _existing_hold(db: Session, task_id: str, user_id: int) -> ai_billing.Hold | None:
    """Резерв, сделанный при создании задачи (ссылка transaction_id), или None."""
    row = db.execute(
        select(Transaction.id, Transaction.amount)
        .join(AITask, AITask.transaction_id == Transaction.id).where(AITask.id == task_id)
    ).first()
    db.rollback()
    return ai_billing.Hold(user_id, row.amount, row.id) if row else None


def run_gemini_generation_task(
    task_id: str,
    user_id: int,
    product_description: str,
    target_audience: str,
    language: str = DEFAULT_LANGUAGE,
    session_factory: Callable[[], Session] = SessionLocal,
) -> None:
    """Фоновая задача, выполняющаяся независимо от HTTP-запроса."""
    with session_factory() as db:
        # 1. Захват
        with write_lock():
            claimed = db.execute(
                update(AITask)
                .where(AITask.id == task_id, AITask.user_id == user_id, AITask.status == AITaskStatus.PENDING)
                .values(status=AITaskStatus.PROCESSING)
            ).rowcount
            db.commit()
        if not claimed:
            return

        # 2. Резерв (обычно уже есть) → модерация → генерация
        hold = _existing_hold(db, task_id, user_id)
        try:
            moderate_text_sync(f"{product_description}\n{target_audience}")

            if hold is None:
                def link(db: Session, transaction_id: int) -> None:
                    db.execute(update(AITask).where(AITask.id == task_id).values(transaction_id=transaction_id))
                hold = ai_billing.reserve(db, user_id, link=link)

            res = gemini_service.generate_ad(product_description, target_audience, language)
        except ContentRejected as e:
            _fail(db, task_id, hold, e.detail)
        except ai_billing.InsufficientFunds as e:
            _fail(db, task_id, hold, e.detail)
        except AIUnavailable as e:
            _fail(db, task_id, hold, f"AI-генерация не выполнена: {e}. Деньги не списаны")
        except Exception:
            # Подробности — в лог сервера, пользователю — без внутренних деталей
            log.exception("Фоновая AI-генерация %s: непредвиденная ошибка", task_id)
            _fail(db, task_id, hold, "Внутренняя ошибка AI-генерации. Деньги не списаны")
        else:
            # 3. Готово: результат и оплата — одной транзакцией
            with write_lock():
                if not _move(db, task_id, AITaskStatus.PROCESSING, AITaskStatus.COMPLETED, result=res["content"]):
                    db.rollback()  # задачу закрыло восстановление и вернуло резерв — не списываем
                    return
                ai_billing.settle(db, hold, res["usage"], PROMPT_TYPE)
                db.commit()


def fail_stale_tasks(session_factory: Callable[[], Session] = SessionLocal, stale_after: timedelta = STALE_AFTER) -> int:
    """Закрывает задачи, которые давно не меняли статус (прерваны перезапуском), и возвращает их резерв."""
    cutoff = datetime.now(timezone.utc) - stale_after
    closed = 0
    with session_factory() as db:
        stale = db.execute(
            select(AITask.id, AITask.status, AITask.user_id, AITask.transaction_id)
            .where(AITask.status.in_([AITaskStatus.PENDING, AITaskStatus.PROCESSING]), AITask.updated_at < cutoff)
        ).all()
        db.rollback()
        for task_id, status, user_id, transaction_id in stale:
            with write_lock():
                if not _move(db, task_id, status, AITaskStatus.FAILED, AITask.updated_at < cutoff,
                             error_message=INTERRUPTED):
                    db.rollback()  # задача успела сдвинуться — она жива
                    continue
                # Резерв незавершённой задачи ещё не рассчитан — возвращаем. Он бывает и у pending:
                # эндпоинт замораживает деньги при создании задачи
                if transaction_id is not None:
                    amount = db.scalar(select(Transaction.amount).where(Transaction.id == transaction_id))
                    if amount is not None:
                        ai_billing.refund(db, ai_billing.Hold(user_id, amount, transaction_id), INTERRUPTED[:255])
                db.commit()
                closed += 1
    if closed:
        log.warning("Закрыто прерванных AI-задач: %s (резерв возвращён)", closed)
    return closed
