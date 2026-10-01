@echo off
chcp 65001 > nul
title Telegram Video Downloader Bot
cd /d "%~dp0"

echo ===================================================
echo     Запуск Telegram Бота для скачивания видео
echo ===================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [!] Виртуальное окружение не найдено. Создание .venv...
    python -m venv .venv
    echo [*] Установка зависимостей...
    .venv\Scripts\python -m pip install --upgrade pip
    .venv\Scripts\python -m pip install -r requirements.txt
)

echo [*] Запуск бота...
.venv\Scripts\python.exe main.py

pause
