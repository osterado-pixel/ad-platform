"""AI-копирайтер: варианты объявления от Gemini с оплатой с баланса.

0. Модерация описания и аудитории (стоп-фразы + OpenAI Moderation) — до денег и до Gemini:
   за запрещённый текст платный запрос не отправляется и с баланса ничего не резервируется.
1–3. Резерв → генерация вне транзакции БД → расчёт по факту или полный возврат
     (подробно — app/services/ai_billing.py).
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.ai import AIUnavailable
from app.auth import get_current_user
from app.database import get_db, write_lock
from app.models import User
from app.schemas import AdCopyResponse, AdGenerateRequest
from app.services import ai_billing, gemini_service
from app.services.moderation_service import moderate_text_sync

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ai", tags=["AI-копирайтер"])


def _refund(db: Session, hold: ai_billing.Hold) -> None:
    with write_lock():
        ai_billing.refund(db, hold)
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
    # 0. Модерация: одним запросом и описание, и аудиторию («аудитория: любители казино» — тоже нарушение).
    #    Эндпоинт синхронный (выполняется в пуле потоков), поэтому sync-вариант moderate_text
    moderate_text_sync(f"{request.product_description}\n{request.target_audience}")

    # 1. Резерв
    try:
        hold = ai_billing.reserve(db, current_user.id)
    except ai_billing.InsufficientFunds as e:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(e)) from None

    # 2. Генерация — без открытой транзакции
    try:
        result = gemini_service.generate_ad(request.product_description, request.target_audience)
    except AIUnavailable as e:
        _refund(db, hold)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"AI-генерация не выполнена: {e}. Деньги не списаны") from e
    except Exception:
        log.exception("AI-копирайтер: непредвиденная ошибка")
        _refund(db, hold)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Внутренняя ошибка AI-генерации. Деньги не списаны") from None

    # 3. Расчёт по факту
    usage = result["usage"]
    with write_lock():
        charge, remaining = ai_billing.settle(db, hold, usage, prompt_type="gemini_ad_copy")
        db.commit()

    return AdCopyResponse(
        data=result["content"],
        billing={"tokens_used": usage["total_tokens"], "cost_deducted": charge, "remaining_balance": remaining},
    )
