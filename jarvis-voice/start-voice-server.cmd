@echo off
REM ============================================================
REM  Jarvis F5 voice server — держит модель в памяти на GPU.
REM  Запускать ПЕРЕД jarvis-app (или он сам стартует через run-jarvis).
REM  Порт: http://127.0.0.1:8123
REM ============================================================
cd /d "%~dp0"
echo Starting Jarvis voice server (F5-TTS on GPU)...
".venv\Scripts\python.exe" tts_server.py
