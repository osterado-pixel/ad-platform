from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.middleware import JsonCharsetMiddleware, PublicCorsMiddleware, SecurityHeadersMiddleware
from app.pagination import PAGINATION_HEADERS
from app.routers import ads, auth, campaigns, placements, stats, users, wallet

# Схема БД управляется миграциями Alembic: `alembic upgrade head` из папки backend/

app = FastAPI(
    title="Ad Platform API",
    version="1.0.0",
    description="API рекламной платформы"
)

# Порядок: последний добавленный выполняется первым (внешний слой)
app.add_middleware(JsonCharsetMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
# CORS для фронтенда на другом домене: только домены из CORS_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,  # токен передаётся заголовком Authorization, cookie не используются
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=PAGINATION_HEADERS,  # фронтенд читает признак «есть ещё» для подгрузки
    max_age=600,
)
# Выдача рекламы — с любых сайтов (снаружи CORSMiddleware, чтобы ответить на preflight первой)
app.add_middleware(PublicCorsMiddleware)
if settings.allowed_host_list != ["*"]:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)

STATIC_DIR = Path(__file__).parent / "static"

app.include_router(auth.router)
app.include_router(placements.router)
app.include_router(campaigns.router)
app.include_router(ads.router)
app.include_router(wallet.router)
app.include_router(users.router)
app.include_router(stats.router)
app.include_router(stats.analytics_router)

@app.get("/")
def read_root():
    return {
        "status": "online",
        "message": "Платформа полностью активна!",
        "web_app": "/app",
        "docs": "/docs",
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
