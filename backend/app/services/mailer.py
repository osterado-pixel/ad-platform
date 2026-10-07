"""Отправка писем по SMTP (восстановление пароля).

Настройки — SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_STARTTLS (см. app/config.py).
SMTP_HOST не задан — письмо не отправляется, а пишется в журнал сервера (уровень INFO): так можно
проверить восстановление пароля локально, без почтового сервиса.

Письма отправляются в фоне (BackgroundTasks): ответ API не ждёт почтовый сервер, а ошибка отправки
не видна пользователю (иначе по ней можно было бы узнать, зарегистрирован ли email) — она в журнале.
"""
import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

log = logging.getLogger(__name__)


def send_email(to: str, subject: str, body: str) -> bool:
    """True — письмо передано почтовому серверу."""
    if not settings.smtp_host:
        log.info("SMTP не настроен (SMTP_HOST) — письмо не отправлено.\nКому: %s\nТема: %s\n\n%s", to, subject, body)
        return False
    msg = EmailMessage()
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        # 465 — сразу TLS (SMTPS); иначе (587, 25) — обычное соединение и STARTTLS
        smtp_cls = smtplib.SMTP_SSL if settings.smtp_port == 465 else smtplib.SMTP
        with smtp_cls(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            if smtp_cls is smtplib.SMTP and settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError):
        log.exception("Не удалось отправить письмо (%s)", subject)
        return False
    return True
