@echo off
set "CLAUDE=%APPDATA%\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe"
echo Using: "%CLAUDE%"
if not exist "%CLAUDE%" (
  echo ERROR: claude.exe not found at that path.
  pause
  exit /b 1
)
echo.
echo === Step 1: login (a browser window will open) ===
"%CLAUDE%" login
echo.
echo === Step 2: test the brain ===
"%CLAUDE%" -p "reply with one word: working"
echo.
echo Done. You can close this window.
pause
