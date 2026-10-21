# Удаление автозапуска MegaTool из планировщика Windows.

$ErrorActionPreference = "Stop"
$taskName = "MegaTool"

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "Задача «$taskName» не найдена — автозапуск уже выключен." -ForegroundColor Yellow
    exit 0
}

Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
Write-Host "Автозапуск MegaTool удалён. Запущенные вручную экземпляры не затронуты." -ForegroundColor Green
