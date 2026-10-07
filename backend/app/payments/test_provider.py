"""Тестовый провайдер: вместо банка — страница платформы «Оплатить / Отменить». Деньги НЕнастоящие.

Только для разработки и демонстрации: PAYMENTS_PROVIDER=test. Уведомления подписываются HMAC-SHA256
(PAYMENTS_TEST_SECRET, иначе SECRET_KEY) — как у настоящих провайдеров, путь зачисления тот же.
"""
import hashlib
import hmac
import json
from decimal import Decimal, InvalidOperation

from app.config import settings
from app.models import Payment, PaymentStatus
from app.payments.base import CreatedPayment, PaymentProvider, WebhookEvent, WebhookRejected

SIGNATURE_HEADER = "x-test-signature"
_STATUSES = {"succeeded": PaymentStatus.SUCCEEDED, "canceled": PaymentStatus.CANCELED,
             "failed": PaymentStatus.FAILED, "refunded": PaymentStatus.REFUNDED}


def _secret() -> bytes:
    return (settings.payments_test_secret or settings.secret_key).encode()


def sign(body: bytes) -> str:
    return hmac.new(_secret(), body, hashlib.sha256).hexdigest()


def webhook_body(provider_payment_id: str, status: str, amount: Decimal) -> bytes:
    """Тело уведомления — как его прислал бы провайдер (для страницы оплаты и тестов)."""
    return json.dumps({"id": provider_payment_id, "status": status, "amount": str(amount)},
                      separators=(",", ":")).encode()


class TestProvider(PaymentProvider):
    name = "test"
    __test__ = False  # не тестовый класс pytest

    def create_payment(self, payment: Payment, description: str, return_url: str) -> CreatedPayment:
        # Страница «оплаты» — у самой платформы, поэтому адрес относительный (тот же домен)
        return CreatedPayment(provider_payment_id=f"test_{payment.id}",
                              confirmation_url=f"/api/v1/payments/test-checkout/{payment.id}")

    def parse_webhook(self, body: bytes, headers: dict[str, str]) -> WebhookEvent:
        signature = headers.get(SIGNATURE_HEADER, "")
        if not hmac.compare_digest(signature.encode(), sign(body).encode()):
            raise WebhookRejected("неверная подпись")
        try:
            data = json.loads(body)
            return WebhookEvent(provider_payment_id=str(data["id"]), status=_STATUSES[data["status"]],
                                amount=Decimal(data["amount"]) if data.get("amount") is not None else None)
        except (ValueError, KeyError, TypeError, InvalidOperation) as e:
            raise WebhookRejected("непонятный формат уведомления") from e
