@echo off
chcp 65001 >nul
setlocal

rem ===== Локальный сервер для index.html (WebUSB требует https:// или localhost) =====
set PORT=8000
cd /d "%~dp0"

echo Проверяю Python...
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo.
    echo [ОШИБКА] Python не найден в PATH.
    echo Установите Python с https://www.python.org/downloads/ ^(отметьте "Add to PATH"^)
    echo и запустите этот файл ещё раз.
    pause
    exit /b 1
)

echo Запускаю сервер на http://localhost:%PORT%/ ...
start "" "http://localhost:%PORT%/index.html"
python -m http.server %PORT%

pause
