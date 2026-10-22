# Запуск MegaTool (Windows). Вся подготовка окружения живёт в start.py:
# эта обёртка только находит Python и передаёт ему аргументы.
param(
    [string]$HostAddr = "",
    [int]$Port = 0,
    [switch]$NoBrowser,
    [string]$Extra = ""
)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$start = Join-Path $root "start.py"

$hasPy = $null -ne (Get-Command py -ErrorAction SilentlyContinue)
$hasPython = $null -ne (Get-Command python -ErrorAction SilentlyContinue)
if (-not $hasPy -and -not $hasPython) {
    Write-Host "Python 3.8+ не найден. Установите его с https://www.python.org/downloads/"
    exit 1
}

$cliArgs = @()
if ($HostAddr) { $cliArgs += @("--host", $HostAddr) }
if ($Port -gt 0) { $cliArgs += @("--port", "$Port") }
if ($NoBrowser) { $cliArgs += "--no-browser" }
if ($Extra) { $cliArgs += ($Extra -split '\s+' | Where-Object { $_ }) }
$cliArgs += $args

Push-Location $root
try {
    if ($hasPy) {
        & py -3 $start @cliArgs
    } else {
        & python $start @cliArgs
    }
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $code
