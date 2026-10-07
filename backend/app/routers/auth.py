import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import ratelimit
from app.auth import (
    authenticate_user, create_access_token, get_current_user, get_password_hash, verify_password,
)
from app.config import settings
from app.database import get_db, write_lock
from app.i18n import current_language, tr
from app.models import PasswordResetToken, User
from app.services import mailer
from app.schemas import ForgotPassword, PasswordChange, ResetPassword, Token, UserCreate, UserResponse
from app.services.partners import find_referrer

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

    user = User(email=data.email, hashed_password=get_password_hash(data.password),
                referred_by_id=find_referrer(db, data.ref), language=current_language())
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
    if user.language != current_language():  # письма — на языке, которым пользуются сейчас
        with write_lock():
            user.language = current_language()
            db.commit()
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


# --- Восстановление пароля по email ---
RESET_MAX_PER_EMAIL_PER_HOUR = 3
RESET_MAX_PER_IP_PER_HOUR = 10
RESET_SENT = "Если такой email зарегистрирован, мы отправили на него ссылку для смены пароля"
RESET_INVALID = "Ссылка для смены пароля недействительна или устарела — запросите новую"


def _reset_hash(token: str) -> str:
    return hmac.new(settings.secret_key.encode(), f"pw-reset:{token}".encode(), hashlib.sha256).hexdigest()


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
def forgot_password(data: ForgotPassword, request: Request, background: BackgroundTasks,
                    db: Session = Depends(get_db)):
    """Письмо со ссылкой для смены пароля. Ответ одинаковый, есть такой email или нет:
    по нему нельзя узнать, кто зарегистрирован. Прежняя ссылка перестаёт действовать."""
    ip = ratelimit.client_ip(request)
    ratelimit.check(db, "reset_ip", ip, RESET_MAX_PER_IP_PER_HOUR, 3600,
                    "Слишком много запросов на смену пароля с вашего адреса.")
    ratelimit.check(db, "reset_email", data.email, RESET_MAX_PER_EMAIL_PER_HOUR, 3600,
                    "Слишком много запросов на смену пароля для этого email.")
    ratelimit.record(db, ("reset_ip", ip), ("reset_email", data.email))

    user = db.scalar(select(User).where(User.email == data.email))
    if user is not None:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.password_reset_minutes)
        with write_lock():
            db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == user.id))
            db.add(PasswordResetToken(user_id=user.id, token_hash=_reset_hash(token), expires_at=expires_at))
            db.commit()
        base = settings.public_url.rstrip("/") or str(request.base_url).rstrip("/")
        # Токен — после #: часть адреса после # браузер не отправляет на сервер (и в журналы прокси)
        link = f"{base}/app#/reset/{token}"
        # Текст — на языке запроса; собираем сейчас: фоновая задача выполняется после ответа
        subject = tr("Смена пароля в Ad Platform")
        body = tr("Чтобы задать новый пароль, откройте ссылку (действует {minutes} мин.):",
                  minutes=settings.password_reset_minutes) + f"\n\n{link}\n\n" + tr(
            "Если вы не запрашивали смену пароля, просто проигнорируйте это письмо.")
        background.add_task(mailer.send_email, user.email, subject, body)
    return {"detail": tr(RESET_SENT)}


@router.post("/reset-password", response_model=Token)
def reset_password(data: ResetPassword, db: Session = Depends(get_db)):
    """Новый пароль по ссылке из письма. Все прежние сеансы завершаются; в ответе — токен для входа."""
    row = db.scalar(select(PasswordResetToken).where(
        PasswordResetToken.token_hash == _reset_hash(data.token),
        PasswordResetToken.expires_at > datetime.now(timezone.utc)))
    if row is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=RESET_INVALID)
    user = db.get(User, row.user_id)
    with write_lock():
        # Удаление — условием по id: две одновременные отправки одной ссылки не сменят пароль дважды
        if not db.execute(delete(PasswordResetToken).where(PasswordResetToken.id == row.id)).rowcount:
            db.rollback()
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=RESET_INVALID)
        user.hashed_password = get_password_hash(data.new_password)
        user.token_version += 1
        db.commit()
    ratelimit.clear(db, "login_email", user.email)  # владелец подтвердил почту — блокировка входа снята
    return Token(access_token=create_access_token(user.id, token_version=user.token_version))
