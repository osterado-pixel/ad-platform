from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User

# bcrypt учитывает только первые 72 байта пароля (не символа: кириллица — 2 байта)
BCRYPT_MAX_BYTES = 72

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_password_hash(password: str) -> str:
    password_bytes = password.encode("utf-8")
    if len(password_bytes) > BCRYPT_MAX_BYTES:
        raise ValueError(f"Пароль длиннее {BCRYPT_MAX_BYTES} байт")
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt(rounds=settings.bcrypt_rounds)).decode("ascii")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    password_bytes = plain_password.encode("utf-8")
    if len(password_bytes) > BCRYPT_MAX_BYTES:
        return False
    try:
        return bcrypt.checkpw(password_bytes, hashed_password.encode("ascii"))
    except ValueError:  # повреждённый хеш в БД
        return False


# Хеш для сравнения, когда пользователя нет: время ответа не выдаёт, существует ли email
_DUMMY_HASH = get_password_hash("dummy-password-for-timing")


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.email == email.lower()))
    if user is None:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


def create_access_token(user_id: int, expires_delta: timedelta | None = None, token_version: int = 0) -> str:
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    # sub — id, а не email: токен не ломается при смене email.
    # ver — версия токенов пользователя: после смены пароля старые токены недействительны
    payload = {"sub": str(user_id), "ver": token_version, "iat": now, "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Не удалось проверить учетные данные",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp"]},
        )
        user_id = int(payload["sub"])
        version = int(payload.get("ver", 0))
    except (jwt.PyJWTError, ValueError, TypeError):
        raise credentials_exception from None

    user = db.get(User, user_id)
    if user is None or user.token_version != version:  # пароль сменили — токен отозван
        raise credentials_exception
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Проверяет, обладает ли текущий аутентифицированный пользователь правами администратора."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав. Требуются права администратора.",
        )
    return current_user


# Имя из учебной инструкции — та же самая зависимость (одна проверка прав, а не две копии)
get_current_admin = require_admin
