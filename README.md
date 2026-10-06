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
установки свой, переживает перезапуск). Для сервера, открытого в интернет, всё равно задайте
свой `POSTGRES_PASSWORD`: пароль по умолчанию есть в этом README.

Приложение ждёт готовности PostgreSQL и само применяет миграции. Данные — в томе `postgres_data`,
переживают пересоздание контейнеров. База доступна только с этого компьютера:
`127.0.0.1:5433` (pgAdmin, DBeaver), пользователь и пароль — из `backend/.env`.

### Docker + PostgreSQL, настройки из корневого .env (сервер)

```bash
cp .env.example .env          # POSTGRES_PASSWORD и SECRET_KEY — свои случайные
docker compose up -d --build  # без .env тоже запустится: значения по умолчанию, ключ генерируется
docker compose exec api python -m app.cli create-admin admin@example.com
```

Платформа: `http://СЕРВЕР:8000/app`. Миграции базы применяются автоматически при старте.
Данные PostgreSQL хранятся в томе `pgdata` и переживают пересборку.

### Боевой сервер: домен + HTTPS

Нужны: сервер с Docker, домен, DNS-запись `A` домена → IP сервера, открытые порты 80 и 443.

```bash
cp .env.example .env     # POSTGRES_PASSWORD, REDIS_PASSWORD, SECRET_KEY, DOMAIN=ads.example.com, ACME_EMAIL=you@example.com
docker compose -f docker-compose.prod.yml -f docker-compose.https.yml up -d
docker compose exec api python -m app.cli create-admin admin@example.com
```

Используется готовый образ из реестра — исходники на сервере не собираются (достаточно
`docker-compose.prod.yml`, `docker-compose.https.yml`, `deploy/Caddyfile` и `.env`).
Платформа: `https://ads.example.com/app`. Сертификат Let's Encrypt Caddy получает и продлевает сам.
API наружу не открыт — только через Caddy, поэтому платформа видит реальный IP посетителя, а подставить
чужой IP в `X-Forwarded-For` нельзя (проверено). HTTP перенаправляется на HTTPS, включён HSTS.

Обновление до новой версии: `docker compose -f docker-compose.prod.yml -f docker-compose.https.yml pull`
и та же команда `up -d` — миграции базы применятся при старте. Точная версия вместо `latest`:
`IMAGE_TAG=sha-df56c40` в `.env` (теги — на странице пакета на GitHub).

Без домена (только HTTP, порт 8000): `docker compose -f docker-compose.prod.yml up -d`.

В составе — **Redis** (для очередей фоновых задач). Как и база, он доступен только внутри сети Docker
(порт 6379 наружу не открыт), с паролем `REDIS_PASSWORD` (по умолчанию `myredispassword123` —
на сервере задайте свой) и сохранением данных между перезапусками (том `prod_redis_data`).
Приложение получает адрес в `REDIS_URL`.

**Celery-воркер** (`celery_worker`) — отдельный процесс из того же образа, выполняет фоновые задачи из
очереди Redis (`app/worker.py`). Настройки (база, ключи, цены, курс) у него те же, что у `api`, —
общий блок `x-app-env` в начале файла. Стартует после `api`, то есть после применения миграций.
Число одновременных задач — `CELERY_CONCURRENCY` (2).

Celery-воркер локально на Windows (без Docker для приложения):

```powershell
# 1. Redis для разработки — один раз; доступен только с этого компьютера (127.0.0.1)
docker run -d --name ad_platform_redis_dev --restart unless-stopped -p 127.0.0.1:6379:6379 redis:7-alpine
# 2. Воркер — из папки backend, через Python из venv (просто `celery` без активации venv не найдётся)
.\venv\Scripts\python.exe -m celery -A app.worker.celery_app worker --loglevel=info -P solo --without-mingle --without-gossip
```

