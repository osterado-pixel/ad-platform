"""AI-копирайтер: варианты объявления от Gemini с оплатой с баланса.

0. Модерация описания и аудитории (стоп-фразы + OpenAI Moderation) — до денег и до Gemini:
   за запрещённый текст платный запрос не отправляется и с баланса ничего не резервируется.
1–3. Резерв → генерация вне транзакции БД → расчёт по факту или полный возврат
     (подробно — app/services/ai_billing.py).

/generate-async + /tasks/{id} — то же в фоне: ответ сразу (id задачи), результат — опросом статуса
(app/services/ai_background.py).
"""
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import AIUnavailable
from app.auth import get_current_user
from app.database import background_session_factory, get_db, write_lock
from app.models import AITask, AITaskStatus, User
from app.pagination import MAX_OFFSET
from app.schemas import AdCopyResponse, AdGenerateRequest, AITaskCreated, AITaskListResponse, AITaskResponse
from app.services import ai_billing, gemini_service
from app.services.ai_background import run_gemini_generation_task
from app.services.billing import hold_user_balance
from app.services.moderation_service import check_local_rules, moderate_text_sync

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

    # 1. Резерв (не хватает денег — InsufficientFunds, это ответ 402)
    hold = ai_billing.reserve(db, current_user.id)

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


# --- Фоновая генерация: задача создаётся сразу, результат — опросом статуса ---
MAX_ACTIVE_TASKS = 5  # незавершённых задач на пользователя: защита от засыпания очереди


def _task_response(task: AITask) -> AITaskResponse:
    return AITaskResponse(task_id=task.id, status=task.status.value, result=task.result,
                          error=task.error_message, created_at=task.created_at, updated_at=task.updated_at)


@router.post("/generate-async", response_model=AITaskCreated, status_code=status.HTTP_202_ACCEPTED)
def start_ad_generation(
    request: AdGenerateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not gemini_service.is_enabled():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="AI-копирайтер выключен: не задан GEMINI_API_KEY")
    # Мгновенная проверка стоп-фраз (без сети); OpenAI Moderation выполнит сама задача
    check_local_rules(f"{request.product_description}\n{request.target_audience}")

    active = db.scalar(select(func.count()).select_from(AITask).where(
        AITask.user_id == current_user.id,
        AITask.status.in_([AITaskStatus.PENDING, AITaskStatus.PROCESSING])))
    if active >= MAX_ACTIVE_TASKS:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail=f"Уже выполняется {active} AI-задач — дождитесь их завершения")

    # Заморозка средств и создание задачи — одной транзакцией БД: при сбое между ними не останется
    # ни замороженных денег без задачи, ни задачи без резерва. Не хватает денег — 402
    created: dict[str, str] = {}

    def create_task(db: Session, transaction_id: int) -> None:
        task = AITask(user_id=current_user.id, transaction_id=transaction_id)
        db.add(task)
        db.flush()
        created["id"] = task.id

    hold = hold_user_balance(db, current_user.id, link=create_task)
    task_id = created["id"]

    background_tasks.add_task(
        run_gemini_generation_task,
        task_id=task_id,
        user_id=current_user.id,
        product_description=request.product_description,
        target_audience=request.target_audience,
        session_factory=background_session_factory(db),
    )
    return AITaskCreated(task_id=task_id, check_status_url=f"/api/v1/ai/tasks/{task_id}", held_amount=hold.amount)


@router.get("/tasks", response_model=AITaskListResponse)
def get_user_ai_tasks(
    status_filter: AITaskStatus | None = Query(
        default=None, alias="status", description="Фильтр по статусу: pending, processing, completed, failed"),
    page: int = Query(default=1, ge=1, description="Номер страницы"),
    size: int = Query(default=10, ge=1, le=100, description="Количество задач на странице"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Фоновые AI-задачи текущего пользователя, новые сверху. Фильтр по статусу и постраничная выдача."""
    offset = (page - 1) * size
    if offset > MAX_OFFSET:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"Слишком далёкая страница: сдвиг не больше {MAX_OFFSET} задач")
    query = select(AITask).where(AITask.user_id == current_user.id)
    if status_filter is not None:
        query = query.where(AITask.status == status_filter)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    # id — второй ключ сортировки: у задач, созданных в одну секунду, порядок между страницами не «прыгает»
    tasks = db.scalars(query.order_by(AITask.created_at.desc(), AITask.id.desc()).offset(offset).limit(size)).all()
    return AITaskListResponse(items=[_task_response(t) for t in tasks], total=total,
                              limit=size, offset=offset, page=page, size=size)


@router.get("/tasks/{task_id}", response_model=AITaskResponse)
def get_task_status(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    task = db.get(AITask, task_id)
    # Чужая задача — тоже 404: не подтверждаем, что такой id существует
    if task is None or task.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задача не найдена")
    return _task_response(task)
