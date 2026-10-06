# Восстановление базы PostgreSQL в Docker из копии, созданной backup.ps1.
#
#   .\restore.ps1                                           # самая свежая копия из .\backups
#   .\restore.ps1 -BackupFile .\backups\backup_ad_platform_db_2026-10-06_10-22-22.dump
#   .\restore.ps1 -Container ad_platform_db -ApiContainer ad_platform_backend   # стек docker-compose.yml
#   .\restore.ps1 -Force                                    # без вопроса (для автоматизации)
#   Если PowerShell запрещает скрипты:  powershell -ExecutionPolicy Bypass -File .\restore.ps1
#
# Как устроено (текущая база не теряется ни при каком сбое):
#   1. копия восстанавливается во ВРЕМЕННУЮ базу и проверяется — рабочая база не тронута;
#   2. API останавливается, оставшиеся подключения сбрасываются;
#   3. рабочая база переименовывается в <база>_before_restore_<дата> (для отката),
#      восстановленная — получает имя рабочей;
#   4. API запускается и сам применяет миграции, если копия сделана на более старой схеме.
#
# Откат (вернуть базу, какой она была до восстановления):
#   docker stop <api>; затем в контейнере базы: ALTER DATABASE <база> RENAME TO <база>_bad;
#   ALTER DATABASE <база>_before_restore_<дата> RENAME TO <база>; docker start <api>
# Когда убедитесь, что всё в порядке, старую базу можно удалить:
#   docker exec <контейнер> dropdb -U postgres <база>_before_restore_<дата>

param(
    [string]$BackupFile,
    [string]$Container = "prod_ad_platform_db",
    [string]$ApiContainer,
    [switch]$Force
)

# Continue, а не Stop: в Windows PowerShell 5.1 вывод программ в stderr при Stop прерывает скрипт.
# Успех каждого шага проверяется по $LASTEXITCODE
$ErrorActionPreference = "Continue"

function Info([string]$m) { Write-Host $m -ForegroundColor Yellow }
function Fail([string]$m) { Write-Host "❌ $m" -ForegroundColor Red; exit 1 }

# SQL от имени администратора базы, подключаясь к служебной базе postgres
function Psql([string]$sql) {
    $out = docker exec $Container psql -U $DbUser -d postgres -v ON_ERROR_STOP=1 -tAc $sql 2>&1
    return @{ Ok = ($LASTEXITCODE -eq 0); Out = ($out -join "`n") }
}

# --- Настройки: пользователь и база — из .env (как в docker-compose), иначе по умолчанию ---
$envValues = @{}
$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*([A-Za-z_]+)\s*=\s*(.*?)\s*$') { $envValues[$Matches[1]] = $Matches[2] }
    }
}
$DbUser = if ($envValues["POSTGRES_USER"]) { $envValues["POSTGRES_USER"] } else { "postgres" }
$DbName = if ($envValues["POSTGRES_DB"]) { $envValues["POSTGRES_DB"] } else { "ad_platform_db" }
if (-not $ApiContainer) { $ApiContainer = $Container -replace "_db$", "_backend" }

# --- Файл копии: указанный или самый свежий ---
if (-not $BackupFile) {
    $latest = Get-ChildItem (Join-Path $PSScriptRoot "backups") -Filter "backup_${DbName}_*.dump" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $latest) { Fail "В .\backups нет копий базы $DbName. Укажите файл: -BackupFile путь" }
    $BackupFile = $latest.FullName
}
if (-not (Test-Path $BackupFile)) {
    Write-Host "❌ Файл бэкапа $BackupFile не найден!" -ForegroundColor Red
    $available = Get-ChildItem (Join-Path $PSScriptRoot "backups") -Filter "*.dump" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending
    if ($available) {
        Write-Host "Доступные копии (новые сверху; backup.ps1 создаёт файлы .dump, а не .sql.gz):"
        $available | ForEach-Object { Write-Host "   .\backups\$($_.Name)   $($_.LastWriteTime)" }
        Write-Host "Самую свежую можно восстановить без параметров: .\restore.ps1"
    } else {
        Write-Host "В .\backups копий нет — создайте: .\backup.ps1"
    }
    exit 1
}
$BackupFile = (Resolve-Path $BackupFile).Path

# --- Контейнеры ---
if ((docker inspect -f "{{.State.Running}}" $Container 2>$null) -ne "true") {
    Fail "Контейнер базы '$Container' не запущен. Запущенные: $((docker ps --format '{{.Names}}') -join ', ')"
}
$apiExists = $false
docker inspect $ApiContainer 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { $apiExists = $true }