`-P solo` обязателен на Windows: обычный режим Celery (prefork) там не работает. Логи: `docker compose -f docker-compose.prod.yml logs -f celery_worker`.

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
4. **Админ → Модерация**: одобряет или отклоняет с причиной (при включённой AI-проверке рядом —
   подсказка ИИ, см. ниже). **Админ → Пользователи**: пополняет баланс
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
| `ANTHROPIC_API_KEY` | — (выключено) | ключ Anthropic API: включает AI-проверку объявлений |
| `AI_MODEL` | `claude-opus-5-5` | модель для AI-проверки |
| `AI_AUTO_REJECT` | `false` | `true` — объявления с вердиктом «отклонить» и высоким риском отклоняются автоматически |
| `GEMINI_API_KEY` | — (выключено) | ключ Google Gemini API (aistudio.google.com/apikey): включает AI-копирайтер |
| `GEMINI_MODEL` | `gemini-3.8-flash` | модель AI-копирайтера |
| `GEMINI_PRICE_INPUT_PER_1M` / `GEMINI_PRICE_OUTPUT_PER_1M` | `0.75` / `3.75` | цены модели, $ за 1 млн токенов; у 3.8 Flash с 01.01.2027 — `1.50` / `7.50` |
| `AI_MARKUP` | `1.5` | наценка платформы к себестоимости AI-запроса |
| `OPENAI_API_KEY` | — (выключено) | ключ OpenAI: бесплатный Moderation API в быстрой модерации текста |
| `AI_CLEANUP_INTERVAL_SECONDS` / `AI_TASK_TIMEOUT_MINUTES` | `300` / `10` | как часто искать зависшие AI-задачи и через сколько минут без изменений задача считается зависшей (не меньше 2) |
| `USD_RATE` | `1` | сколько единиц валюты баланса стоит 1 $ (баланс в рублях — курс, например `90`) |

**За nginx/балансировщиком** обязательно передавайте реальный IP посетителя (`X-Forwarded-For`) и
укажите адрес прокси в `FORWARDED_ALLOW_IPS` (без Docker — флаги uvicorn
`--proxy-headers --forwarded-allow-ips АДРЕС`). Иначе все посетители будут «с одного IP» прокси, и
защита от повторных кликов перестанет оплачивать клики разных людей.

**SQLite или PostgreSQL.** SQLite подходит для одного сервера и одного процесса (≈ 300 оплаченных
кликов/с). Для нескольких процессов (`WEB_WORKERS` > 1) и роста — PostgreSQL.

## AI-проверка объявлений

Включается ключом `ANTHROPIC_API_KEY` (без него всё работает как раньше, модерация только вручную).

- После отправки на модерацию объявление (заголовок, текст, ссылки) в фоне проверяет Claude —
  рекламодатель ответа не ждёт. Через несколько секунд в карточке **Админ → Модерация** появляется
  подсказка: вердикт (можно одобрить / нужна проверка / отклонить), риск, причины.
- **Решение принимает модератор.** Подсказку видит только админ; рекламодатель — лишь итог модерации.
- Кнопка «Перепроверить ИИ» — повторная проверка (`POST /api/v1/campaigns/{id}/ai-review`),
  например если фоновая не удалась (сеть, лимиты). Сбой проверки кампанию не блокирует.
- `AI_AUTO_REJECT=true` — объявления, которые ИИ уверенно считает нарушением (reject + высокий риск),
  отклоняются сразу с причиной «Автоматическая проверка: …»; рекламодатель может исправить и
  отправить снова. По умолчанию выключено.
- Если пока шла проверка, админ уже вынес решение или объявление изменили, устаревший результат
  отбрасывается. Повторная отправка сбрасывает прошлую подсказку.
- Текст объявления передаётся модели как данные, а не инструкции: «одобри меня» в заголовке не сработает.
- Каждая проверка — платный запрос к Anthropic API. Тесты настоящий API не вызывают.

## AI-копирайтер (Gemini)

Включается ключом `GEMINI_API_KEY`. В интерфейсе — блок «AI-копирайтер» над формой кампании:
описание товара → «Сгенерировать варианты» → индикатор, пока задача выполняется (опрос статуса) →
3 варианта, кнопка «Использовать» подставляет заголовок и текст в форму. Без ключа блока нет.
`GET /api/v1/ai/status` — включён ли копирайтер и сколько замораживается на генерацию.

API: `POST /api/v1/ai/generate-copy` с телом
`{"product_description": "...", "target_audience": "..."}` возвращает 3 варианта объявления
(заголовок, текст, призыв к действию) и списывает стоимость с баланса.

- Первым шагом описание и аудитория проходят быструю модерацию текста (см. ниже): запрещённый текст —
  ответ 422 с причиной, без резерва денег и без платного запроса к Gemini.
- Цена — по фактическим токенам: себестоимость × `AI_MARKUP` × `USD_RATE`, вверх до копейки,
  не меньше 0.01. Списание видно в истории кошелька («AI-копирайтер»), подробности
  (модель, токены, себестоимость) — в таблице `ai_logs`.
