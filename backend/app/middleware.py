"""Промежуточные слои (чистый ASGI — без накладных расходов @app.middleware("http"))."""
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Публичная часть: виджет на любом сайте-партнёре запрашивает рекламу и переходит по кликам
PUBLIC_PREFIXES = ("/api/v1/ad/", "/widget.js")

# Веб-интерфейс /app: только свои скрипты и стили; картинки объявлений — с любых https/http
APP_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "   # style="" в разметке графиков и легенд
    "img-src 'self' data: https: http:; "  # картинки рекламодателей лежат на их серверах
    "connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
)
# API отдаёт только JSON: ничего не исполняется и не встраивается
API_CSP = "default-src 'none'; frame-ancestors 'none'"


def _is_public(path: str) -> bool:
    return path.startswith(PUBLIC_PREFIXES)


class PublicCorsMiddleware:
    """CORS для публичной выдачи рекламы: доступна с любого сайта.

    Основной CORSMiddleware пускает к API только домены фронтенда (CORS_ORIGINS), а виджет
    вставляют на произвольные сайты. Cookie и токены здесь не используются, поэтому «*» безопасно.
    Стоит снаружи CORSMiddleware: отвечает на preflight раньше, чем тот отклонит чужой домен.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or not _is_public(scope["path"]):
            return await self.app(scope, receive, send)

        headers = dict(scope["headers"])
        if scope["method"] == "OPTIONS" and b"access-control-request-method" in headers:
            await send({"type": "http.response.start", "status": 204, "headers": [
                (b"access-control-allow-origin", b"*"),
                (b"access-control-allow-methods", b"GET, OPTIONS"),
                (b"access-control-allow-headers", b"Content-Type"),
                (b"access-control-max-age", b"600"),
            ]})
            await send({"type": "http.response.body", "body": b""})
            return

        async def send_public(message: Message):
            if message["type"] == "http.response.start":
                h = MutableHeaders(scope=message)
                h["access-control-allow-origin"] = "*"
                # Без cookie/токенов: «*» и credentials вместе браузер всё равно запретит
                if "access-control-allow-credentials" in h:
                    del h["access-control-allow-credentials"]
            await send(message)

        await self.app(scope, receive, send_public)


class SecurityHeadersMiddleware:
    """Заголовки безопасности для всех ответов."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]

        async def send_secure(message: Message):
            if message["type"] == "http.response.start":
                h = MutableHeaders(scope=message)
                # Браузер не угадывает тип: JSON не исполнится как скрипт или HTML
                h.setdefault("X-Content-Type-Options", "nosniff")
                # Не встраивать в чужие iframe (кликджекинг)
                h.setdefault("X-Frame-Options", "DENY")
                h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
                if path == "/app" or path.startswith("/static/ui/"):
                    h.setdefault("Content-Security-Policy", APP_CSP)
                    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
                elif path.startswith("/api/"):
                    h.setdefault("Content-Security-Policy", API_CSP)
                # HSTS только по HTTPS (за прокси — при uvicorn --proxy-headers):
                # по HTTP браузер его игнорирует, а на localhost сломал бы разработку
                if scope.get("scheme") == "https":
                    h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
            await send(message)

        await self.app(scope, receive, send_secure)


class JsonCharsetMiddleware:
    """Добавляет charset=utf-8 к application/json.

    Без charset Windows PowerShell 5.1 читает ответ как ISO-8859-1 и портит кириллицу.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_charset(message: Message):
            if message["type"] == "http.response.start":
                message["headers"] = [
                    (k, b"application/json; charset=utf-8")
                    if k == b"content-type" and v == b"application/json" else (k, v)
                    for k, v in message["headers"]
                ]
            await send(message)

        await self.app(scope, receive, send_with_charset)
