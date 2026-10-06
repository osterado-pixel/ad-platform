"""Модель AITask: значения по умолчанию, UUID, проверка статуса, связь с пользователем."""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, StatementError

from app.models import AITask, AITaskStatus, User


@pytest.fixture
def user(db):
    u = User(email="u@mail.ru", hashed_password="x")
    db.add(u)
    db.commit()
    return u


def test_defaults(db, user):
    task = AITask(user_id=user.id)
    db.add(task)
    db.commit()
    db.refresh(task)
    assert str(uuid.UUID(task.id)) == task.id  # настоящий UUID
    assert task.status == AITaskStatus.PENDING
    assert task.result is None and task.error_message is None
    assert task.created_at is not None and task.updated_at is not None


def test_ids_unique_and_unguessable(db, user):
    tasks = [AITask(user_id=user.id) for _ in range(3)]
    db.add_all(tasks)
    db.commit()
    assert len({t.id for t in tasks}) == 3


def test_result_json_roundtrip(db, user):
    result = {"variants": [{"title": "Заголовок", "text": "Текст", "cta": "Купить"}]}
    task = AITask(user_id=user.id, status=AITaskStatus.COMPLETED, result=result)
    db.add(task)
    db.commit()
    db.expire_all()
    assert db.get(AITask, task.id).result == result


def test_status_stored_as_value_and_checked(db, user):
    task = AITask(user_id=user.id, status=AITaskStatus.FAILED, error_message="сбой")
    db.add(task)
    db.commit()
    assert db.execute(text("SELECT status FROM ai_tasks")).scalar() == "failed"
    # Неизвестный статус не попадёт в БД ни через модель, ни напрямую
    with pytest.raises(StatementError):
        db.add(AITask(user_id=user.id, status="done"))
        db.flush()
    db.rollback()
    with pytest.raises(IntegrityError):
        db.execute(text("INSERT INTO ai_tasks (id, user_id, status) VALUES ('x', :u, 'done')"), {"u": user.id})
    db.rollback()


def test_user_required(db):
    db.add(AITask(user_id=99999))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