- Резерв виден как «замороженный» баланс: `held_balance` в `/auth/me` и `/wallet/balance`, карточка
  «Заморожено» в кошельке. Доступный баланс (`balance`) его уже не включает.
- Перед запросом к модели на балансе резервируется максимально возможная цена (сейчас 0.03 при
  `USD_RATE=1`), после ответа лишнее сразу возвращается. Не хватает на резерв — ответ 402,
  платный запрос к Gemini не отправляется. Параллельные запросы не уведут баланс в минус.
- Генерация не удалась (сеть, лимиты, блокировка) — резерв возвращается целиком (операция «Возврат»).
- Описание товара передаётся модели как данные, а не как инструкции.
- На бесплатном тарифе Google AI Studio запросы используются Google для улучшения продуктов,
  на платном — нет.

### Фоновая генерация

`POST /api/v1/ai/generate-async` (то же тело) сразу замораживает деньги (вместе с созданием задачи —
одной транзакцией; не хватает — 402) и отвечает 202 с `task_id` и `held_amount`, статус и результат —
`GET /api/v1/ai/tasks/{task_id}` (только своя задача; не больше 5 незавершённых на пользователя).
Все свои задачи, новые сверху: `GET /api/v1/ai/tasks?status=completed&page=1&size=10`.
`app/services/ai_background.py` — та же генерация, но в фоне: задача (`ai_tasks`) проходит статусы
pending → processing → completed / failed, результат или понятная причина ошибки — в задаче.
Оплата та же (`app/services/ai_billing.py`): резерв → расчёт по факту, при ошибке — полный возврат.
Одна задача не выполняется дважды. Если сервер перезапустился посреди генерации, при следующем
запуске такие задачи (без изменений дольше 10 минут) помечаются failed, а резерв возвращается.
Пока сервер работает, та же проверка повторяется каждые `AI_CLEANUP_INTERVAL_SECONDS` (300 с) — прямо внутри
процесса сервера, без отдельного Celery Beat; порог «зависания» — `AI_TASK_TIMEOUT_MINUTES` (10 мин).
Вручную: `cleanup_stuck_ai_tasks(timeout_minutes)` из `app/services/ai_cleanup.py`.

## Быстрая модерация текста

`app/services/moderation_service.py` — проверка текста до платных и долгих шагов:

1. **Локальные стоп-фразы** (без сети, мгновенно): казино и ставки на спорт, финансовые пирамиды,
   «заработок без вложений», «гарантированный доход», покупка дипломов и документов, подделки.
   Простые обходы не работают: латинские буквы-двойники («кaзино»), «к.а.з.и.н.о», невидимые символы.
   Формулировки узкие, чтобы не мешать честной рекламе: «ставки по вкладам», «строительная рулетка»,
   «рамка для диплома» проходят. Правила — список `_RULES` в начале файла.
2. **OpenAI Moderation API** (бесплатный, нужен `OPENAI_API_KEY`): насилие, угрозы, ненависть, 18+,
   самоповреждение, незаконное. Спам и мошенничество он не распознаёт — для этого шаг 1 и AI-проверка.
   Нет ключа или OpenAI недоступен — шаг пропускается, текст всё равно проверит модератор.

Нарушение — `ContentRejected` (HTTP 422) со списком причин на русском.

## Мониторинг ошибок (Sentry)

Включается переменной `SENTRY_DSN` (sentry.io → новый проект Python/FastAPI → Client Keys). Без неё
ничего не отправляется. Оповещения приходят о необработанных ошибках в запросах и задачах Celery и о
записях лога уровня ERROR — например, о непредвиденной ошибке фоновой AI-генерации. Ожидаемые сбои
(лимит Gemini, отказ модерации) — предупреждения: в оповещения не попадают.

Личные данные не отправляются: ни IP посетителей, ни cookie, ни токены (в том числе из переменных стека —
значения локальных переменных Sentry не получает), ни тела запросов. `SENTRY_ENVIRONMENT` — метка
окружения (production / staging), `SENTRY_TRACES_SAMPLE_RATE` — доля запросов с замером скорости (0).

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

**PostgreSQL (Docker), Windows** — скрипт `backup.ps1` в корне проекта:

```powershell
.\backup.ps1                             # боевой стек; для docker-compose.yml: -Container ad_platform_db
```

