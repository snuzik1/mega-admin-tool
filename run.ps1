# Запуск MegaTool (Windows). Первый запуск сам создаст окружение и поставит
# только runtime-зависимости; при следующих запусках pip не вызывается.
param(
    [string]$HostAddr = "127.0.0.1",
    [int]$Port = 8787,
    [switch]$NoBrowser,
    [string]$Extra = ""
)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"

# Быстрая проверка: если зависимости уже на месте, установку пропускаем.
$ready = $false
if (Test-Path $py) {
    & $py -c "import fastapi, uvicorn, websockets, psutil, yaml" 2>$null
    $ready = ($LASTEXITCODE -eq 0)
}
if (-not $ready) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "scripts\bootstrap.ps1")
}

Push-Location $root
try {
    $cliArgs = @("-m", "megatool", "--host", $HostAddr, "--port", "$Port")
    if ($NoBrowser) { $cliArgs += "--no-browser" }
    if ($Extra) { $cliArgs += $Extra.Split(" ") }
    & $py @cliArgs
}
finally {
    Pop-Location
}
