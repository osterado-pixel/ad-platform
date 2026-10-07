"""Сообщения API на языке запроса (Accept-Language): каталог app/messages.py и перевод на выходе."""
import ast
import re
from pathlib import Path

import pytest

from app.i18n import language_from_header, localize, reset_language, set_language, tr
from app.messages import INVALID_EMAIL, MESSAGES, VALIDATION

EN = {"Accept-Language": "en"}
DE = {"Accept-Language": "de-DE,de;q=0.9,en;q=0.8"}
APP = Path(__file__).resolve().parent.parent / "app"
CYRILLIC = re.compile("[А-Яа-яЁё]")
PLACEHOLDER = re.compile(r"\{(\w+)\}")


# ---------- Язык запроса ----------
@pytest.mark.parametrize("header, expected", [
    (None, "ru"), ("", "ru"),                       # без заголовка — как раньше, по-русски
    ("en", "en"), ("de-AT", "de"), ("RU-ru", "ru"),
    ("fr-FR,fr;q=0.9", "ru"),                       # ни одного поддерживаемого
    ("fr-FR,fr;q=0.9,en;q=0.8,de;q=0.7", "en"),     # первый поддерживаемый по весу
    ("de;q=0.5,en;q=0.9", "en"), ("en;q=0,de", "de"),  # q=0 — «не надо»
    ("de;q=abc,en;q=0.1", "en"),                    # испорченный вес не роняет разбор
])
def test_language_from_header(header, expected):
    assert language_from_header(header) == expected


# ---------- Каталог ----------
def test_catalog_has_unique_sources():
    sources = [m["ru"] for m in MESSAGES]
    assert len(sources) == len(set(sources))


@pytest.mark.parametrize("entry", MESSAGES, ids=lambda m: m["ru"][:40])
def test_catalog_entry_is_complete(entry):
    assert set(entry) == {"ru", "en", "de"}
    expected = sorted(PLACEHOLDER.findall(entry["ru"]))
    for lang in ("en", "de"):
        assert entry[lang].strip()
        assert sorted(PLACEHOLDER.findall(entry[lang])) == expected, lang
        assert not CYRILLIC.search(entry[lang]), lang


def test_validation_catalog_is_complete():
    for kind, texts in VALIDATION.items():
        assert set(texts) == {"ru", "de"}, kind  # английский — текст pydantic
        assert PLACEHOLDER.findall(texts["ru"]) == PLACEHOLDER.findall(texts["de"]), kind
        assert not CYRILLIC.search(texts["de"]), kind
    assert set(INVALID_EMAIL) == {"ru", "en", "de"}


# ---------- В коде нет сообщений без перевода ----------
# Не для пользователя: настройки сервера (их читает администратор при запуске), инструкции модели,
# шаблоны стоп-фраз, ответы платёжной системе, внутренние проверки программиста
SKIP_FILES = {"cli.py", "config.py", "maintenance.py", "ai_cleanup.py", "i18n.py", "messages.py"}
SKIP_CALLS = {"Field", "Query", "Path", "Body", "Header", "Form", "FastAPI", "APIRouter", "WebhookRejected",
              "info", "warning", "error", "exception", "debug", "critical", "maketrans", "replace", "search"}
SKIP_TEXTS = {"Сумма резерва должна быть больше 0", "русском", "английском", "немецком"}


