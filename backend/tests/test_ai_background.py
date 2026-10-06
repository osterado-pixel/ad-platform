"""Фоновая AI-генерация (app/services/ai_background.py). Gemini подменён (фикстура gemini)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import sessionmaker

from app.ai import AIUnavailable
from app.models import AILog, AITask, AITaskStatus, Transaction, TransactionType
from app.services import ai_background as bg

from tests.test_ai_copy import VARIANTS, assert_ledger_matches, balance_of, gemini, held_of, user_with_balance  # noqa: F401

DESC, AUD = "Онлайн-курс Python с нуля", "новички"


@pytest.fixture
def factory(db):
    return sessionmaker(bind=db.get_bind(), autoflush=False)


def new_task(db, user_id) -> str:
    task = AITask(user_id=user_id)
    db.add(task)
    db.commit()
    return task.id


def run(task_id, user_id, factory, desc=DESC, aud=AUD):
    bg.run_gemini_generation_task(task_id, user_id, desc, aud, session_factory=factory)


def task_of(db, task_id) -> AITask:
    db.expire_all()
    return db.get(AITask, task_id)


def make_old(db, task_id, minutes=30):
    db.execute(update(AITask).where(AITask.id == task_id)
               .values(updated_at=datetime.now(timezone.utc) - timedelta(minutes=minutes)))
    db.commit()


def test_success(db, gemini, factory):
    user, _ = user_with_balance(db, "10")
    tid = new_task(db, user.id)
    run(tid, user.id, factory)
    task = task_of(db, tid)
    assert task.status == AITaskStatus.COMPLETED and task.result == VARIANTS and task.error_message is None
    assert balance_of(db, user.id) == Decimal("9.99")
    [entry] = db.scalars(select(AILog)).all()
    assert entry.prompt_type == "gemini_background_ad" and entry.transaction_id == task.transaction_id
    assert db.get(Transaction, task.transaction_id).amount == Decimal("0.01")
    assert_ledger_matches(db, user.id)


def test_moderation_fails_task_without_money(db, gemini, factory):
    user, _ = user_with_balance(db, "10")
    tid = new_task(db, user.id)
    run(tid, user.id, factory, desc="Лучшее онлайн-казино")
    task = task_of(db, tid)
    assert task.status == AITaskStatus.FAILED and "азартные игры" in task.error_message
    assert gemini.calls == [] and task.transaction_id is None
    assert balance_of(db, user.id) == Decimal("10")
    assert_ledger_matches(db, user.id)


def test_insufficient_funds(db, gemini, factory):
    user, _ = user_with_balance(db, "0.02")
    tid = new_task(db, user.id)
    run(tid, user.id, factory)
    task = task_of(db, tid)
    assert task.status == AITaskStatus.FAILED and "Недостаточно средств" in task.error_message
    assert gemini.calls == [] and balance_of(db, user.id) == Decimal("0.02")


@pytest.mark.parametrize("error,message,hidden", [
    (AIUnavailable("превышен лимит запросов к Gemini API"), "превышен лимит", None),
    (RuntimeError("секрет: внутренняя деталь"), "Внутренняя ошибка", "секрет"),
])
def test_generation_error_refunds(db, gemini, factory, error, message, hidden):
    gemini.error = error
    user, _ = user_with_balance(db, "10")
    tid = new_task(db, user.id)
    run(tid, user.id, factory)
    task = task_of(db, tid)
    assert task.status == AITaskStatus.FAILED and message in task.error_message
    assert "Деньги не списаны" in task.error_message
    if hidden:
        assert hidden not in task.error_message
    assert balance_of(db, user.id) == Decimal("10")
    types = db.scalars(select(Transaction.type).where(Transaction.user_id == user.id).order_by(Transaction.id)).all()
    assert types == [TransactionType.DEPOSIT, TransactionType.AI_SPEND, TransactionType.REFUND]
    assert_ledger_matches(db, user.id)


def test_runs_once(db, gemini, factory):
    user, _ = user_with_balance(db, "10")
    tid = new_task(db, user.id)
    run(tid, user.id, factory)
    run(tid, user.id, factory)  # повторный вызов — ничего не делает
    assert len(gemini.calls) == 1 and balance_of(db, user.id) == Decimal("9.99")


def test_foreign_or_missing_task_ignored(db, gemini, factory):
    user, _ = user_with_balance(db, "10")
    other, _ = user_with_balance(db, "10", email="other@mail.ru")
    tid = new_task(db, user.id)
    run(tid, other.id, factory)          # чужой user_id
    run("нет-такой-задачи", user.id, factory)
    assert gemini.calls == [] and task_of(db, tid).status == AITaskStatus.PENDING
    assert balance_of(db, other.id) == Decimal("10")


# --- Задачи, прерванные перезапуском ---
def test_stale_processing_task_refunded(db, gemini, factory):
    user, _ = user_with_balance(db, "10")
    tid = new_task(db, user.id)

    def server_died_during_generation():
        raise SystemExit  # процесс «упал»: задача осталась processing с резервом

    gemini.during = server_died_during_generation
    with pytest.raises(SystemExit):
        run(tid, user.id, factory)
    assert task_of(db, tid).status == AITaskStatus.PROCESSING
    assert balance_of(db, user.id) == Decimal("9.97")  # 0.03 в резерве
    assert held_of(db, user.id) == Decimal("0.03")
    assert_ledger_matches(db, user.id)

    assert bg.fail_stale_tasks(factory) == 0  # свежая — не трогаем, вдруг ещё работает
    make_old(db, tid)
    assert bg.fail_stale_tasks(factory) == 1
    task = task_of(db, tid)
    assert task.status == AITaskStatus.FAILED and "перезапуск сервера" in task.error_message
    assert balance_of(db, user.id) == Decimal("10")
    assert_ledger_matches(db, user.id)
    assert bg.fail_stale_tasks(factory) == 0  # повторно резерв не возвращается


def test_stale_pending_failed_without_refund(db, gemini, factory):
    user, _ = user_with_balance(db, "10")
    old_pending, done = new_task(db, user.id), new_task(db, user.id)
    run(done, user.id, factory)
    make_old(db, old_pending)
    make_old(db, done)
    assert bg.fail_stale_tasks(factory) == 1
    assert task_of(db, old_pending).status == AITaskStatus.FAILED
    assert task_of(db, done).status == AITaskStatus.COMPLETED  # завершённые не трогаем
    assert balance_of(db, user.id) == Decimal("9.99")
    assert_ledger_matches(db, user.id)


def test_recovery_during_generation_means_no_charge(db, gemini, factory):
    """Восстановление закрыло задачу, пока модель ещё отвечала: деньги возвращены один раз, не списаны."""
    user, _ = user_with_balance(db, "10")
    tid = new_task(db, user.id)
    gemini.during = lambda: bg.fail_stale_tasks(factory, stale_after=timedelta(minutes=-1))
    run(tid, user.id, factory)
    task = task_of(db, tid)
    assert task.status == AITaskStatus.FAILED and task.result is None
    assert balance_of(db, user.id) == Decimal("10")
    assert db.scalar(select(func.count()).select_from(AILog)) == 0
    assert_ledger_matches(db, user.id)


def test_startup_survives_missing_table():
    # Сервер запускается, даже если миграции ещё не применены (в тестах БД по умолчанию пустая)
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        assert c.get("/api/v1/health").status_code in (200, 503)
