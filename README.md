# Ad Platform — рекламная платформа

Рекламодатели создают кампании, администратор их модерирует, сайты-партнёры показывают
баннеры одной строкой кода, клики оплачиваются с баланса рекламодателя.

- **Веб-интерфейс** — кабинет рекламодателя и админ-панель: `/app`
- **Виджет для сайтов** — `<script>` одной строкой: `/widget.js`, демо: `/demo`
- **REST API** с документацией: `/docs`
- Бэкенд: FastAPI, SQLAlchemy 2, Alembic; база — SQLite (локально) или PostgreSQL (сервер)

## Быстрый старт

### Windows, без Docker

Нужен Python 3.12–3.14 ([python.org](https://www.python.org/downloads/)).

```powershell
cd backend
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

Скрипт сам создаст окружение, установит зависимости, создаст `backend\.env` с секретным ключом,
базу `backend\app.db` и запустит сервер. Повторные запуски — той же командой.

Первый администратор (в другом окне, из папки `backend`):

```powershell
.\venv\Scripts\python.exe -m app.cli create-admin admin@example.com
```

Откройте <http://127.0.0.1:8000/app> и войдите.

### Docker + PostgreSQL (сервер)

```bash
cp .env.example .env          # заполните POSTGRES_PASSWORD и SECRET_KEY
docker compose up -d --build
docker compose exec api python -m app.cli create-admin admin@example.com
```

Платформа: `http://СЕРВЕР:8000/app`. Миграции базы применяются автоматически при старте.
Данные PostgreSQL хранятся в томе `pgdata` и переживают пересборку.

### Вручную (любая ОС)

```bash
cd backend
python -m venv venv
venv/bin/pip install -r requirements.txt          # Windows: venv\Scripts\pip
cp .env.example .env                              # впишите SECRET_KEY
venv/bin/alembic upgrade head
venv/bin/python -m app.cli create-admin admin@example.com
venv/bin/uvicorn app.main:app --reload --reload-dir app
```

## Как пользоваться

1. **Админ → Площадки**: создайте рекламное место (код, цена клика) и скопируйте код вставки.
2. **Сайт-партнёр** вставляет код туда, где должен быть баннер:
   ```html
   <script async src="https://ВАШ-СЕРВЕР/widget.js" data-placement="header_banner"></script>
   ```
   Несколько мест на странице: `<div data-adp-placement="код"></div>` + один `<script src=".../widget.js">`.
   Нет рекламы — место остаётся пустым, без ошибок в консоли сайта.
3. **Рекламодатель** регистрируется на `/app`, создаёт кампанию (заголовок, текст, картинка, ссылка,
   период показа) и отправляет на модерацию.
4. **Админ → Модерация**: одобряет или отклоняет с причиной. **Админ → Пользователи**: пополняет баланс
   (платёжной системы нет — зачисление вручную).
5. Одобренная кампания показывается на площадке; каждый уникальный клик списывает цену клика
   с баланса и перенаправляет посетителя на сайт рекламодателя.

Статистика (показы, клики, CTR, расход по дням) — в разделах «Обзор» и на странице кампании;
выручка платформы — «Админ → Платформа».

## Правила, которые стоит знать

| Тема | Как работает |
|---|---|
| Статусы кампании | черновик → на модерации → активна ⇄ на паузе → завершена; отклонённую можно исправить и отправить снова |
| Изменение одобренной кампании | правка текста, ссылки, картинки или площадки отправляет её на повторную модерацию (защита от подмены); даты — нет |
| Нет денег | кампании владельца не показываются, клики по старым баннерам не списываются; после пополнения показ возобновляется сам |
| Повторные клики | один IP по одной кампании оплачивается не чаще раза в `CLICK_DEDUP_MINUTES` (10 минут); боты и предпросмотры ссылок (Telegram, Slack…) — бесплатно |
| Удаление | только черновик или отклонённая кампания без показов; остальные завершаются — статистика сохраняется |
| Деньги | в базе и расчётах — `Decimal` (точно, без ошибок `0.1 + 0.2`); в JSON — число, округлённое до копеек (`95.0`, `12.35`); на вход принимается и число, и строка. Каждое движение — в журнале `/wallet/history`, баланс всегда равен сумме журнала |
| CTR | клики / показы, %, 2 знака; всегда число, `0` — если показов не было |
| Цена за день | хранится у площадки, но пока не списывается — оплата только за клики |

## Настройки

`backend/.env` (локально) или `.env` рядом с `docker-compose.yml` (Docker):

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `SECRET_KEY` | — (обязательно) | ключ подписи токенов входа, ≥ 32 символов |
| `DATABASE_URL` | `sqlite:///backend/app.db` | например `postgresql+psycopg2://user:pass@host:5432/db` |
| `CORS_ORIGINS` | локальные порты 3000, 5173, 8080 | домены отдельного фронтенда (React/Vue/Next.js), через запятую. Выдача рекламы и виджет доступны с любых сайтов независимо от этой настройки |
| `ALLOWED_HOSTS` | `*` | домены платформы, например `ads.example.com` (защита от подмены `Host`) |
| `CLICK_DEDUP_MINUTES` | `10` | окно защиты от повторных кликов |
| `BCRYPT_ROUNDS` | `12` | стоимость хеширования паролей |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | (Docker) адрес прокси, которому доверять `X-Forwarded-For` |
| `WEB_WORKERS` | `2` | (Docker) число процессов сервера |

**За nginx/балансировщиком** обязательно передавайте реальный IP посетителя (`X-Forwarded-For`) и
укажите адрес прокси в `FORWARDED_ALLOW_IPS` (без Docker — флаги uvicorn
`--proxy-headers --forwarded-allow-ips АДРЕС`). Иначе все посетители будут «с одного IP» прокси, и
защита от повторных кликов перестанет оплачивать клики разных людей.

**SQLite или PostgreSQL.** SQLite подходит для одного сервера и одного процесса (≈ 300 оплаченных
кликов/с). Для нескольких процессов (`WEB_WORKERS` > 1) и роста — PostgreSQL.

## Подключение своего фронтенда (React, Vue, Next.js)

Встроенный интерфейс `/app` работает без настроек. Для отдельного фронтенда на другом домене
добавьте его адрес в `CORS_ORIGINS` (для разработки `http://localhost:3000`, `:5173`, `:8080` уже
разрешены):

```js
const API = "http://127.0.0.1:8000/api/v1";
// Вход: форма (OAuth2), поле username — это email
const { access_token } = await fetch(`${API}/auth/login`, {
  method: "POST", body: new URLSearchParams({ username: email, password }),
}).then((r) => r.json());
const auth = { Authorization: `Bearer ${access_token}` };   // cookie не используются

// Списки — постранично
const r = await fetch(`${API}/campaigns/my?limit=50`, { headers: auth });
const campaigns = await r.json();
const hasMore = r.headers.get("X-Has-More") === "true";
const next = r.headers.get("X-Next-Before-Id");             // → ?before_id=${next}
```

**Постраничная выдача.** Все списки ограничены: `limit` (по умолчанию 50), `offset` (не больше
10 000). Для длинных лент (`/campaigns/my`, `/wallet/history`) — курсор `before_id` из заголовка
`X-Next-Before-Id`: следующая страница берётся по индексу за одинаковое время на любой глубине.
Признак «есть ещё» — заголовок `X-Has-More` (общее число не считается: `COUNT(*)` по миллионам
строк на каждую страницу — лишняя нагрузка). В сводках `/stats/me` (он же `/analytics/summary`) и `/stats/platform` списки
ограничены параметрами `campaigns_limit` / `placements_limit`, признак — `campaigns_has_more` /
`placements_has_more`.

**Заголовки безопасности** ставятся на все ответы: `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`; у API —
`Content-Security-Policy: default-src 'none'`, у `/app` — строгий CSP (только свои скрипты);
по HTTPS — `Strict-Transport-Security`.

## Команды администратора

Из папки `backend` (в Docker — `docker compose exec api ...`):

```bash
python -m app.cli create-admin admin@example.com      # создать админа (пароль спросит)
python -m app.cli make-admin user@example.com         # сделать админом существующего
python -m app.cli add-balance user@example.com 1000   # пополнить баланс
alembic upgrade head                                  # применить миграции базы
```

## Тесты

```bash
cd backend
venv/Scripts/python -m pip install -r requirements-dev.txt
venv/Scripts/python -m pytest                    # ~210 тестов, ~5 с, SQLite в памяти
venv/Scripts/python -m pytest -m e2e             # сквозные в браузере (Edge или Chrome)
```

На PostgreSQL (нужны пустые базы):

```bash
TEST_DATABASE_URL=postgresql+psycopg2://user@host/adp_test pytest
E2E_DATABASE_URL=postgresql+psycopg2://user@host/adp_e2e  pytest -m e2e
```

Тесты никогда не трогают рабочую `app.db`. В VS Code: «Run and Debug» → «Pytest: все тесты» или
«FastAPI: отладка».

## Изменение схемы базы

```bash
alembic revision --autogenerate -m "что изменилось"
alembic upgrade head
```

Проверьте сгенерированный файл в `backend/migrations/versions/` перед применением.

## Структура

```
backend/
  app/
    main.py          приложение, CORS, раздача интерфейса и виджета
    models.py        таблицы: users, placements, campaigns, clicks, transactions, campaign_daily_stats
    schemas.py       проверка входных и выходных данных
    auth.py          пароли (bcrypt), токены (JWT), require_admin
    routers/         auth, placements, campaigns, ads (выдача и клики), wallet, users, stats
    static/          widget.js, demo.html, ui/ (веб-интерфейс)
    cli.py           команды администратора
  migrations/        миграции Alembic
  tests/             тесты (pytest), включая браузерные (test_ui_e2e.py)
docker-compose.yml   API + PostgreSQL
```
