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


def test_secret_key_generated_and_persisted_when_missing(tmp_path, monkeypatch):
    """Без SECRET_KEY платформа не падает: генерирует случайный ключ и хранит его в DATA_DIR."""
    from app import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    first = config.Settings(_env_file=None, secret_key="").secret_key
    assert len(first) >= 32
    assert "super_secret" not in first and first != config.Settings(_env_file=None, secret_key="x" * 32).secret_key
    # после «перезапуска» — тот же ключ (иначе все вышли бы из системы)
    assert config.Settings(_env_file=None, secret_key="").secret_key == first
    assert (tmp_path / "secret_key").read_text(encoding="utf-8") == first
    # у другой установки — другой
    other = tmp_path / "other"
    monkeypatch.setattr(config, "DATA_DIR", other)
    assert config.Settings(_env_file=None, secret_key="").secret_key != first


def test_short_secret_key_rejected():
    from pydantic import ValidationError
    from app.config import Settings
    with pytest.raises(ValidationError):
        Settings(_env_file=None, secret_key="short")


def test_algorithm_alias(monkeypatch):
    from app.config import Settings
    monkeypatch.setenv("ALGORITHM", "HS512")
    monkeypatch.delenv("JWT_ALGORITHM", raising=False)
    assert Settings(_env_file=None, secret_key="x" * 32).jwt_algorithm == "HS512"


def test_secret_key_same_for_concurrent_workers(tmp_path, monkeypatch):
    """Процессы сервера, стартующие одновременно, получают один и тот же ключ."""
    from concurrent.futures import ThreadPoolExecutor
    from app import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    with ThreadPoolExecutor(8) as ex:
        keys = set(ex.map(lambda _: config._load_or_create_secret_key(), range(32)))
    assert len(keys) == 1
    assert [p.name for p in tmp_path.iterdir()] == ["secret_key"]  # временные файлы убраны


def test_api_docs_can_be_disabled():
    # Настройка читается при создании приложения — проверяем в отдельном процессе с API_DOCS=false
    import os
    import subprocess
    import sys
    code = (
        "from fastapi.testclient import TestClient\n"
        "from app.main import app\n"
        "c = TestClient(app)\n"
        "print([c.get(p).status_code for p in ('/docs', '/redoc', '/openapi.json')], 'docs' in c.get('/').json())\n"
    )
    env = {**os.environ, "API_DOCS": "false", "DATABASE_URL": "sqlite://"}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                         cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert out.stdout.strip() == "[404, 404, 404] False", out.stderr
