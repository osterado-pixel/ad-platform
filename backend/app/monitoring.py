"""Sentry: оповещения об ошибках веб-сервера и Celery-воркера.

Включается переменной SENTRY_DSN; без неё ничего не отправляется. В Sentry попадают:
- необработанные исключения в запросах (FastAPI) и задачах Celery;
- записи лога уровня ERROR — например, log.exception «непредвиденная ошибка» фоновой AI-генерации.
Ожидаемые сбои (лимит Gemini, отказ модерации) пишутся как WARNING и в оповещения не попадают —
только в «хлебные крошки» события, чтобы было видно, что происходило перед ошибкой.
"""
import logging

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

from app.config import settings

log = logging.getLogger(__name__)


def init_sentry(component: str) -> bool:
    """Включает Sentry, если задан SENTRY_DSN. component — «api» или «worker» (тег событий)."""
    if not settings.sentry_dsn:
        return False
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        # Без личных данных: IP посетителей, cookie, тела запросов. Заголовки с токенами, пароли и
        # ключи Sentry вырезает и так (стандартный список чувствительных полей)
        send_default_pii=False,
        # Без значений локальных переменных в стеке: там бывают сырые заголовки запроса (токен входа —
        # проверено тестом), пароли, ключи API и строки из БД. Тип ошибки, сообщение и строки кода остаются
        include_local_variables=False,
        # ERROR и выше — событие (оповещение), INFO и выше — «хлебные крошки» к нему.
        # Интеграции FastAPI и Celery включаются сами: пакеты установлены
        integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
    )
    sentry_sdk.set_tag("component", component)
    log.info("Sentry включён (%s, окружение %s)", component, settings.sentry_environment)
    return True
