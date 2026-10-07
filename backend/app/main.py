import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.database import get_db
from app.i18n import localize, localize_validation_errors, tr
from app.middleware import JsonCharsetMiddleware, LanguageMiddleware, PublicCorsMiddleware, SecurityHeadersMiddleware
from app.monitoring import init_sentry
from app.pagination import PAGINATION_HEADERS
from app.routers import (
    ads, ai, auth, campaigns, partners, payments, placements, plans, stats, telegram, users, wallet,
)

# Схема БД управляется миграциями Alembic: `alembic upgrade head` из папки backend/

logger = logging.getLogger(__name__)

# До создания приложения: Sentry подключается к FastAPI при его создании
init_sentry("api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from app.services.ai_cleanup import cleanup_stuck_ai_tasks, schedule_task_cleanup
    from app import payments
    payments.startup_check(logger)
    # 1. Сразу при запуске — задачи, прерванные прошлым перезапуском (резерв денег возвращается).
    #    Ошибка БД (например, миграции ещё не применены) не мешает серверу запуститься
    await cleanup_stuck_ai_tasks(timeout_minutes=settings.ai_task_timeout_minutes)
    # 2. Затем — периодически, пока сервер работает
    logger.info("Запуск фоновой очистки зависших AI-задач (каждые %s с)", settings.ai_cleanup_interval_seconds)
    cleanup_bg_task = asyncio.create_task(schedule_task_cleanup())
    # 3. Раз в сутки — удаление старых служебных записей (клики, попытки входа, завершённые AI-задачи)
    from app.maintenance import schedule_daily_purge
    purge_bg_task = asyncio.create_task(schedule_daily_purge())
    yield
    for task in (cleanup_bg_task, purge_bg_task):
        task.cancel()
    await asyncio.gather(cleanup_bg_task, purge_bg_task, return_exceptions=True)


app = FastAPI(
    title="Ad Platform API",
    version="1.0.0",
    description="API рекламной платформы",
    # API_DOCS=false — документации нет: адреса отвечают 404
    docs_url="/docs" if settings.api_docs else None,
    redoc_url="/redoc" if settings.api_docs else None,
    openapi_url="/openapi.json" if settings.api_docs else None,
    lifespan=lifespan,
)

# Порядок: последний добавленный выполняется первым (внешний слой)
app.add_middleware(LanguageMiddleware)
app.add_middleware(JsonCharsetMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
# CORS для фронтенда на другом домене: только домены из CORS_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,  # токен передаётся заголовком Authorization, cookie не используются
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Accept-Language"],
    expose_headers=PAGINATION_HEADERS,  # фронтенд читает признак «есть ещё» для подгрузки
    max_age=600,
)
# Выдача рекламы — с любых сайтов (снаружи CORSMiddleware, чтобы ответить на preflight первой)
app.add_middleware(PublicCorsMiddleware)
if settings.allowed_host_list != ["*"]:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)


# Ошибки — на языке запроса (Accept-Language). В коде сообщения пишутся по-русски, перевод — app/messages.py
@app.exception_handler(StarletteHTTPException)
async def localized_http_exception(request: Request, exc: StarletteHTTPException):
    if isinstance(exc.detail, str):
        exc = StarletteHTTPException(exc.status_code, localize(exc.detail), exc.headers)
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def localized_validation_error(request: Request, exc: RequestValidationError):
    # Тот же формат, что у FastAPI по умолчанию: {"detail": [{"loc", "msg", "type", …}]}
    return JSONResponse(status_code=422,
                        content={"detail": jsonable_encoder(localize_validation_errors(exc.errors()))})


STATIC_DIR = Path(__file__).parent / "static"

app.include_router(auth.router)
app.include_router(placements.router)
app.include_router(campaigns.router)
app.include_router(ads.router)
app.include_router(wallet.router)
app.include_router(users.router)
app.include_router(stats.router)
app.include_router(stats.analytics_router)
app.include_router(ai.router)
app.include_router(telegram.router)
app.include_router(telegram.bot_router)
app.include_router(payments.router)
app.include_router(plans.router)
app.include_router(partners.router)
app.include_router(partners.admin_router)

@app.get("/")
def read_root():
    return {
        "status": "online",
        "message": tr("Платформа полностью активна!"),
        "web_app": "/app",
        **({"docs": "/docs"} if settings.api_docs else {}),
    }

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/app", include_in_schema=False)
def web_app():
    # Веб-интерфейс: кабинет рекламодателя и админ-панель
    return FileResponse(STATIC_DIR / "ui" / "index.html", media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-cache"})


@app.get("/app/", include_in_schema=False)
def web_app_slash():
    return RedirectResponse("/app")


@app.get("/widget.js", include_in_schema=False)
def widget_js():
    # Кешируем на 5 минут: обновление виджета дойдёт до сайтов быстро
    return FileResponse(STATIC_DIR / "widget.js", media_type="application/javascript; charset=utf-8",
                        headers={"Cache-Control": "public, max-age=300"})


@app.get("/demo", include_in_schema=False)
def widget_demo():
    return FileResponse(STATIC_DIR / "demo.html", media_type="text/html; charset=utf-8")


@app.get("/api/v1/health")
def health_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok", "database": "ok"}
