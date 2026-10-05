"""
Сквозной тест в настоящем браузере: весь путь от регистрации до оплаченного клика.

Запуск (нужны `pip install playwright` и установленный Microsoft Edge или Google Chrome):
    python -m pytest -m e2e -v

Поднимает отдельный сервер на временной БД — рабочая app.db не затрагивается.
На PostgreSQL: E2E_DATABASE_URL=postgresql+psycopg2://user@host/пустая_бд python -m pytest -m e2e
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e
playwright_api = pytest.importorskip("playwright.sync_api")

BACKEND = Path(__file__).resolve().parent.parent
PASSWORD = "password123"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    tmp = tempfile.mkdtemp()
    port = _free_port()
    # E2E_DATABASE_URL — прогон на PostgreSQL (нужна пустая база), иначе временный SQLite-файл
    db_url = os.environ.get("E2E_DATABASE_URL") or "sqlite:///" + str(Path(tmp) / "e2e.db").replace("\\", "/")
    env = {**os.environ,
           "DATABASE_URL": db_url,
           "SECRET_KEY": "e2e-secret-key-0123456789abcdefghijklmnopq",
           "BCRYPT_ROUNDS": "4"}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env,
                   check=True, capture_output=True)
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port),
                             "--log-level", "warning"], cwd=BACKEND, env=env)
    base = f"http://127.0.0.1:{port}"
    import httpx2 as httpx
    for _ in range(100):
        try:
            httpx.get(base + "/api/v1/health")
            break
        except Exception:
            time.sleep(0.1)

    # Админ и площадка: админ регистрируется через API и назначается командой CLI
    httpx.post(base + "/api/v1/auth/register", json={"email": "admin@e2e.ru", "password": PASSWORD})
    subprocess.run([sys.executable, "-m", "app.cli", "make-admin", "admin@e2e.ru"], cwd=BACKEND,
                   env=env, check=True, capture_output=True)
    token = httpx.post(base + "/api/v1/auth/login",
                       data={"username": "admin@e2e.ru", "password": PASSWORD}).json()["access_token"]
    r = httpx.post(base + "/api/v1/placements", headers={"Authorization": f"Bearer {token}"},
                   json={"name": "E2E баннер", "code_identifier": "e2e_banner", "price_per_click": 5})
    assert r.status_code == 201
    yield base
    proc.terminate()
    proc.wait(10)


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        b = None
        for channel in ("msedge", "chrome"):
            try:
                b = p.chromium.launch(channel=channel, headless=True)
                break
            except Exception:
                continue
        if b is None:
            pytest.skip("Не найден Microsoft Edge или Google Chrome")
        yield b
        b.close()


OPEN_PAGES = []


@pytest.fixture(autouse=True)
def screenshots_on_failure(request):
    """При падении сохраняет скриншоты всех вкладок теста — видно, на каком экране сбой."""
    OPEN_PAGES.clear()
    yield
    report = getattr(request.node, "rep_call", None)
    if report is not None and report.failed:
        out = Path(tempfile.gettempdir()) / "adp_e2e_failures"
        out.mkdir(exist_ok=True)
        for i, page in enumerate(OPEN_PAGES):
            path = out / f"{request.node.name}_{i}.png"
            try:
                page.screenshot(path=str(path), full_page=True)
                print(f"\n[e2e] скриншот вкладки {i} ({page.url}): {path}")
            except Exception:
                pass


def new_page(browser, errors):
    ctx = browser.new_context(locale="ru-RU")
    # Внешние сайты подменяем: тест не зависит от интернета
    ctx.route("https://example.com/**", lambda route: route.fulfill(
        status=200, content_type="text/html", body="<h1>Сайт рекламодателя</h1>"))
    page = ctx.new_page()
    OPEN_PAGES.append(page)
    page.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text}"))
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("dialog", lambda d: (errors.append(f"dialog: {d.message}") if "XSS" in d.message else None,
                                 d.accept()))
    return page


def login(page, base, email):
    page.goto(base + "/app")
    page.fill("#auth-email", email)
    page.fill("#auth-password", PASSWORD)
    page.click("form button[type=submit]")
    page.wait_for_selector("h1:has-text('Обзор')")


def test_full_ui_flow(server, browser):
    base, errors = server, []

    # 1. Рекламодатель регистрируется и создаёт кампанию
    adv = new_page(browser, errors)
    adv.goto(base + "/app")
    adv.click(".tabs button:has-text('Регистрация')")
    adv.fill("#auth-email", "adv@e2e.ru")
    adv.fill("#auth-password", PASSWORD)
    adv.click("form button[type=submit]")
    adv.wait_for_selector("h1:has-text('Обзор')")

    adv.click("nav a:has-text('Кампании')")
    adv.click("a:has-text('+ Новая кампания')")
    adv.fill("#f-title", "E2E кампания")
    adv.fill("#f-description", "Скидка 50%")
    adv.fill("#f-target", "https://example.com/landing")
    adv.click("button:has-text('Создать черновик')")
    adv.wait_for_selector("h1:has-text('E2E кампания')")
    adv.click("button:has-text('На модерацию')")
    adv.wait_for_selector(".badge:has-text('На модерации')")

    # XSS: заголовок с HTML должен показаться как текст, а не выполниться
    adv.goto(base + "/app#/campaigns/new")
    adv.fill("#f-title", "<img src=x onerror=alert('XSS')>")
    adv.fill("#f-target", "https://example.com/x")
    adv.click("button:has-text('Создать черновик')")
    adv.wait_for_selector("h1:has-text(\"<img src=x onerror=alert('XSS')>\")")

    # 2. Админ одобряет и пополняет баланс рекламодателю
    admin = new_page(browser, errors)
    login(admin, base, "admin@e2e.ru")
    admin.click("nav a:has-text('Модерация')")
    card = admin.locator(".card", has_text="E2E кампания")
    card.locator("button:has-text('Одобрить')").click()
    admin.wait_for_selector("text=Очередь пуста")

    admin.click("nav a:has-text('Пользователи')")
    row = admin.locator("tr", has_text="adv@e2e.ru")
    row.locator("input[type=number]").fill("100")
    row.locator("button:has-text('Пополнить')").click()
    admin.wait_for_selector("tr:has-text('adv@e2e.ru') >> text=100,00")

    # Код вставки площадки виден админу
    admin.click("nav a:has-text('Площадки')")
    admin.wait_for_selector("pre:has-text('data-placement=\"e2e_banner\"')")

    # 3. Посетитель сайта-партнёра видит баннер (виджет) и кликает
    visitor = new_page(browser, errors)
    visitor.goto(base + "/demo?placement=e2e_banner")
    banner = visitor.locator("a:has-text('E2E кампания')")  # Playwright видит Shadow DOM
    banner.wait_for()
    assert banner.get_attribute("rel") == "noopener noreferrer sponsored"
    with visitor.expect_popup() as popup:
        banner.click()
    popup.value.wait_for_load_state()
    assert popup.value.url == "https://example.com/landing"

    # 4. Деньги списаны, клик и показ в статистике, операция в истории
    adv.goto(base + "/app#/overview")
    adv.wait_for_selector(".stat:has-text('Баланс') >> text=95,00")
    adv.wait_for_selector("#balance:has-text('95,00')")
    adv.click("a:has-text('E2E кампания')")
    adv.wait_for_selector(".stat:has-text('Всего кликов') >> text=1")
    adv.wait_for_selector(".stat:has-text('Всего показов') >> text=1")
    adv.click("nav a:has-text('Кошелёк')")
    adv.wait_for_selector("td:has-text('Оплата клика')")
    adv.wait_for_selector("td:has-text('Пополнение')")

    # Пауза — баннер пропадает с сайта
    adv.click("nav a:has-text('Кампании')")
    adv.locator("tr", has_text="E2E кампания").locator("button:has-text('Пауза')").click()
    adv.wait_for_selector("tr:has-text('E2E кампания') >> .badge:has-text('На паузе')")
    with visitor.expect_response(lambda r: "/api/v1/ad/serve" in r.url) as served:
        visitor.goto(base + "/demo?placement=e2e_banner")
    assert served.value.status == 204  # рекламы нет; 204, а не 404 — без ошибки в консоли сайта
    assert visitor.locator("a:has-text('E2E кампания')").count() == 0

    # Админ видит выручку
    admin.click("nav a:has-text('Платформа')")
    admin.wait_for_selector(".stat:has-text('Выручка за период') >> text=5,00")

    assert errors == [], errors


def test_expired_token_shows_login(server, browser):
    errors = []
    page = new_page(browser, errors)
    page.goto(server + "/app")
    page.evaluate("localStorage.setItem('adp_token', 'expired.invalid.token')")
    page.goto(server + "/app#/overview")
    page.wait_for_selector("#auth-email")
    assert page.locator("text=Не удалось загрузить профиль").count() == 0


def test_advertiser_has_no_admin_pages(server, browser):
    errors = []
    page = new_page(browser, errors)
    page.goto(server + "/app")
    page.click(".tabs button:has-text('Регистрация')")
    page.fill("#auth-email", "plain@e2e.ru")
    page.fill("#auth-password", PASSWORD)
    page.click("form button[type=submit]")
    page.wait_for_selector("h1:has-text('Обзор')")
    assert page.locator("nav a:has-text('Модерация')").count() == 0
    page.goto(server + "/app#/admin/users")
    page.wait_for_selector("h1:has-text('Обзор')")  # недоступная страница → обзор


def test_campaigns_load_more(server, browser):
    import httpx2 as httpx
    errors = []
    base = server
    httpx.post(base + "/api/v1/auth/register", json={"email": "many@e2e.ru", "password": PASSWORD})
    token = httpx.post(base + "/api/v1/auth/login",
                       data={"username": "many@e2e.ru", "password": PASSWORD}).json()["access_token"]
    pid = httpx.get(base + "/api/v1/placements").json()[0]["id"]
    with httpx.Client(headers={"Authorization": f"Bearer {token}"}) as c:
        for i in range(55):
            assert c.post(base + "/api/v1/campaigns", json={
                "placement_id": pid, "title": f"Кампания {i:02d}", "target_url": "https://example.com/"}).status_code == 201

    page = new_page(browser, errors)
    login(page, base, "many@e2e.ru")
    page.click("nav a:has-text('Кампании')")
    expect = playwright_api.expect  # ждёт с повторами, а не проверяет мгновенно
    rows = page.locator("main tbody tr")
    expect(rows).to_have_count(50)
    expect(rows.first).to_contain_text("Кампания 54")
    page.click("button:has-text('Показать ещё')")
    expect(rows).to_have_count(55)
    expect(rows.last).to_contain_text("Кампания 00")
    expect(page.locator("button:has-text('Показать ещё')")).to_be_hidden()
    assert errors == [], errors


def test_change_password_in_ui(server, browser):
    import httpx2 as httpx
    errors = []
    httpx.post(server + "/api/v1/auth/register", json={"email": "pwd@e2e.ru", "password": PASSWORD})
    page = new_page(browser, errors)
    login(page, server, "pwd@e2e.ru")
    other = httpx.post(server + "/api/v1/auth/login",
                       data={"username": "pwd@e2e.ru", "password": PASSWORD}).json()["access_token"]

    page.click("a:has-text('pwd@e2e.ru')")
    page.fill("#p-current", PASSWORD)
    page.fill("#p-new", "brandnew123")
    page.fill("#p-again", "brandnew123")
    page.click("button:has-text('Сменить пароль')")
    page.wait_for_selector("text=Пароль изменён")
    # Текущий сеанс работает с новым токеном, другой «устройство» — разлогинен
    page.click("nav a:has-text('Кошелёк')")
    page.wait_for_selector("h1:has-text('Кошелёк')")
    assert httpx.get(server + "/api/v1/auth/me", headers={"Authorization": f"Bearer {other}"}).status_code == 401
    assert errors == [], errors
