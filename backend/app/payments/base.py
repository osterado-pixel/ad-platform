"""Интерфейс платёжного провайдера. Новый провайдер (ЮKassa, Stripe, CloudPayments…) — класс-наследник
PaymentProvider в этой папке и строка в PROVIDERS (app/payments/__init__.py). Остальной код
(платежи, зачисление, тарифы, уведомления) не меняется.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

from app.models import Payment, PaymentStatus


class PaymentProviderError(Exception):
    """Провайдер недоступен или отклонил создание платежа — пользователю: «попробуйте позже»."""


class WebhookRejected(Exception):
    """Уведомление не от провайдера (неверная подпись) или непонятного формата — отвечаем 400."""


@dataclass(frozen=True)
class CreatedPayment:
    provider_payment_id: str
    confirmation_url: str  # куда отправить пользователя для оплаты


@dataclass(frozen=True)
class WebhookEvent:
    """Уведомление провайдера, приведённое к общему виду."""
    provider_payment_id: str
    status: PaymentStatus
    amount: Decimal | None = None   # для сверки с суммой платежа (если провайдер её присылает)
    currency: str | None = None


class PaymentProvider(ABC):
    name: str

    @abstractmethod
    def create_payment(self, payment: Payment, description: str, return_url: str) -> CreatedPayment:
        """Создать платёж у провайдера. PaymentProviderError — если не получилось."""

    @abstractmethod
    def parse_webhook(self, body: bytes, headers: dict[str, str]) -> WebhookEvent:
        """Проверить подлинность уведомления (подпись / IP / повторный запрос к API провайдера)
        и привести его к WebhookEvent. WebhookRejected — если уведомление не подлинное."""
