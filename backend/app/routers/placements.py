from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import Placement, User
from app.pagination import fetch_page_with_total, limit_param, offset_param
from app.schemas import PaginatedResponse, PlacementCreate, PlacementResponse, PlacementUpdate

router = APIRouter(prefix="/api/v1/placements", tags=["Рекламные места"])

CODE_TAKEN = "Площадка с таким кодовым идентификатором уже существует"


# "/" дублирует путь без слеша: при редиректе клиенты (curl, PowerShell) теряют заголовок Authorization
@router.post("", response_model=PlacementResponse, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=PlacementResponse, status_code=status.HTTP_201_CREATED,
             include_in_schema=False)
def create_placement(
    placement_data: PlacementCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),  # создавать площадки может только админ
):
    exists = db.scalar(
        select(Placement.id).where(Placement.code_identifier == placement_data.code_identifier)
    )
    if exists is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=CODE_TAKEN)

    new_placement = Placement(**placement_data.model_dump())
    db.add(new_placement)
    try:
        db.commit()
    except IntegrityError:  # параллельное создание с тем же кодом
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=CODE_TAKEN) from None
    db.refresh(new_placement)
    return new_placement


@router.get("", response_model=PaginatedResponse[PlacementResponse])
@router.get("/", response_model=PaginatedResponse[PlacementResponse], include_in_schema=False)
def get_placements(
    limit: int = limit_param(default=10, maximum=500),
    offset: int = offset_param(),
    db: Session = Depends(get_db),
):
    # Публичный список: только активные площадки. Сортировка по id обязательна —
    # без неё база может отдавать строки в разном порядке, и страницы «перемешаются»
    query = select(Placement).where(Placement.is_active.is_(True)).order_by(Placement.id)
    return fetch_page_with_total(db, query, limit, offset)


# --- Все площадки, включая отключённые (для админа) ---
@router.get("/all", response_model=PaginatedResponse[PlacementResponse])
def get_all_placements(
    limit: int = limit_param(default=10, maximum=500),
    offset: int = offset_param(),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    # Все площадки, включая отключённые (для админа)
    return fetch_page_with_total(db, select(Placement).order_by(Placement.id), limit, offset)


# --- Изменение площадки: название, цены, включение/отключение ---
@router.patch("/{placement_id}", response_model=PlacementResponse)
def update_placement(
    placement_id: int,
    data: PlacementUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    placement = db.get(Placement, placement_id)
    if placement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Площадка не найдена")
    # null в полях игнорируется: название и цены нельзя «очистить»
    for field, value in data.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(placement, field, value)
    db.commit()
    db.refresh(placement)
    return placement
