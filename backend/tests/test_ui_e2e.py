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
SERVER_DB = {}  # адрес БД тестового сервера — для тестов, которым нужно подготовить данные напрямую


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
           "BCRYPT_ROUNDS": "4",
           # Все пользователи тестов регистрируются с 127.0.0.1; сам лимит проверяют юнит-тесты
           "REGISTER_MAX_PER_IP_PER_HOUR": "1000",
           "PAYMENTS_PROVIDER": "test",  # оплата картой — тестовым провайдером (деньги ненастоящие)
           "ANTHROPIC_API_KEY": "", "GEMINI_API_KEY": "", "OPENAI_API_KEY": ""}  # без обращений к внешним API
    SERVER_DB["url"] = db_url
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
        # Установленный Edge/Chrome, иначе встроенный Chromium Playwright (CI: playwright install chromium)
        for channel in ("msedge", "chrome", None):
            try:
                b = p.chromium.launch(channel=channel, headless=True)
                break
            except Exception:
                continue
        if b is None:
            pytest.skip("Нет браузера: установите Edge/Chrome или выполните playwright install chromium")
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


def new_page(browser, errors, locale="ru-RU"):
    # Язык браузера ru-RU: кабинет открывается по-русски (cookie lang не задан) — тексты в тестах русские
    ctx = browser.new_context(locale=locale)
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
    pid = httpx.get(base + "/api/v1/placements").json()["items"][0]["id"]
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


def test_ai_hint_in_moderation_queue(server, browser):
    """Подсказка AI видна в очереди модерации; текст модели выводится как текст, не как HTML."""
    from datetime import datetime, timezone

    import httpx2 as httpx
    from sqlalchemy import create_engine, update

    from app.models import Campaign

    errors, base = [], server
    httpx.post(base + "/api/v1/auth/register", json={"email": "ai@e2e.ru", "password": PASSWORD})
    token = httpx.post(base + "/api/v1/auth/login",
                       data={"username": "ai@e2e.ru", "password": PASSWORD}).json()["access_token"]
    pid = httpx.get(base + "/api/v1/placements").json()["items"][0]["id"]
    with httpx.Client(headers={"Authorization": f"Bearer {token}"}) as c:
        cid = c.post(base + "/api/v1/campaigns", json={
            "placement_id": pid, "title": "Заработок без вложений", "target_url": "https://example.com/"}).json()["id"]
        assert c.post(f"{base}/api/v1/campaigns/{cid}/submit").status_code == 200

    # Ключа API у тестового сервера нет — результат проверки записываем как будто его сохранил фон
    engine = create_engine(SERVER_DB["url"])
    with engine.begin() as conn:
        conn.execute(update(Campaign).where(Campaign.id == cid).values(
            ai_verdict="reject", ai_risk="high", ai_summary="Похоже на финансовую пирамиду",
            ai_reasons=["Гарантированный доход", "<img src=x onerror=alert('XSS')>"],
            ai_checked_at=datetime.now(timezone.utc)))
    engine.dispose()

    admin = new_page(browser, errors)
    login(admin, base, "admin@e2e.ru")
    admin.click("nav a:has-text('Модерация')")
    card = admin.locator(".card", has_text="Заработок без вложений")
    hint = card.locator(".notice.error")
    playwright_api.expect(hint).to_contain_text("ИИ: рекомендует отклонить · риск высокий")
    playwright_api.expect(hint).to_contain_text("Похоже на финансовую пирамиду")
    playwright_api.expect(hint.locator("li")).to_have_text(
        ["Гарантированный доход", "<img src=x onerror=alert('XSS')>"])
    assert hint.locator("img").count() == 0
    # AI выключен — кнопки перепроверки нет, решение за модератором
    assert card.locator("button:has-text('ИИ')").count() == 0

    card.locator("input").fill("Мошенничество")
    card.locator("button:has-text('Отклонить')").click()
    admin.wait_for_selector("text=Очередь пуста")
    assert errors == [], errors


