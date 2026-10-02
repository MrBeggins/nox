@echo off
chcp 65001 >nul
title Установка Nox
rem --- самоподнятие прав администратора ---
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Запрашиваю права администратора...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
echo ============================================
echo   Установка Nox - подождите, это надолго.
echo   Всё поставится само. Не закрывайте окно.
echo ============================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
pause
