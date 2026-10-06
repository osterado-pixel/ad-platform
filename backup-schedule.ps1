# Ежедневный автоматический бэкап через Планировщик заданий Windows.
#
#   .\backup-schedule.ps1                  # создать (или обновить) задачу: каждый день в 03:00
#   .\backup-schedule.ps1 -At 02:30        # другое время
#   .\backup-schedule.ps1 -Remove          # удалить задачу
#   Если PowerShell запрещает скрипты:  powershell -ExecutionPolicy Bypass -File .\backup-schedule.ps1
#
# То же, что вручную в taskschd.msc → «Создать простую задачу», но с правильным путём к backup.ps1,
# журналом и запуском пропущенного бэкапа. Права администратора не нужны.
# Результат каждого запуска — в backups\backup.log. Docker Desktop в это время должен работать.

param(
    [string]$At = "03:00",
    [string]$Container = "prod_ad_platform_db",
    [switch]$Remove
)

$TaskName = "AdPlatform DB Backup"
$script = Join-Path $PSScriptRoot "backup.ps1"
$log = Join-Path $PSScriptRoot "backups\backup.log"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Задача «$TaskName» удалена"
    exit 0
}

if (-not (Test-Path $script)) { Write-Host "❌ Не найден $script" -ForegroundColor Red; exit 1 }

$action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $PSScriptRoot `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -Container $Container -LogFile `"$log`""
$trigger = New-ScheduledTaskTrigger -Daily -At $At
# StartWhenAvailable: если в это время компьютер был выключен или спал — бэкап выполнится при включении
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force `
    -Description "Ежедневная резервная копия базы Ad Platform (backup.ps1). Журнал: $log" | Out-Null
if (-not $?) { Write-Host "❌ Не удалось создать задачу" -ForegroundColor Red; exit 1 }

$next = (Get-ScheduledTaskInfo -TaskName $TaskName).NextRunTime
Write-Host "✅ Задача «$TaskName» создана: ежедневно в $At (ближайший запуск: $next)" -ForegroundColor Green
Write-Host "   Журнал: $log"
Write-Host "   Запустить сейчас для проверки: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "   Удалить: .\backup-schedule.ps1 -Remove"
exit 0