def _ai_routes(page, task_states, enabled=True):
    """Подменяет ответы /api/v1/ai/* в браузере: Gemini не нужен, проверяется логика интерфейса."""
    import json as _json
    page.route("**/api/v1/ai/status", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=_json.dumps({"enabled": enabled, "hold_amount": 0.03, "max_active_tasks": 5})))
    page.route("**/api/v1/ai/generate-async", lambda route: route.fulfill(
        status=202, content_type="application/json",
        body=_json.dumps({"task_id": "t-1", "status": "pending", "check_status_url": "/api/v1/ai/tasks/t-1",
                          "held_amount": 0.03, "message": "Средства зарезервированы, задача запущена"})))
    states = list(task_states)

    def task(route):
        body = states.pop(0) if len(states) > 1 else states[0]
        route.fulfill(status=200, content_type="application/json", body=_json.dumps({
            "task_id": "t-1", "created_at": "2026-10-06T10:00:00Z", "updated_at": "2026-10-06T10:00:01Z",
            "result": None, "error": None, **body}))
    page.route("**/api/v1/ai/tasks/t-1", task)


def _advertiser(server, email):
    import httpx2 as httpx
    httpx.post(server + "/api/v1/auth/register", json={"email": email, "password": PASSWORD})


def test_ai_copywriter_in_campaign_form(server, browser):
    errors = []
    _advertiser(server, "copy@e2e.ru")
    page = new_page(browser, errors)
    xss = "<img src=x onerror=alert('XSS')>"
    variants = [{"title": "Python с нуля за 3 месяца", "text": "Практика и ментор", "cta": "Записаться"},
                {"title": xss, "text": "Вариант 2", "cta": "Купить"},
                {"title": "Курс Python", "text": "Вариант 3", "cta": "Начать"}]
    _ai_routes(page, [{"status": "pending"}, {"status": "processing"},
                      {"status": "completed", "result": {"variants": variants}}])
    login(page, server, "copy@e2e.ru")
    page.goto(server + "/app#/campaigns/new")

    page.fill("#ai-product", "коротко")  # меньше 10 символов — запрос не отправляется
    page.click("#ai-generate")
    page.wait_for_selector("text=Опишите товар подробнее")

    page.fill("#ai-product", "Онлайн-курс Python для начинающих, 40 уроков")
    page.click("#ai-generate")
    playwright_api.expect(page.locator(".ai-variant")).to_have_count(3, timeout=15000)
    playwright_api.expect(page.locator("#ai-copywriter .notice.ok")).to_contain_text("Готово")
    # Текст модели — как текст, не как HTML
    playwright_api.expect(page.locator(".ai-variant").nth(1)).to_contain_text(xss)
    assert page.locator(".ai-variant img").count() == 0

    page.locator(".ai-variant").first.locator("button:has-text('Использовать')").click()
    assert page.input_value("#f-title") == "Python с нуля за 3 месяца"
    assert page.input_value("#f-description") == "Практика и ментор Записаться"
    playwright_api.expect(page.locator(".ad-preview, .card").filter(has_text="Python с нуля за 3 месяца").first).to_be_visible()
    assert errors == [], errors


def test_ai_copywriter_failed_task_and_disabled(server, browser):
    errors = []
    _advertiser(server, "copyfail@e2e.ru")
    page = new_page(browser, errors)
    _ai_routes(page, [{"status": "failed", "error": "AI-генерация не выполнена: превышен лимит. Деньги не списаны"}])
    login(page, server, "copyfail@e2e.ru")
    page.goto(server + "/app#/campaigns/new")
    page.fill("#ai-product", "Онлайн-курс Python для начинающих")
    page.click("#ai-generate")
    playwright_api.expect(page.locator("#ai-copywriter .notice.error")).to_contain_text("Деньги не списаны", timeout=15000)
    assert page.locator(".ai-variant").count() == 0
    playwright_api.expect(page.locator("#ai-generate")).to_be_enabled()  # можно попробовать снова

    # Копирайтер выключен (на тестовом сервере нет GEMINI_API_KEY) — блока нет, форма работает
    page2 = new_page(browser, errors)
    login(page2, server, "copyfail@e2e.ru")
    page2.goto(server + "/app#/campaigns/new")
    page2.wait_for_selector("#f-title")
    assert page2.locator("#ai-copywriter").count() == 0
    assert errors == [], errors


