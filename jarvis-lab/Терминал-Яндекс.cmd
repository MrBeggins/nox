@echo off
chcp 65001 >nul
echo Zapusk otdelnogo profilya Yandex dlya Dzharvisa (s portom otladki 9222).
echo V etom okne odin raz nastroyte: rasshireniya Trading Tools i MindStocks,
echo token TT, vhod v T-Bank, terminal s panelyami novostey i kalendarya.
echo Eto okno dolzhno byt vsegda otkryto (mozhno ubrat v storonu).
echo.
set "YB=C:\Program Files\Yandex\YandexBrowser\Application\browser.exe"
if not exist "%YB%" set "YB=C:\Program Files (x86)\Yandex\YandexBrowser\Application\browser.exe"
if not exist "%YB%" set "YB=%LOCALAPPDATA%\Yandex\YandexBrowser\Application\browser.exe"
if not exist "%YB%" (
  echo Yandex Browser ne nayden. Ustanovite ego: https://browser.yandex.ru/
  pause
  exit /b 1
)
start "" "%YB%" --user-data-dir="C:\jarvis-voice\yandex_jarvis" --remote-debugging-port=9222 --no-first-run --no-default-browser-check "https://www.tbank.ru/terminal/"
timeout /t 3 >nul
