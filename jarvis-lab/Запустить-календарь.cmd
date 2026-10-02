@echo off
chcp 65001 >nul
echo Zapuskayu server ekonomicheskogo kalendarya (port 8125)...
start "cal" /min cmd /c ""C:\jarvis-voice\.venv\Scripts\python.exe" "C:\jarvis-voice\calendar_server.py" > "C:\jarvis-voice\cal_out.log" 2> "C:\jarvis-voice\cal_err.log""
echo Gotovo. Server podnimaetsya v fone (~20-40 sek, brauzer prohodit Cloudflare).
echo Logi: C:\jarvis-voice\cal_out.log / cal_err.log. Okno mozhno zakryt.
timeout /t 4 >nul
