#!/usr/bin/env bash
# Однократная настройка чистого сервера (Ubuntu 24.04, DigitalOcean Droplet) под платформу.
# Запуск от root на сервере (повторный запуск безопасен — уже сделанное пропускается):
#
#   curl -fsSL https://raw.githubusercontent.com/<владелец>/<репозиторий>/main/deploy/bootstrap.sh -o bootstrap.sh
#   DOMAIN=ads.example.com ACME_EMAIL=you@example.com bash bootstrap.sh
#
# Что делает:
#  - обновления безопасности ставятся сами (unattended-upgrades), защита SSH от перебора (fail2ban);
#  - файрвол: открыты только 22 (SSH), 80 и 443 (сайт); база, Redis и API наружу закрыты;
#  - вход по SSH только по ключу; пользователь deploy (для CI) с тем же ключом, что у root;
#  - Docker, swap 2 ГБ (сборка и пики памяти на маленьком сервере);
#  - /opt/ad-platform: .env со случайными паролями и ключом (если .env ещё нет);
#  - ежедневная копия базы в 03:30 (systemd-таймер, deploy/backup.sh).
# Файлы платформы (docker-compose.*.yml, deploy/) на сервер кладёт CI при каждом выпуске.
set -euo pipefail

APP_DIR=/opt/ad-platform
DEPLOY_USER=deploy
: "${DOMAIN:?Укажите DOMAIN=ваш-домен}"
: "${ACME_EMAIL:?Укажите ACME_EMAIL=почта для сертификата HTTPS}"

log() { echo "== $*"; }
[ "$(id -u)" -eq 0 ] || { echo "Запустите от root"; exit 1; }

log "Пакеты и обновления безопасности"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get upgrade -yq
apt-get install -yq ca-certificates curl ufw fail2ban unattended-upgrades docker.io docker-compose-v2
dpkg-reconfigure -f noninteractive unattended-upgrades
systemctl enable --now docker fail2ban

log "Файрвол: 22, 80, 443"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

log "Swap"
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

log "Пользователь $DEPLOY_USER (для выпуска версий из CI)"
id "$DEPLOY_USER" > /dev/null 2>&1 || useradd -m -s /bin/bash "$DEPLOY_USER"
usermod -aG docker "$DEPLOY_USER"
if [ -s /root/.ssh/authorized_keys ]; then
  install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"
  install -m 600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" /root/.ssh/authorized_keys "/home/$DEPLOY_USER/.ssh/authorized_keys"
fi

log "SSH: только по ключу"
if [ -s /root/.ssh/authorized_keys ]; then
  # Пароли отключаем, только если ключ уже есть — иначе можно потерять доступ к серверу
  cat > /etc/ssh/sshd_config.d/90-ad-platform.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
  systemctl reload ssh || systemctl reload sshd || true
else
  echo "ВНИМАНИЕ: у root нет SSH-ключа — вход по паролю оставлен. Добавьте ключ и запустите скрипт снова."
fi

log "Каталог $APP_DIR и .env"
install -d -m 750 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$APP_DIR" "$APP_DIR/deploy" "$APP_DIR/backups"
if [ ! -f "$APP_DIR/.env" ]; then
  rand() { tr -dc 'A-Za-z0-9' < /dev/urandom | head -c "$1"; }
  cat > "$APP_DIR/.env" <<EOF
# Создано bootstrap.sh $(date '+%Y-%m-%d'). Пароли и ключ — случайные, храните файл в секрете
DOMAIN=$DOMAIN
ACME_EMAIL=$ACME_EMAIL
PUBLIC_URL=https://$DOMAIN
ALLOWED_HOSTS=$DOMAIN
POSTGRES_PASSWORD=$(rand 32)
REDIS_PASSWORD=$(rand 32)
SECRET_KEY=$(rand 64)
IMAGE_TAG=latest
# Дальше — по желанию (см. .env.example в репозитории): ANTHROPIC_API_KEY, SMTP_*, S3_* для копий вне сервера
EOF
  chown "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
else
  echo ".env уже есть — не трогаю"
fi

log "Ежедневная копия базы (03:30)"
cat > /etc/systemd/system/ad-platform-backup.service <<EOF
[Unit]
Description=Ad Platform: резервная копия базы
After=docker.service

[Service]
Type=oneshot
User=$DEPLOY_USER
ExecStart=$APP_DIR/deploy/backup.sh
EOF
cat > /etc/systemd/system/ad-platform-backup.timer <<'EOF'
[Unit]
Description=Ad Platform: ежедневная копия базы

[Timer]
OnCalendar=*-*-* 03:30:00
RandomizedDelaySec=10m
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now ad-platform-backup.timer

log "Готово. Дальше:"
echo "  1. DNS: запись A для $DOMAIN → IP этого сервера"
echo "  2. GitHub → Settings → Secrets and variables → Actions:"
echo "     секреты DEPLOY_HOST (IP), DEPLOY_USER=$DEPLOY_USER, DEPLOY_SSH_KEY (закрытый ключ),"
echo "     DEPLOY_KNOWN_HOSTS (вывод: ssh-keyscan -t ed25519 <IP>), переменная PUBLIC_URL=https://$DOMAIN"
echo "  3. Push в main — CI проверит, опубликует образы и выпустит версию на этот сервер"
echo "  4. Первый администратор: cd $APP_DIR && docker compose -f docker-compose.prod.yml exec api python -m app.cli create-admin you@example.com"