def test_telegram_card_in_profile(server, browser):
    import json as _json
    errors = []
    _advertiser(server, "tg@e2e.ru")
    page = new_page(browser, errors)
    state = {"linked": False}
    page.route("**/api/v1/telegram/status", lambda r: r.fulfill(status=200, content_type="application/json",
               body=_json.dumps({"enabled": True, "bot_username": "AdPlatformBot", "linked": state["linked"]})))
    page.route("**/api/v1/telegram/link-code", lambda r: r.fulfill(status=200, content_type="application/json",
               body=_json.dumps({"code": "ABCD2345", "expires_at": "2026-10-07T12:00:00Z",
                                 "deep_link": "https://t.me/AdPlatformBot?start=ABCD2345"})))

    def unlink(route):
        state["linked"] = False
        route.fulfill(status=204)
    page.route("**/api/v1/telegram/link", unlink)

    login(page, server, "tg@e2e.ru")
    page.goto(server + "/app#/profile")
    card = page.locator("#telegram-card")
    playwright_api.expect(card).to_contain_text("@AdPlatformBot")
    card.locator("button:has-text('Получить код привязки')").click()
    playwright_api.expect(card).to_contain_text("ABCD2345")
    link = card.locator("a:has-text('Открыть бота')")
    assert link.get_attribute("href") == "https://t.me/AdPlatformBot?start=ABCD2345"
    assert link.get_attribute("rel") == "noopener noreferrer"

    state["linked"] = True
    page.reload()
    playwright_api.expect(page.locator("#telegram-card .notice.ok")).to_contain_text("Telegram привязан")
    page.locator("#telegram-card button:has-text('Отвязать')").click()
    playwright_api.expect(page.locator("#telegram-card")).to_contain_text("Получить код привязки")

    # Бот не подключён к платформе (на тестовом сервере нет секрета) — блока нет
    page2 = new_page(browser, errors)
    login(page2, server, "tg@e2e.ru")
    page2.goto(server + "/app#/profile")
    page2.wait_for_selector("h1:has-text('Профиль')")
    assert page2.locator("#telegram-card").count() == 0
    assert errors == [], errors


def test_top_up_by_card_and_plan(server, browser):
    """Пополнение картой (тестовый провайдер) и покупка тарифа — на настоящем бэкенде."""
    import httpx2 as httpx
    errors = []
    _advertiser(server, "pay@e2e.ru")
    admin_token = httpx.post(server + "/api/v1/auth/login",
                             data={"username": "admin@e2e.ru", "password": PASSWORD}).json()["access_token"]
    r = httpx.post(server + "/api/v1/plans", headers={"Authorization": f"Bearer {admin_token}"},
                   json={"code": "pro_e2e", "name": "Pro E2E", "price": "19", "period_days": 30,
                         "description": "Для проверки", "features": {"ai_generations": 300}})
    assert r.status_code == 201, r.text

    page = new_page(browser, errors)
    login(page, server, "pay@e2e.ru")
    page.click("nav a:has-text('Кошелёк')")
    playwright_api.expect(page.locator(".notice.warn")).to_contain_text("деньги ненастоящие")
    page.fill("#pay-amount", "75")
    page.click("button:has-text('Пополнить картой')")
    page.wait_for_selector("h1:has-text('Тестовая оплата')")       # «страница банка»
    playwright_api.expect(page.locator("body")).to_contain_text("75.00 RUB")
    page.click("button:has-text('Оплатить')")
    page.wait_for_selector("h1:has-text('Кошелёк')")                # возврат в кошелёк
    playwright_api.expect(page.locator("#balance")).to_have_text("75,00")
    playwright_api.expect(page.locator("td:has-text('Оплата картой')")).to_be_visible()

    # Тариф: покупка через ту же оплату
    card = page.locator("#plans-card")
    playwright_api.expect(card).to_contain_text("Тариф не подключён")
    card.locator("button:has-text('Купить за 19,00')").click()
    page.wait_for_selector("h1:has-text('Тестовая оплата')")
    page.click("button:has-text('Оплатить')")
    page.wait_for_selector("h1:has-text('Кошелёк')")
    playwright_api.expect(page.locator("#plans-card .notice.ok")).to_contain_text("Pro E2E")
    playwright_api.expect(page.locator("#balance")).to_have_text("75,00")  # тариф оплачен картой, не с баланса

    # Отмена оплаты — деньги не зачисляются
    page.fill("#pay-amount", "10")
    page.click("button:has-text('Пополнить картой')")
    page.wait_for_selector("h1:has-text('Тестовая оплата')")
    page.click("button:has-text('Отменить')")
    page.wait_for_selector("h1:has-text('Кошелёк')")
    playwright_api.expect(page.locator("#balance")).to_have_text("75,00")
    assert errors == [], errors


