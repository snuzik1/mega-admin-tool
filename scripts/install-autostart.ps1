# Автозапуск MegaTool в Windows: задача планировщика при входе пользователя.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1 -Port 9000
#
# Задача работает без окна консоли (pythonw.exe), перезапускается при сбое и
# не требует прав администратора. Удаление: scripts\uninstall-autostart.ps1.

param(
    [int]$Port = 0,
    [switch]$AtStartup
)

$ErrorActionPreference = "Stop"
$taskName = "MegaTool"
$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $root ".venv\Scripts"

$pythonw = Join-Path $venv "pythonw.exe"
$python = Join-Path $venv "python.exe"
if (Test-Path $pythonw) {
    $exe = $pythonw
} elseif (Test-Path $python) {
    $exe = $python
} else {
    Write-Host "Виртуальное окружение не найдено: $venv" -ForegroundColor Red
    Write-Host "Сначала запустите start.cmd (создаст .venv и поставит зависимости)." -ForegroundColor Yellow
    exit 1
}

$arguments = "-m megatool --no-browser"
if ($Port -gt 0) {
    $arguments += " --port $Port"
}

$action = New-ScheduledTaskAction -Execute $exe -Argument $arguments -WorkingDirectory $root
$trigger = if ($AtStartup) {
    New-ScheduledTaskTrigger -AtStartup
} else {
    New-ScheduledTaskTrigger -AtLogOn
}
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "MegaTool — локальная консоль администратора" -Force | Out-Null
Start-ScheduledTask -TaskName $taskName

$shownPort = if ($Port -gt 0) { $Port } else {
    try {
        $configPath = Join-Path $env:USERPROFILE ".megatool\config.json"
        if (Test-Path $configPath) {
            (Get-Content $configPath -Raw | ConvertFrom-Json).server.port
        } else { 8787 }
    } catch { 8787 }
}
Write-Host ""
Write-Host "Автозапуск включён: задача «$taskName» ($(if ($AtStartup) { 'при загрузке' } else { 'при входе' }))." -ForegroundColor Green
Write-Host "Интерфейс: http://127.0.0.1:$shownPort/"
Write-Host "Проверить:  Get-ScheduledTask -TaskName $taskName"
Write-Host "Остановить: Stop-ScheduledTask -TaskName $taskName"
Write-Host "Удалить:    powershell -ExecutionPolicy Bypass -File scripts\uninstall-autostart.ps1"
