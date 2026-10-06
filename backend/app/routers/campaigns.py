from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app import ai
from app.auth import get_current_user, require_admin
from app.config import settings
from app.database import get_db
from app.models import Campaign, CampaignStatus, Placement, User, UserRole
from app.moderation import run_ai_review
from app.pagination import fetch_page_with_total, limit_param, offset_param
from app.schemas import (
    AIStatus, CampaignAdminResponse, CampaignCreate, CampaignModerate, CampaignResponse, CampaignUpdate,
    PaginatedResponse,
)

router = APIRouter(prefix="/api/v1/campaigns", tags=["Рекламные кампании"])


# "/" дублирует путь без слеша: при редиректе клиенты (curl, PowerShell) теряют заголовок Authorization
@router.post("", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED,
             include_in_schema=False)
def create_campaign(
    campaign_data: CampaignCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 1. Проверяем, существует ли выбранное рекламное место
    _require_active_placement(db, campaign_data.placement_id)

    # 2. Создаем рекламную кампанию: владелец и статус задаются сервером
    new_campaign = Campaign(
        **campaign_data.model_dump(),
        user_id=current_user.id,
        status=CampaignStatus.DRAFT,
    )
    db.add(new_campaign)
    db.commit()
    db.refresh(new_campaign)
    return new_campaign


def _require_active_placement(db: Session, placement_id: int) -> None:
    exists = db.scalar(
        select(Placement.id).where(Placement.id == placement_id, Placement.is_active.is_(True))
    )
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Указанная рекламная площадка не найдена или неактивна",
        )