def test_cabinet_language_follows_browser_and_switcher(server, browser):
    expect = playwright_api.expect
    errors = []
    # Немецкий браузер без выбора языка → кабинет по-немецки
    page = new_page(browser, errors, locale="de-DE")
    page.goto(server + "/app")
    expect(page.locator(".tabs button.on")).to_have_text("Anmelden")
    assert page.evaluate("document.documentElement.lang") == "de"

    # Ошибка сервера — тоже по-немецки: кабинет передаёт язык в Accept-Language
    page.fill("#auth-email", "nobody@e2e.ru")
    page.fill("#auth-password", PASSWORD)
    page.click("form button[type=submit]")
    expect(page.locator(".notice.error")).to_have_text("E-Mail-Adresse oder Passwort ist falsch")
    errors[:] = [e for e in errors if "401" not in e]  # браузер пишет в консоль ожидаемый ответ 401

    page.click(".tabs button:has-text('Registrieren')")
    page.fill("#auth-email", "lang@e2e.ru")
    page.fill("#auth-password", PASSWORD)
    page.click("form button[type=submit]")
    page.wait_for_selector("h1:has-text('Übersicht')")
    expect(page.locator("#balance")).to_have_text("0,00")  # деньги в немецком формате

    # Переключатель: страница перерисовывается на английском, выбор запоминается в cookie сайта
    page.click(".topbar .langs button:has-text('EN')")
    page.wait_for_selector("h1:has-text('Overview')")
    expect(page.locator("nav a.active")).to_have_text("Overview")
    expect(page.locator(".topbar .langs button.on")).to_have_text("EN")
    assert page.evaluate("document.documentElement.lang") == "en"
    assert any(c["name"] == "lang" and c["value"] == "en" for c in page.context.cookies())
    expect(page.locator("#balance")).to_have_text("0.00")

    # Cookie важнее языка браузера: после перезагрузки — снова английский
    page.reload()
    page.wait_for_selector("h1:has-text('Overview')")
    page.click("nav a:has-text('Wallet')")
    page.wait_for_selector("h2:has-text('Transaction history')")

    # Обратно на русский — на той же странице
    page.click(".topbar .langs button:has-text('RU')")
    page.wait_for_selector("h1:has-text('Кошелёк')")
    assert errors == [], errors


def _api_login(httpx, base, email):
    token = httpx.post(base + "/api/v1/auth/login", data={"username": email, "password": PASSWORD}).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


