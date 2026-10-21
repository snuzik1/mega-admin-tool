@echo off
rem Запуск MegaTool двойным щелчком (Windows). Аргументы передаются в start.py.
where python >nul 2>nul
if errorlevel 1 (
  py -3 "%~dp0start.py" %*
) else (
  python "%~dp0start.py" %*
)
if errorlevel 1 pause
