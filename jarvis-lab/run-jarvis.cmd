@echo off
REM ============================================================
REM  Launcher for the Jarvis experiment copy (with Claude brain)
REM  Must run from an ASCII path (this folder is C:\Users\tyako\jarvis-lab).
REM ============================================================
cd /d "%~dp0target\debug"
set RUST_LOG=info

REM Voice speed: 1.0 = normal, 1.5 = ~1.5x faster (range 0.5..6.0)
set JARVIS_TTS_RATE=1.3

REM Screen capture / vision: OFF (не снимает экран, не тратит токены)
set JARVIS_VISION=0

REM --- Мозг: локальная модель Ollama (офлайн, бесплатно, на GPU) ---
set JARVIS_BRAIN_BACKEND=openai
set JARVIS_OPENAI_URL=http://127.0.0.1:11434/v1
set JARVIS_OPENAI_MODEL=qwen2.5:7b
REM  Голосом можно переключаться: "переключись на клод" / "используй gpt" / "включи режим авто"
REM  auto = локальная модель, при сбое -> Claude (нужен VPN + claude login)

REM --- Optional brain tuning (uncomment / edit as needed) ---
REM set JARVIS_BRAIN=0                       & REM disable the brain entirely
REM set JARVIS_BRAIN_TIMEOUT=45             & REM seconds before giving up
REM set JARVIS_OPENAI_KEY=sk-...            & REM ключ настоящего ChatGPT (platform.openai.com)
REM set JARVIS_BRAIN_MODEL=claude-haiku-4-5-20251001  & REM модель для Claude-бэкенда

REM --- Поднять F5 голосовой сервер, если ещё не запущен (порт 8123) ---
powershell -NoProfile -Command "$c=New-Object Net.Sockets.TcpClient; try{$c.Connect('127.0.0.1',8123);$c.Close();Write-Host 'Voice server already up.'}catch{Write-Host 'Starting voice server...'; Start-Process -WindowStyle Minimized -FilePath 'C:\jarvis-voice\start-voice-server.cmd'}"

echo Starting Jarvis (say "джарвис" to wake it, or use the GUI/text client)...
jarvis-app.exe
