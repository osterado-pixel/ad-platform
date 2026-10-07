"""Языки платформы. Новый язык — код здесь и словари переводов (сайт, кабинет, сообщения API, бот)."""
from typing import Literal

Language = Literal["ru", "en", "de"]
LANGUAGES: tuple[str, ...] = ("ru", "en", "de")
DEFAULT_LANGUAGE: Language = "ru"

# Для инструкции модели: «...объявления на {название} языке»
PROMPT_LANGUAGE_NAMES: dict[str, str] = {"ru": "русском", "en": "английском", "de": "немецком"}
