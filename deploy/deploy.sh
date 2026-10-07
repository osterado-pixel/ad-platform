#!/usr/bin/env bash
# Выпуск новой версии на сервере — запускает CI после зелёных тестов (или вручную):
#
#   /opt/ad-platform/deploy/deploy.sh sha-1a2b3c4     # тег образа из ghcr.io (CI публикует sha-<коммит> и latest)
#
# 1. Копия базы (deploy/backup.sh) — до обновления.
# 2. Работающие сейчас образы сохраняются под тегом pre-deploy — откат возможен, даже если тег
#    прошлой версии (например, latest) уже указывает на новую, сломанную сборку.
# 3. Новые образы, перезапуск (миграции база применяет сама при старте api).
# 4. Проверка: контейнер api «healthy» и /api/v1/health отвечает.
# Любой сбой шагов 3–4 (образ не скачался, не запустился, не прошёл проверку) — АВТООТКАТ на pre-deploy.
#
# Откат возвращает код, но не схему БД: миграции в проекте только добавляют таблицы и колонки,
# прежняя версия с новой схемой работает.
set -euo pipefail

TAG="${1:?Укажите тег образа: deploy.sh sha-1a2b3c4}"
APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$APP_DIR"
COMPOSE=(docker compose -f docker-compose.prod.yml)
[ -f docker-compose.https.yml ] && [ -n "$(grep -E '^DOMAIN=.+' .env 2>/dev/null || true)" ] \
  && COMPOSE+=(-f docker-compose.https.yml)
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-180}"
ROLLBACK_TAG=pre-deploy
# Сервисы с нашими образами (у всех один IMAGE_TAG); db, redis, caddy — сторонние образы
APP_SERVICES=(api celery_worker frontend bot)

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

# Сохранить работающие образы под тегом pre-deploy. Возвращает 0, если есть к чему откатываться
save_running_images() {
  local service container image repo saved=1
  for service in "${APP_SERVICES[@]}"; do
    container="$("${COMPOSE[@]}" ps -q "$service" 2>/dev/null | head -1)"
    [ -n "$container" ] || continue
    image="$(docker inspect -f '{{.Image}}' "$container")"            # ID образа, а не тег
    repo="$(docker inspect -f '{{.Config.Image}}' "$container")"      # ghcr.io/…/ad-platform:тег
    docker tag "$image" "${repo%:*}:$ROLLBACK_TAG"
    saved=0
  done
  return $saved
}

release() {
  "${COMPOSE[@]}" pull --quiet && "${COMPOSE[@]}" up -d --remove-orphans && healthy
}

previous="$(grep '^IMAGE_TAG=' .env 2>/dev/null | cut -d= -f2 || true)"
log "Выпуск $TAG (сейчас: ${previous:-не задан})"

if docker ps --format '{{.Names}}' | grep -qx prod_ad_platform_db; then
  ./deploy/backup.sh || { rc=$?; [ $rc -eq 2 ] || { log "Копия базы не создана — выпуск отменён"; exit 1; }; }
fi

can_rollback=0
save_running_images && can_rollback=1

set_tag "$TAG"
if release; then
  echo "${previous:-}" > .deploy-previous-tag
  docker image prune -f > /dev/null   # старые образы не копятся (pre-deploy остаётся — он с тегом)
  log "Готово: работает $TAG"
  exit 0
fi

log "ОШИБКА: $TAG не запустился или не прошёл проверку"
"${COMPOSE[@]}" logs --tail=80 api || true
if [ $can_rollback -eq 0 ]; then
  set_tag "${previous:-latest}"
  log "ВНИМАНИЕ: откатываться не к чему (первый запуск) — нужна ручная проверка"
  exit 1
fi
log "Откат на образы, работавшие до выпуска ($ROLLBACK_TAG)"
set_tag "$ROLLBACK_TAG"
# Без pull: образы pre-deploy есть только локально
if "${COMPOSE[@]}" up -d --remove-orphans && healthy; then
  log "Откат выполнен: работает прежняя версия"
else
  log "ВНИМАНИЕ: и прежняя версия не отвечает — нужна ручная проверка"
fi
exit 1
