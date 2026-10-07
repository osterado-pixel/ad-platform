"""Платёжные провайдеры. Выбор — настройка PAYMENTS_PROVIDER; пусто — приём платежей выключен."""
from app.config import settings
from app.payments.base import (  # noqa: F401 — общий интерфейс для вызывающего кода
    CreatedPayment, PaymentProvider, PaymentProviderError, WebhookEvent, WebhookRejected,
)
from app.payments.test_provider import TestProvider

# Новый провайдер — сюда: "yookassa": YooKassaProvider, "stripe": StripeProvider …
PROVIDERS: dict[str, type[PaymentProvider]] = {
    "test": TestProvider,
}


def get_provider(name: str | None = None) -> PaymentProvider | None:
    """Провайдер по имени (по умолчанию — из настроек). None — выключено или неизвестно."""
    cls = PROVIDERS.get((settings.payments_provider if name is None else name).strip().lower())
    return cls() if cls else None


def is_enabled() -> bool:
    return get_provider() is not None


def startup_check(log) -> None:
    """Предупреждения при запуске: тестовые деньги и опечатка в имени провайдера видны сразу."""
    name = settings.payments_provider.strip().lower()
    if not name:
        return
    if name not in PROVIDERS:
        log.error("PAYMENTS_PROVIDER=%r не поддерживается (есть: %s) — приём платежей выключен",
                  settings.payments_provider, ", ".join(PROVIDERS))
    elif name == "test":
        log.warning("ТЕСТОВЫЙ режим платежей (PAYMENTS_PROVIDER=test): деньги ненастоящие, "
                    "оплату может подтвердить любой — не включайте на боевом сервере")
