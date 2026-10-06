"""app/services/ai_cleanup.py: очистка зависших задач с возвратом резерва."""
import asyncio
from decimal import Decimal

import pytest
from sqlalchemy.exc import OperationalError

from app.models import AITaskStatus
from app.services import ai_cleanup
from tests.test_ai_background import factory, make_old, new_task, run, task_of  # noqa: F401
from tests.test_ai_copy import assert_ledger_matches, balance_of, gemini, user_with_balance  # noqa: F401


def stuck_processing(db, gemini, factory, user):
    """Задача, «упавшая» посреди генерации: processing с резервом 0.03."""
    tid = new_task(db, user.id)

    def died():
        raise SystemExit
    gemini.during = died
    with pytest.raises(SystemExit):
        run(tid, user.id, factory)
    gemini.during = None
    return tid


def test_cleanup_fails_and_refunds(db, gemini, factory):
    user, _ = user_with_balance(db, "10")
    tid = stuck_processing(db, gemini, factory, user)
    assert ai_cleanup.cleanup_stuck_ai_tasks_sync(10, factory) == 0  # свежая — работает, не трогаем
    make_old(db, tid, minutes=11)
    assert ai_cleanup.cleanup_stuck_ai_tasks_sync(10, factory) == 1
    task = task_of(db, tid)
    assert task.status == AITaskStatus.FAILED and "Деньги не списаны" in task.error_message
    assert balance_of(db, user.id) == Decimal("10")
    assert_ledger_matches(db, user.id)


def test_timeout_respected(db, gemini, factory):
    user, _ = user_with_balance(db, "10")
    tid = stuck_processing(db, gemini, factory, user)
    make_old(db, tid, minutes=11)
    assert ai_cleanup.cleanup_stuck_ai_tasks_sync(30, factory) == 0  # 11 мин < 30 мин
    assert task_of(db, tid).status == AITaskStatus.PROCESSING


def test_too_small_timeout_rejected(factory):
    # Порог меньше времени генерации закрывал бы работающие задачи
    with pytest.raises(ValueError):
        ai_cleanup.cleanup_stuck_ai_tasks_sync(1, factory)


def test_db_error_logged_not_raised(monkeypatch, caplog):
    def broken(*_args):
        raise OperationalError("SELECT", {}, Exception("нет связи с БД"))
    monkeypatch.setattr(ai_cleanup, "fail_stale_tasks", broken)
    assert ai_cleanup.cleanup_stuck_ai_tasks_sync() == 0
    assert "Ошибка при очистке" in caplog.text


def test_async_name_from_tutorial(monkeypatch):
    calls = []
    monkeypatch.setattr(ai_cleanup, "fail_stale_tasks", lambda factory, stale_after: calls.append(stale_after) or 3)
    assert asyncio.run(ai_cleanup.cleanup_stuck_ai_tasks(15)) == 3
    assert calls[0].total_seconds() == 15 * 60
