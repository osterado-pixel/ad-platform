"""AI-проверка объявлений и сайтов через Gemini (если нет ключа Anthropic). Клиент Gemini подменён."""
import json
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors, types

from app import ai
from app.config import settings
from app.services import gemini_service


class FakeModels:
    def __init__(self):
        self.response, self.error, self.calls = None, None, []

    def generate_content(self, model, contents, config):
        self.calls.append((model, contents, config))
        if self.error:
            raise self.error
        return self.response


def _response(payload=None, finish=types.FinishReason.STOP, text=None):
    return SimpleNamespace(candidates=[SimpleNamespace(finish_reason=finish)],
                           text=text if text is not None else json.dumps(payload))


@pytest.fixture
def gemini(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "gemini_api_key", "test-gemini-key")
    monkeypatch.setattr(settings, "ai_moderation_provider", "auto")
    models = FakeModels()
    monkeypatch.setattr(gemini_service, "_get_client", lambda: SimpleNamespace(models=models))
    return models


APPROVE = {"verdict": "approve", "risk": "low", "reasons": [], "summary": "Обычное объявление"}


@pytest.mark.parametrize("anthropic, gemini_key, choice, expected", [
    ("sk-ant", "g", "auto", "anthropic"),     # Claude — если есть
    ("", "g", "auto", "gemini"),              # иначе Gemini
    ("sk-ant", "g", "gemini", "gemini"),      # можно выбрать явно
    ("", "g", "anthropic", None),             # выбран Claude, а ключа нет — проверка выключена
    ("", "", "auto", None),
])
def test_provider_choice(monkeypatch, anthropic, gemini_key, choice, expected):
    monkeypatch.setattr(settings, "anthropic_api_key", anthropic)
    monkeypatch.setattr(settings, "gemini_api_key", gemini_key)
    monkeypatch.setattr(settings, "ai_moderation_provider", choice)
    assert ai.provider() == expected
    assert ai.is_enabled() is (expected is not None)


def test_moderate_ad_via_gemini(gemini):
    gemini.response = _response(APPROVE)
    result = ai.moderate_ad("Курсы Python", "Скидка", "https://example.com", None)
    assert (result.verdict, result.risk) == ("approve", "low")
    model, contents, config = gemini.calls[0]
    assert model == settings.gemini_model
    assert config.system_instruction == ai.SYSTEM_PROMPT and config.response_schema is ai.AIModerationResult
    assert "<ad>" in contents and "Курсы Python" in contents
    assert ai.model_name() == settings.gemini_model


def test_moderate_site_via_gemini(gemini):
    gemini.response = _response({"verdict": "reject", "risk": "high", "reasons": ["Казино"], "summary": "Казино"})
    result = ai.moderate_site("casino.example.com", "Казино", "Ставки")
    assert result.verdict == "reject" and gemini.calls[0][2].system_instruction == ai.SITE_PROMPT


@pytest.mark.parametrize("finish, message", [
    (types.FinishReason.SAFETY, "модель отказалась проверять объявление — нужна ручная модерация"),
    (types.FinishReason.MAX_TOKENS, "ответ модели обрезан — нужна ручная модерация"),
])
def test_refusal_and_truncation_go_to_human(gemini, finish, message):
    gemini.response = _response(APPROVE, finish=finish)
    with pytest.raises(ai.AIUnavailable, match=message):
        ai.moderate_ad("t", None, "https://example.com", None)


def test_blocked_prompt_without_candidates(gemini):
    gemini.response = SimpleNamespace(candidates=[], text="")
    with pytest.raises(ai.AIUnavailable, match="отказалась"):
        ai.moderate_ad("t", None, "https://example.com", None)


def test_bad_json(gemini):
    gemini.response = _response(text='{"verdict": "maybe"}')
    with pytest.raises(ai.AIUnavailable, match="не по схеме"):
        ai.moderate_ad("t", None, "https://example.com", None)


@pytest.mark.parametrize("error, message", [
    (errors.ClientError(429, {"error": {"message": "quota"}}), "лимит"),
    (errors.ClientError(400, {"error": {"message": "API key not valid"}}), "неверный GEMINI_API_KEY"),
    (errors.ClientError(404, {"error": {"message": "not found"}}), "ошибка Gemini API .404."),
    (errors.ServerError(503, {"error": {"message": "busy"}}), "временно недоступен"),
    (httpx.ReadTimeout("slow"), "не ответил вовремя"),
    (httpx.ConnectError("down"), "нет связи"),
])
def test_api_errors_go_to_human(gemini, error, message):
    gemini.error = error
    with pytest.raises(ai.AIUnavailable, match=message):
        ai.moderate_ad("t", None, "https://example.com", None)


def test_ai_status_shows_gemini(client, auth_headers, gemini):
    r = client.get("/api/v1/campaigns/ai-status", headers=auth_headers).json()
    assert (r["enabled"], r["model"]) == (True, settings.gemini_model)
