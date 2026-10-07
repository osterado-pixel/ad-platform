"""Telegram-бот (папка bot/): привязка аккаунта и действия бота от имени пользователя.

Привязка без пароля в Telegram:
1. В кабинете пользователь получает одноразовый код (POST /api/v1/telegram/link-code), действует
   TELEGRAM_LINK_CODE_MINUTES минут; храним только HMAC кода.
2. Отправляет его боту (/start <код> или ссылкой t.me/<бот>?start=<код>), бот вызывает POST /api/v1/bot/link.
3. Дальше бот вызывает /api/v1/bot/* с telegram_id пользователя.

/api/v1/bot/* закрыты общим секретом бота и API (заголовок X-Bot-Secret): без него telegram_id
мог бы подставить кто угодно. Секрет не задан — бот выключен, эти адреса отвечают 404.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import ratelimit
from app.auth import get_current_user
from app.config import settings
from app.database import get_db, write_lock
from app.models import AITask, TelegramLinkCode, User
from app.routers.ai import queue_ad_generation, task_response
from app.schemas import (
    AITaskCreated, AITaskResponse, BotGenerateRequest, BotLinkRequest, BotMe, BotUserRequest,
    TelegramLinkCodeResponse, TelegramStatus,
)

router = APIRouter(prefix="/api/v1/telegram", tags=["Telegram"])
bot_router = APIRouter(prefix="/api/v1/bot", tags=["Telegram-бот (только для бота)"])

# Без похожих символов (0/O, 1/I/L): код набирают вручную
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8  # 31^8 ≈ 8,5·10^11 вариантов при ≤ 10 попытках за 15 минут на один Telegram
LINK_ATTEMPTS = 10
LINK_WINDOW_SECONDS = 15 * 60


def _code_hash(code: str) -> str:
    normalized = code.strip().upper().replace("-", "").replace(" ", "")
    return hmac.new(settings.secret_key.encode(), f"tg-link:{normalized}".encode(), hashlib.sha256).hexdigest()


def _bot_enabled() -> bool:
    return bool(settings.telegram_bot_secret)


# --- Кабинет: получить код привязки, отвязать ---
@router.get("/status", response_model=TelegramStatus)
def telegram_status(current_user: User = Depends(get_current_user)):
    return TelegramStatus(enabled=_bot_enabled(), bot_username=settings.telegram_bot_username.lstrip("@") or None,
                          linked=current_user.telegram_linked)


@router.post("/link-code", response_model=TelegramLinkCodeResponse)
def create_link_code(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Новый одноразовый код (прежний перестаёт действовать)."""
    if not _bot_enabled():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Telegram-бот не подключён")
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.telegram_link_code_minutes)
    with write_lock():
        # Один код на пользователя: прежний заменяется новым (старый перестаёт действовать)
        link = db.scalar(select(TelegramLinkCode).where(TelegramLinkCode.user_id == current_user.id))
        if link is None:
            db.add(TelegramLinkCode(user_id=current_user.id, code_hash=_code_hash(code), expires_at=expires_at))
        else:
            link.code_hash, link.expires_at = _code_hash(code), expires_at
        db.commit()
    username = settings.telegram_bot_username.lstrip("@")
    return TelegramLinkCodeResponse(code=code, expires_at=expires_at,
                                    deep_link=f"https://t.me/{username}?start={code}" if username else None)


@router.delete("/link", status_code=status.HTTP_204_NO_CONTENT)
def unlink_from_account(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    with write_lock():
        db.execute(update(User).where(User.id == current_user.id).values(telegram_id=None))
        db.commit()


# --- Бот ---
def require_bot(x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret")) -> None:
    if not _bot_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    # compare_digest — время сравнения не подсказывает, сколько символов совпало
    if not x_bot_secret or not hmac.compare_digest(x_bot_secret.encode(), settings.telegram_bot_secret.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный секрет бота")


def _linked_user(db: Session, telegram_id: int) -> User:
    user = db.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Telegram не привязан к аккаунту: получите код в кабинете и отправьте его боту")
    return user


@bot_router.post("/link", response_model=BotMe, dependencies=[Depends(require_bot)])
def bot_link(body: BotLinkRequest, db: Session = Depends(get_db)):
    tg = str(body.telegram_id)
    ratelimit.check(db, "tg_link", tg, LINK_ATTEMPTS, LINK_WINDOW_SECONDS,
                    "Слишком много неверных кодов — попробуйте через 15 минут")
    now = datetime.now(timezone.utc)
    link = db.scalar(select(TelegramLinkCode).where(
        TelegramLinkCode.code_hash == _code_hash(body.code), TelegramLinkCode.expires_at > now))
    if link is None:
        ratelimit.record(db, ("tg_link", tg))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Код неверный или устарел — получите новый в кабинете")
    other = db.scalar(select(User).where(User.telegram_id == body.telegram_id, User.id != link.user_id))
    if other is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Этот Telegram уже привязан к другому аккаунту — сначала отвяжите его (/unlink)")
    with write_lock():
        db.execute(update(User).where(User.id == link.user_id).values(telegram_id=body.telegram_id))
        db.delete(link)  # код одноразовый
        try:
            db.commit()
        except IntegrityError:  # одновременно привязали к другому аккаунту
            db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="Этот Telegram уже привязан к другому аккаунту") from None
    ratelimit.clear(db, "tg_link", tg)
    user = db.get(User, link.user_id)
    db.refresh(user)
    return BotMe(email=user.email, balance=user.balance, held_balance=user.held_balance)


@bot_router.post("/unlink", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_bot)])
def bot_unlink(body: BotUserRequest, db: Session = Depends(get_db)):
    user = _linked_user(db, body.telegram_id)
    with write_lock():
        db.execute(update(User).where(User.id == user.id).values(telegram_id=None))
        db.commit()


@bot_router.get("/me", response_model=BotMe, dependencies=[Depends(require_bot)])
def bot_me(telegram_id: int = Query(gt=0), db: Session = Depends(get_db)):
    user = _linked_user(db, telegram_id)
    return BotMe(email=user.email, balance=user.balance, held_balance=user.held_balance)


@bot_router.post("/generate", response_model=AITaskCreated, status_code=status.HTTP_202_ACCEPTED,
                 dependencies=[Depends(require_bot)])
def bot_generate(body: BotGenerateRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """То же, что POST /ai/generate-async у сайта: модерация, лимит задач, заморозка денег."""
    user = _linked_user(db, body.telegram_id)
    return queue_ad_generation(db, user.id, body, background_tasks, status_url_prefix="/api/v1/bot/tasks")


@bot_router.get("/tasks/{task_id}", response_model=AITaskResponse, dependencies=[Depends(require_bot)])
def bot_task(task_id: str, telegram_id: int = Query(gt=0), db: Session = Depends(get_db)):
    user = _linked_user(db, telegram_id)
    task = db.get(AITask, task_id)
    if task is None or task.user_id != user.id:  # чужая задача — как несуществующая
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задача не найдена")
    return task_response(task)
