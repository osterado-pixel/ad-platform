"""AI-задачи и биллинг: заморозка, 402, статус, список, возврат (сценарии из учебной инструкции).

Gemini не вызывается: фоновая задача подменена (mock), где нужна генерация — фикстура mock_gemini.
Суммы — не фиксированные $0.10, а резерв платформы (максимальная цена генерации, billing.hold_amount).
"""
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import update

from app.models import AITask, AITaskStatus, User
from app.services import billing
from app.services.ai_billing import hold_amount
from tests.conftest import GEMINI_VARIANTS

PAYLOAD = {"product_description": "Онлайн-курс по Python", "target_audience": "Начинающие программисты"}


def refreshed(db, user: User) -> User:
    db.expire_all()
    return db.get(User, user.id)


# 1. Создание асинхронной задачи и заморозка баланса
def test_generate_ad_async_success(client, db, test_user, user_headers, mock_gemini):
    with patch("app.routers.ai.run_gemini_generation_task") as background:  # «очередь ещё не дошла»
        response = client.post("/api/v1/ai/generate-async", json=PAYLOAD, headers=user_headers)

    assert response.status_code == 202
    data = response.json()
    assert "task_id" in data and data["status"] == "pending"
    hold = hold_amount()
    assert Decimal(str(data["held_amount"])) == hold
    background.assert_called_once()  # задача поставлена в фон с нужными данными
    assert background.call_args.kwargs["task_id"] == data["task_id"]

    user = refreshed(db, test_user)
    assert user.balance == Decimal("10.00") - hold
    assert user.held_balance == hold
    mock_gemini.assert_not_called()


# 2. Недостаточно средств — 402, задача не создаётся
def test_generate_ad_async_insufficient_funds(client, db, test_user, user_headers, mock_gemini):
    db.execute(update(User).where(User.id == test_user.id).values(balance=Decimal("0.01")))
    db.commit()

    response = client.post("/api/v1/ai/generate-async", json=PAYLOAD, headers=user_headers)

    assert response.status_code == 402
    assert "Недостаточно средств" in response.json()["detail"]
    assert db.query(AITask).count() == 0
    assert refreshed(db, test_user).held_balance == Decimal("0")


# 3. Статус конкретной задачи
def test_get_task_status(client, db, test_user, user_headers):
    task = AITask(id="test-task-uuid-1234", user_id=test_user.id, status=AITaskStatus.COMPLETED,
                  result=GEMINI_VARIANTS)
    db.add(task)
    db.commit()

    response = client.get(f"/api/v1/ai/tasks/{task.id}", headers=user_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == "test-task-uuid-1234"
    assert data["status"] == "completed"
    assert data["result"]["variants"][0]["title"] == "Заголовок 1"


# 4. Список задач: пагинация и фильтр по статусу
def test_get_user_ai_tasks_list(client, db, test_user, user_headers):
    db.add_all([
        AITask(id="uuid-1", user_id=test_user.id, status=AITaskStatus.COMPLETED),
        AITask(id="uuid-2", user_id=test_user.id, status=AITaskStatus.COMPLETED),
        AITask(id="uuid-3", user_id=test_user.id, status=AITaskStatus.FAILED, error_message="Gemini timeout"),
    ])
    db.commit()

    response = client.get("/api/v1/ai/tasks?page=1&size=10", headers=user_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3 and len(data["items"]) == 3

    filtered = client.get("/api/v1/ai/tasks?status=completed", headers=user_headers)
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 2 and len(filtered.json()["items"]) == 2
    assert {t["status"] for t in filtered.json()["items"]} == {"completed"}


# 5. Возврат средств при ошибке воркера
def test_refund_on_worker_failure(db, test_user):
    # Состояние после заморозки — настоящей, через billing (с записью в журнал), а не правкой полей
    hold = billing.hold_user_balance(db, test_user.id)
    user = refreshed(db, test_user)
    assert (user.balance, user.held_balance) == (Decimal("10.00") - hold.amount, hold.amount)

    billing.refund_user_balance(db, hold)

    user = refreshed(db, test_user)
    assert user.balance == Decimal("10.00")
    assert user.held_balance == Decimal("0.00")
