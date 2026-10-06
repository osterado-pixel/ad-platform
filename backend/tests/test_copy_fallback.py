"""Резервная модель копирайтера: Claude, если Gemini не ответил. Настоящие API не вызываются."""
import json
from decimal import Decimal
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from google.genai import errors

from app import ai
from app.ai import AIUnavailable, ContentRefused
from app.config import settings
from app.models import AILog
from app.services import claude_copywriter, gemini_service
from tests.test_ai_copy import BODY, URL, assert_ledger_matches, balance_of, user_with_balance
from tests.test_gemini_service import FakeClient as FakeGemini, response as gemini_response

VARIANTS = {"variants": [{"title": f"Claude {i}", "text": "Текст", "cta": "Купить"} for i in range(3)]}


class FakeClaude:
    """Подменяет anthropic.Anthropic: messages.create возвращает заданный ответ или ошибку."""

    def __init__(self, payload=VARIANTS, stop_reason="end_turn", error=None, usage=(500, 300)):
        self.calls = []
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        self._response = SimpleNamespace(
            stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=usage[0], output_tokens=usage[1]))
        self._error = error
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._response


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "AIza-test")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    monkeypatch.setattr(settings, "ai_copy_fallback", True)
    monkeypatch.setattr(settings, "usd_rate", Decimal("1"))

    def use(gemini=None, claude=None):
        if gemini is not None:
            monkeypatch.setattr(gemini_service, "_get_client", lambda: gemini)
        if claude is not None:
            monkeypatch.setattr(ai, "_get_client", lambda: claude)
        return gemini, claude
    return use


def gemini_error(code, message="error"):
    cls = errors.ServerError if code >= 500 else errors.ClientError
    return cls(code, {"error": {"code": code, "message": message, "status": "X"}})


# --- Сам Claude-копирайтер ---
def test_claude_request_and_parsing(keys):
    _, claude = keys(claude=FakeClaude())
    result = claude_copywriter.generate_ad("Курс Python", "новички")
    assert result["content"] == VARIANTS
    assert result["usage"] == {"model": "claude-haiku-4-5", "prompt_tokens": 500, "completion_tokens": 300,
                               "total_tokens": 800, "cost": claude_copywriter.calculate_cost(500, 300)}
    [call] = claude.calls
    assert call["model"] == settings.claude_copy_model == "claude-haiku-4-5"
    assert call["system"] == gemini_service.SYSTEM_INSTRUCTION  # та же инструкция, что у Gemini
    assert call["output_config"] == {"format": {"type": "json_schema", "schema": claude_copywriter.SCHEMA}}
    # structured outputs не поддерживают ограничения длины — их проверяет AdVariants после ответа
    assert "minLength" not in json.dumps(claude_copywriter.SCHEMA)
    assert "thinking" not in call and "effort" not in json.dumps(call["output_config"])
    assert json.loads(call["messages"][0]["content"].split("\n", 1)[1])["product_description"] == "Курс Python"


def test_claude_cost(monkeypatch):
    monkeypatch.setattr(settings, "ai_markup", Decimal("1.5"))
    # (1 000 000 * $1 + 200 000 * $5) / 1e6 = $2; * 1.5 = $3
    assert claude_copywriter.calculate_cost(1_000_000, 200_000) == Decimal("3.000000")


@pytest.mark.parametrize("claude,error,match", [
    (FakeClaude(stop_reason="refusal"), ContentRefused, "отказалась"),
    (FakeClaude(stop_reason="max_tokens"), AIUnavailable, "обрезан"),
    (FakeClaude(payload="не json"), AIUnavailable, "не по схеме"),
    (FakeClaude(payload={"variants": [{"title": "", "text": "x", "cta": "y"}]}), AIUnavailable, "не по схеме"),
    (FakeClaude(error=anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))),
     AIUnavailable, "нет связи"),
])
def test_claude_errors(keys, claude, error, match):
    keys(claude=claude)
    with pytest.raises(error, match=match):
        claude_copywriter.generate_ad("x", "y")


