"""/balance, /unlink."""
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from api_client import ApiError, PlatformAPI
from texts import Texts


async def balance(message: Message, api: PlatformAPI, site_url: str, t: Texts) -> None:
    try:
        account = await api.me(message.from_user.id)
    except ApiError as e:
        await message.answer(t.not_linked(site_url) if e.status == 404 else e.detail)
        return
    text = t("balance", email=account.email, balance=t.money(account.balance))
    if account.held_balance > 0:
        text += "\n" + t("held", amount=t.money(account.held_balance))
    await message.answer(text)


async def unlink(message: Message, api: PlatformAPI, site_url: str, t: Texts) -> None:
    try:
        await api.unlink(message.from_user.id)
    except ApiError as e:
        await message.answer(t("already_unlinked") if e.status == 404 else e.detail)
        return
    await message.answer(t("unlinked", cabinet=t.cabinet(site_url)))


def register(router: Router) -> None:
    """Обработчики модуля — в переданный роутер (порядок важен: фильтры проверяются по очереди)."""
    router.message.register(balance, Command("balance"))
    router.message.register(unlink, Command("unlink"))
