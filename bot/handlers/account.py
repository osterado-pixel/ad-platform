"""/balance, /unlink."""
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

import texts
from api_client import ApiError, PlatformAPI


async def balance(message: Message, api: PlatformAPI, site_url: str) -> None:
    try:
        account = await api.me(message.from_user.id)
    except ApiError as e:
        await message.answer(texts.not_linked(site_url) if e.status == 404 else e.detail)
        return
    text = f"Аккаунт: {account.email}\nБаланс: {texts.money(account.balance)}"
    if account.held_balance > 0:
        text += f"\nЗаморожено под генерации: {texts.money(account.held_balance)}"
    await message.answer(text)


async def unlink(message: Message, api: PlatformAPI, site_url: str) -> None:
    try:
        await api.unlink(message.from_user.id)
    except ApiError as e:
        await message.answer("Telegram и так не привязан." if e.status == 404 else e.detail)
        return
    await message.answer("Аккаунт отвязан. Привязать снова — кодом из профиля в " + texts.cabinet(site_url))


def register(router: Router) -> None:
    """Обработчики модуля — в переданный роутер (порядок важен: фильтры проверяются по очереди)."""
    router.message.register(balance, Command("balance"))
    router.message.register(unlink, Command("unlink"))
