"""/generate — объявление через AI-копирайтер платформы (фоновая задача + опрос статуса)."""
import asyncio
import time
from collections.abc import Awaitable, Callable

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from api_client import ApiError, PlatformAPI
from texts import AUDIENCE_PREFIXES, Texts


POLL_LIMIT_SECONDS = 150  # дольше генерация не идёт — дальше задачу закроет очистка зависших на сервере
MIN_DESCRIPTION = 10


class GenerateStates(StatesGroup):
    description = State()


def parse_input(text: str, default_audience: str) -> tuple[str, str]:
    """«Описание\\nАудитория: …» → (описание, аудитория). «Audience:» и «Zielgruppe:» — тоже."""
    description, audience = text.strip(), default_audience
    lines = description.splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower().startswith(AUDIENCE_PREFIXES):
            audience = line.split(":", 1)[1].strip() or audience
            description = "\n".join(lines[:i] + lines[i + 1:]).strip()
            break
    return description, audience


async def wait_for_task(api: PlatformAPI, telegram_id: int, task_id: str,
                        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                        limit_seconds: float = POLL_LIMIT_SECONDS) -> dict | None:
    """Опрос статуса задачи: 1 → 4 с, до limit_seconds. None — не дождались."""
    started, delay = time.monotonic(), 1.0
    while time.monotonic() - started < limit_seconds:
        await sleep(delay)
        delay = min(delay * 1.5, 4.0)
        task = await api.task(telegram_id, task_id)
        if task["status"] in ("completed", "failed"):
            return task
    return None


async def run_generation(message: Message, text: str, api: PlatformAPI, site_url: str, t: Texts) -> None:
    description, audience = parse_input(text, t("default_audience"))
    if len(description) < MIN_DESCRIPTION:
        await message.answer(f"{t('too_short', count=MIN_DESCRIPTION)}\n\n{t('ask_description')}")
        return
    telegram_id = message.from_user.id
    try:
        # Объявление — на языке пользователя; ошибки сервер присылает на нём же
        created = await api.generate(telegram_id, description, audience, language=t.lang)
    except ApiError as e:
        # 404 — не привязан; 402 — мало денег; 422 — текст не прошёл модерацию; 429 — много задач; 503 — выключен
        await message.answer(t.not_linked(site_url) if e.status == 404 else e.detail)
        return

    progress = await message.answer(t("progress", amount=t.money(created["held_amount"])))
    try:
        task = await wait_for_task(api, telegram_id, created["task_id"])
    except ApiError as e:
        await progress.edit_text(t("result_error", detail=e.detail))
        return
    if task is None:
        await progress.edit_text(t("timeout"))
    elif task["status"] == "completed":
        await progress.edit_text(t.variants(task["result"]["variants"]))
    else:
        await progress.edit_text(task.get("error") or t("failed"))


async def generate_command(message: Message, command: CommandObject, state: FSMContext,
                           api: PlatformAPI, site_url: str, t: Texts) -> None:
    if command.args:  # /generate описание — сразу
        await state.clear()
        await run_generation(message, command.args, api, site_url, t)
        return
    await state.set_state(GenerateStates.description)
    await message.answer(t("ask_description"))


async def cancel(message: Message, state: FSMContext, t: Texts) -> None:
    await state.clear()
    await message.answer(f"{t('cancelled')}\n\n{t('help')}")


async def description_entered(message: Message, state: FSMContext, api: PlatformAPI, site_url: str,
                              t: Texts) -> None:
    await state.clear()
    await run_generation(message, message.text, api, site_url, t)


async def anything_else(message: Message, t: Texts) -> None:
    await message.answer(f"{t('unknown')}\n\n{t('help')}")


def register(router: Router) -> None:
    """Обработчики модуля — в переданный роутер (порядок важен: фильтры проверяются по очереди)."""
    router.message.register(generate_command, Command("generate"))
    router.message.register(cancel, Command("cancel"))
    router.message.register(description_entered, GenerateStates.description, F.text)
    router.message.register(anything_else, F.text)
