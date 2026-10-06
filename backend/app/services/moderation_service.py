"""Быстрая модерация текста: локальные стоп-фразы + OpenAI Moderation API.

1. Локальные правила — регулярные выражения, без сети и за доли миллисекунды. Текст
   нормализуется: латиница-«двойник» (кaзино с латинской a), ё, невидимые символы,
   «к.а.з.и.н.о» / «к а з и н о» — так простые обходы фильтра не работают.
2. OpenAI Moderation (бесплатный, но нужен OPENAI_API_KEY): насилие, ненависть, угрозы, 18+,
   самоповреждение, незаконное. Спам и мошенничество он НЕ распознаёт — это делают локальные
   правила и AI-проверка модерации (app/ai.py).

Нет ключа или OpenAI недоступен — второй шаг пропускается (с записью в лог): текст всё равно
проверит модератор. Отличия от учебной инструкции — в README («Быстрая модерация текста»).
"""
import asyncio
import logging
import re
import unicodedata

import openai
from fastapi import HTTPException, status

from app.config import settings

log = logging.getLogger(__name__)


class ContentRejected(HTTPException):
    """Текст не прошёл модерацию. Это HTTPException (422): в эндпоинте можно не перехватывать."""

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                         detail="Текст не прошёл модерацию: " + "; ".join(reasons))


# --- 1. Локальные правила: (шаблон, причина для пользователя) ---
# Шаблоны — по основам слов (\w* — любое окончание) и для нормализованного текста (строчные, е вместо ё).
# Узкие формулировки вместо одиночных слов: «ставки по вкладам» и «строительная рулетка» — честная реклама
_RULES = [
    (r"\bказино|\bcasino", "азартные игры"),
    (r"\bбукмекер\w*|\bставк\w*\s+на\s+(спорт|матч|футбол|хоккей)\w*|\bигров\w*\s+автомат\w*"
     r"|\bонлайн[\s-]*рулетк\w*|\bслот\w*\s+на\s+деньги", "азартные игры"),
    (r"\b(крипто|финансов\w*\s+)пирамид\w*", "финансовая пирамида"),
    (r"\bзаработ\w*\s+без\s+(вложени|опыт\w*\s+и\s+вложени)\w*", "обещание заработка без вложений"),
    (r"\bгарант\w*\s+(доход|прибыл|заработ)\w*", "гарантированный доход"),
    (r"\b(куп|прода|сдела|изготов)\w*\s+(\w+\s+)?(диплом|аттестат|паспорт|удостоверени|водительск\w*\s+прав)\w*",
     "поддельные документы"),
    (r"\b(фальшив|поддельн)\w*\s+(деньг|купюр|банкнот|документ|паспорт|диплом)\w*", "подделки"),
]
FORBIDDEN_PATTERNS = [pattern for pattern, _ in _RULES]  # имя из инструкции
_COMPILED = [(re.compile(pattern), reason) for pattern, reason in _RULES]

# Латинские буквы, похожие на кириллические: «кaзино» с латинской a не должно проходить фильтр
_HOMOGLYPHS = str.maketrans("aceopxykmthb", "асеорхукмтнв")
_INVISIBLE = re.compile("[­​-‏⁠﻿]")
# «к.а.з.и.н.о», «к а з и н о», «к-а-з-и-н-о»: 4+ одиночные буквы через один разделитель
_SPELLED = re.compile(r"\b(?:\w[\s.\-_*·]){3,}\w\b")


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE.sub("", text).lower().replace("ё", "е")
    return _SPELLED.sub(lambda m: re.sub(r"[\s.\-_*·]", "", m.group()), text)


def _with_cyrillic_lookalikes(text: str) -> str:
    # Подменяем латиницу только в словах, где есть кириллица: английские слова не трогаем
    return re.sub(r"\w+", lambda m: m.group().translate(_HOMOGLYPHS)
                  if re.search("[а-я]", m.group()) else m.group(), text)


def find_local_violations(text: str) -> list[str]:
    """Причины нарушений по локальным правилам (без повторов); пусто — нарушений нет."""
    normalized = _normalize(text)
    variants = {normalized, _with_cyrillic_lookalikes(normalized)}
    reasons: list[str] = []
    for regex, reason in _COMPILED:
        if reason not in reasons and any(regex.search(v) for v in variants):
            reasons.append(reason)
    return reasons


def check_local_rules(text: str) -> None:
    """Проверка по локальным стоп-фразам (без сети). ContentRejected — если нарушение найдено."""
    reasons = find_local_violations(text)
    if reasons:
        raise ContentRejected(reasons)


# --- 2. OpenAI Moderation API ---
OPENAI_CATEGORIES = {
    "harassment": "оскорбления", "harassment/threatening": "угрозы",
    "hate": "ненависть и дискриминация", "hate/threatening": "угрозы по признаку группы",
    "illicit": "незаконная деятельность", "illicit/violent": "незаконная деятельность с насилием",
    "self-harm": "самоповреждение", "self-harm/intent": "самоповреждение",
    "self-harm/instructions": "самоповреждение", "sexual": "контент 18+",
    "sexual/minors": "сексуальный контент с несовершеннолетними",
    "violence": "насилие", "violence/graphic": "жестокие сцены",
}


def is_openai_enabled() -> bool:
    return bool(settings.openai_api_key)


_client: openai.OpenAI | None = None


def _get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        # Не при импорте: OpenAI() без ключа бросает ошибку и уронил бы всё приложение
        _client = openai.OpenAI(api_key=settings.openai_api_key, timeout=10, max_retries=1)
    return _client


def find_openai_violations(text: str) -> list[str]:
    """Категории нарушений по OpenAI Moderation. Нет ключа или сбой API — пусто (проверка пропущена)."""
    if not is_openai_enabled():
        return []
    try:
        response = _get_client().moderations.create(model=settings.openai_moderation_model, input=text)
    except openai.OpenAIError as e:
        # Модерация не должна ломать работу платформы: текст ещё проверит модератор
        log.warning("OpenAI Moderation недоступен, проверка пропущена: %s", e)
        return []
    reasons: list[str] = []
    for result in response.results:
        if not result.flagged:
            continue
        for category, flagged in result.categories.model_dump(by_alias=True).items():
            name = OPENAI_CATEGORIES.get(category, category)
            if flagged and name not in reasons:
                reasons.append(name)
    return reasons


def moderate_text_sync(text: str) -> None:
    """Обе проверки: сначала локальная (бесплатно, мгновенно), затем OpenAI. ContentRejected — при нарушении."""
    check_local_rules(text)
    reasons = find_openai_violations(text)
    if reasons:
        raise ContentRejected(reasons)


# --- async-имена из инструкции: сетевой запрос — в отдельном потоке, сервер не блокируется ---
async def check_ai_moderation(text: str) -> None:
    reasons = await asyncio.to_thread(find_openai_violations, text)
    if reasons:
        raise ContentRejected(reasons)


async def moderate_text(text: str) -> None:
    await asyncio.to_thread(moderate_text_sync, text)
