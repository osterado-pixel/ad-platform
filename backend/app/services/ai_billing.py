"""Оплата AI-генерации с баланса: резерв → генерация → расчёт по факту (или возврат).

1. reserve: атомарно переносим максимально возможную цену из balance в held_balance
   (UPDATE ... WHERE balance >= резерв) и сразу пишем операцию ai_spend в журнал — баланс всегда равен сумме журнала, а параллельные
   запросы не потратят больше, чем есть на балансе.
2. Генерация — вне транзакции БД.
3. settle: резерв снимается с held_balance, операция уменьшается до фактической цены, разница
   возвращается в balance, запись в ai_logs.
   refund: резерв целиком возвращается из held_balance в balance (операция refund).

Журнал: balance = пополнения + возвраты − списания (резерв в журнале — уже списание ai_spend);
held_balance — сколько из этих списаний пока лишь заморожено.

settle и refund не делают commit: вызывающий код фиксирует их вместе со своими изменениями
(например, со статусом фоновой задачи), под write_lock().
"""
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_UP, Decimal

from fastapi import HTTPException, status
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import settings
from app.database import write_lock
from app.ledger import add_transaction
from app.models import AILog, Transaction, TransactionType, User
from app.services import gemini_service

CENT = Decimal("0.01")


def to_balance(usd: Decimal) -> Decimal:
    """$ → валюта баланса, вверх до копейки и не меньше 0.01 (в журнале сумма всегда > 0)."""
    return max((usd * settings.usd_rate).quantize(CENT, rounding=ROUND_UP), CENT)


def hold_amount() -> Decimal:
    """Сколько резервируется на одну генерацию (наибольшая возможная цена)."""
    return to_balance(gemini_service.max_cost())


@dataclass(frozen=True)
class Hold:
    user_id: int
    amount: Decimal
    transaction_id: int


class InsufficientFunds(HTTPException):
    """Не хватает денег на резерв. Это HTTPException (402): в эндпоинте можно не перехватывать."""

    def __init__(self, amount: Decimal):
        self.amount = amount
        super().__init__(status_code=status.HTTP_402_PAYMENT_REQUIRED,
                         detail=f"Недостаточно средств для AI-генерации: нужно не меньше {amount:.2f} "
                                "на балансе (лишнее вернётся после генерации)")


def reserve(db: Session, user_id: int, link: Callable[[Session, int], None] | None = None,
            amount: Decimal | None = None) -> Hold:
    """Резервирует деньги и фиксирует это (commit). InsufficientFunds — если не хватает.

    link(db, transaction_id) — записать ссылку на резерв в той же транзакции БД (например, в задачу):
    тогда не бывает резерва, о котором никто не знает.
    """
    amount = hold_amount() if amount is None else amount
    if amount <= 0:
        raise ValueError("Сумма резерва должна быть больше 0")
    with write_lock():
        reserved = db.execute(
            update(User).where(User.id == user_id, User.balance >= amount)
            .values(balance=User.balance - amount, held_balance=User.held_balance + amount)
        ).rowcount
        if not reserved:
            db.rollback()  # UPDATE открыл транзакцию записи — освобождаем сразу
            raise InsufficientFunds(amount)
        tx = add_transaction(db, user_id=user_id, amount=amount, type=TransactionType.AI_SPEND,
                             description="AI-копирайтер: резерв")
        if link is not None:
            db.flush()  # нужен tx.id
            link(db, tx.id)
        db.commit()
        return Hold(user_id=user_id, amount=amount, transaction_id=tx.id)


def refund(db: Session, hold: Hold, description: str = "Возврат: AI-генерация не выполнена") -> None:
    """Возвращает резерв целиком. Без commit."""
    db.execute(update(User).where(User.id == hold.user_id).values(
        balance=User.balance + hold.amount, held_balance=User.held_balance - hold.amount))
    add_transaction(db, user_id=hold.user_id, amount=hold.amount, type=TransactionType.REFUND,
                    description=description[:255])


def settle(db: Session, hold: Hold, usage: dict, prompt_type: str) -> tuple[Decimal, Decimal]:
    """Расчёт по факту: (списано, остаток баланса). Дороже резерва не берём. Без commit."""
    charge = min(to_balance(usage["cost"]), hold.amount)
    remaining = db.scalar(
        update(User).where(User.id == hold.user_id)
        .values(balance=User.balance + (hold.amount - charge), held_balance=User.held_balance - hold.amount)
        .returning(User.balance)
    )
    db.execute(update(Transaction).where(Transaction.id == hold.transaction_id).values(
        amount=charge,
        description=f"AI-копирайтер: {usage['total_tokens']} токенов ({usage['model']})"[:255]))
    db.add(AILog(
        user_id=hold.user_id, prompt_type=prompt_type, model=usage["model"],
        prompt_tokens=usage["prompt_tokens"], completion_tokens=usage["completion_tokens"],
        total_tokens=usage["total_tokens"], cost=usage["cost"], charged=charge,
        transaction_id=hold.transaction_id,
    ))
    return charge, remaining
