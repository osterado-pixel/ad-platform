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

### Docker + PostgreSQL, настройки из backend/.env (проще всего)

```bash
cd backend
cp .env.example .env     # заполните POSTGRES_PASSWORD и SECRET_KEY (случайные!)
docker compose up -d --build
docker compose exec api python -m app.cli create-admin admin@example.com
```

Если `.env` нет, запуск не падает: у всех переменных есть значения по умолчанию, а вместо
`SECRET_KEY` приложение генерирует случайный ключ и хранит его в томе `app_data` (у каждой
установки свой, переживает перезапуск). Для сервера, открытого в интернет, задайте свои
`POSTGRES_PASSWORD` и `SECRET_KEY` — или используйте вариант из корня проекта, где это обязательно.

Приложение ждёт готовности PostgreSQL и само применяет миграции. Данные — в томе `postgres_data`,
переживают пересоздание контейнеров. База доступна только с этого компьютера:
`127.0.0.1:5433` (pgAdmin, DBeaver), пользователь и пароль — из `backend/.env`.

### Docker + PostgreSQL, настройки из корневого .env (сервер)

```bash
cp .env.example .env          # заполните POSTGRES_PASSWORD и SECRET_KEY
docker compose up -d --build
docker compose exec api python -m app.cli create-admin admin@example.com
```

Платформа: `http://СЕРВЕР:8000/app`. Миграции базы применяются автоматически при старте.
Данные PostgreSQL хранятся в томе `pgdata` и переживают пересборку.

### Боевой сервер: домен + HTTPS

Нужны: сервер с Docker, домен, DNS-запись `A` домена → IP сервера, открытые порты 80 и 443.

```bash
cp .env.example .env     # POSTGRES_PASSWORD, SECRET_KEY, DOMAIN=ads.example.com, ACME_EMAIL=you@example.com
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose exec api python -m app.cli create-admin admin@example.com
```

Платформа: `https://ads.example.com/app`. Сертификат Let's Encrypt Caddy получает и продлевает сам.
API наружу не открыт — только через Caddy, поэтому платформа видит реальный IP посетителя, а подставить
чужой IP в `X-Forwarded-For` нельзя (проверено). HTTP перенаправляется на HTTPS, включён HSTS.

Обновление до новой версии: `git pull` и та же команда `up -d --build` — миграции базы применятся
при старте.

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

### Тестовые данные

```bash
cd backend && python seed.py                 # в Docker: docker compose exec api python seed.py
```

Создаёт рекламодателя `advertiser@example.com` / `password123` (баланс 500), площадку
`habr_main_banner` и активную кампанию со статистикой. Повторный запуск ничего не дублирует.
Пароль известен всем — только для разработки и демонстрации, не для боевого сервера.

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
const page = await fetch(`${API}/campaigns/my?limit=20&offset=0`, { headers: auth }).then((r) => r.json());
// page = { items: [...], total: 42, limit: 20, offset: 0 } → следующая страница: offset=20

const r = await fetch(`${API}/wallet/history?limit=50`, { headers: auth });
const { items, total } = await r.json();
// глубже 10 000 записей — курсором: ?before_id=${r.headers.get("X-Next-Before-Id")}
```

**Постраничная выдача.** Все списки ограничены: `limit` (1–200, у площадок до 500), `offset` (до 10 000).

- Кампании и площадки — объект `PaginatedResponse`:
  `{"items": [...], "total": 42, "limit": 10, "offset": 0}`. `GET /campaigns` учитывает роль:
  админ видит все кампании (фильтры `status`, `user_id`), рекламодатель — только свои
  (то же, что `/campaigns/my`).
- История кошелька `/wallet/history` (по записи на каждый клик — могут быть миллионы) — тоже
  `PaginatedResponse`, но рассчитана на большой объём: сортировка по `id` по индексу (страница ~1 мс
  при 900 тыс. записей; сортировка по времени — ~230 мс), `total` — из счётчика пользователя, без
  `COUNT(*)`. Для прокрутки дальше 10 000 записей — курсор `?before_id=` из заголовка
  `X-Next-Before-Id` (признак «есть ещё» — `X-Has-More`).
- Сводки `/stats/me` (он же `/analytics/summary`) и `/stats/platform`: списки ограничены
  `campaigns_limit` / `placements_limit`, признак — `campaigns_has_more` / `placements_has_more`.

**Заголовки безопасности** ставятся на все ответы: `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`; у API —
`Content-Security-Policy: default-src 'none'`, у `/app` — строгий CSP (только свои скрипты);
по HTTPS — `Strict-Transport-Security`.

## Резервные копии и обслуживание

**PostgreSQL (Docker)** — копия и восстановление:

```bash
docker compose exec -T db pg_dump -U adp -Fc adp > backup-$(date +%F).dump
docker compose exec -T db pg_restore -U adp -d adp --clean --if-exists < backup-2026-10-05.dump
```

**SQLite** (без Docker) — копия на ходу, без остановки сервера: `python -m app.cli backup-db backups`.

Делайте копию ежедневно (cron / Планировщик заданий) и храните вне сервера.

**Очистка служебных записей** — раз в сутки (`docker compose exec api ...` в Docker):

```bash
python -m app.cli purge --clicks-days 30
```

Удаляет записи о кликах старше 30 дней (они нужны только для защиты от повторов; статистика по дням
и журнал денег не затрагиваются) и старые попытки входа.

## Безопасность

- Вход: не больше 5 неудачных попыток на email и 20 на IP за 15 минут, затем `429` с `Retry-After`;
  регистрация — не больше 10 с IP в час. Счётчики в базе — работают с несколькими процессами.
- Смена пароля (`/app` → профиль, или `POST /api/v1/auth/change-password`) завершает все остальные сеансы.
- Забыл пароль (почтовой рассылки нет): администратор сбрасывает — `python -m app.cli set-password email`.
- Пароли — bcrypt; email и IP в служебных таблицах — только как HMAC-хеш.

## Команды администратора

Из папки `backend` (в Docker — `docker compose exec api ...`):

```bash
python -m app.cli create-admin admin@example.com      # создать админа (пароль спросит)
python -m app.cli make-admin user@example.com         # сделать админом существующего
python -m app.cli add-balance user@example.com 1000   # пополнить баланс
python -m app.cli set-password user@example.com       # сбросить пароль (сеансы завершатся)
python -m app.cli backup-db backups                   # копия SQLite
python -m app.cli purge --clicks-days 30              # очистка служебных записей
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
backend/docker-compose.yml  приложение + PostgreSQL (настройки из backend/.env)
docker-compose.yml   API + PostgreSQL
docker-compose.prod.yml  + Caddy: домен и HTTPS (накладывается на docker-compose.yml)
deploy/Caddyfile     настройки HTTPS-прокси
```
