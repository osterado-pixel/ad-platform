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


# --- Периодическая очистка (schedule_task_cleanup) и lifespan ---
def _run_scheduler(seconds, **kwargs):
    async def main():
        task = asyncio.create_task(ai_cleanup.schedule_task_cleanup(**kwargs))
        await asyncio.sleep(seconds)
        task.cancel()
        results = await asyncio.gather(task, return_exceptions=True)
        return task, results
    return asyncio.run(main())


def test_scheduler_runs_periodically_and_stops(monkeypatch):
    calls = []

    async def fake_cleanup(timeout_minutes):
        calls.append(timeout_minutes)
        return 0
    monkeypatch.setattr(ai_cleanup, "cleanup_stuck_ai_tasks", fake_cleanup)
    task, results = _run_scheduler(0.25, interval_seconds=0.05, timeout_minutes=7)
    assert len(calls) >= 3 and set(calls) == {7}
    assert task.cancelled()  # остановка доходит до вызвавшего — сервер не ждёт вечно
    assert isinstance(results[0], asyncio.CancelledError)


def test_scheduler_survives_errors(monkeypatch, caplog):
    calls = []

    async def flaky_cleanup(timeout_minutes):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("сбой")
        return 0
    monkeypatch.setattr(ai_cleanup, "cleanup_stuck_ai_tasks", flaky_cleanup)
    _run_scheduler(0.25, interval_seconds=0.05, timeout_minutes=10)
    assert len(calls) >= 2  # после ошибки цикл продолжил работу
    assert "Ошибка в цикле очистки" in caplog.text and "RuntimeError" in caplog.text  # с подробностями


def test_scheduler_uses_settings(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "ai_cleanup_interval_seconds", 0.05)
    monkeypatch.setattr(settings, "ai_task_timeout_minutes", 12)
    calls = []

    async def fake_cleanup(timeout_minutes):
        calls.append(timeout_minutes)
        return 0
    monkeypatch.setattr(ai_cleanup, "cleanup_stuck_ai_tasks", fake_cleanup)
    _run_scheduler(0.2)
    assert calls and set(calls) == {12}


def test_lifespan_cleans_on_start_and_stops_loop(monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app
    calls, loop_state = [], {}

    async def fake_cleanup(timeout_minutes):
        calls.append(timeout_minutes)
        return 0

    async def fake_loop():
        loop_state["started"] = True
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            loop_state["stopped"] = True
            raise
    monkeypatch.setattr(ai_cleanup, "cleanup_stuck_ai_tasks", fake_cleanup)
    monkeypatch.setattr(ai_cleanup, "schedule_task_cleanup", fake_loop)
    with TestClient(app):
        assert calls == [settings.ai_task_timeout_minutes]  # очистка — сразу при запуске
    assert loop_state == {"started": True, "stopped": True}  # цикл запущен и остановлен вместе с сервером
