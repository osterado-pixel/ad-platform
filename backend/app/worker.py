"""Celery: очередь фоновых задач в Redis и отдельный процесс-обработчик (воркер).

Запуск воркера: celery -A app.worker.celery_app worker --loglevel=info
(в Docker — сервис celery_worker в docker-compose.prod.yml).
"""
from celery import Celery

from app.config import settings
from app.monitoring import init_sentry

init_sentry("worker")

celery_app = Celery("ad_platform", broker=settings.redis_url)

celery_app.conf.update(
    # Только JSON: pickle позволил бы выполнить произвольный код, подложив задачу в Redis
    task_serializer="json",
    accept_content=["json"],
    # Результаты задач хранятся в БД (таблица ai_tasks), а не в Redis
    task_ignore_result=True,
    # Задача подтверждается после выполнения: если воркер упал посреди неё, Redis отдаст её снова.
    # Повтор безопасен — фоновая AI-генерация захватывает задачу атомарно (pending → processing)
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Каждый процесс берёт по одной задаче: долгая генерация не задерживает соседние в очереди
    worker_prefetch_multiplier=1,
    # Redis ещё не готов при старте — подождать, а не упасть
    broker_connection_retry_on_startup=True,
    timezone="UTC",
    enable_utc=True,
)
