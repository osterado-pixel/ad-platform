# Резервная копия базы PostgreSQL из Docker-контейнера.
#
#   .\backup.ps1                                  # боевой стек (prod_ad_platform_db)
#   .\backup.ps1 -Container ad_platform_db        # стек из docker-compose.yml
#   .\backup.ps1 -KeepDays 14
#   .\backup.ps1 -OffsiteDir "D:\Backups"         # + копия вне этого диска (см. ниже)
#   Если PowerShell запрещает скрипты:  powershell -ExecutionPolicy Bypass -File .\backup.ps1
#
# Копия ВНЕ компьютера — -OffsiteDir или BACKUP_OFFSITE_DIR в .env (тогда и для расписания):
# папка Google Drive / Dropbox / OneDrive (синхронизируется в облако), внешний диск или сетевая
# папка (\\nas\backups). Если умрёт диск компьютера, копии там уцелеют. Файл сверяется по SHA-256;
# там действуют те же правила очистки. Сбой этой копии — код выхода 2 (локальная копия при этом есть).
# В копии — email пользователей и хеши паролей: облако выбирайте своё, с двухфакторным входом.
#
# Создаёт ./backups/backup_<база>_<дата>.dump (сжатый формат pg_dump, восстанавливается pg_restore),
# проверяет копию и удаляет копии старше KeepDays дней — только если новая создана успешно,
# и не трогая KeepAtLeast самых свежих.
#
# Восстановление (ВНИМАНИЕ: заменяет текущие данные):
#   docker cp .\backups\backup_ad_platform_db_<дата>.dump prod_ad_platform_db:/tmp/restore.dump
#   docker exec prod_ad_platform_db pg_restore -U postgres -d ad_platform_db --clean --if-exists /tmp/restore.dump
#
# Ежедневно по расписанию (Планировщик заданий Windows), один раз:
#   powershell -ExecutionPolicy Bypass -File .\backup-schedule.ps1   (создаёт задачу «AdPlatform DB Backup», 03:00)

param(
    [string]$Container = "prod_ad_platform_db",
    [int]$KeepDays = 7,
    [int]$KeepAtLeast = 3,
    [string]$BackupDir = (Join-Path $PSScriptRoot "backups"),
    # Журнал запусков: при запуске по расписанию окна не видно — только так узнать о сбое
    [string]$LogFile,
    # Вторая копия вне этого диска (облачная папка, внешний диск, сеть). Пусто — BACKUP_OFFSITE_DIR из .env
    [string]$OffsiteDir
)

# Continue, а не Stop: в Windows PowerShell 5.1 вывод программ в stderr при Stop прерывает скрипт.
# Успех каждого шага проверяется по $LASTEXITCODE
$ErrorActionPreference = "Continue"

function Say([string]$message, [string]$color = "Gray") {
    Write-Host $message -ForegroundColor $color
    if ($LogFile) {
        $dir = Split-Path $LogFile -Parent
        if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
        Add-Content -Path $LogFile -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $message" -Encoding UTF8
    }
}

function Fail([string]$message) {
    Say "❌ $message" "Red"
    exit 1
}

# Пользователь и база — из .env рядом со скриптом (как в docker-compose), иначе по умолчанию
$envValues = @{}
$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path $envFile) {
    # UTF-8, как читает .env docker compose: без -Encoding PowerShell 5.1 прочтёт кириллицу в пути как ANSI
    foreach ($line in Get-Content $envFile -Encoding UTF8) {
        if ($line -match '^\s*([A-Za-z_]+)\s*=\s*(.*?)\s*$') { $envValues[$Matches[1]] = $Matches[2] }
    }
}
$DbUser = if ($envValues["POSTGRES_USER"]) { $envValues["POSTGRES_USER"] } else { "postgres" }
$DbName = if ($envValues["POSTGRES_DB"]) { $envValues["POSTGRES_DB"] } else { "ad_platform_db" }
if (-not $OffsiteDir -and $envValues["BACKUP_OFFSITE_DIR"]) { $OffsiteDir = $envValues["BACKUP_OFFSITE_DIR"].Trim('"', "'") }

# Очистка папки с копиями: старше KeepDays, но всегда оставляя KeepAtLeast самых свежих —
# иначе неделя сбоев удалила бы все рабочие копии
function Remove-OldBackups([string]$dir) {
    $all = Get-ChildItem -Path $dir -Filter "backup_${DbName}_*.dump" | Sort-Object LastWriteTime -Descending
    $old = $all | Select-Object -Skip $KeepAtLeast | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$KeepDays) }
    foreach ($f in $old) {
        Remove-Item $f.FullName
        Say "🗑  Удалён старый бэкап: $($f.FullName)"
    }
    Say "Копий в ${dir}: $(@($all).Count - @($old).Count)"
}

$date = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$fileName = Join-Path $BackupDir "backup_${DbName}_${date}.dump"
$tmpInContainer = "/tmp/backup_${date}.dump"

# Контейнер базы должен работать
$state = docker inspect -f "{{.State.Running}}" $Container 2>$null
if ($LASTEXITCODE -ne 0) { Fail "Контейнер '$Container' не найден. Запущенные: $((docker ps --format '{{.Names}}') -join ', ')" }
if ($state -ne "true") { Fail "Контейнер '$Container' не запущен" }

if (-not (Test-Path $BackupDir)) { New-Item -ItemType Directory -Path $BackupDir | Out-Null }

Say "⏳ Создание резервной копии базы $DbName (контейнер $Container)..." "Yellow"

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
Say "✅ Бэкап успешно создан: $fileName ($sizeKb КБ)" "Green"

# 4. Очистка — только после успешной копии
Remove-OldBackups $BackupDir

# 5. Копия вне компьютера
if ($OffsiteDir) {
    try {
        if (-not (Test-Path $OffsiteDir)) { New-Item -ItemType Directory -Path $OffsiteDir -ErrorAction Stop | Out-Null }
        $dest = Join-Path $OffsiteDir (Split-Path $fileName -Leaf)
        Copy-Item -Path $fileName -Destination $dest -ErrorAction Stop
    } catch {
        Say "❌ Копия вне компьютера НЕ сделана ($OffsiteDir): $($_.Exception.Message). Локальная копия есть" "Red"
        exit 2
    }
    # Сверка содержимого: файл в облачной папке или на сетевом диске должен совпадать байт в байт
    $srcHash = (Get-FileHash -Path $fileName -Algorithm SHA256).Hash
    $dstHash = (Get-FileHash -Path $dest -Algorithm SHA256).Hash
    if ($srcHash -ne $dstHash) {
        Remove-Item $dest -ErrorAction SilentlyContinue
        Say "❌ Копия вне компьютера повреждена при записи (SHA-256 не совпадает) — удалена. Локальная копия есть" "Red"
        exit 2
    }
    Say "☁  Копия вне компьютера: $dest (SHA-256 совпадает)" "Green"
    Remove-OldBackups $OffsiteDir
}
exit 0
