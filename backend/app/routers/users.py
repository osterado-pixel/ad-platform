from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import User
from app.pagination import fetch_page, limit_param, offset_param
from app.schemas import RoleUpdate, UserResponse

router = APIRouter(prefix="/api/v1/users", tags=["Пользователи (для админа)"])


@router.get("", response_model=list[UserResponse])
def list_users(
    response: Response,
    q: str | None = Query(default=None, max_length=255, description="Поиск по части email"),
    limit: int = limit_param(),
    offset: int = offset_param(),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    query = select(User)
    if q:
        query = query.where(User.email.contains(q.strip().lower(), autoescape=True))
    return fetch_page(db, query.order_by(User.id.desc()), response, limit, offset)


@router.get("/{user_id}", response_model=UserResponse)
def get_user(user_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    return user


@router.patch("/{user_id}/role", response_model=UserResponse)
def change_role(
    user_id: int,
    data: RoleUpdate,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    if user_id == admin_user.id:
        # Иначе последний админ может случайно лишить платформу управления
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Нельзя изменить собственную роль")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    user.role = data.role
    db.commit()
    db.refresh(user)
    return user
