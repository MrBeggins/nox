@echo off
echo ===== WHOAMI / HOST =====
whoami
hostname
echo.
echo ===== npm folder (%APPDATA%\npm) =====
dir "%APPDATA%\npm"
echo.
echo ===== claude-code bin =====
dir "%APPDATA%\npm\node_modules\@anthropic-ai\claude-code\bin"
echo.
echo ===== jarvis-lab folder =====
dir C:\Users\tyako\jarvis-lab
echo.
echo ===== jarvis-app.exe =====
dir C:\Users\tyako\jarvis-lab\target\debug\jarvis-app.exe
echo.
echo ===== tools on PATH =====
where node
where cargo
where claude
echo.
echo ===== DONE =====
pause
