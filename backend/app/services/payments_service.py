"""Платежи: создание у провайдера и применение уведомлений (зачисление, активация тарифа).

Деньги зачисляются ТОЛЬКО по уведомлению провайдера, никогда — по возврату пользователя на сайт
(его можно подделать). Повторное уведомление ничего не делает: статус меняется одним UPDATE
с условием «ещё pending», зачисление — в той же транзакции БД.
"""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.database import write_lock
from app.ledger import add_transaction
from app.models import (
    Payment, PaymentPurpose, PaymentStatus, Plan, Subscription, SubscriptionStatus, TransactionType, User,
)
from app.payments import PaymentProviderError, WebhookEvent, WebhookRejected, get_provider

log = logging.getLogger(__name__)


class PaymentsDisabled(Exception):
    """Приём платежей выключен (PAYMENTS_PROVIDER не задан)."""


def create_payment(db: Session, user_id: int, purpose: PaymentPurpose, amount: Decimal, return_url: str,
                   plan: Plan | None = None) -> Payment:
    """Платёж у провайдера; пользователя затем отправляют по payment.confirmation_url."""
    provider = get_provider()
    if provider is None:
        raise PaymentsDisabled
    # Сначала запись у нас (pending): уведомление провайдера может прийти раньше, чем мы получим ответ
    payment = Payment(user_id=user_id, provider=provider.name, purpose=purpose, amount=amount,
                      currency=settings.payments_currency, plan_id=plan.id if plan else None)
    with write_lock():
        db.add(payment)
        db.commit()
    description = f"Тариф «{plan.name}»" if plan else "Пополнение баланса Ad Platform"
    try:
        created = provider.create_payment(payment, description, return_url)
    except PaymentProviderError:
        log.exception("Провайдер %s не создал платёж %s", provider.name, payment.id)
        with write_lock():
            db.execute(update(Payment).where(Payment.id == payment.id).values(status=PaymentStatus.FAILED))
            db.commit()
        raise
    with write_lock():
        payment.provider_payment_id = created.provider_payment_id
        payment.confirmation_url = created.confirmation_url
        db.commit()
    db.refresh(payment)
    return payment


def activate_subscription(db: Session, user_id: int, plan: Plan, now: datetime,
                          payment_id: str | None = None) -> Subscription:
    """Тариф после оплаты (или сразу, если бесплатный). Без commit. Тот же тариф — продлевается с конца текущего, другой — заменяет текущий.
    Перерасчёт при смене тарифа (остаток дней) — место для доработки."""
    current = db.scalar(select(Subscription).where(
        Subscription.user_id == user_id, Subscription.status == SubscriptionStatus.ACTIVE,
    ).order_by(Subscription.id.desc()))
    starts = now
    if current is not None:
        if current.plan_id == plan.id and current.ends_at and _aware(current.ends_at) > now:
            # Продление: текущая действует до конца, новая — сразу за ней (возможности без перерыва)
            starts = _aware(current.ends_at)
        elif current.plan_id != plan.id:
            current.status = SubscriptionStatus.CANCELED  # смена тарифа: новый — с сегодняшнего дня
    ends = starts + timedelta(days=plan.period_days) if plan.period_days else None
    sub = Subscription(user_id=user_id, plan_id=plan.id, starts_at=starts, ends_at=ends,
                       features=dict(plan.features or {}), payment_id=payment_id)
    db.add(sub)
    return sub


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # SQLite не хранит пояс


def apply_event(db: Session, provider_name: str, event: WebhookEvent) -> Payment | None:
    """Применяет уведомление провайдера. None — платёж нам неизвестен (уведомление игнорируется)."""
    payment = db.scalar(select(Payment).where(
        Payment.provider == provider_name, Payment.provider_payment_id == event.provider_payment_id))
    if payment is None:
        log.warning("Уведомление %s о неизвестном платеже %s", provider_name, event.provider_payment_id)
        return None
    if event.amount is not None and Decimal(event.amount) != payment.amount:
        # Сумма не совпала — не зачисляем: это подделка или ошибка. ERROR — оповещение Sentry
        log.error("Платёж %s: сумма в уведомлении %s ≠ %s", payment.id, event.amount, payment.amount)
        raise WebhookRejected("сумма не совпадает с платежом")

    now = datetime.now(timezone.utc)
    with write_lock():
        if event.status == PaymentStatus.SUCCEEDED:
            moved = db.execute(update(Payment).where(Payment.id == payment.id, Payment.status == PaymentStatus.PENDING)
                               .values(status=PaymentStatus.SUCCEEDED, paid_at=now)).rowcount
            if moved:
                if payment.purpose == PaymentPurpose.TOP_UP:
                    db.execute(update(User).where(User.id == payment.user_id)
                               .values(balance=User.balance + payment.amount))
                    tx = add_transaction(db, user_id=payment.user_id, amount=payment.amount,
                                         type=TransactionType.DEPOSIT,
                                         description=f"Оплата картой, платёж {payment.id[:8]}")
                    db.flush()
                    db.execute(update(Payment).where(Payment.id == payment.id).values(transaction_id=tx.id))
                else:
                    plan = db.get(Plan, payment.plan_id)
                    activate_subscription(db, payment.user_id, plan, now, payment_id=payment.id)
        elif event.status in (PaymentStatus.CANCELED, PaymentStatus.FAILED):
            db.execute(update(Payment).where(Payment.id == payment.id, Payment.status == PaymentStatus.PENDING)
                       .values(status=event.status))
        elif event.status == PaymentStatus.REFUNDED:
            moved = db.execute(update(Payment).where(Payment.id == payment.id,
                                                     Payment.status == PaymentStatus.SUCCEEDED)
                               .values(status=PaymentStatus.REFUNDED)).rowcount
            if moved:
                # Место для доработки: списать возвращённое с баланса / отменить тариф. Пока — вручную,
                # ERROR — чтобы администратор узнал (Sentry)
                log.error("Платёж %s возвращён плательщику — спишите %s %s с баланса или отмените тариф вручную",
                          payment.id, payment.amount, payment.currency)
        db.commit()
    db.refresh(payment)
    return payment
