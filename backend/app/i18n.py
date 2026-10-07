"""Языки платформы. Новый язык — код здесь и словари переводов (сайт, кабинет, сообщения API, бот).

Сообщения API пишутся в коде по-русски — это исходный текст (и то, что хранится в базе: описания
операций, ошибки AI-задач). Перевод — на выходе, на языке запроса (Accept-Language): обработчики
ошибок и поля ответов вызывают localize(). Каталог переводов — app/messages.py.
"""
import re
from contextvars import ContextVar
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Literal

Language = Literal["ru", "en", "de"]
LANGUAGES: tuple[str, ...] = ("ru", "en", "de")
DEFAULT_LANGUAGE: Language = "ru"

# Для инструкции модели: «...объявления на {название} языке»
PROMPT_LANGUAGE_NAMES: dict[str, str] = {"ru": "русском", "en": "английском", "de": "немецком"}

# Язык текущего запроса. Без заголовка — русский: старые клиенты (скрипты, бот до обновления)
# получают сообщения как раньше
_language: ContextVar[str] = ContextVar("language", default=DEFAULT_LANGUAGE)


def language_from_header(header: str | None) -> str:
    """«de-AT,de;q=0.9,en;q=0.8» → «de»: первый поддерживаемый язык по весу, иначе русский."""
    if not header:
        return DEFAULT_LANGUAGE
    ranked = []
    for position, part in enumerate(header.split(",")):
        tag, *params = part.strip().split(";")
        q = 1.0
        for param in params:
            name, _, value = param.strip().partition("=")
            if name == "q":
                try:
                    q = float(value)
                except ValueError:
                    q = 0.0
        lang = tag.strip().lower().split("-")[0]
        if lang in LANGUAGES and q > 0:
            ranked.append((-q, position, lang))
    return min(ranked)[2] if ranked else DEFAULT_LANGUAGE


def current_language() -> str:
    return _language.get()


def set_language(lang: str):
    """Язык для текущего контекста (запроса). Возвращает токен для сброса."""
    return _language.set(lang if lang in LANGUAGES else DEFAULT_LANGUAGE)


def reset_language(token) -> None:
    _language.reset(token)


# ---------- Перевод ----------
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
# Подстановки-суммы выводятся в формате языка: 1234.50 → 1,234.50 / 1.234,50
_NUMBER_PLACEHOLDERS = {"amount", "min", "max"}
# Подстановки, в которых тоже сообщение из каталога («AI-генерация не выполнена: {reason}») —
# переводятся по частям. Остальные (название кампании, email, код) — текст пользователя, как есть
_MESSAGE_PLACEHOLDERS = {"reason", "reasons", "message", "fallback"}


def _format_number(value: str, lang: str) -> str:
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        return value
    if lang == "ru":
        return value  # исходный текст не меняем
    text = f"{number:,.2f}"
    return text.replace(",", " ").replace(".", ",").replace(" ", ".") if lang == "de" else text


@lru_cache(maxsize=1)
def _catalog():
    """(точные переводы, шаблоны с подстановками) из app/messages.py."""
    from app.messages import MESSAGES

    exact: dict[str, dict[str, str]] = {}
    patterns: list[tuple[re.Pattern, dict[str, str]]] = []
    for entry in MESSAGES:
        source = entry["ru"]
        if _PLACEHOLDER.search(source):
            parts = _PLACEHOLDER.split(source)
            regex = "".join(re.escape(p) if i % 2 == 0 else f"(?P<{p}>.+?)" for i, p in enumerate(parts))
            patterns.append((re.compile(f"^{regex}$", re.S), entry))
        else:
            exact[source] = entry
    # Длинные шаблоны первыми: при вложенных сообщениях выигрывает самое точное совпадение
    patterns.sort(key=lambda item: -len(item[1]["ru"]))
    return exact, patterns


def localize(text: str | None, lang: str | None = None) -> str | None:
    """Готовый русский текст → язык запроса. Неизвестный текст (ввод пользователя) — без изменений."""
    if not text:
        return text
    lang = lang or current_language()
    if lang == DEFAULT_LANGUAGE or lang not in LANGUAGES:
        return text
    exact, patterns = _catalog()
    if text in exact:
        return exact[text][lang]
    for regex, entry in patterns:
        m = regex.match(text)
        if m:
            values = {name: _localize_value(name, value, lang) for name, value in m.groupdict().items()}
            return _PLACEHOLDER.sub(lambda p: values[p.group(1)], entry[lang])
    return text


def _localize_value(name: str, value: str, lang: str) -> str:
    if name in _NUMBER_PLACEHOLDERS:
        return _format_number(value, lang)
    if name not in _MESSAGE_PLACEHOLDERS:
        return value
    translated = localize(value, lang)
    if translated != value:
        return translated
    # Список причин «a; b; c» — каждую отдельно
    if "; " in value:
        return "; ".join(localize(part, lang) for part in value.split("; "))
    return value


class _KeepMissing(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def localize_validation_errors(errors: list[dict]) -> list[dict]:
    """Ошибки проверки данных (422) на языке запроса.

    Свои проверки (ValueError в схемах) — по каталогу, без служебного «Value error, ».
    Встроенные (обязательное поле, длина, число…) — по типу ошибки; английский — текст pydantic.
    """
    from app.messages import INVALID_EMAIL, VALIDATION

    lang = current_language()
    result = []
    for error in errors:
        error = dict(error)
        kind, msg = error.get("type"), str(error.get("msg", ""))
        if kind in ("value_error", "assertion_error"):
            text = msg.removeprefix("Value error, ").removeprefix("Assertion failed, ")
            # EmailStr: подробности email-validator только на английском — коротко на любом языке
            error["msg"] = INVALID_EMAIL[lang] if "valid email address" in text else localize(text, lang)
        elif lang != "en" and kind in VALIDATION:
            error["msg"] = VALIDATION[kind][lang].format_map(_KeepMissing(error.get("ctx") or {}))
        result.append(error)
    return result


def tr(template: str, **values) -> str:
    """Русский шаблон с подстановками → текст на языке запроса: tr("Сумма: {amount}", amount=...)."""
    return localize(template.format(**values)) if values else localize(template)