# --- Список кампаний: админ — все (очередь модерации, поиск), рекламодатель — свои ---
@router.get("", response_model=PaginatedResponse[CampaignAdminResponse])
@router.get("/", response_model=PaginatedResponse[CampaignAdminResponse], include_in_schema=False)
def list_campaigns(
    status_filter: CampaignStatus | None = Query(default=None, alias="status"),
    user_id: int | None = Query(default=None, description="Только для админа: кампании пользователя"),
    limit: int = limit_param(default=10),
    offset: int = offset_param(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = select(Campaign).options(selectinload(Campaign.owner), selectinload(Campaign.placement))
    if current_user.role != UserRole.ADMIN:
        query = query.where(Campaign.user_id == current_user.id)  # чужие кампании не видны
    elif user_id is not None:
        query = query.where(Campaign.user_id == user_id)
    if status_filter is not None:
        query = query.where(Campaign.status == status_filter)
    # Очередь модерации — по порядку поступления, остальное — новые сверху
    order = Campaign.id.asc() if status_filter == CampaignStatus.MODERATION else Campaign.id.desc()
    page = fetch_page_with_total(db, query.order_by(order), limit, offset)
    if current_user.role != UserRole.ADMIN:
        # Подсказка AI — только для модератора: рекламодатель видит лишь итог модерации
        hidden = dict.fromkeys(_AI_FIELDS)
        page["items"] = [CampaignAdminResponse.model_validate(c).model_copy(update=hidden) for c in page["items"]]
    return page


_AI_FIELDS = ("ai_verdict", "ai_risk", "ai_reasons", "ai_summary", "ai_checked_at")


@router.get("/my", response_model=PaginatedResponse[CampaignResponse])
def get_my_campaigns(
    limit: int = limit_param(default=10),
    offset: int = offset_param(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Кампании текущего пользователя, новые сверху. Сортировка обязательна:
    # без неё база может менять порядок строк, и страницы «перемешаются»
    query = select(Campaign).where(Campaign.user_id == current_user.id).order_by(Campaign.id.desc())
    return fetch_page_with_total(db, query, limit, offset)


@router.get("/ai-status", response_model=AIStatus)
def ai_status(_admin: User = Depends(require_admin)):
    """Включена ли AI-проверка объявлений (для админ-панели)."""
    return AIStatus(enabled=ai.is_enabled(), model=settings.ai_model, auto_reject=settings.ai_auto_reject)


def _session_factory(db: Session):
    # Отдельные сессии для фоновой проверки — к той же базе, что и запрос
    # (в тестах — к тестовой). Сессия запроса к тому времени уже закрыта
    return sessionmaker(bind=db.get_bind(), autoflush=False)


def _get_campaign_for_update(db: Session, campaign_id: int, user_id: int | None = None) -> Campaign:
    # FOR UPDATE: два параллельных запроса не изменят статус одновременно (в PostgreSQL)
    query = select(Campaign).where(Campaign.id == campaign_id).with_for_update()
    if user_id is not None:
        query = query.where(Campaign.user_id == user_id)
    campaign = db.scalar(query)
    if campaign is None:
        # Чужая кампания тоже 404: не раскрываем, что она существует
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кампания не найдена")
    return campaign


# Из каких статусов рекламодатель может отправить кампанию на модерацию
SUBMITTABLE = {CampaignStatus.DRAFT, CampaignStatus.REJECTED}


# --- 1. Отправка кампании на модерацию пользователем ---
@router.post("/{campaign_id}/submit", response_model=CampaignResponse)
def submit_campaign_for_review(
    campaign_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    campaign = _get_campaign_for_update(db, campaign_id, user_id=current_user.id)

    if campaign.status not in SUBMITTABLE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="На модерацию можно отправить только черновик или отклонённую кампанию",
        )

    campaign.status = CampaignStatus.MODERATION
    campaign.rejection_reason = None
    # Результат прошлой AI-проверки относится к прежнему содержимому — сбрасываем
    campaign.ai_verdict = campaign.ai_risk = campaign.ai_reasons = None
    campaign.ai_summary = campaign.ai_checked_at = None
    db.commit()
    db.refresh(campaign)
    if ai.is_enabled():
        # В фоне, после ответа: рекламодатель не ждёт модель (это секунды)
        background_tasks.add_task(run_ai_review, _session_factory(db), campaign.id)
    return campaign


# --- 2. Модерация кампании (Одобрение / Отклонение) ---
@router.patch("/{campaign_id}/moderate", response_model=CampaignResponse)
def moderate_campaign(
    campaign_id: int,
    moderation_data: CampaignModerate,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin),  # <-- Доступ только для ADMIN
):
    campaign = _get_campaign_for_update(db, campaign_id)

    if campaign.status != CampaignStatus.MODERATION:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Модерировать можно только кампанию со статусом «на модерации»",
        )

    # Схема уже гарантирует: status — active или rejected, причина есть только у rejected
    campaign.status = moderation_data.status
    campaign.rejection_reason = moderation_data.rejection_reason
    db.commit()
    db.refresh(campaign)
    return campaign


# --- Просмотр одной кампании ---
@router.get("/{campaign_id}", response_model=CampaignResponse)
def get_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    campaign = db.get(Campaign, campaign_id)
    # Владелец видит свою кампанию, админ — любую; чужая — 404, не раскрываем существование
    if campaign is None or (
        campaign.user_id != current_user.id and current_user.role != UserRole.ADMIN
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кампания не найдена")
    return campaign


# --- Редактирование кампании владельцем ---
# Изменение этих полей у одобренной кампании требует повторной модерации:
# иначе можно пройти проверку с одним баннером и подменить его на другой
CONTENT_FIELDS = {"placement_id", "title", "description", "image_url", "target_url"}
EDITABLE = {CampaignStatus.DRAFT, CampaignStatus.REJECTED, CampaignStatus.ACTIVE, CampaignStatus.PAUSED}


def _utc(value: datetime) -> datetime:
    # Из SQLite даты приходят без пояса (там всегда UTC)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@router.patch("/{campaign_id}", response_model=CampaignResponse)
def update_campaign(
    campaign_id: int,
    data: CampaignUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    campaign = _get_campaign_for_update(db, campaign_id, user_id=current_user.id)
    if campaign.status not in EDITABLE:
        detail = ("Кампания на модерации — дождитесь решения"
                  if campaign.status == CampaignStatus.MODERATION
                  else "Завершённую кампанию изменить нельзя")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

    def differs(field, value):
        current = getattr(campaign, field)
        if isinstance(current, datetime) and isinstance(value, datetime):
            return _utc(current) != value
        return current != value

    changes = {k: v for k, v in data.model_dump(exclude_unset=True).items() if differs(k, v)}
    if "placement_id" in changes:
        _require_active_placement(db, changes["placement_id"])

    start = changes.get("start_date", campaign.start_date)
    end = changes.get("end_date", campaign.end_date)
    if start is not None and end is not None and _utc(end) < _utc(start):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="Дата окончания (end_date) раньше даты начала (start_date)")

    for field, value in changes.items():
        setattr(campaign, field, value)
    if CONTENT_FIELDS & changes.keys() and campaign.status in {CampaignStatus.ACTIVE, CampaignStatus.PAUSED}:
        campaign.status = CampaignStatus.MODERATION  # показ остановится до повторного одобрения
    db.commit()
    db.refresh(campaign)
    return campaign


def _transition(db: Session, campaign_id: int, user: User, allowed: set,
                target: CampaignStatus, error: str) -> Campaign:
    campaign = _get_campaign_for_update(db, campaign_id, user_id=user.id)
    if campaign.status not in allowed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error)
    campaign.status = target
    db.commit()
    db.refresh(campaign)
    return campaign


# --- Пауза / возобновление / завершение ---
@router.post("/{campaign_id}/pause", response_model=CampaignResponse)
def pause_campaign(campaign_id: int, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    return _transition(db, campaign_id, current_user, {CampaignStatus.ACTIVE},
                       CampaignStatus.PAUSED, "Поставить на паузу можно только активную кампанию")


@router.post("/{campaign_id}/resume", response_model=CampaignResponse)
def resume_campaign(campaign_id: int, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    return _transition(db, campaign_id, current_user, {CampaignStatus.PAUSED},
                       CampaignStatus.ACTIVE, "Возобновить можно только кампанию на паузе")


@router.post("/{campaign_id}/complete", response_model=CampaignResponse)
def complete_campaign(campaign_id: int, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    # Завершение окончательное; статистика и история списаний сохраняются
    return _transition(db, campaign_id, current_user, set(CampaignStatus) - {CampaignStatus.COMPLETED},
                       CampaignStatus.COMPLETED, "Кампания уже завершена")


# --- Удаление (только то, что ни разу не показывалось) ---
@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_campaign(campaign_id: int, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    campaign = _get_campaign_for_update(db, campaign_id, user_id=current_user.id)
    if campaign.status not in {CampaignStatus.DRAFT, CampaignStatus.REJECTED} or campaign.impressions_count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Удалить можно только черновик или отклонённую кампанию без показов. "
                   "Остальные завершите — статистика сохранится",
        )
    db.delete(campaign)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- AI-проверка по кнопке администратора (повторно или если фоновая не удалась) ---
@router.post("/{campaign_id}/ai-review", response_model=CampaignAdminResponse)
def ai_review_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    if not ai.is_enabled():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="AI-проверка выключена: не задан ANTHROPIC_API_KEY")
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кампания не найдена")
    if campaign.status != CampaignStatus.MODERATION:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="AI-проверка — только для кампаний на модерации")
    db.rollback()  # сетевой запрос к модели — без открытой транзакции
    run_ai_review(_session_factory(db), campaign_id)
    return db.scalar(
        select(Campaign).options(selectinload(Campaign.owner), selectinload(Campaign.placement))
        .where(Campaign.id == campaign_id).execution_options(populate_existing=True)
    )
