"""Сервис финансовых операций: заморозка → списание по факту или возврат.

Имена функций — из учебной инструкции; сама логика — в app/services/ai_billing.py (её же используют
эндпоинты копирайтера и фоновые задачи), второго пути работы с деньгами нет. Отличия от инструкции:
- атомарный UPDATE ... WHERE balance >= сумма вместо SELECT ... FOR UPDATE: блокировка строки
  в SQLite не работает, а условие в UPDATE защищает от гонки в любой БД;
- каждая операция — запись в журнале (/wallet/history): баланс всегда равен сумме журнала;
- цена — по фактическим токенам, а не фиксированные $0.10; заморозка — максимально возможная цена;
- повторное подтверждение или возврат одного резерва не проходит «молча»: заморозка ушла бы
  в минус, и CHECK held_balance >= 0 в базе отменяет такую операцию.
"""
from collections.abc import Callable
from decimal import Decimal

from sqlalchemy.orm import Session

from app.database import write_lock
from app.services import ai_billing
from app.services.ai_billing import Hold, InsufficientFunds  # noqa: F401 — для вызывающего кода


def hold_user_balance(db: Session, user_id: int, amount: Decimal | None = None,
                      link: Callable[[Session, int], None] | None = None) -> Hold:
    """Заморозка перед запуском задачи (balance → held_balance). Без суммы — максимальная цена генерации.

    InsufficientFunds (HTTP 402) — если доступных денег не хватает.
    link(db, transaction_id) — выполняется в той же транзакции БД, что и заморозка (например, создаёт
    задачу со ссылкой на резерв): не бывает заморозки без задачи или задачи без заморозки.
    """
    return ai_billing.reserve(db, user_id, link=link, amount=amount)


def confirm_user_charge(db: Session, hold: Hold, usage: dict, prompt_type: str = "gemini_ad_copy") -> Decimal:
    """Успех: снимаем заморозку, списываем по факту (usage — расход токенов от gemini_service),
    остаток возвращаем на баланс. Возвращает списанную сумму."""
    with write_lock():
        charge, _remaining = ai_billing.settle(db, hold, usage, prompt_type)
        db.commit()
    return charge


def refund_user_balance(db: Session, hold: Hold, description: str = "Возврат: AI-генерация не выполнена") -> None:
    """Ошибка выполнения: вся заморозка возвращается на баланс (операция «Возврат» в журнале)."""
    with write_lock():
        ai_billing.refund(db, hold, description)
        db.commit()