def _text(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
    return None


def _user_facing_strings():
    for path in sorted(APP.rglob("*.py")):
        if path.name in SKIP_FILES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            text = _text(node)
            if not text or not CYRILLIC.search(text) or text in SKIP_TEXTS:
                continue
            parent = parents.get(node)
            if isinstance(parent, (ast.JoinedStr, ast.Expr)):  # часть f-строки или docstring
                continue
            if "\n" in text or "\\" in text or "[а-я]" in text:  # инструкция модели или шаблон поиска
                continue
            call, keyword = parent, None
            while call is not None and not isinstance(call, ast.Call):
                if isinstance(call, ast.keyword) and keyword is None:
                    keyword = call.arg
                call = parents.get(call)
            name = ""
            if isinstance(call, ast.Call):
                name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
            if name in SKIP_CALLS or keyword in ("description", "summary", "json_schema_extra"):
                continue
            # «префикс: » + "; ".join(причины) — в каталоге как «префикс: {…}»
            if isinstance(parent, ast.BinOp) or text.endswith(": "):
                text += "{}"
            # tr("Тариф «{name}»", …) — шаблон с именованной подстановкой
            yield f"{path.relative_to(APP)}:{node.lineno}", PLACEHOLDER.sub("{}", text)


def test_every_user_message_is_translated():
    known = {PLACEHOLDER.sub("{}", m["ru"]) for m in MESSAGES}
    found = list(_user_facing_strings())
    assert len(found) > 100  # проверка действительно видит сообщения
    missing = [f"{where}: {text}" for where, text in found if text not in known]
    assert not missing, "Нет перевода в app/messages.py:\n" + "\n".join(missing)


# ---------- Перевод готовых сообщений ----------
@pytest.mark.parametrize("lang, expected", [
    ("ru", "AI-генерация не выполнена: превышен лимит запросов к Gemini API. Деньги не списаны"),
    ("en", "AI generation failed: Gemini API rate limit exceeded. You were not charged"),
    ("de", "KI-Generierung fehlgeschlagen: Anfragelimit der Gemini API überschritten. Es wurde nichts abgebucht"),
])
def test_nested_message(lang, expected):
    text = "AI-генерация не выполнена: превышен лимит запросов к Gemini API. Деньги не списаны"
    assert localize(text, lang) == expected


def test_backup_model_message_translated_in_parts():
    text = ("AI-генерация не выполнена: ошибка Gemini API (500); резервная модель: "
            "нет связи с Anthropic API. Деньги не списаны")
    assert localize(text, "en") == ("AI generation failed: Gemini API error (500); backup model: "
                                    "no connection to Anthropic API. You were not charged")


def test_reason_list_translated_item_by_item():
    text = "Текст не прошёл модерацию: азартные игры; гарантированный доход; что-то от модели"
    assert localize(text, "de") == ("Der Text hat die Moderation nicht bestanden: "
                                    "Glücksspiel; garantiertes Einkommen; что-то от модели")


def test_amounts_in_language_format():
    text = "Недостаточно средств для AI-генерации: нужно не меньше 1234.50 на балансе (лишнее вернётся после генерации)"
    assert "1,234.50" in localize(text, "en")
    assert "1.234,50" in localize(text, "de")
    assert localize("Пополнение баланса на 99.00", "de") == "Aufladung um 99,00"


def test_user_text_is_never_translated():
    # Название кампании совпадает с сообщением каталога — всё равно остаётся как есть
    assert localize("Списание за клик по кампании #7 (Задача не найдена)", "en") == \
        "Click charge for campaign #7 (Задача не найдена)"
    assert localize("Картинка не соответствует правилам", "en") == "Картинка не соответствует правилам"
    assert localize(None, "en") is None and localize("", "en") == ""


def test_tr_uses_request_language():
    token = set_language("de")
    try:
        assert tr("Тариф «{name}»", name="Pro") == "Tarif „Pro“"
        assert tr("Тестовая оплата") == "Testzahlung"
    finally:
        reset_language(token)
    assert tr("Тестовая оплата") == "Тестовая оплата"


# ---------- Ответы API ----------
def test_http_error_in_request_language(client, user_headers):
    for headers, expected in (({}, "Кампания не найдена"), (EN, "Campaign not found"),
                              (DE, "Kampagne nicht gefunden")):
        r = client.get("/api/v1/campaigns/999", headers={**user_headers, **headers})
        assert r.status_code == 404 and r.json()["detail"] == expected


def test_language_headers_on_api_responses(client):
    r = client.get("/api/v1/health", headers=DE)
    assert r.headers["content-language"] == "de"
    assert "Accept-Language" in r.headers["vary"]
    assert client.get("/api/v1/health").headers["content-language"] == "ru"


def test_login_error_and_rate_limit_translated(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "register_max_per_ip_per_hour", 1)
    body = {"email": "lang@example.com", "password": "password123"}
    assert client.post("/api/v1/auth/register", json=body).status_code == 201
    r = client.post("/api/v1/auth/register", json={**body, "email": "lang2@example.com"}, headers=EN)
    assert r.status_code == 429
    assert re.fullmatch(r"Too many sign-ups from your address\. Try again in \d+ min\.", r.json()["detail"])
    r = client.post("/api/v1/auth/login", data={"username": "lang@example.com", "password": "wrong-pass"}, headers=DE)
    assert r.json()["detail"] == "E-Mail-Adresse oder Passwort ist falsch"


def test_validation_errors_translated(client):
    r = client.post("/api/v1/auth/register", json={"email": "x@example.com"}, headers=DE)
    assert r.status_code == 422
    assert {"loc": ["body", "password"], "msg": "Pflichtfeld"}.items() <= r.json()["detail"][0].items()
    r = client.post("/api/v1/auth/register", json={"email": "x@example.com"})
    assert r.json()["detail"][0]["msg"] == "Обязательное поле"
    r = client.post("/api/v1/auth/register", json={"email": "x@example.com"}, headers=EN)
    assert r.json()["detail"][0]["msg"] == "Field required"  # английский — текст pydantic
    r = client.post("/api/v1/auth/register", json={"email": "x@example.com", "password": "short"}, headers=DE)
    assert r.json()["detail"][0]["msg"] == "Mindestens 8 Zeichen"
    r = client.post("/api/v1/auth/register", json={"email": "not-an-email", "password": "password123"}, headers=EN)
    assert r.json()["detail"][0]["msg"] == "Invalid email address"


def test_own_validation_message_translated_without_prefix(client, auth_headers):
    body = {"status": "rejected"}  # отказ без причины
    r = client.patch("/api/v1/campaigns/1/moderate", json=body, headers={**auth_headers, **EN})
    assert r.status_code == 422
    assert r.json()["detail"][0]["msg"] == "Specify a reason (rejection_reason) when rejecting a campaign"
    r = client.patch("/api/v1/campaigns/1/moderate", json=body, headers=auth_headers)
    assert r.json()["detail"][0]["msg"] == "При отклонении кампании укажите причину (rejection_reason)"


def test_stored_descriptions_translated_on_read(client, test_user, user_headers, auth_headers):
    r = client.post(f"/api/v1/wallet/deposit?user_id={test_user.id}", json={"amount": "1234.5"}, headers=auth_headers)
    assert r.status_code == 200
    ru = client.get("/api/v1/wallet/history", headers=user_headers).json()["items"]
    en = client.get("/api/v1/wallet/history", headers={**user_headers, **EN}).json()["items"]
    de = client.get("/api/v1/wallet/history", headers={**user_headers, **DE}).json()["items"]
    assert ru[0]["description"] == "Пополнение баланса на 1234.50"  # в базе — как было
    assert en[0]["description"] == "Balance top-up of 1,234.50"
    assert de[0]["description"] == "Aufladung um 1.234,50"


def test_root_message(client):
    assert client.get("/", headers=EN).json()["message"] == "The platform is fully up!"


def test_test_checkout_page_in_browser_language(client, test_user, user_headers, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "payments_provider", "test")
    pid = client.post("/api/v1/payments/top-up", json={"amount": "42.00"}, headers=user_headers).json()["id"]
    page = client.get(f"/api/v1/payments/test-checkout/{pid}", headers=DE).text
    assert "<html lang='de'>" in page and "<h1>Testzahlung</h1>" in page and ">Bezahlen</button>" in page
    assert "Деньги" not in page


def test_ai_task_error_translated_on_read(client, db, test_user, user_headers):
    from app.models import AITask, AITaskStatus
    task = AITask(id="t-lang", user_id=test_user.id, status=AITaskStatus.FAILED,
                  error_message="AI-генерация не выполнена: нет связи с Gemini API. Деньги не списаны")
    db.add(task)
    db.commit()
    r = client.get("/api/v1/ai/tasks/t-lang", headers={**user_headers, **EN})
    assert r.json()["error"] == "AI generation failed: no connection to Gemini API. You were not charged"


def test_amount_formatting_keeps_non_numbers():
    # Не число в «денежной» подстановке — как есть, без ошибки
    assert localize("Сумма — от abc до 5", "de") == "Der Betrag muss zwischen abc und 5,00 liegen"
