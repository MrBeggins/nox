@echo off
chcp 65001 >nul
title Nox - sborka ustanovshika
py -3.11 "%~dp0build.py" %*
echo.
pause
