"""Многоязычность AI: язык объявлений (ru/en/de) и стоп-фразы на английском и немецком."""
import pytest

from app.i18n import LANGUAGES
from app.services import claude_copywriter, gemini_service
from app.services.moderation_service import find_local_violations
from tests.test_ai_copy import BODY, URL, gemini, user_with_balance  # noqa: F401

# --- Стоп-фразы: запрещённое ловится, честная реклама проходит ---
BLOCKED = [
    "Best online casino with bonus", "Sports betting with cashback", "Trusted bookmaker", "Join our Ponzi scheme",
    "Earn money from home without investment", "Make $500 a day without any investment", "No investment needed!",
    "Guaranteed income every month", "Buy a diploma online", "Buy fake passport", "Counterfeit money for sale",
    "Online roulette for real money",
    "Bestes Online-Kasino", "Sportwetten mit Bonus", "Seriöser Buchmacher", "Spielautomaten online",
    "Kein Schneeballsystem!", "Geld verdienen ohne Investition", "Garantiertes Einkommen jeden Monat",
    "Garantierte Rendite 20%", "Diplom kaufen ohne Studium", "Kaufen Sie einen Führerschein", "Falschgeld zu verkaufen",
    "Gefälschte Dokumente",
]
HONEST = [
    "Best interest rates on savings", "Tape measure 5 m", "Buy a diploma frame", "Online course with certificate",
    "30-day money-back guarantee", "Guaranteed delivery in 24 hours", "Casual wear sale",
    "Make money with our investment fund", "Passport photos in 5 minutes", "Betting on quality: handmade furniture",
    "Get your driver's license with our driving school", "Order a passport online via the official portal",
    "Wetterbericht für morgen", "Neue Kasse im Laden", "Garantierte Lieferung in 24 Stunden", "Diplomrahmen kaufen",
    "Kaufen Sie einen Diplomrahmen", "Reisepass-Fotos sofort", "Spielzeug für Kinder",
    "Geld verdienen mit Ihrem Online-Shop", "Fahrschule: Führerschein in 4 Wochen",
]


@pytest.mark.parametrize("text", BLOCKED)
def test_blocked_en_de(text):
    assert find_local_violations(text), text


@pytest.mark.parametrize("text", HONEST)
def test_honest_en_de_pass(text):
    assert find_local_violations(text) == [], text


# --- Язык объявлений ---
@pytest.mark.parametrize("language,word", [("ru", "русском"), ("en", "английском"), ("de", "немецком")])
def test_system_instruction_language(language, word):
    text = gemini_service.system_instruction(language)
    assert f"на {word} языке" in text and "ДАННЫМИ" in text
    assert "3 разных варианта" in text


def test_default_instruction_is_russian():
    assert gemini_service.SYSTEM_INSTRUCTION == gemini_service.system_instruction("ru")
    assert gemini_service.system_instruction("xx") == gemini_service.system_instruction("ru")  # неизвестный → по умолчанию
    assert set(LANGUAGES) == {"ru", "en", "de"}


def test_language_reaches_gemini_and_claude(monkeypatch):
    from tests.test_copy_fallback import FakeClaude, gemini_error, VARIANTS
    from tests.test_gemini_service import FakeClient as FakeGemini, response
    from app import ai
    from app.config import settings
    monkeypatch.setattr(settings, "gemini_api_key", "AIza-test")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    gemini = FakeGemini(response())
    monkeypatch.setattr(gemini_service, "_get_client", lambda: gemini)
    gemini_service.generate_ad("Kaffee am Bahnhof", "Pendler", "de")
    assert "на немецком языке" in gemini.calls[0]["config"].system_instruction

    # Резервная модель получает тот же язык
    gemini2, claude = FakeGemini(error=gemini_error(503)), FakeClaude()
    monkeypatch.setattr(gemini_service, "_get_client", lambda: gemini2)
    monkeypatch.setattr(ai, "_get_client", lambda: claude)
    assert gemini_service.generate_ad("Coffee shop", "Commuters", "en")["content"] == VARIANTS
    assert "на английском языке" in claude.calls[0]["system"]
    assert claude_copywriter.max_cost() > 0


def test_endpoint_and_background_pass_language(client, db, gemini):
    _, h = user_with_balance(db, "10")
    assert client.post(URL, json={**BODY, "language": "en"}, headers=h).status_code == 200
    assert client.post("/api/v1/ai/generate-async", json={**BODY, "language": "de"}, headers=h).status_code == 202
    assert gemini.languages == ["en", "de"]  # синхронная и фоновая генерация
    assert client.post(URL, json={**BODY}, headers=h).status_code == 200
    assert gemini.languages[-1] == "ru"  # по умолчанию — как раньше
    r = client.post(URL, json={**BODY, "language": "fr"}, headers=h)
    assert r.status_code == 422  # только поддерживаемые языки
