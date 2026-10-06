"""POST /api/v1/ai/generate-async и GET /api/v1/ai/tasks/{id}. Gemini подменён (фикстура gemini).

TestClient выполняет фоновые задачи сразу после ответа, поэтому к моменту опроса задача уже готова;
промежуточный статус проверяется изнутри генерации (gemini.during).
"""
from decimal import Decimal

from sqlalchemy import func, select

from app.ai import AIUnavailable
from app.models import AITask, AITaskStatus
from tests.test_ai_copy import BODY, VARIANTS, assert_ledger_matches, balance_of, gemini, user_with_balance  # noqa: F401

START = "/api/v1/ai/generate-async"
TASK = "/api/v1/ai/tasks/{}"


def tasks_count(db) -> int:
    return db.scalar(select(func.count()).select_from(AITask))


def test_full_cycle(client, db, gemini):
    user, h = user_with_balance(db, "10")
    seen = {}
    # Пока модель «думает», клиент опрашивает статус (id — из базы: ответ клиенту уже ушёл)
    gemini.during = lambda: seen.update(
        client.get(TASK.format(db.scalar(select(AITask.id))), headers=h).json())

    r = client.post(START, json=BODY, headers=h)
    assert r.status_code == 202
    task_id = r.json()["task_id"]
    assert r.json() == {"task_id": task_id, "status": "pending", "check_status_url": TASK.format(task_id)}
    assert seen["status"] == "processing" and seen["result"] is None  # пока модель «думает»

    body = client.get(r.json()["check_status_url"], headers=h).json()
    assert body["status"] == "completed" and body["result"] == VARIANTS and body["error"] is None
    assert body["created_at"].endswith("Z") or "+00:00" in body["created_at"]
    assert balance_of(db, user.id) == Decimal("9.99")
    assert_ledger_matches(db, user.id)


def test_failed_task_shows_reason_and_refunds(client, db, gemini):
    gemini.error = AIUnavailable("превышен лимит запросов к Gemini API")
    user, h = user_with_balance(db, "10")
    task_id = client.post(START, json=BODY, headers=h).json()["task_id"]
    body = client.get(TASK.format(task_id), headers=h).json()
    assert body["status"] == "failed" and "превышен лимит" in body["error"] and body["result"] is None
    assert balance_of(db, user.id) == Decimal("10")
    assert_ledger_matches(db, user.id)


def test_instant_checks_before_task(client, db, gemini, monkeypatch):
    _, h = user_with_balance(db, "10")
    # Стоп-фразы — сразу 422, задача не создаётся
    r = client.post(START, json={"product_description": "Лучшее онлайн-казино"}, headers=h)
    assert r.status_code == 422 and "азартные игры" in r.json()["detail"]
    # Данные — в теле запроса и с проверками, а не в строке URL
    assert client.post(START, params=BODY, headers=h).status_code == 422
    assert client.post(START, json={"product_description": "x" * 2001}, headers=h).status_code == 422
    # Мало денег — сразу 402
    _, poor = user_with_balance(db, "0.02", email="poor@mail.ru")
    assert client.post(START, json=BODY, headers=poor).status_code == 402
    # Копирайтер выключен — 503
    from app.config import settings
    monkeypatch.setattr(settings, "gemini_api_key", "")
    assert client.post(START, json=BODY, headers=h).status_code == 503
    assert tasks_count(db) == 0 and gemini.calls == []


def test_active_tasks_limit(client, db, gemini):
    user, h = user_with_balance(db, "10")
    db.add_all([AITask(user_id=user.id, status=AITaskStatus.PENDING) for _ in range(5)])
    db.commit()
    r = client.post(START, json=BODY, headers=h)
    assert r.status_code == 429 and "дождитесь" in r.json()["detail"]
    # Завершённые задачи лимит не занимают
    db.query(AITask).update({"status": AITaskStatus.COMPLETED})
    db.commit()
    assert client.post(START, json=BODY, headers=h).status_code == 202


def test_foreign_and_unknown_tasks_404(client, db, gemini):
    _, h = user_with_balance(db, "10")
    _, other = user_with_balance(db, "10", email="other@mail.ru")
    task_id = client.post(START, json=BODY, headers=h).json()["task_id"]
    assert client.get(TASK.format(task_id), headers=other).status_code == 404
    assert client.get(TASK.format("00000000-0000-0000-0000-000000000000"), headers=h).status_code == 404
    assert client.get(TASK.format(task_id)).status_code == 401
    assert client.post(START, json=BODY).status_code == 401


# --- GET /api/v1/ai/tasks: список задач пользователя ---
LIST = "/api/v1/ai/tasks"


def _tasks(db, user_id, statuses):
    """Задачи с явным временем создания: новые — в конце списка statuses."""
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    tasks = [AITask(user_id=user_id, status=s, created_at=base + timedelta(minutes=i))
             for i, s in enumerate(statuses)]
    db.add_all(tasks)
    db.commit()
    return [t.id for t in tasks]


def test_list_only_own_newest_first(client, db, gemini):
    user, h = user_with_balance(db, "10")
    other, _ = user_with_balance(db, "10", email="other@mail.ru")
    ids = _tasks(db, user.id, [AITaskStatus.COMPLETED, AITaskStatus.FAILED, AITaskStatus.PENDING])
    _tasks(db, other.id, [AITaskStatus.COMPLETED])
    body = client.get(LIST, headers=h).json()
    assert [t["task_id"] for t in body["items"]] == ids[::-1]
    assert (body["total"], body["page"], body["size"], body["limit"], body["offset"]) == (3, 1, 10, 10, 0)
    # Формат элемента — тот же, что у GET /tasks/{id}
    assert body["items"][0] == client.get(TASK.format(ids[-1]), headers=h).json()


def test_list_status_filter(client, db, gemini):
    user, h = user_with_balance(db, "10")
    _tasks(db, user.id, [AITaskStatus.COMPLETED, AITaskStatus.FAILED, AITaskStatus.COMPLETED])
    body = client.get(LIST, params={"status": "completed"}, headers=h).json()
    assert body["total"] == 2 and {t["status"] for t in body["items"]} == {"completed"}
    # Неизвестный статус — ошибка, а не молча пустой список
    assert client.get(LIST, params={"status": "done"}, headers=h).status_code == 422


def test_list_pages(client, db, gemini):
    user, h = user_with_balance(db, "10")
    ids = _tasks(db, user.id, [AITaskStatus.COMPLETED] * 5)[::-1]  # новые сверху
    p1 = client.get(LIST, params={"page": 1, "size": 2}, headers=h).json()
    p3 = client.get(LIST, params={"page": 3, "size": 2}, headers=h).json()
    p4 = client.get(LIST, params={"page": 4, "size": 2}, headers=h).json()
    assert [t["task_id"] for t in p1["items"]] == ids[:2] and p1["total"] == 5
    assert [t["task_id"] for t in p3["items"]] == ids[4:] and p3["offset"] == 4
    assert p4["items"] == [] and p4["total"] == 5
    for bad in ({"page": 0}, {"size": 0}, {"size": 101}, {"page": 100_000, "size": 100}):
        assert client.get(LIST, params=bad, headers=h).status_code == 422
    assert client.get(LIST).status_code == 401
