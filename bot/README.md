# Telegram-бот Ad Platform

Баланс и AI-копирайтер прямо в Telegram (aiogram 3, long polling — открытый порт и HTTPS не нужны).

| Команда | Что делает |
|---|---|
| `/start` | Приветствие; если аккаунт не привязан — как привязать |
| `/start КОД`, `/link КОД` | Привязка аккаунта кодом из кабинета |
| `/generate` | Объявление: бот спросит описание (аудитория — строкой «Аудитория: …») или `/generate описание` сразу |
| `/balance` | Баланс и замороженная сумма |
| `/unlink` | Отвязать аккаунт |
| `/cancel`, `/help` | Отменить ввод, список команд |

## Как это устроено

Бот не знает паролей. Пользователь в кабинете (`/app` → Профиль → Telegram) получает одноразовый код на
10 минут и открывает ссылку `t.me/<бот>?start=<код>` — бот передаёт код бэкенду, аккаунты связываются.
Дальше бот вызывает `/api/v1/bot/*` с `telegram_id` пользователя и общим секретом `TELEGRAM_BOT_SECRET`
в заголовке `X-Bot-Secret`; бэкенд применяет те же правила, что и для сайта: модерация текста, лимит задач,
заморозка и списание денег по факту. Ответы бота — обычный текст (без HTML/Markdown), поэтому разметка в
тексте объявлений не сработает.

**Языки.** Бот говорит на языке из настроек Telegram пользователя: английский, русский или немецкий
(другой язык — английский, как на сайте); на нём же меню команд, объявления от AI-копирайтера и ошибки
сервера (бот передаёт язык в `Accept-Language`). Аудиторию можно указать строкой «Аудитория:»,
«Audience:» или «Zielgruppe:». Тексты — `texts.py` (полноту проверяет `tests/test_texts.py`).

## Запуск

1. Создайте бота у [@BotFather](https://t.me/BotFather) (`/newbot`) и получите токен.
2. В `.env` рядом с `docker-compose*.yml`:
   ```
   BOT_TOKEN=123456:ABC...
   TELEGRAM_BOT_SECRET=<не короче 32 символов: python -c "import secrets; print(secrets.token_urlsafe(32))">
   TELEGRAM_BOT_USERNAME=имя_бота_без_@
   SITE_URL=https://ads.example.com
   ```
3. `docker compose -f docker-compose.prod.yml --profile bot up -d` (или `docker compose --profile bot up -d --build`).
   Бэкенд получает тот же `TELEGRAM_BOT_SECRET` и включает эндпоинты бота; без профиля `bot` бот не запускается.

## Разработка

```bash
cd bot
python -m venv venv && venv/Scripts/python -m pip install -r requirements-dev.txt
venv/Scripts/python -m pytest          # тесты: сообщения идут через диспетчер aiogram, Telegram и API подменены
BOT_TOKEN=... TELEGRAM_BOT_SECRET=... API_URL=http://127.0.0.1:8000 venv/Scripts/python main.py
```
