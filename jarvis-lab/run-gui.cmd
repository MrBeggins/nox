@echo off
REM Launches the Jarvis GUI configurator window (release build).
REM Поднимает голосовой сервер F5, если он ещё не запущен.
powershell -NoProfile -Command "$c=New-Object Net.Sockets.TcpClient; try{$c.Connect('127.0.0.1',8123);$c.Close()}catch{Start-Process -WindowStyle Minimized -FilePath 'C:\jarvis-voice\start-voice-server.cmd'}"
cd /d "%~dp0target\release"
start "" jarvis-gui.exe
