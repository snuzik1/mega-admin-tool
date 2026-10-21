# Создаёт .venv и ставит runtime-зависимости (быстро).
# Для разработки: powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -Dev
param(
    [switch]$Dev
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $root ".venv"

if (-not (Test-Path $venv)) {
    Write-Host "[bootstrap] создаю виртуальное окружение"
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m venv $venv
    } else {
        & python -m venv $venv
    }
}

$py = Join-Path $venv "Scripts\python.exe"
$req = if ($Dev) { "requirements-dev.txt" } else { "requirements.txt" }

Write-Host "[bootstrap] ставлю зависимости ($req)..."
& $py -m pip install --disable-pip-version-check --no-input --quiet -r (Join-Path $root $req)
if ($LASTEXITCODE -ne 0) { throw "не удалось установить зависимости" }

Write-Host "[bootstrap] готово."
Write-Host "Запуск:        .\run.ps1  (или start.cmd двойным щелчком)"
Write-Host "Для разработки: .\.venv\Scripts\python.exe -m pytest -q"
