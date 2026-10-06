"""app/worker.py: настройки Celery, от которых зависят безопасность и надёжность очереди."""
from app.config import settings
from app.worker import celery_app


def test_broker_from_settings():
    assert celery_app.conf.broker_url == settings.redis_url


def test_json_only():
    # pickle позволил бы выполнить произвольный код через подложенную в Redis задачу
    assert celery_app.conf.task_serializer == "json"
    assert list(celery_app.conf.accept_content) == ["json"]


def test_reliability():
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.task_ignore_result is True
