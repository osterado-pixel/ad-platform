"""Тексты бота на трёх языках и язык запросов к API платформы."""
import asyncio
import re

import pytest
from aiohttp import web

from api_client import ApiError, PlatformAPI
from texts import DEFAULT_LANGUAGE, LANGUAGES, MESSAGES, Texts, language_of

PLACEHOLDER = re.compile(r"\{(\w+)\}")
CYRILLIC = re.compile("[А-Яа-яЁё]")


def test_same_keys_in_every_language():
    assert set(MESSAGES) == set(LANGUAGES) and DEFAULT_LANGUAGE == "en"
    for lang in LANGUAGES:
        assert set(MESSAGES[lang]) == set(MESSAGES["ru"]), lang


@pytest.mark.parametrize("lang", ["en", "de"])
def test_same_placeholders_and_no_russian(lang):
    for key, text in MESSAGES["ru"].items():
        assert sorted(PLACEHOLDER.findall(MESSAGES[lang][key])) == sorted(PLACEHOLDER.findall(text)), key
        assert MESSAGES[lang][key].strip() and not CYRILLIC.search(MESSAGES[lang][key]), key


@pytest.mark.parametrize("code, expected", [
    ("ru", "ru"), ("de", "de"), ("en", "en"), ("EN-gb", "en"), ("de-AT", "de"),
    ("uk", "en"), ("", "en"), (None, "en"),
])
def test_language_of(code, expected):
    assert language_of(code) == expected


def test_money_formats():
    assert Texts("ru").money(1234.5) == "1 234,50"
    assert Texts("en").money(1234.5) == "1,234.50"
    assert Texts("de").money(1234.5) == "1.234,50"


def test_cabinet_link_or_name():
    assert Texts("de").not_linked("https://ads.example.com").count("(https://ads.example.com/app)") == 1
    assert "(Dashboard der Plattform)" in Texts("de").not_linked("")


def test_model_text_with_braces_is_safe():
    # Подстановка не разбирает фигурные скобки в тексте от модели
    out = Texts("en").variants([{"title": "{n} {title}", "text": "{0}", "cta": "{}"}])
    assert "Headline: {n} {title}" in out and "Text: {0}" in out


# ---------- Клиент API: Accept-Language и общая сессия ----------
def test_api_client_sends_language_and_shares_session():
    seen = []

    async def me(request: web.Request):
        seen.append((request.headers.get("Accept-Language"), request.headers.get("X-Bot-Secret")))
        if request.query["telegram_id"] == "404":
            # Как настоящий сервер: сообщение уже на языке запроса
            return web.json_response({"detail": "Telegram isn't linked"}, status=404)
        return web.json_response({"email": "a@example.com", "balance": 1.0, "held_balance": 0.0})

    async def scenario():
        app = web.Application()
        app.router.add_get("/api/v1/bot/me", me)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        api = PlatformAPI(f"http://127.0.0.1:{port}", "s" * 32)
        try:
            german, english = api.with_language("de"), api.with_language("en")
            assert (await german.me(1)).email == "a@example.com"
            with pytest.raises(ApiError) as err:
                await english.me(404)
            assert err.value.status == 404 and err.value.detail == "Telegram isn't linked"
            # Одна сессия на все копии: закрытие исходного клиента закрывает её
            assert german._shared is api._shared and english._shared is api._shared
        finally:
            await api.close()
            await runner.cleanup()
        assert api._shared["session"].closed

    asyncio.run(scenario())
    assert seen == [("de", "s" * 32), ("en", "s" * 32)]


def test_api_unavailable_message_in_user_language():
    async def scenario():
        # Порт 9 никто не слушает: отказ в соединении (Linux) или тишина до таймаута (Windows)
        api = PlatformAPI("http://127.0.0.1:9", "s" * 32, timeout_seconds=0.5)
        try:
            with pytest.raises(ApiError) as err:
                await api.with_language("de").me(1)
        finally:
            await api.close()
        return err.value

    error = asyncio.run(scenario())
    assert error.status == 0 and error.detail in (MESSAGES["de"]["api_unavailable"], MESSAGES["de"]["api_timeout"])
