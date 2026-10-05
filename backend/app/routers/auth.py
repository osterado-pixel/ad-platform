from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import ratelimit
from app.auth import (
    authenticate_user, create_access_token, get_current_user, get_password_hash, verify_password,
)
from app.config import settings
from app.database import get_db, write_lock
from app.models import User
from app.schemas import PasswordChange, Token, UserCreate, UserResponse

router = APIRouter(prefix="/api/v1/auth", tags=["Авторизация"])

EMAIL_TAKEN = "Пользователь с таким email уже существует"


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(data: UserCreate, request: Request, db: Session = Depends(get_db)):
    ip = ratelimit.client_ip(request)
    ratelimit.check(db, "register_ip", ip, settings.register_max_per_ip_per_hour, 3600,
                    "Слишком много регистраций с вашего адреса.")
    ratelimit.record(db, ("register_ip", ip))
    if db.scalar(select(User.id).where(User.email == data.email)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=EMAIL_TAKEN)

    user = User(email=data.email, hashed_password=get_password_hash(data.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:  # параллельная регистрация с тем же email
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=EMAIL_TAKEN) from None
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    request: Request,
    db: Session = Depends(get_db),
):
    # По стандарту OAuth2 поле называется username, в нём передаётся email
    email, ip = form.username.strip().lower(), ratelimit.client_ip(request)
    window = settings.login_window_minutes * 60
    # Проверка до сверки пароля: заблокированный перебор не тратит время сервера на bcrypt
    ratelimit.check(db, "login_email", email, settings.login_max_failures_per_email, window,
                    "Слишком много неудачных попыток входа для этого email.")
    ratelimit.check(db, "login_ip", ip, settings.login_max_failures_per_ip, window,
                    "Слишком много неудачных попыток входа с вашего адреса.")

    user = authenticate_user(db, email, form.password)
    if user is None:
        ratelimit.record(db, ("login_email", email), ("login_ip", ip))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль",
            headers={"WWW-Authenticate": "Bearer"},
        )
    ratelimit.clear(db, "login_email", email)  # владелец вошёл — счётчик ошибок по email сброшен
    return Token(access_token=create_access_token(user.id, token_version=user.token_version))


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/change-password", response_model=Token)
def change_password(
    data: PasswordChange,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Меняет пароль. Все ранее выданные токены (на других устройствах) перестают действовать;
    в ответе — новый токен для текущего сеанса."""
    if not verify_password(data.current_password, current_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Текущий пароль указан неверно")
    if data.new_password == data.current_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Новый пароль совпадает с текущим")
    with write_lock():
        current_user.hashed_password = get_password_hash(data.new_password)
        current_user.token_version += 1
        db.commit()
    return Token(access_token=create_access_token(current_user.id, token_version=current_user.token_version))
