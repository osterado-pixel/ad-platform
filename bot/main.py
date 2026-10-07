"""Telegram-бот Ad Platform: баланс и AI-копирайтер в Telegram.

Запуск: python main.py (переменные окружения — config.py). Режим long polling: бот сам забирает
обновления у Telegram, открытый порт и HTTPS ему не нужны.
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand

from api_client import PlatformAPI
from config import load_config
from handlers import build_router

COMMANDS = [
    BotCommand(command="generate", description="Составить объявление"),
    BotCommand(command="balance", description="Баланс"),
    BotCommand(command="link", description="Привязать аккаунт кодом"),
    BotCommand(command="unlink", description="Отвязать аккаунт"),
    BotCommand(command="help", description="Что умеет бот"),
]


def build_dispatcher(api: PlatformAPI, site_url: str) -> Dispatcher:
    # api и site_url попадают в обработчики аргументами с теми же именами
    dp = Dispatcher(api=api, site_url=site_url)
    dp.include_router(build_router())
    return dp


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    config = load_config()
    api = PlatformAPI(config.api_url, config.bot_secret)
    # Без parse_mode: сообщения — обычный текст, разметка из текста объявлений не сработает
    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=None))
    dp = build_dispatcher(api, config.site_url)
    try:
        await bot.set_my_commands(COMMANDS)
        await dp.start_polling(bot)  # останавливается по Ctrl+C / docker stop
    finally:
        await api.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
