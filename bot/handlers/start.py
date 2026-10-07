"""/start, /start КОД (ссылка привязки из кабинета), /link КОД, /help."""
from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from api_client import ApiError, PlatformAPI
from texts import Texts


async def _link(message: Message, code: str, api: PlatformAPI, site_url: str, t: Texts) -> None:
    try:
        account = await api.link(message.from_user.id, code)
    except ApiError as e:
        failed = t("link_failed", detail=e.detail)
        await message.answer(f"{failed}\n\n{t.not_linked(site_url)}" if e.status == 400 else failed)
        return
    await message.answer(f"{t('linked', email=account.email, balance=t.money(account.balance))}\n\n{t('help')}")


async def start_with_code(message: Message, command: CommandObject, api: PlatformAPI, site_url: str,
                          t: Texts) -> None:
    """Ссылка t.me/<бот>?start=<код> из кабинета: Telegram присылает /start <код>."""
    await _link(message, command.args, api, site_url, t)


async def start(message: Message, api: PlatformAPI, site_url: str, t: Texts) -> None:
    try:
        account = await api.me(message.from_user.id)
    except ApiError as e:
        if e.status == 404:
            await message.answer(f"{t('hello_unlinked')}\n\n{t.not_linked(site_url)}")
        else:
            await message.answer(e.detail)
        return
    await message.answer(f"{t('hello', email=account.email)}\n\n{t('help')}")


async def link_command(message: Message, command: CommandObject, api: PlatformAPI, site_url: str,
                       t: Texts) -> None:
    if not command.args:
        await message.answer(f"{t('link_usage')}\n\n{t.not_linked(site_url)}")
        return
    await _link(message, command.args, api, site_url, t)


async def help_command(message: Message, t: Texts) -> None:
    await message.answer(t("help"))


def register(router: Router) -> None:
    """Обработчики модуля — в переданный роутер (порядок важен: фильтры проверяются по очереди)."""
    router.message.register(start_with_code, CommandStart(deep_link=True))
    router.message.register(start, CommandStart())
    router.message.register(link_command, Command("link"))
    router.message.register(help_command, Command("help"))
