# Сайт платформы (Next.js). Три этапа: зависимости → сборка → маленький итоговый образ
# только с тем, что нужно для работы (.next/standalone), без исходников и всего node_modules.
#
#   docker build -t ad-platform-frontend .
#   docker build --build-arg BACKEND_URL=http://api:8000 -t ad-platform-frontend .

# Node 24 — текущая LTS-версия (Next.js 16 требует ≥ 20.9)
FROM node:24-alpine AS deps
WORKDIR /app
# Сначала только списки зависимостей: слой кешируется, пока они не меняются
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:24-alpine AS build
WORKDIR /app
# Адрес бэкенда вшивается в сервер при сборке (rewrites в next.config.ts): в docker compose это
# сервис api. На боевом сервере эти пути отдаёт Caddy, сюда они не доходят
ARG BACKEND_URL=http://api:8000
ENV BACKEND_URL=${BACKEND_URL} \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:24-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
# Не от root: при уязвимости в приложении у злоумышленника меньше прав в контейнере
RUN addgroup -S -g 10001 app && adduser -S -u 10001 -G app app
# standalone — сервер и только нужные ему модули; статика и public по документации копируются отдельно
COPY --from=build --chown=app:app /app/.next/standalone ./
COPY --from=build --chown=app:app /app/.next/static ./.next/static
COPY --from=build --chown=app:app /app/public ./public
USER app
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"
CMD ["node", "server.js"]
