"""app/services/moderation_service.py: локальные стоп-фразы и OpenAI Moderation (подменён)."""
import asyncio
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.config import settings
from app.services import moderation_service as ms


@pytest.mark.parametrize("text,reason", [
    ("Лучшее онлайн-казино!", "азартные игры"),
    ("Кaзино (латинская a)", "азартные игры"),
    ("к.а.з.и.н.о с бонусом", "азартные игры"),
    ("К А З И Н О", "азартные игры"),
    ("Ставки на спорт с кэшбэком", "азартные игры"),
    ("Надёжный букмекер", "азартные игры"),
    ("Вступай в криптопирамиду", "финансовая пирамида"),
    ("Это не финансовая пирамида!", "финансовая пирамида"),
    ("Заработок без вложений от 5000 в день", "обещание заработка без вложений"),
    ("100% гарантия дохода", "гарантированный доход"),
    ("Гарантированная прибыль каждый месяц", "гарантированный доход"),
    ("Купить диплом недорого", "поддельные документы"),
    ("Продам диплом вуза", "поддельные документы"),
    ("Сделаем водительские права за день", "поддельные документы"),
    ("Фальшивые деньги высокого качества", "подделки"),
    ("Поддельный паспорт", "подделки"),
    ("Зарaботок без влoжений", "обещание заработка без вложений"),  # латиница внутри слов
    ("Казино​ онлайн", "азартные игры"),                      # невидимый символ
    ("ЗАРАБОТОК БЕЗ ВЛОЖЕНИЙ", "обещание заработка без вложений"),
])
def test_local_rules_block(text, reason):
    assert reason in ms.find_local_violations(text)
    with pytest.raises(ms.ContentRejected) as e:
        ms.check_local_rules(text)
    assert e.value.status_code == 422 and reason in e.value.detail


@pytest.mark.parametrize("text", [
    "Выгодные ставки по вкладам до 18%",       # банк — честная реклама
    "Строительная рулетка 5 м",                # инструмент
    "Ипотека: ставка от 6%",
    "Рамка для диплома из дуба",
    "Курсы Python с дипломом установленного образца",
    "Гарантия возврата денег 14 дней",
    "Заработок для фрилансеров: курс по дизайну",
    "Казначейские облигации",                  # «казн», а не «казино»
    "Москва — лучший город",
    "Скидка 50% на ёлочные игрушки",
    "Casual-одежда",
])
def test_local_rules_allow_honest_ads(text):
    assert ms.find_local_violations(text) == []
    ms.check_local_rules(text)


def test_reasons_not_duplicated():
    assert ms.find_local_violations("Казино! Казино! Букмекер!") == ["азартные игры"]


def test_tutorial_name_kept():
    assert len(ms.FORBIDDEN_PATTERNS) == len(ms._RULES)


# --- OpenAI Moderation ---
def moderation_result(flagged, **categories):
    from openai.types.moderation import Categories
    # Модель принимает имена API («harassment/threatening»), а не питоновские
    cats = {(f.alias or name): categories.get(name, False) for name, f in Categories.model_fields.items()}
    return SimpleNamespace(results=[SimpleNamespace(flagged=flagged, categories=Categories.model_validate(cats))])


class FakeOpenAI:
    def __init__(self, result=None, error=None):
        self.calls, self._result, self._error = [], result, error
        self.moderations = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._result


@pytest.fixture
def openai_on(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")

    def use(client):
        monkeypatch.setattr(ms, "_get_client", lambda: client)
        return client
    return use


def test_openai_disabled_without_key(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(ms, "_get_client", lambda: pytest.fail("API не должен вызываться без ключа"))
    assert ms.find_openai_violations("любой текст") == []
    ms.moderate_text_sync("Обычное объявление")


def test_app_starts_without_openai_key():
    import app.main  # noqa: F401 — в инструкции AsyncOpenAI() при импорте ронял приложение без ключа
    assert ms._client is None


def test_openai_flagged_categories_in_russian(openai_on):
    client = openai_on(FakeOpenAI(moderation_result(True, violence=True, harassment_threatening=True)))
    with pytest.raises(ms.ContentRejected) as e:
        ms.moderate_text_sync("Обычный на вид текст")
    assert set(e.value.reasons) == {"насилие", "угрозы"}
    assert client.calls == [{"model": "omni-moderation-latest", "input": "Обычный на вид текст"}]


def test_openai_clean_text_passes(openai_on):
    openai_on(FakeOpenAI(moderation_result(False)))
    ms.moderate_text_sync("Курсы английского")


def test_local_rules_run_first_without_api_call(openai_on):
    client = openai_on(FakeOpenAI(moderation_result(False)))
    with pytest.raises(ms.ContentRejected):
        ms.moderate_text_sync("Казино")
    assert client.calls == []  # бесплатная мгновенная проверка отсекла раньше сетевой


@pytest.mark.parametrize("error", [
    lambda: openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com")),
    lambda: openai.APITimeoutError(request=httpx.Request("POST", "https://api.openai.com")),
    lambda: openai.AuthenticationError("bad key", response=httpx.Response(
        401, request=httpx.Request("POST", "https://api.openai.com")), body=None),
])
def test_openai_failure_skips_check(openai_on, error, caplog):
    openai_on(FakeOpenAI(error=error()))
    ms.moderate_text_sync("Курсы английского")  # не падает: проверку выполнит модератор
    assert "OpenAI Moderation недоступен" in caplog.text


def test_async_facade(openai_on):
    openai_on(FakeOpenAI(moderation_result(True, hate=True)))
    with pytest.raises(ms.ContentRejected, match="ненависть"):
        asyncio.run(ms.moderate_text("текст"))
    with pytest.raises(ms.ContentRejected, match="ненависть"):
        asyncio.run(ms.check_ai_moderation("текст"))
    with pytest.raises(ms.ContentRejected, match="азартные"):
        asyncio.run(ms.moderate_text("казино"))
