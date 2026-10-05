# Запуск платформы на Windows без Docker (SQLite или PostgreSQL из backend\.env).
#   cd backend
#   .\start.ps1            # первый запуск: создаст venv, поставит зависимости, .env и базу
#   .\start.ps1 -Port 8080
# Если PowerShell запрещает скрипты:  powershell -ExecutionPolicy Bypass -File .\start.ps1
param([int]$Port = 8000)

# Continue, а не Stop: в Windows PowerShell 5.1 вывод в stderr внешних программ (py, pip)
# при Stop прерывает скрипт. Успех каждого шага проверяется по $LASTEXITCODE
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

# 1. Виртуальное окружение
if (-not (Test-Path ".\venv\Scripts\python.exe")) {
    Write-Host "Создаю виртуальное окружение venv..." -ForegroundColor Cyan
    # Проверенные версии по порядку. Не "py -3": он берёт самую новую (например, 3.15),
    # а под неё у части пакетов ещё нет готовых сборок
    $created = $false
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in "3.14", "3.13", "3.12") {
            & py "-$v" -c "import sys" 2>$null
            if ($LASTEXITCODE -eq 0) {
                Write-Host "Python $v" -ForegroundColor Cyan
                & py "-$v" -m venv venv
                $created = ($LASTEXITCODE -eq 0); break
            }
        }
    }
    if (-not $created) { & python -m venv venv; $created = ($LASTEXITCODE -eq 0) }
    if (-not $created) { throw "Не удалось создать venv. Установите Python 3.12–3.14 с python.org" }
}
$python = ".\venv\Scripts\python.exe"

# 2. Зависимости (быстро, если уже стоят)
Write-Host "Проверяю зависимости..." -ForegroundColor Cyan
& $python -m pip install -q --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Не удалось установить зависимости (requirements.txt)" }

# 3. .env с секретным ключом
if (-not (Test-Path ".\.env")) {
    $key = & $python -c "import secrets; print(secrets.token_urlsafe(48))"
    # Без BOM: Out-File -Encoding utf8 в Windows PowerShell 5.1 добавляет BOM в начало файла
    [IO.File]::WriteAllText("$PSScriptRoot\.env", "# Локальные настройки. Не коммитить.`nSECRET_KEY=$key`n",
        (New-Object Text.UTF8Encoding $false))
    Write-Host "Создан backend\.env с новым SECRET_KEY" -ForegroundColor Green
}

# 4. База данных: миграции до последней версии
Write-Host "Обновляю схему базы данных..." -ForegroundColor Cyan
& $python -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw "Миграции не применились — см. ошибку выше" }

# 5. Подсказка про администратора
$hasAdmin = & $python -c "from app.database import SessionLocal; from app.models import User, UserRole; db=SessionLocal(); print(db.query(User).filter(User.role==UserRole.ADMIN).count())"
if ($hasAdmin -eq "0") {
    Write-Host ""
    Write-Host "Администратора ещё нет. Создайте его в другом окне:" -ForegroundColor Yellow
    Write-Host "  cd $PSScriptRoot; .\venv\Scripts\python.exe -m app.cli create-admin admin@example.com" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Платформа:      http://127.0.0.1:$Port/app" -ForegroundColor Green
Write-Host "Документация:   http://127.0.0.1:$Port/docs" -ForegroundColor Green
Write-Host "Демо виджета:   http://127.0.0.1:$Port/demo" -ForegroundColor Green
Write-Host "Остановить: Ctrl+C"
Write-Host ""
& $python -m uvicorn app.main:app --reload --reload-dir app --port $Port