def test_partner_program_in_ui(server, browser):
    """Партнёр приходит по реферальной ссылке, добавляет сайт и площадку; админ одобряет сайт;
    клик по баннеру на сайте партнёра — заработок у партнёра и вознаграждение пригласившему."""
    import httpx2 as httpx
    base, errors = server, []
    admin_h = _api_login(httpx, base, "admin@e2e.ru")
    ref = httpx.get(base + "/api/v1/partner/referral", headers=admin_h).json()

    # 1. Партнёр открывает реферальную ссылку: сразу форма регистрации
    pub = new_page(browser, errors)
    pub.goto(ref["link"])
    pub.wait_for_selector(".tabs button.on:has-text('Регистрация')")
    pub.fill("#auth-email", "pub@e2e.ru")
    pub.fill("#auth-password", PASSWORD)
    pub.click("form button[type=submit]")
    pub.wait_for_selector("h1:has-text('Обзор')")
    assert httpx.get(base + "/api/v1/partner/referral", headers=admin_h).json()["invited"] == ref["invited"] + 1

    # 2. Сайт и площадка
    pub.click("nav a:has-text('Партнёрам')")
    pub.fill("input[placeholder='Название сайта']", "Блог E2E")
    pub.fill("input[type=url]", "https://blog-e2e.example.com")
    pub.click("button:has-text('Добавить сайт')")
    pub.wait_for_selector(".badge:has-text('На проверке')")
    pub.fill("input[placeholder='Например: баннер под статьёй']", "Под статьёй")
    pub.click("button:has-text('Создать площадку')")
    snippet = pub.locator("pre:has-text('data-placement=\"s')").first
    snippet.wait_for()
    code = snippet.inner_text().split('data-placement="')[1].split('"')[0]

    # 3. Админ одобряет сайт
    admin = new_page(browser, errors)
    login(admin, base, "admin@e2e.ru")
    admin.click("nav a:has-text('Сайты партнёров')")
    admin.locator("tr", has_text="Блог E2E").locator("button:has-text('Одобрить')").click()
    admin.wait_for_selector("text=Сайтов нет")

    # 4. Рекламодатель (через API): кампания на всю сеть со ставкой 1.00, одобрена, баланс пополнен
    httpx.post(base + "/api/v1/auth/register", json={"email": "net@e2e.ru", "password": PASSWORD})
    adv_h = _api_login(httpx, base, "net@e2e.ru")
    adv_id = httpx.get(base + "/api/v1/auth/me", headers=adv_h).json()["id"]
    cid = httpx.post(base + "/api/v1/campaigns", headers=adv_h, json={
        "title": "Сетевая E2E", "target_url": "https://example.com/net", "cpc_bid": 1}).json()["id"]
    httpx.post(f"{base}/api/v1/campaigns/{cid}/submit", headers=adv_h)
    httpx.patch(f"{base}/api/v1/campaigns/{cid}/moderate", headers=admin_h, json={"status": "active"})
    httpx.post(f"{base}/api/v1/wallet/deposit?user_id={adv_id}", headers=admin_h, json={"amount": 10})

    # 5. Посетитель сайта партнёра кликает по баннеру
    visitor = new_page(browser, errors)
    visitor.goto(f"{base}/demo?placement={code}")
    banner = visitor.locator("a:has-text('Сетевая E2E')")
    banner.wait_for()
    with visitor.expect_popup() as popup:
        banner.click()
    popup.value.wait_for_load_state()

    # 6. У партнёра — 60% от 1.00 «созревает», сайт одобрен
    pub.reload()
    pub.wait_for_selector(".badge:has-text('Одобрен')")
    pub.wait_for_selector(".stat:has-text('Созревает') >> text=0,60")
    shots = os.environ.get("E2E_SCREENSHOTS")
    if shots:  # для просмотра страниц глазами: E2E_SCREENSHOTS=папка
        pub.screenshot(path=str(Path(shots) / "partner.png"), full_page=True)
        admin.click("nav a:has-text('Сайты партнёров')")
        admin.click(".tabs button:has-text('Одобрен')")
        admin.wait_for_selector("tr:has-text('Блог E2E')")
        admin.screenshot(path=str(Path(shots) / "admin_sites.png"), full_page=True)
    assert errors == [], errors
