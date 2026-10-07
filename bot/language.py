"""Язык пользователя для каждого сообщения: тексты бота (t) и клиент API с Accept-Language (api)."""
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from texts import Texts, language_of


class LanguageMiddleware(BaseMiddleware):
    """Обработчики получают t — тексты на языке из настроек Telegram — и api, отвечающий на том же языке."""

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        lang = language_of(user.language_code if user else None)
        data["t"] = Texts(lang)
        data["api"] = data["api"].with_language(lang)
        return await handler(event, data)
