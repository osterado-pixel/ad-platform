"""CORS для фронтенда, публичная выдача рекламы и заголовки безопасности."""
import pytest
from fastapi.testclient import TestClient

from app.main import app

FRONTEND = "http://localhost:5173"   # Vite (React/Vue) — в CORS_ORIGINS по умолчанию
EVIL = "https://evil.example"


def test_frontend_origin_allowed_for_private_api(client, auth_headers):
    r = client.get("/api/v1/auth/me", headers={**auth_headers, "Origin": FRONTEND})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == FRONTEND
    assert "access-control-allow-credentials" not in r.headers


def test_unknown_origin_gets_no_cors_for_private_api(client, auth_headers):
    r = client.get("/api/v1/auth/me", headers={**auth_headers, "Origin": EVIL})
    # Сервер отвечает, но без разрешения CORS браузер не отдаст ответ скрипту чужого сайта
    assert "access-control-allow-origin" not in r.headers


@pytest.mark.parametrize("origin,expected", [(FRONTEND, 200), (EVIL, 400)])
def test_private_preflight(client, origin, expected):
    r = client.options("/api/v1/campaigns", headers={
        "Origin": origin, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization, content-type",
    })
    assert r.status_code == expected
    if expected == 200:
        assert r.headers["access-control-allow-origin"] == FRONTEND
        assert "authorization" in r.headers["access-control-allow-headers"].lower()


def test_expose_pagination_headers(client, auth_headers):
    r = client.get("/api/v1/wallet/history", headers={**auth_headers, "Origin": FRONTEND})
    exposed = r.headers["access-control-expose-headers"].lower()
    assert "x-has-more" in exposed and "x-next-before-id" in exposed


@pytest.mark.parametrize("origin", [FRONTEND, EVIL, "https://any-partner.ru"])
def test_public_ad_endpoints_open_to_any_site(client, origin):
    r = client.get("/api/v1/ad/serve", params={"placement_code": "nope"}, headers={"Origin": origin})
    assert r.headers["access-control-allow-origin"] == "*"
    pre = client.options("/api/v1/ad/serve", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
    assert (pre.status_code, pre.headers["access-control-allow-origin"]) == (204, "*")
    assert client.get("/widget.js", headers={"Origin": origin}).headers["access-control-allow-origin"] == "*"


def test_security_headers_on_api(client):
    r = client.get("/api/v1/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in r.headers["permissions-policy"]
    assert r.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert "strict-transport-security" not in r.headers  # по HTTP HSTS не отдаётся


def test_app_has_strict_csp(client):
    csp = client.get("/app").headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-eval" not in csp
    assert "frame-ancestors 'none'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]


def test_docs_not_broken_by_csp(client):
    # Swagger UI грузит скрипты с CDN и использует встроенный скрипт — строгий CSP его бы сломал
    r = client.get("/docs")
    assert r.status_code == 200
    assert "content-security-policy" not in r.headers


def test_hsts_over_https():
    r = TestClient(app, base_url="https://testserver").get("/api/v1/health")
    assert r.headers["strict-transport-security"].startswith("max-age=31536000")


def test_charset_still_added(client):
    assert client.get("/api/v1/health").headers["content-type"] == "application/json; charset=utf-8"


def test_allowed_hosts_keeps_internal_addresses():
    from app.config import Settings
    s = Settings(secret_key="x" * 32, allowed_hosts="ads.example.com")
    assert s.allowed_host_list == ["ads.example.com", "127.0.0.1", "localhost"]
    assert Settings(secret_key="x" * 32, allowed_hosts="*").allowed_host_list == ["*"]
