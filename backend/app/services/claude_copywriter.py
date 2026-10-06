"""Резервная модель AI-копирайтера — Claude (Anthropic API), если Gemini не ответил.

Та же инструкция, те же данные и та же проверка ответа (AdVariants), что у Gemini: пользователь
получает варианты в том же формате, а оплата считается по ценам Claude. Вызывается из
gemini_service.generate_ad, сам по себе копирайтер не включает.
"""
import json
import logging
from decimal import ROUND_HALF_UP, Decimal

import anthropic
from pydantic import ValidationError

from app import ai
from app.ai import AIUnavailable, ContentRefused
from app.config import settings
from app.services.gemini_service import (
    MAX_PROMPT_TOKENS, SYSTEM_INSTRUCTION, VARIANTS_COUNT, AdVariants, _user_data,
)

log = logging.getLogger(__name__)

MAX_TOKENS = 2048

# Схема для API: без minLength/maxLength — structured outputs их не поддерживают
# (длину проверяет AdVariants после ответа)
SCHEMA = {
    "type": "object",
    "properties": {
        "variants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                    "cta": {"type": "string"},
                },
                "required": ["title", "text", "cta"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["variants"],
    "additionalProperties": False,
}


def is_enabled() -> bool:
    return settings.ai_copy_fallback and bool(settings.anthropic_api_key)


def calculate_cost(input_tokens: int, output_tokens: int) -> Decimal:
    """Стоимость запроса в $ с наценкой платформы, 6 знаков (как calculate_gemini_cost)."""
    cost = (input_tokens * settings.claude_price_input_per_1m
            + output_tokens * settings.claude_price_output_per_1m) / Decimal(1_000_000)
    return (cost * settings.ai_markup).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def max_cost() -> Decimal:
    return calculate_cost(MAX_PROMPT_TOKENS, MAX_TOKENS)


def generate_ad(product_description: str, target_audience: str) -> dict:
    """Варианты объявления от Claude в формате gemini_service.generate_ad. AIUnavailable — при сбое."""
    model = settings.claude_copy_model
    try:
        # Клиент — общий с AI-модерацией (ключ ANTHROPIC_API_KEY, таймаут, повторы)
        response = ai._get_client().messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_INSTRUCTION,
            messages=[{"role": "user", "content": _user_data(product_description, target_audience)}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )
    except anthropic.AuthenticationError as e:
        raise AIUnavailable("неверный ANTHROPIC_API_KEY") from e
    except anthropic.RateLimitError as e:
        raise AIUnavailable("превышен лимит запросов к Anthropic API") from e
    except anthropic.APITimeoutError as e:
        raise AIUnavailable("Anthropic API не ответил вовремя") from e
    except anthropic.APIConnectionError as e:
        raise AIUnavailable("нет связи с Anthropic API") from e
    except anthropic.APIStatusError as e:
        raise AIUnavailable(f"ошибка Anthropic API ({e.status_code})") from e

    if response.stop_reason == "refusal":
        raise ContentRefused("модель отказалась составлять объявление — измените описание")
    if response.stop_reason == "max_tokens":
        raise AIUnavailable("ответ модели обрезан — попробуйте ещё раз")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        content = AdVariants.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as e:
        log.warning("Claude-копирайтер: ответ не по схеме: %r", text[:500])
        raise AIUnavailable("модель вернула ответ не по схеме") from e

    input_tokens, output_tokens = response.usage.input_tokens, response.usage.output_tokens
    return {
        "content": {"variants": [v.model_dump() for v in content.variants[:VARIANTS_COUNT]]},
        "usage": {
            "model": model,
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost": calculate_cost(input_tokens, output_tokens),
        },
    }
