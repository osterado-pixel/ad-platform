"""
Языки кабинета (/app): словари static/ui/i18n.js полны и согласованы с app.js и сервером.

Без браузера: словари разбираются как текст (формат в i18n.js строго построчный).
Браузерная проверка переключения — в test_ui_e2e.py.
"""
import json
import re
import typing
from pathlib import Path

import pytest

from app import schemas
from app.models import CampaignStatus, PartnerTxType, PayoutStatus, SiteStatus, TransactionType, UserRole

UI = Path(__file__).resolve().parent.parent / "app" / "static" / "ui"
SITE_CONFIG = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n" / "config.ts"

ENTRY = re.compile(r'^    "([\w.]+)": (.+),$')
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _parse_messages() -> dict[str, dict[str, str]]:
    source = (UI / "i18n.js").read_text(encoding="utf-8")
    body = source.split("const MESSAGES = {", 1)[1].split("\n};", 1)[0]
    result = {}
    for lang, block in re.findall(r"^  (\w+): \{\n(.*?)^  \},", body, re.M | re.S):
        entries = {}
        for line in block.splitlines():
            if not line.strip() or line.strip().startswith("//"):
                continue
            m = ENTRY.match(line)
            assert m, f"{lang}: строка словаря не в формате «\"ключ\": значение,» — {line!r}"
            key, value = m.groups()
            assert key not in entries, f"{lang}: ключ {key} повторяется"
            entries[key] = value
        result[lang] = entries
    return result


MESSAGES = _parse_messages()


def _strings(value: str) -> list[str]:
    """Тексты значения: строка или объект форм множественного числа."""
    return [json.loads(f'"{s}"') for s in re.findall(r'"((?:[^"\\]|\\.)*)"', value)]


def _app_source() -> str:
    return (UI / "app.js").read_text(encoding="utf-8")


def test_three_languages_like_the_site():
    assert set(MESSAGES) == {"en", "ru", "de"}
    source = (UI / "i18n.js").read_text(encoding="utf-8")
    assert 'const LOCALES = ["en", "ru", "de"];' in source
    assert 'const DEFAULT_LOCALE = "en";' in source
    assert 'const LOCALE_COOKIE = "lang";' in source
    if SITE_CONFIG.exists():  # сайт и кабинет читают один cookie — список языков должен совпадать
        site = SITE_CONFIG.read_text(encoding="utf-8")
        assert 'LOCALES = ["en", "ru", "de"]' in site
        assert 'LOCALE_COOKIE = "lang"' in site


@pytest.mark.parametrize("lang", ["en", "de"])
def test_same_keys_in_every_language(lang):
    ru, other = set(MESSAGES["ru"]), set(MESSAGES[lang])
    assert not ru - other, f"{lang}: нет перевода для {sorted(ru - other)}"
    assert not other - ru, f"{lang}: лишние ключи {sorted(other - ru)}"


@pytest.mark.parametrize("lang", ["en", "de"])
def test_same_placeholders_in_every_language(lang):
    for key, value in MESSAGES["ru"].items():
        expected = {p for s in _strings(value) for p in PLACEHOLDER.findall(s)}
        got = {p for s in _strings(MESSAGES[lang][key]) for p in PLACEHOLDER.findall(s)}
        assert got == expected, f"{lang}.{key}: подстановки {got}, а в ru — {expected}"


def test_plural_forms_have_other_and_count():
    for lang, entries in MESSAGES.items():
        for key, value in entries.items():
            if value.startswith("{"):
                assert re.search(r"\bother: ", value), f"{lang}.{key}: нет формы other"
                assert all("{n}" in s for s in _strings(value)), f"{lang}.{key}: число не подставляется"


def test_no_empty_or_untranslated_values():
    for lang, entries in MESSAGES.items():
        for key, value in entries.items():
            assert all(s.strip() for s in _strings(value)), f"{lang}.{key}: пустой перевод"
            if lang != "ru":
                assert not re.search("[А-Яа-яЁё]", value), f"{lang}.{key}: остался русский текст"


def test_every_key_used_in_app_exists():
    namespaces = {k.split(".")[0] for k in MESSAGES["ru"]}
    used = {
        key for key in re.findall(r'"([a-z]+(?:\.[A-Za-z_]+)+)"', _app_source())
        if key.split(".")[0] in namespaces
    }
    assert len(used) > 200  # проверка действительно видит ключи
    missing = used - set(MESSAGES["ru"])
    assert not missing, f"app.js использует ключи без перевода: {sorted(missing)}"


def test_server_codes_have_translations():
    """Коды с сервера показываются переводом: новый статус без перевода заметен сразу."""
    keys = set(MESSAGES["ru"])
    for status in CampaignStatus:
        assert f"status.{status.value}" in keys
    for tx_type in TransactionType:
        assert f"tx.{tx_type.value}" in keys
    for role in UserRole:
        assert f"role.{role.value}" in keys
    # Партнёрская программа: статусы сайтов и выплат, операции заработка, способы выплаты
    for site_status in SiteStatus:
        assert f"sstatus.{site_status.value}" in keys
    for payout_status in PayoutStatus:
        assert f"pstatus.{payout_status.value}" in keys
    for ptx in PartnerTxType:
        assert f"ptx.{ptx.value}" in keys
    for method in typing.get_args(schemas.PayoutMethod):
        assert f"method.{method}" in keys
    for flag in typing.get_args(schemas.FraudFlag):
        assert f"fraudflag.{flag}" in keys
    # Literal[...] | None из карточки модерации → значения Literal
    fields = schemas.CampaignAdminResponse.model_fields
    verdicts = typing.get_args(typing.get_args(fields["ai_verdict"].annotation)[0])
    risks = typing.get_args(typing.get_args(fields["ai_risk"].annotation)[0])
    assert "approve" in verdicts and "high" in risks
    for verdict in verdicts:
        assert f"moderation.ai.{verdict}" in keys
    for risk in risks:
        assert f"moderation.ai.risk.{risk}" in keys


def test_no_hardcoded_russian_in_app():
    """Тексты интерфейса — только через словарь; русский в app.js остаётся лишь в комментариях."""
    code = re.sub(r"/\*.*?\*/", "", _app_source(), flags=re.S)
    code = "\n".join(line.split("//")[0] for line in code.splitlines())
    leftovers = [line.strip() for line in code.splitlines() if re.search("[А-Яа-яЁё]", line)]
    assert not leftovers, leftovers


def test_page_loads_dictionary_before_app():
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert html.index("/static/ui/i18n.js") < html.index("/static/ui/app.js")
    assert '<html lang="en">' in html
    assert not re.search("[А-Яа-яЁё]", html)


def test_dictionary_served_with_app_csp(client):
    r = client.get("/static/ui/i18n.js")
    assert r.status_code == 200
    assert "script-src 'self'" in r.headers["content-security-policy"]
