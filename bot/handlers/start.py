"""/start, /start КОД (ссылка привязки из кабинета), /link КОД, /help."""
from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

import texts
from api_client import ApiError, PlatformAPI


async def _link(message: Message, code: str, api: PlatformAPI, site_url: str) -> None:
    try:
        account = await api.link(message.from_user.id, code)
    except ApiError as e:
        await message.answer(f"Не удалось привязать: {e.detail}\n\n{texts.not_linked(site_url)}"
                             if e.status == 400 else f"Не удалось привязать: {e.detail}")
        return
    await message.answer(
        f"Аккаунт {account.email} привязан.\nБаланс: {texts.money(account.balance)}\n\n{texts.HELP}")


async def start_with_code(message: Message, command: CommandObject, api: PlatformAPI, site_url: str) -> None:
    """Ссылка t.me/<бот>?start=<код> из кабинета: Telegram присылает /start <код>."""
    await _link(message, command.args, api, site_url)


async def start(message: Message, api: PlatformAPI, site_url: str) -> None:
    try:
        account = await api.me(message.from_user.id)
    except ApiError as e:
        if e.status == 404:
            await message.answer("Здравствуйте! Я помогаю рекламодателям платформы Ad Platform.\n\n"
                                 + texts.not_linked(site_url))
        else:
            await message.answer(e.detail)
        return
    await message.answer(f"Здравствуйте! Аккаунт: {account.email}\n\n{texts.HELP}")


async def link_command(message: Message, command: CommandObject, api: PlatformAPI, site_url: str) -> None:
    if not command.args:
        await message.answer("Отправьте код вместе с командой: /link КОД\n\n" + texts.not_linked(site_url))
        return
    await _link(message, command.args, api, site_url)


async def help_command(message: Message) -> None:
    await message.answer(texts.HELP)


def register(router: Router) -> None:
    """Обработчики модуля — в переданный роутер (порядок важен: фильтры проверяются по очереди)."""
    router.message.register(start_with_code, CommandStart(deep_link=True))
    router.message.register(start, CommandStart())
    router.message.register(link_command, Command("link"))
    router.message.register(help_command, Command("help"))
