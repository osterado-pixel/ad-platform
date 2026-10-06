"""Журнал движения денег. Все записи в transactions создаются только через add_transaction:
вместе с записью в той же транзакции БД растёт счётчик users.transactions_count —
это total для постраничной истории без COUNT(*) по миллионам строк."""
from decimal import Decimal

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import Transaction, TransactionType, User


def add_transaction(db: Session, *, user_id: int, amount: Decimal, type: TransactionType,
                    campaign_id: int | None = None, description: str | None = None) -> Transaction:
    tx = Transaction(user_id=user_id, amount=amount, type=type, campaign_id=campaign_id,
                     description=description)
    db.add(tx)
    # UPDATE на стороне БД: параллельные клики не потеряют приращение
    db.execute(update(User).where(User.id == user_id)
               .values(transactions_count=User.transactions_count + 1))
    return tx
