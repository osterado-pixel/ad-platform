"""Приём платежей: пополнение баланса картой, уведомления провайдера, тестовая страница оплаты."""
import html

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.i18n import current_language, tr
from app.models import Payment, PaymentPurpose, PaymentStatus, User
from app.pagination import fetch_page_with_total, limit_param, offset_param
from app.payments import PaymentProviderError, WebhookRejected, get_provider
from app.payments import test_provider
from app.schemas import PaginatedResponse, PaymentResponse, PaymentsConfig, TopUpRequest
from app.services import payments_service

router = APIRouter(prefix="/api/v1/payments", tags=["Платежи"])


def site_url(request: Request) -> str:
    """Адрес сайта для возврата после оплаты: PUBLIC_URL, иначе — адрес, по которому пришёл запрос."""
    return settings.public_url.rstrip("/") or str(request.base_url).rstrip("/")


def _disabled() -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                         detail="Приём платежей не подключён — пополнение через администратора")


@router.get("/config", response_model=PaymentsConfig)
def payments_config(_user: User = Depends(get_current_user)):
    provider = get_provider()
    return PaymentsConfig(enabled=provider is not None, provider=provider.name if provider else None,
                          test_mode=provider is not None and provider.name == "test",
                          currency=settings.payments_currency, min_amount=settings.payments_min_amount,
                          max_amount=settings.payments_max_amount)


@router.post("/top-up", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
def top_up(body: TopUpRequest, request: Request, db: Session = Depends(get_db),
           current_user: User = Depends(get_current_user)):
    """Пополнение баланса: платёж у провайдера → перейти по confirmation_url. Деньги зачислятся
    по уведомлению провайдера об оплате."""
    if not settings.payments_min_amount <= body.amount <= settings.payments_max_amount:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"Сумма — от {settings.payments_min_amount} до {settings.payments_max_amount}")
    try:
        return payments_service.create_payment(db, current_user.id, PaymentPurpose.TOP_UP, body.amount,
                                               return_url=f"{site_url(request)}/app#/wallet")
    except payments_service.PaymentsDisabled:
        raise _disabled() from None
    except PaymentProviderError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail="Платёжная система не ответила — попробуйте позже") from None


@router.get("", response_model=PaginatedResponse[PaymentResponse])
def my_payments(limit: int = limit_param(default=20, maximum=100), offset: int = offset_param(),
                db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    query = select(Payment).where(Payment.user_id == current_user.id).order_by(Payment.created_at.desc(),
                                                                             Payment.id.desc())
    return fetch_page_with_total(db, query, limit, offset)


@router.get("/{payment_id}", response_model=PaymentResponse)
def get_payment(payment_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    payment = db.get(Payment, payment_id)
    if payment is None or payment.user_id != current_user.id:  # чужой — как несуществующий
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Платёж не найден")
    return payment


@router.post("/webhook/{provider_name}", status_code=status.HTTP_200_OK)
async def webhook(provider_name: str, request: Request, db: Session = Depends(get_db)):
    """Уведомление провайдера об изменении платежа. Принимается только от включённого провайдера
    и только с верной подписью; повторы безопасны."""
    provider = get_provider()
    if provider is None or provider.name != provider_name:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    body = await request.body()  # подпись считается по «сырому» телу — до разбора JSON
    try:
        event = provider.parse_webhook(body, {k.lower(): v for k, v in request.headers.items()})
        # Работа с БД синхронная — в пуле потоков, чтобы не останавливать сервер на время запроса
        await run_in_threadpool(payments_service.apply_event, db, provider.name, event)
    except WebhookRejected as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from None
    return {"ok": True}


# --- Тестовый провайдер: страница «оплаты» вместо банка (только PAYMENTS_PROVIDER=test) ---
def _test_payment(db: Session, payment_id: str) -> Payment:
    provider = get_provider()
    payment = db.get(Payment, payment_id)
    if provider is None or provider.name != "test" or payment is None or payment.provider != "test":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    return payment


@router.get("/test-checkout/{payment_id}", response_class=HTMLResponse, include_in_schema=False)
def test_checkout_page(payment_id: str, db: Session = Depends(get_db)):
    payment = _test_payment(db, payment_id)
    amount = html.escape(f"{payment.amount:.2f} {payment.currency}")
    # Язык страницы — язык браузера (Accept-Language); тексты переводов — наши, без пользовательского ввода
    title = tr("Тестовая оплата")
    if payment.status != PaymentStatus.PENDING:
        body = (f"<p>{tr('Платёж уже обработан:')} <b>{html.escape(payment.status.value)}</b>.</p>"
                f"<p><a href='/app#/wallet'>{tr('В кошелёк')}</a></p>")
    else:
        body = (f"<p>{tr('Сумма:')} <b>{amount}</b></p>"
                f"<form method='post'><button name='result' value='succeeded'>{tr('Оплатить')}</button> "
                f"<button name='result' value='canceled'>{tr('Отменить')}</button></form>")
    return HTMLResponse(
        f"<!doctype html><html lang='{current_language()}'><head><meta charset='utf-8'><meta name='viewport' "
        f"content='width=device-width,initial-scale=1'><title>{title}</title></head>"
        "<body style='font-family:system-ui;max-width:420px;margin:48px auto;padding:0 16px'>"
        f"<h1>{title}</h1><p style='color:#b45309'><b>{tr('Деньги ненастоящие')}</b> — "
        f"{tr('тестовый режим платежей (PAYMENTS_PROVIDER=test).')}</p>" + body + "</body></html>",
        # Своя политика вместо API-шной default-src 'none': встроенные стили страницы и отправка формы —
        # только себе; скрипты, картинки, встраивание во фреймы — по-прежнему запрещены
        headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                                            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})


@router.post("/test-checkout/{payment_id}", include_in_schema=False)
def test_checkout_submit(payment_id: str, result: str = Form(), db: Session = Depends(get_db)):
    """Имитация банка: подписанное уведомление — тем же путём, что от настоящего провайдера."""
    payment = _test_payment(db, payment_id)
    if result not in ("succeeded", "canceled"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Неизвестный результат")
    body = test_provider.webhook_body(payment.provider_payment_id, result, payment.amount)
    provider = get_provider()
    event = provider.parse_webhook(body, {test_provider.SIGNATURE_HEADER: test_provider.sign(body)})
    payments_service.apply_event(db, provider.name, event)
    return RedirectResponse("/app#/wallet", status_code=status.HTTP_303_SEE_OTHER)