# --- Подтверждение ---
$info = Get-Item $BackupFile
Write-Host "⚠️ ВНИМАНИЕ: база данных $DbName (контейнер $Container) будет заменена копией" -ForegroundColor Yellow
Write-Host "   $($info.Name) от $($info.LastWriteTime) ($([math]::Round($info.Length / 1KB, 1)) КБ)" -ForegroundColor Yellow
Write-Host "   Текущая база сохранится под другим именем — откатиться можно (см. начало скрипта)." -ForegroundColor Yellow
if ($apiExists) { Write-Host "   API ($ApiContainer) будет остановлен на время восстановления." -ForegroundColor Yellow }
if (-not $Force) {
    $confirmation = Read-Host "Продолжить? (y/n)"
    if ($confirmation -ne 'y') { Write-Host "Отменено."; exit 0 }
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$tmpDb = "${DbName}_restoring_$stamp"
$oldDb = "${DbName}_before_restore_$stamp"
$tmpFile = "/tmp/restore_$stamp.dump"

# --- 1. Восстановление во временную базу (рабочая не тронута) ---
Info "⏳ Копирую файл в контейнер..."
docker cp $BackupFile "${Container}:${tmpFile}" | Out-Null   # байт в байт, без конвейера PowerShell
if ($LASTEXITCODE -ne 0) { Fail "Не удалось скопировать файл в контейнер" }

Info "⏳ Восстанавливаю во временную базу $tmpDb..."
docker exec $Container createdb -U $DbUser $tmpDb
if ($LASTEXITCODE -ne 0) { docker exec $Container sh -c "unlink $tmpFile" | Out-Null; Fail "Не удалось создать временную базу" }
docker exec $Container pg_restore -U $DbUser -d $tmpDb --no-owner --exit-on-error $tmpFile
$restored = ($LASTEXITCODE -eq 0)
docker exec $Container sh -c "unlink $tmpFile" | Out-Null
if (-not $restored) {
    docker exec $Container dropdb -U $DbUser --if-exists $tmpDb | Out-Null
    Fail "Копия не восстанавливается (повреждена или не того формата). Рабочая база не изменена"
}
$tables = (docker exec $Container psql -U $DbUser -d $tmpDb -tAc "select count(*) from information_schema.tables where table_schema='public'")
Write-Host "   восстановлено таблиц: $tables"

# --- 2. Останавливаем API и сбрасываем подключения ---
if ($apiExists) {
    Info "⏳ Останавливаю API ($ApiContainer)..."
    docker stop $ApiContainer | Out-Null
}
Info "⏳ Завершение активных подключений к базе..."
$r = Psql "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE datname = '$DbName' AND pid <> pg_backend_pid();"
Write-Host "   сброшено подключений: $($r.Out)"

# --- 3. Подмена базы переименованием (атомарно для приложения; старая остаётся для отката) ---
Info "⏳ Подменяю базу..."
$exists = (Psql "SELECT 1 FROM pg_database WHERE datname = '$DbName'").Out -eq "1"
if ($exists) {
    $r = Psql "ALTER DATABASE `"$DbName`" RENAME TO `"$oldDb`""
    if (-not $r.Ok) {
        docker exec $Container dropdb -U $DbUser --if-exists $tmpDb | Out-Null
        if ($apiExists) { docker start $ApiContainer | Out-Null }
        Fail "Не удалось переименовать текущую базу (к ней ещё есть подключения?): $($r.Out). Рабочая база не изменена"
    }
}
$r = Psql "ALTER DATABASE `"$tmpDb`" RENAME TO `"$DbName`""
if (-not $r.Ok) {
    if ($exists) { Psql "ALTER DATABASE `"$oldDb`" RENAME TO `"$DbName`"" | Out-Null }   # вернуть как было
    if ($apiExists) { docker start $ApiContainer | Out-Null }
    Fail "Не удалось подменить базу: $($r.Out). Рабочая база возвращена"
}

# --- 4. Запуск API (при старте сам применит миграции, если копия на старой схеме) ---
if ($apiExists) {
    Info "⏳ Запускаю API..."
    docker start $ApiContainer | Out-Null
    $healthy = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        $state = docker inspect -f "{{.State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{end}}" $ApiContainer
        if ($state -eq "true healthy") { $healthy = $true; break }
    }
    if (-not $healthy) {
        Write-Host "⚠️ API не подтвердил готовность за 60 с — проверьте: docker logs $ApiContainer" -ForegroundColor Yellow
    }
}

Write-Host "✅ База данных успешно восстановлена!" -ForegroundColor Green
if ($exists) {
    Write-Host "   Прежняя база сохранена как $oldDb (откат — см. начало скрипта)."
    Write-Host "   Удалить, когда убедитесь: docker exec $Container dropdb -U $DbUser $oldDb"
}
exit 0
