from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import Placement, User
from app.pagination import fetch_page, limit_param, offset_param
from app.schemas import PlacementCreate, PlacementResponse, PlacementUpdate

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


@router.get("", response_model=list[PlacementResponse])
@router.get("/", response_model=list[PlacementResponse], include_in_schema=False)
def get_placements(
    response: Response,
    limit: int = limit_param(default=200, maximum=500),
    offset: int = offset_param(),
    db: Session = Depends(get_db),
):
    # Публичный список активных рекламных мест
    query = select(Placement).where(Placement.is_active.is_(True)).order_by(Placement.id)
    return fetch_page(db, query, response, limit, offset)


# --- Все площадки, включая отключённые (для админа) ---
@router.get("/all", response_model=list[PlacementResponse])
def get_all_placements(
    response: Response,
    limit: int = limit_param(default=200, maximum=500),
    offset: int = offset_param(),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    return fetch_page(db, select(Placement).order_by(Placement.id), response, limit, offset)


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
