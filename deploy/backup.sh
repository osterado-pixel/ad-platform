#!/usr/bin/env bash
# Резервная копия базы боевого стека (docker-compose.prod.yml) на Linux-сервере.
#
#   /opt/ad-platform/deploy/backup.sh            # вручную
#   systemctl list-timers ad-platform-backup     # по расписанию (ставит bootstrap.sh), каждый день в 03:30
#
# Создаёт backups/backup_<база>_<дата>.dump (формат pg_dump -Fc, восстанавливается pg_restore),
# проверяет, что копия читается, и удаляет копии старше KEEP_DAYS — только после успешной новой
# и не трогая KEEP_AT_LEAST самых свежих.
#
# Копия вне сервера (умер диск или сервер — копии целы): задайте в .env
#   S3_BUCKET=имя-bucket   S3_ENDPOINT=https://fra1.digitaloceanspaces.com
#   S3_ACCESS_KEY=…        S3_SECRET_KEY=…
# Загрузка — образом amazon/aws-cli (ставить ничего не нужно). Сбой загрузки — код выхода 2
# (локальная копия при этом есть).
#
# Восстановление (ВНИМАНИЕ: заменяет текущие данные):
#   docker cp backups/<файл>.dump prod_ad_platform_db:/tmp/restore.dump
#   docker exec prod_ad_platform_db pg_restore -U postgres -d ad_platform_db --clean --if-exists /tmp/restore.dump
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
CONTAINER="${CONTAINER:-prod_ad_platform_db}"
BACKUP_DIR="${BACKUP_DIR:-$APP_DIR/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
KEEP_AT_LEAST="${KEEP_AT_LEAST:-3}"

# Настройки из .env (база, пользователь, хранилище) — если файл есть
if [ -f "$APP_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$APP_DIR/.env"
  set +a
fi
DB="${POSTGRES_DB:-ad_platform_db}"
DB_USER="${POSTGRES_USER:-postgres}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"   # в копии — email пользователей и хеши паролей
stamp="$(date '+%Y-%m-%d_%H-%M-%S')"
file="$BACKUP_DIR/backup_${DB}_${stamp}.dump"
tmp="$file.part"

log "Копия базы $DB (контейнер $CONTAINER)..."
docker exec "$CONTAINER" pg_dump -U "$DB_USER" -d "$DB" -Fc > "$tmp"
# Копия читается целиком — иначе это не копия (обрыв, нет места на диске)
docker exec -i "$CONTAINER" pg_restore --list > /dev/null < "$tmp"
mv "$tmp" "$file"
chmod 600 "$file"
log "Готово: $file ($(du -h "$file" | cut -f1))"

# Старые копии — только после успешной новой; самые свежие KEEP_AT_LEAST не трогаем никогда
mapfile -t all < <(ls -1t "$BACKUP_DIR"/backup_"${DB}"_*.dump 2>/dev/null)
for old in "${all[@]:$KEEP_AT_LEAST}"; do
  if [ -n "$(find "$old" -mtime +"$KEEP_DAYS" 2>/dev/null)" ]; then
    rm -f "$old" && log "Удалена старая копия: $(basename "$old")"
  fi
done

# Копия вне сервера
if [ -n "${S3_BUCKET:-}" ]; then
  log "Загрузка в хранилище s3://$S3_BUCKET/ ..."
  if docker run --rm -v "$BACKUP_DIR:/backups:ro" \
      -e AWS_ACCESS_KEY_ID="${S3_ACCESS_KEY:-}" -e AWS_SECRET_ACCESS_KEY="${S3_SECRET_KEY:-}" \
      -e AWS_DEFAULT_REGION="${S3_REGION:-us-east-1}" \
      amazon/aws-cli s3 cp "/backups/$(basename "$file")" "s3://$S3_BUCKET/db/$(basename "$file")" \
      ${S3_ENDPOINT:+--endpoint-url "$S3_ENDPOINT"} --only-show-errors; then
    log "Загружено в хранилище"
  else
    log "ОШИБКА: не удалось загрузить копию в хранилище (локальная копия есть)"
    exit 2
  fi
fi
