"""Ограничение частоты попыток входа и регистрации.

Счётчики — в БД (таблица auth_attempts), а не в памяти: работают при нескольких процессах
сервера и переживают перезапуск. Email и IP хранятся только как HMAC-хеш.
"""
import hashlib
import hmac
import math
import random
import time

from fastapi import HTTPException, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import write_lock
from app.models import AuthAttempt

KEEP_SECONDS = 24 * 3600  # дольше хранить незачем: самое длинное окно — час


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _key(kind: str, value: str) -> str:
    return hmac.new(settings.secret_key.encode(), f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()


def check(db: Session, kind: str, value: str, limit: int, window_seconds: int, message: str) -> None:
    """429, если за окно уже было limit попыток. Retry-After — когда освободится место."""
    since = int(time.time()) - window_seconds
    key = _key(kind, value)
    count, oldest = db.execute(
        select(func.count(), func.min(AuthAttempt.ts))
        .where(AuthAttempt.kind == kind, AuthAttempt.key == key, AuthAttempt.ts > since)
    ).one()
    if count >= limit:
        retry_after = max(1, (oldest or since) + window_seconds - int(time.time()))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"{message} Повторите через {math.ceil(retry_after / 60)} мин.",
            headers={"Retry-After": str(retry_after)},
        )


def record(db: Session, *items: tuple[str, str]) -> None:
    """Записывает попытки (kind, value) и изредка чистит устаревшие записи."""
    now = int(time.time())
    with write_lock():
        for kind, value in items:
            db.add(AuthAttempt(kind=kind, key=_key(kind, value), ts=now))
        if random.random() < 0.02:  # без отдельного планировщика таблица не разрастается
            db.execute(delete(AuthAttempt).where(AuthAttempt.ts < now - KEEP_SECONDS))
        db.commit()


def clear(db: Session, kind: str, value: str) -> None:
    with write_lock():
        db.execute(delete(AuthAttempt).where(AuthAttempt.kind == kind, AuthAttempt.key == _key(kind, value)))
        db.commit()
