#!/usr/bin/env bash
# Проверка deploy/deploy.sh с подставной командой docker (ничего настоящего не трогается).
# Запуск: bash deploy/tests/test_deploy.sh (выполняется и в CI). Ненулевой код — какой-то сценарий не прошёл
set -u
REPO_DEPLOY="${1:-$(cd "$(dirname "$0")/.." && pwd)/deploy.sh}"
W="$(mktemp -d)"
mkdir -p "$W/app/deploy" "$W/bin"
cp "$REPO_DEPLOY" "$W/app/deploy/deploy.sh"
printf '#!/usr/bin/env bash\nexit 0\n' > "$W/app/deploy/backup.sh"
chmod +x "$W/app/deploy/"*.sh
touch "$W/app/docker-compose.prod.yml"

cat > "$W/bin/docker" <<'EOF'
#!/usr/bin/env bash
echo "docker $*" >> "$STUB_LOG"
tag() { grep '^IMAGE_TAG=' "$APP_DIR/.env" | cut -d= -f2; }
case "$1" in
  ps) echo prod_ad_platform_db ;;
  tag) echo "$3" >> "$STUB_TAGS" ;;
  image) ;;
  inspect)
    case "$*" in
      *State.Health*) [ "$(tag)" = "$BAD_HEALTH" ] && echo unhealthy || echo healthy ;;
      *Config.Image*) echo "ghcr.io/x/ad-platform:$(tag)" ;;
      *"{{.Image}}"*) echo "sha256:running" ;;
    esac ;;
  compose)
    case "$*" in
      *" ps -q "*) [ "$RUNNING" = 1 ] && echo cid123 ;;
      *" pull "*) [ "$(tag)" = "$BAD_PULL" ] && exit 1 ;;
      *" exec "*) [ "$(tag)" = "$BAD_HEALTH" ] && exit 1 ;;
    esac ;;
esac
exit 0
EOF
chmod +x "$W/bin/docker"
export PATH="$W/bin:$PATH" APP_DIR="$W/app" STUB_LOG="$W/log" STUB_TAGS="$W/tags" HEALTH_TIMEOUT=6

FAILED=0
run() {  # name, tag, BAD_PULL, BAD_HEALTH, RUNNING, expected exit, expected final tag
  printf 'IMAGE_TAG=latest\n' > "$W/app/.env"; : > "$STUB_TAGS"
  BAD_PULL="$3" BAD_HEALTH="$4" RUNNING="$5" bash "$W/app/deploy/deploy.sh" "$2" > "$W/out" 2>&1
  rc=$?
  final="$(grep '^IMAGE_TAG=' "$W/app/.env" | cut -d= -f2)"
  if [ "$rc" = "$6" ] && [ "$final" = "$7" ]; then
    echo "OK   $1 (код $rc, IMAGE_TAG=$final, сохранено образов: $(wc -l < "$STUB_TAGS"))"
  else
    echo "FAIL $1: код $rc (ждали $6), IMAGE_TAG=$final (ждали $7)"; cat "$W/out"
    FAILED=1
  fi
}

run "успешный выпуск"                 sha-new    none       none       1 0 sha-new
run "образ не скачался -> откат"       sha-bad    sha-bad    none       1 1 pre-deploy
run "не прошёл проверку -> откат"      sha-bad    none       sha-bad    1 1 pre-deploy
run "первый запуск, откатывать некуда" sha-bad    sha-bad    none       0 1 latest
rm -rf "$W"
exit $FAILED
