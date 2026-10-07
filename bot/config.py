"""Настройки бота — из переменных окружения (в Docker их задаёт docker-compose)."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    bot_token: str        # от @BotFather
    api_url: str          # адрес бэкенда платформы (в Docker — http://api:8000)
    bot_secret: str       # общий секрет бота и API: тот же TELEGRAM_BOT_SECRET, что у бэкенда
    site_url: str         # адрес сайта для ссылок в сообщениях (кабинет — site_url + /app)


def load_config() -> Config:
    missing = [name for name in ("BOT_TOKEN", "TELEGRAM_BOT_SECRET") if not os.environ.get(name)]
    if missing:
        raise SystemExit(f"Не заданы переменные окружения: {', '.join(missing)}")
    secret = os.environ["TELEGRAM_BOT_SECRET"]
    if len(secret) < 32:
        raise SystemExit("TELEGRAM_BOT_SECRET должен быть не короче 32 символов (тот же, что у бэкенда)")
    return Config(
        bot_token=os.environ["BOT_TOKEN"],
        api_url=os.environ.get("API_URL", "http://api:8000").rstrip("/"),
        bot_secret=secret,
        site_url=os.environ.get("SITE_URL", "").rstrip("/"),
    )
