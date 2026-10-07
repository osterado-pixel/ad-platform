"""Тексты сообщений бота (обычный текст, без HTML/Markdown: текст объявлений приходит от модели)."""


def money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def cabinet(site_url: str) -> str:
    return f"{site_url}/app" if site_url else "кабинете платформы"


def not_linked(site_url: str) -> str:
    return (
        "Telegram ещё не привязан к аккаунту платформы.\n\n"
        f"1. Откройте профиль в {cabinet(site_url)} и нажмите «Получить код привязки».\n"
        "2. Нажмите «Открыть бота и привязать» или отправьте сюда: /link КОД"
    )


HELP = (
    "Что умеет бот:\n"
    "/generate — составить рекламное объявление (3 варианта)\n"
    "/balance — баланс аккаунта\n"
    "/link КОД — привязать аккаунт (код — в профиле кабинета)\n"
    "/unlink — отвязать аккаунт\n"
    "/cancel — отменить ввод"
)

ASK_DESCRIPTION = (
    "Опишите товар или услугу: что это, чем хороши, цена, особенности (от 10 символов).\n"
    "Аудиторию можно указать с новой строки после «Аудитория:».\n\n"
    "/cancel — отменить"
)


def variants(items: list[dict]) -> str:
    blocks = [
        f"Вариант {i}\nЗаголовок: {v['title']}\nТекст: {v['text']}\nПризыв: {v['cta']}"
        for i, v in enumerate(items, start=1)
    ]
    return "Готово:\n\n" + "\n\n".join(blocks) + "\n\nСкопируйте понравившийся вариант в форму кампании в кабинете."
