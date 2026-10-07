"""Обработчики команд бота. Порядок важен: ввод описания (generate) — после команд.

Роутер создаётся заново для каждого диспетчера (aiogram подключает роутер только к одному).
"""
from aiogram import Router

from handlers import account, generate, start


def build_router() -> Router:
    root = Router(name="bot")
    for module in (start, account, generate):
        module.register(root)
    return root
