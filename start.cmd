@echo off
rem Запуск MegaTool двойным щелчком (Windows). Аргументы передаются в start.py.
where py >nul 2>nul
if errorlevel 1 (
  python "%~dp0start.py" %*
) else (
  py -3 "%~dp0start.py" %*
)
if errorlevel 1 pause
