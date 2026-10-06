"""AI-копирайтер: варианты объявления от Gemini с оплатой с баланса.

Порядок оплаты (резерв → генерация → расчёт):
1. Атомарно резервируем максимально возможную стоимость запроса (UPDATE ... WHERE balance >= резерв)
   и сразу пишем операцию ai_spend в журнал: баланс всегда равен сумме журнала, а параллельные
   запросы не потратят больше, чем есть на балансе (иначе 10 одновременных запросов прошли бы
   проверку «баланс ≥ 0.01», а оплачен был бы один).
2. Генерация — вне транзакции БД (запрос к Gemini длится секунды).
3. Успех: операция уменьшается до фактической цены, разница возвращается на баланс, запись в ai_logs.
   Ошибка: резерв возвращается полностью (операция refund) — за неудачную генерацию не платят.
"""
import logging
from decimal import ROUND_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.ai import AIUnavailable
from app.auth import get_current_user
from app.config import settings
from app.database import get_db, write_lock
from app.ledger import add_transaction
from app.models import AILog, Transaction, TransactionType, User
from app.schemas import AdCopyResponse, AdGenerateRequest
from app.services import gemini_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ai", tags=["AI-копирайтер"])

CENT = Decimal("0.01")


def to_balance(usd: Decimal) -> Decimal:
    """$ → валюта баланса, вверх до копейки и не меньше 0.01 (в журнале сумма всегда > 0)."""
    return max((usd * settings.usd_rate).quantize(CENT, rounding=ROUND_UP), CENT)


def _refund_hold(db: Session, user_id: int, hold: Decimal) -> None:
    with write_lock():
        db.execute(update(User).where(User.id == user_id).values(balance=User.balance + hold))
        add_transaction(db, user_id=user_id, amount=hold, type=TransactionType.REFUND,
                        description="Возврат: AI-генерация не выполнена")
        db.commit()


@router.post("/generate-copy", response_model=AdCopyResponse)
def generate_ad_copy(
    request: AdGenerateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not gemini_service.is_enabled():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="AI-копирайтер выключен: не задан GEMINI_API_KEY")
    user_id = current_user.id
    hold = to_balance(gemini_service.max_cost())

    # 1. Резерв
    with write_lock():
        reserved = db.execute(
            update(User).where(User.id == user_id, User.balance >= hold)
            .values(balance=User.balance - hold)
        ).rowcount
        if not reserved:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"Недостаточно средств для AI-генерации: нужно не меньше {hold:.2f} на балансе "
                       "(лишнее вернётся после генерации)",
            )
        tx = add_transaction(db, user_id=user_id, amount=hold, type=TransactionType.AI_SPEND,
                             description="AI-копирайтер: резерв")
        db.commit()
        tx_id = tx.id

    # 2. Генерация — без открытой транзакции
    try:
        result = gemini_service.generate_ad(request.product_description, request.target_audience)
    except AIUnavailable as e:
        _refund_hold(db, user_id, hold)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"AI-генерация не выполнена: {e}. Деньги не списаны") from e
    except Exception:
        log.exception("AI-копирайтер: непредвиденная ошибка")
        _refund_hold(db, user_id, hold)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Внутренняя ошибка AI-генерации. Деньги не списаны") from None

    # 3. Расчёт по факту. Если вдруг дороже резерва — берём не больше резерва
    usage = result["usage"]
    charge = min(to_balance(usage["cost"]), hold)
    with write_lock():
        remaining = db.scalar(
            update(User).where(User.id == user_id)
            .values(balance=User.balance + (hold - charge)).returning(User.balance)
        )
        db.execute(update(Transaction).where(Transaction.id == tx_id).values(
            amount=charge,
            description=f"AI-копирайтер: {usage['total_tokens']} токенов ({usage['model']})"[:255]))
        db.add(AILog(
            user_id=user_id, prompt_type="gemini_ad_copy", model=usage["model"],
            prompt_tokens=usage["prompt_tokens"], completion_tokens=usage["completion_tokens"],
            total_tokens=usage["total_tokens"], cost=usage["cost"], charged=charge, transaction_id=tx_id,
        ))
        db.commit()

    return AdCopyResponse(
        data=result["content"],
        billing={"tokens_used": usage["total_tokens"], "cost_deducted": charge, "remaining_balance": remaining},
    )
