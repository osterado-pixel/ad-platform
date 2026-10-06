"""app/maintenance.py: удаление старых служебных записей — без потери денег и финансовой истории."""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app import cli, maintenance
from app.config import settings
from app.models import AILog, AITask, AITaskStatus, Transaction
from app.services import billing
from tests.test_ai_copy import assert_ledger_matches, held_of, user_with_balance


def task(db, user_id, status, days_old, **kw):
    t = AITask(user_id=user_id, status=status, **kw)
    db.add(t)
    db.flush()
    db.execute(update(AITask).where(AITask.id == t.id)
               .values(updated_at=datetime.now(timezone.utc) - timedelta(days=days_old)))
    db.commit()
    return t.id


def ids(db):
    db.expire_all()
    return set(db.scalars(select(AITask.id)))


def test_old_finished_ai_tasks_deleted_unfinished_kept(db):
    user, _ = user_with_balance(db, "10")
    old_done = task(db, user.id, AITaskStatus.COMPLETED, 100, result={"variants": []})
    old_failed = task(db, user.id, AITaskStatus.FAILED, 100, error_message="сбой")
    fresh_done = task(db, user.id, AITaskStatus.COMPLETED, 10)
    # Незавершённая задача с замороженным резервом — даже очень старая — не удаляется:
    # её резерв вернёт очистка зависших задач, а без задачи он потерялся бы
    hold = billing.hold_user_balance(db, user.id)
    old_running = task(db, user.id, AITaskStatus.PROCESSING, 100, transaction_id=hold.transaction_id)
    old_pending = task(db, user.id, AITaskStatus.PENDING, 100)

    result = maintenance.purge_old_records(db, clicks_days=30, ai_tasks_days=90)

    assert result.ai_tasks == 2
    assert ids(db) == {fresh_done, old_running, old_pending}
    assert old_done not in ids(db) and old_failed not in ids(db)
    assert held_of(db, user.id) == Decimal("0.03")  # резерв на месте
    assert_ledger_matches(db, user.id)


def test_money_and_ai_logs_never_deleted(db):
    user, _ = user_with_balance(db, "10")
    hold = billing.hold_user_balance(db, user.id)
    billing.confirm_user_charge(db, hold, {"model": "m", "prompt_tokens": 1, "completion_tokens": 1,
                                           "total_tokens": 2, "cost": Decimal("0.004")})
    task(db, user.id, AITaskStatus.COMPLETED, 400, transaction_id=hold.transaction_id)
    tx_before = db.scalar(select(func.count()).select_from(Transaction))

    maintenance.purge_old_records(db, clicks_days=30, ai_tasks_days=1)

    assert db.scalar(select(func.count()).select_from(AITask)) == 0
    assert db.scalar(select(func.count()).select_from(Transaction)) == tx_before
    assert db.scalar(select(func.count()).select_from(AILog)) == 1
    assert_ledger_matches(db, user.id)


def test_limits():
    with pytest.raises(ValueError, match="повторных кликов"):
        maintenance.purge_old_records(None, clicks_days=0, ai_tasks_days=90)
    with pytest.raises(ValueError, match="AI-задачи"):
        maintenance.purge_old_records(None, clicks_days=30, ai_tasks_days=0)


def test_cli_ai_tasks_days(db, monkeypatch, capsys):
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=db.get_bind()))
    user, _ = user_with_balance(db, "10")
    keep = task(db, user.id, AITaskStatus.COMPLETED, 5)
    task(db, user.id, AITaskStatus.COMPLETED, 20)
    assert cli.purge(ai_tasks_days=10) == 0
    assert ids(db) == {keep}
    assert "завершённых AI-задач 1" in capsys.readouterr().out
    assert cli.purge(ai_tasks_days=0) == 1


def test_purge_sync_uses_settings_and_logs_db_errors(db, monkeypatch, caplog):
    user, _ = user_with_balance(db, "10")
    task(db, user.id, AITaskStatus.FAILED, 40)
    monkeypatch.setattr(settings, "ai_tasks_retention_days", 30)
    assert maintenance.purge_sync(sessionmaker(bind=db.get_bind())).ai_tasks == 1

    def broken():
        raise OperationalError("DELETE", {}, Exception("нет связи с БД"))
    assert maintenance.purge_sync(broken) is None
    assert "Ошибка при удалении старых записей" in caplog.text


def _run_loop(seconds, **kw):
    async def main():
        t = asyncio.create_task(maintenance.schedule_daily_purge(**kw))
        await asyncio.sleep(seconds)
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)
        return t
    return asyncio.run(main())


def test_daily_loop_runs_at_start_and_repeats(monkeypatch):
    calls = []
    monkeypatch.setattr(maintenance, "purge_sync", lambda: calls.append(1))
    t = _run_loop(0.25, interval_seconds=0.1)
    assert len(calls) >= 2  # сразу после запуска и затем по интервалу
    assert t.cancelled()


def test_daily_loop_respects_auto_purge_off(monkeypatch):
    calls = []
    monkeypatch.setattr(maintenance, "purge_sync", lambda: calls.append(1))
    monkeypatch.setattr(settings, "auto_purge", False)
    _run_loop(0.2, interval_seconds=0.05)
    assert calls == []


def test_daily_loop_survives_errors(monkeypatch, caplog):
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("сбой")
    monkeypatch.setattr(maintenance, "purge_sync", flaky)
    _run_loop(0.3, interval_seconds=0.05)
    assert len(calls) >= 2 and "Ошибка в цикле удаления" in caplog.text
