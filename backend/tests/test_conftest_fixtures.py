"""Фикстуры из conftest.py: test_user, user_headers, mock_gemini — на настоящем эндпоинте."""
from decimal import Decimal

from app.ai import AIUnavailable
from tests.conftest import GEMINI_VARIANTS

URL = "/api/v1/ai/generate-copy"
BODY = {"product_description": "Онлайн-курс Python с нуля", "target_audience": "новички"}


def test_test_user(client, test_user, user_headers):
    me = client.get("/api/v1/auth/me", headers=user_headers).json()
    assert (me["email"], me["balance"], me["held_balance"]) == ("test@example.com", 10.0, 0.0)
    assert client.get("/api/v1/wallet/history", headers=user_headers).json()["total"] == 1


def test_mock_gemini_success(client, test_user, user_headers, mock_gemini):
    r = client.post(URL, json=BODY, headers=user_headers)
    assert r.status_code == 200 and r.json()["data"] == GEMINI_VARIANTS
    assert r.json()["billing"]["remaining_balance"] == 9.99
    mock_gemini.assert_called_once_with(BODY["product_description"], BODY["target_audience"], "ru")


def test_mock_gemini_error(client, test_user, user_headers, mock_gemini):
    mock_gemini.side_effect = AIUnavailable("нет связи с Gemini API")
    r = client.post(URL, json=BODY, headers=user_headers)
    assert r.status_code == 503 and "нет связи" in r.json()["detail"]
    assert client.get("/api/v1/wallet/balance", headers=user_headers).json() == {
        "balance": 10.0, "held_balance": 0.0}


def test_auth_is_real(client, test_user, mock_gemini):
    # Пользователь не подменяется: без токена — 401, как в работе
    assert client.post(URL, json=BODY).status_code == 401
    mock_gemini.assert_not_called()
