#!/usr/bin/env bash
# Выпуск новой версии на сервере — запускает CI после зелёных тестов (или вручную):
#
#   /opt/ad-platform/deploy/deploy.sh sha-1a2b3c4     # тег образа из ghcr.io (CI публикует sha-<коммит> и latest)
#
# 1. Копия базы (deploy/backup.sh) — до обновления.
# 2. Новые образы, перезапуск (миграции база применяет сама при старте api).
# 3. Проверка: контейнер api «healthy» и /api/v1/health отвечает. Нет — АВТООТКАТ на прошлый тег.
#
# Текущий тег — в .env (IMAGE_TAG), прошлый — в .deploy-previous-tag. Откат возвращает код, но не схему БД:
# миграции в проекте только добавляют таблицы и колонки, прежняя версия с новой схемой работает.
set -euo pipefail

TAG="${1:?Укажите тег образа: deploy.sh sha-1a2b3c4}"
APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$APP_DIR"
COMPOSE=(docker compose -f docker-compose.prod.yml)
[ -f docker-compose.https.yml ] && [ -n "$(grep -E '^DOMAIN=.+' .env 2>/dev/null || true)" ] \
  && COMPOSE+=(-f docker-compose.https.yml)
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-180}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

set_tag() {
  if grep -q '^IMAGE_TAG=' .env 2>/dev/null; then
    sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$1/" .env
  else
    echo "IMAGE_TAG=$1" >> .env
  fi
}

healthy() {
  # Docker HEALTHCHECK контейнера api и ответ /api/v1/health изнутри (порт наружу может быть закрыт)
  local deadline=$((SECONDS + HEALTH_TIMEOUT)) status
  while [ $SECONDS -lt $deadline ]; do
    status="$(docker inspect -f '{{.State.Health.Status}}' prod_ad_platform_backend 2>/dev/null || echo none)"
    if [ "$status" = "healthy" ] && "${COMPOSE[@]}" exec -T api python -c \
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=5)" \
        > /dev/null 2>&1; then
      return 0
    fi
    sleep 5
  done
  return 1
}

previous="$(grep '^IMAGE_TAG=' .env 2>/dev/null | cut -d= -f2 || true)"
previous="${previous:-latest}"
log "Выпуск $TAG (сейчас: $previous)"

if docker ps --format '{{.Names}}' | grep -qx prod_ad_platform_db; then
  ./deploy/backup.sh || { rc=$?; [ $rc -eq 2 ] || { log "Копия базы не создана — выпуск отменён"; exit 1; }; }
fi

set_tag "$TAG"
"${COMPOSE[@]}" pull --quiet
"${COMPOSE[@]}" up -d --remove-orphans

if healthy; then
  echo "$previous" > .deploy-previous-tag
  docker image prune -f > /dev/null   # старые образы не копятся на диске
  log "Готово: работает $TAG"
  exit 0
fi

log "ОШИБКА: $TAG не прошёл проверку — откат на $previous"
"${COMPOSE[@]}" logs --tail=80 api || true
set_tag "$previous"
"${COMPOSE[@]}" pull --quiet || true
"${COMPOSE[@]}" up -d --remove-orphans
if healthy; then
  log "Откат выполнен: работает $previous"
else
  log "ВНИМАНИЕ: и прежняя версия не отвечает — нужна ручная проверка"
fi
exit 1
