# Резервная копия базы PostgreSQL из Docker-контейнера.
#
#   .\backup.ps1                                  # боевой стек (prod_ad_platform_db)
#   .\backup.ps1 -Container ad_platform_db        # стек из docker-compose.yml
#   .\backup.ps1 -KeepDays 14
#   Если PowerShell запрещает скрипты:  powershell -ExecutionPolicy Bypass -File .\backup.ps1
#
# Создаёт ./backups/backup_<база>_<дата>.dump (сжатый формат pg_dump, восстанавливается pg_restore),
# проверяет копию и удаляет копии старше KeepDays дней — только если новая создана успешно,
# и не трогая KeepAtLeast самых свежих.
#
# Восстановление (ВНИМАНИЕ: заменяет текущие данные):
#   docker cp .\backups\backup_ad_platform_db_<дата>.dump prod_ad_platform_db:/tmp/restore.dump
#   docker exec prod_ad_platform_db pg_restore -U postgres -d ad_platform_db --clean --if-exists /tmp/restore.dump
#
# Ежедневно по расписанию (Планировщик заданий Windows), один раз от администратора:
#   schtasks /Create /TN "AdPlatformBackup" /SC DAILY /ST 03:00 /TR "powershell -ExecutionPolicy Bypass -File `"$PSScriptRoot\backup.ps1`""

param(
    [string]$Container = "prod_ad_platform_db",
    [int]$KeepDays = 7,
    [int]$KeepAtLeast = 3,
    [string]$BackupDir = (Join-Path $PSScriptRoot "backups")
)

# Continue, а не Stop: в Windows PowerShell 5.1 вывод программ в stderr при Stop прерывает скрипт.
# Успех каждого шага проверяется по $LASTEXITCODE
$ErrorActionPreference = "Continue"

function Fail([string]$message) {
    Write-Host "❌ $message" -ForegroundColor Red
    exit 1
}

# Пользователь и база — из .env рядом со скриптом (как в docker-compose), иначе по умолчанию
$envValues = @{}
$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*([A-Za-z_]+)\s*=\s*(.*?)\s*$') { $envValues[$Matches[1]] = $Matches[2] }
    }
}
$DbUser = if ($envValues["POSTGRES_USER"]) { $envValues["POSTGRES_USER"] } else { "postgres" }
$DbName = if ($envValues["POSTGRES_DB"]) { $envValues["POSTGRES_DB"] } else { "ad_platform_db" }

$date = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$fileName = Join-Path $BackupDir "backup_${DbName}_${date}.dump"
$tmpInContainer = "/tmp/backup_${date}.dump"

# Контейнер базы должен работать
$state = docker inspect -f "{{.State.Running}}" $Container 2>$null
if ($LASTEXITCODE -ne 0) { Fail "Контейнер '$Container' не найден. Запущенные: $((docker ps --format '{{.Names}}') -join ', ')" }
if ($state -ne "true") { Fail "Контейнер '$Container' не запущен" }

if (-not (Test-Path $BackupDir)) { New-Item -ItemType Directory -Path $BackupDir | Out-Null }

Write-Host "⏳ Создание резервной копии базы $DbName (контейнер $Container)..." -ForegroundColor Yellow

# 1. Дамп ВНУТРИ контейнера, в файл: без -t и без конвейера PowerShell — иначе Windows PowerShell
#    перекодирует двоичные данные как текст и копия испортится. -Fc — сжатый формат pg_dump
docker exec $Container pg_dump -U $DbUser -d $DbName -Fc -f $tmpInContainer
if ($LASTEXITCODE -ne 0) { Fail "pg_dump завершился с ошибкой — копия не создана, старые копии не тронуты" }

# 2. Проверка: копию можно прочитать (оглавление восстанавливается)
docker exec $Container pg_restore --list $tmpInContainer | Out-Null
if ($LASTEXITCODE -ne 0) {
    docker exec $Container rm -f $tmpInContainer | Out-Null
    Fail "Копия повреждена (pg_restore --list) — старые копии не тронуты"
}

# 3. Копируем файл из контейнера как есть, байт в байт
docker cp "${Container}:${tmpInContainer}" $fileName | Out-Null
$copied = $LASTEXITCODE
docker exec $Container rm -f $tmpInContainer | Out-Null
if ($copied -ne 0 -or -not (Test-Path $fileName) -or (Get-Item $fileName).Length -eq 0) {
    Fail "Не удалось скопировать копию из контейнера — старые копии не тронуты"
}

$sizeKb = [math]::Round((Get-Item $fileName).Length / 1KB, 1)
Write-Host "✅ Бэкап успешно создан: $fileName ($sizeKb КБ)" -ForegroundColor Green

# 4. Очистка — только после успешной копии и всегда оставляя KeepAtLeast самых свежих:
#    иначе неделя сбоев удалила бы все рабочие копии
$all = Get-ChildItem -Path $BackupDir -Filter "backup_${DbName}_*.dump" | Sort-Object LastWriteTime -Descending
$old = $all | Select-Object -Skip $KeepAtLeast | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$KeepDays) }
foreach ($f in $old) {
    Remove-Item $f.FullName
    Write-Host "🗑  Удалён старый бэкап: $($f.Name)"
}
Write-Host "Копий в ${BackupDir}: $(@($all).Count - @($old).Count)"
exit 0
