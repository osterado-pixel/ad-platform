# Сайт Ad Platform (Next.js)

Лендинг и страница AI-копирайтера. Кабинет рекламодателя и администратора — встроенный интерфейс
бэкенда `/app`; сайт и кабинет работают на одном домене, поэтому вход общий.

| Страница | Что |
|---|---|
| `/` | Лендинг: как работает, возможности, пример AI-копирайтера (без запросов к API), цены, вопросы |
| `/generate` | AI-копирайтер: запуск фоновой задачи и опрос статуса (`src/hooks/useAdTask.ts`) |

Всё остальное (`/api`, `/app`, `/widget.js`, `/demo`, `/docs`) — бэкенд: на боевом сервере эти пути
отдаёт Caddy, при разработке и без Caddy — `rewrites` в `next.config.ts` (адрес — `BACKEND_URL`).

## Разработка

Нужны Node.js 20.9+ и запущенный бэкенд на `http://127.0.0.1:8000`.

```bash
npm install
npm run dev        # http://localhost:3000
npm run lint
npm run build      # продакшн-сборка (.next/standalone)
```

## Docker

```bash
docker build -t ad-platform-frontend .                                  # бэкенд — сервис api в compose
docker build --build-arg BACKEND_URL=http://host.docker.internal:8000 .  # бэкенд на этой машине
```

Адрес бэкенда вшивается при сборке. Образ — только `standalone`-сборка, запуск не от root.
