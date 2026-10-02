@echo off
chcp 65001 >nul
echo Otkryvaetsya okno mindstocks.ru dlya vhoda.
echo Voydite v svoy akkaunt MindStocks (kak v terminale), potom vernites syuda i nazhmite Enter.
"C:\jarvis-voice\.venv\Scripts\python.exe" "C:\jarvis-voice\mind_server.py" --login