# --- Переключение ---
@pytest.mark.parametrize("failure", [
    lambda: FakeGemini(error=gemini_error(429)),                               # лимит
    lambda: FakeGemini(error=gemini_error(503)),                               # недоступен
    lambda: FakeGemini(error=gemini_error(400, "API key not valid")),          # неверный ключ
    lambda: FakeGemini(gemini_response(finish="MAX_TOKENS")),                  # обрезан
    lambda: FakeGemini(gemini_response("не json")),                            # не по схеме
])
def test_fallback_on_gemini_failure(keys, failure, caplog):
    gemini, claude = keys(gemini=failure(), claude=FakeClaude())
    result = gemini_service.generate_ad("Курс Python", "новички")
    assert result["content"] == VARIANTS and result["usage"]["model"] == "claude-haiku-4-5"
    assert len(gemini.calls) == 1 and len(claude.calls) == 1
    assert "резервной моделью" in caplog.text


def test_invalid_gemini_key_alerts_even_if_fallback_works(keys, caplog):
    keys(gemini=FakeGemini(error=gemini_error(400, "API key not valid")), claude=FakeClaude())
    gemini_service.generate_ad("x", "y")
    assert any(r.levelname == "ERROR" and "GEMINI_API_KEY" in r.getMessage() for r in caplog.records)


def test_gemini_success_no_fallback(keys):
    _, claude = keys(gemini=FakeGemini(gemini_response()), claude=FakeClaude())
    assert gemini_service.generate_ad("x", "y")["usage"]["model"] == settings.gemini_model
    assert claude.calls == []


@pytest.mark.parametrize("finish", ["SAFETY", "PROHIBITED_CONTENT"])
def test_content_refusal_not_retried(keys, finish):
    """Gemini отказал из-за текста — Claude не вызывается: откажет так же, а запрос платный."""
    _, claude = keys(gemini=FakeGemini(gemini_response(finish=finish)), claude=FakeClaude())
    with pytest.raises(ContentRefused):
        gemini_service.generate_ad("x", "y")
    assert claude.calls == []


def test_no_fallback_without_anthropic_key(keys, monkeypatch):
    keys(gemini=FakeGemini(error=gemini_error(503)))
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    with pytest.raises(AIUnavailable, match="временно недоступен") as e:
        gemini_service.generate_ad("x", "y")
    assert "резервная" not in str(e.value)


def test_fallback_disabled_by_setting(keys, monkeypatch):
    _, claude = keys(gemini=FakeGemini(error=gemini_error(503)), claude=FakeClaude())
    monkeypatch.setattr(settings, "ai_copy_fallback", False)
    with pytest.raises(AIUnavailable):
        gemini_service.generate_ad("x", "y")
    assert claude.calls == []


def test_both_fail(keys):
    keys(gemini=FakeGemini(error=gemini_error(503)),
         claude=FakeClaude(error=anthropic.APITimeoutError(request=httpx2.Request("POST", "https://x"))))
    with pytest.raises(AIUnavailable) as e:
        gemini_service.generate_ad("x", "y")
    assert "временно недоступен" in str(e.value) and "резервная модель: Anthropic API не ответил" in str(e.value)


def test_hold_covers_more_expensive_model(keys, monkeypatch):
    gemini_only = gemini_service.calculate_gemini_cost(gemini_service.MAX_PROMPT_TOKENS,
                                                       gemini_service.MAX_OUTPUT_TOKENS)
    assert gemini_service.max_cost() == max(gemini_only, claude_copywriter.max_cost())
    monkeypatch.setattr(settings, "claude_price_output_per_1m", Decimal("50"))  # резерв дороже Gemini
    assert gemini_service.max_cost() == claude_copywriter.max_cost() > gemini_only
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    assert gemini_service.max_cost() == gemini_only


# --- Через эндпоинт: деньги по цене той модели, что ответила ---
def test_endpoint_charges_fallback_price(client, db, keys):
    keys(gemini=FakeGemini(error=gemini_error(429)), claude=FakeClaude(usage=(600, 400)))
    user, h = user_with_balance(db, "10")
    r = client.post(URL, json=BODY, headers=h)
    assert r.status_code == 200 and r.json()["data"] == VARIANTS
    entry = db.query(AILog).one()
    assert entry.model == "claude-haiku-4-5" and entry.cost == claude_copywriter.calculate_cost(600, 400)
    assert balance_of(db, user.id) == Decimal("10") - entry.charged
    assert_ledger_matches(db, user.id)
