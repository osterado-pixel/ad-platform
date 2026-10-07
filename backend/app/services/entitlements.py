"""Возможности пользователя по тарифу — единая точка, к которой подключаются лимиты и скидки.

Сейчас тарифы только хранятся и покупаются; что именно они дают, решается здесь. Пример будущей
интеграции с AI-копирайтером (app/services/ai_billing.py):

    ent = entitlements(db, user_id)
    if ent.get("ai_generations", 0) > used_this_period(...):   # генерация входит в тариф
        ... не замораживать деньги / списывать 0 ...

Ключи features задаёт администратор у тарифа (POST /api/v1/plans), при покупке они копируются
в подписку: последующая правка тарифа не меняет уже оплаченное.
"""
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Plan, Subscription, SubscriptionStatus


def active_subscription(db: Session, user_id: int, now: datetime | None = None) -> Subscription | None:
    """Подписка, действующая сейчас (оплаченные на будущее — продления — не считаются)."""
    now = now or datetime.now(timezone.utc)
    return db.scalar(
        select(Subscription)
        .where(Subscription.user_id == user_id, Subscription.status == SubscriptionStatus.ACTIVE,
               Subscription.starts_at <= now,
               or_(Subscription.ends_at.is_(None), Subscription.ends_at > now))
        .order_by(Subscription.starts_at.desc())
    )


def entitlements(db: Session, user_id: int, now: datetime | None = None) -> dict:
    """Возможности пользователя: {"plan": код тарифа или None, ...features подписки}."""
    sub = active_subscription(db, user_id, now)
    if sub is None:
        return {"plan": None}
    plan = db.get(Plan, sub.plan_id)
    return {"plan": plan.code if plan else None, **(sub.features or {})}