Создаёт `backups\backup_<база>_<дата>.dump`, проверяет, что копию можно прочитать, и удаляет копии
старше 7 дней (`-KeepDays`) — только после успешной копии и всегда оставляя 3 самые свежие.
Ежедневный автоматический бэкап (Планировщик заданий Windows, задача «AdPlatform DB Backup»,
03:00; пропущенный запуск выполняется при включении компьютера):

```powershell
.\backup-schedule.ps1            # создать; -At 02:30 — другое время; -Remove — удалить
Start-ScheduledTask -TaskName 'AdPlatform DB Backup'   # проверить сейчас
```

Результат каждого запуска — в `backups\backup.log`. В это время должны работать компьютер
(вход в учётную запись) и Docker Desktop.

Восстановление — `restore.ps1`:

```powershell
.\restore.ps1                              # самая свежая копия; или -BackupFile .\backups\<файл>.dump
```

Копия сначала восстанавливается во временную базу и проверяется; затем API останавливается, рабочая
база переименовывается в `<база>_before_restore_<дата>` (для отката), восстановленная занимает её
место, API запускается и сам применяет миграции. При любой ошибке рабочая база не меняется.

**PostgreSQL (Docker)** — вручную, копия и восстановление:

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
venv/Scripts/python -m pytest                    # ~470 тестов, ~20 с, SQLite в памяти, с покрытием
venv/Scripts/python -m pytest --no-cov tests/test_ai_copy.py   # один файл, быстро, без покрытия
venv/Scripts/python -m pytest -m e2e --no-cov    # сквозные в браузере (Edge или Chrome)
```

На PostgreSQL (нужны пустые базы):

```bash
TEST_DATABASE_URL=postgresql+psycopg2://user@host/adp_test pytest
E2E_DATABASE_URL=postgresql+psycopg2://user@host/adp_e2e  pytest -m e2e
```

Тесты никогда не трогают рабочую `app.db`.

Покрытие считается при каждом запуске (настройки — `backend/pytest.ini`) и должно быть **не ниже 80%**,
иначе прогон падает — и в CI тоже. Отдельный файл покрывает лишь часть кода — его запускайте с `--no-cov`.
В терминале — файлы с непокрытыми строками и номерами этих строк, полный отчёт —
`backend/htmlcov/index.html`.

**CI (GitHub Actions)** — `.github/workflows/tests.yml`, запускается на каждый push и pull request
в `main` / `master` / `develop` (и вручную — кнопкой в разделе Actions). Одна задача, шаги по порядку,
любой упавший шаг останавливает остальные: миграции на PostgreSQL (накат, сверка с моделями, откат),
pytest на SQLite и на PostgreSQL (Python 3.14, как в Docker-образе; покрытие не ниже 80%),
браузерные тесты в Chromium (скриншоты сбоев — в артефактах), сборка Docker-образа и проверка,
что он запускается.

**Готовый образ (GHCR).** После каждого push в `main`, если прошли все проверки, образ
публикуется в GitHub Container Registry с тегами `latest` и коротким хешем коммита:

```bash
docker pull ghcr.io/osterado-pixel/ad-platform:latest
```

Новый пакет на GitHub по умолчанию приватный: чтобы скачивать без входа, откройте его в профиле →
Packages → `ad-platform` → Package settings → Change visibility. Для приватного —
`docker login ghcr.io` с токеном, у которого есть право `read:packages`. В VS Code: «Run and Debug» → «Pytest: все тесты» или
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
    ai.py            AI-проверка объявления (Claude, Anthropic API)
    moderation.py    запуск AI-проверки и сохранение результата
    worker.py        Celery: очередь фоновых задач (Redis)
    monitoring.py    Sentry: оповещения об ошибках
    services/        billing.py — заморозка/списание/возврат (поверх ai_billing.py);
                     gemini_service.py — AI-копирайтер (Google Gemini); moderation_service.py — быстрая модерация текста
    routers/         auth, placements, campaigns, ads (выдача и клики), wallet, users, stats, ai
    static/          widget.js, demo.html, ui/ (веб-интерфейс)
    cli.py           команды администратора
  migrations/        миграции Alembic
  tests/             тесты (pytest), включая браузерные (test_ui_e2e.py)
backend/docker-compose.yml  приложение + PostgreSQL (настройки из backend/.env)
docker-compose.yml   API + PostgreSQL
docker-compose.prod.yml  готовый образ из ghcr.io (без сборки)
docker-compose.https.yml + Caddy: домен и HTTPS (надстройка к любому варианту)
deploy/Caddyfile     настройки HTTPS-прокси
```
