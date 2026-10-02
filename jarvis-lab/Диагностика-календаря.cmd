@echo off
chcp 65001 >nul
cd /d C:\jarvis-voice
".venv\Scripts\python.exe" test_calendar.py
pause
