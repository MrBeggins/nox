# Тихий запуск Nox — без консольных окон.
# Голосовой сервер поднимается на pythonw (без консоли), затем открывается GUI.

# 0) Мозг Ollama (:11434) — иначе на не-локальные фразы Nox говорит «не найдено».
$oup = $false
try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', 11434); $c.Close(); $oup = $true } catch {}
if (-not $oup) {
    $ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
    if (Test-Path $ollama) {
        Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
        Start-Sleep -Seconds 3
    }
}

# 1) Голосовой сервер F5 (если ещё не запущен на :8123)
$up = $false
try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', 8123); $c.Close(); $up = $true } catch {}
if (-not $up) {
    Start-Process -FilePath "C:\jarvis-voice\.venv\Scripts\pythonw.exe" `
        -ArgumentList "tts_server.py" `
        -WorkingDirectory "C:\jarvis-voice" `
        -WindowStyle Hidden `
        -RedirectStandardOutput "C:\jarvis-voice\tts_out.log" `
        -RedirectStandardError  "C:\jarvis-voice\tts_err.log"
}

# 2) Читатель Invest API (портфель/котировки) — если ещё не запущен на :8124
$rup = $false
try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', 8124); $c.Close(); $rup = $true } catch {}
if (-not $rup) {
    Start-Process -FilePath "C:\jarvis-voice\.venv\Scripts\pythonw.exe" `
        -ArgumentList "invest_reader.py" `
        -WorkingDirectory "C:\jarvis-voice" `
        -WindowStyle Hidden `
        -RedirectStandardOutput "C:\jarvis-voice\reader_out.log" `
        -RedirectStandardError  "C:\jarvis-voice\reader_err.log"
}

# 2b) Окно Яндекса с терминалом (отдельный профиль Джарвиса + порт отладки 9222),
#     из него читаются новости Trading Tools и календарь MindStocks. Если не запущено.
$yup = $false
try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', 9222); $c.Close(); $yup = $true } catch {}
if (-not $yup) {
    Start-Process -FilePath "C:\Program Files\Yandex\YandexBrowser\Application\browser.exe" `
        -ArgumentList '--user-data-dir=C:\jarvis-voice\yandex_jarvis','--remote-debugging-port=9222','--no-first-run','--no-default-browser-check','https://www.tbank.ru/terminal/'
    Start-Sleep -Seconds 6
}

# 2c) Читатель терминала (новости ТТ + календарь MindStocks) — если не на :8126
$tup = $false
try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', 8126); $c.Close(); $tup = $true } catch {}
if (-not $tup) {
    # ВАЖНО: reader под pythonw.exe НЕ биндит 8126 — запускаем через python.exe скрыто.
    Start-Process -FilePath "C:\jarvis-voice\.venv\Scripts\python.exe" `
        -ArgumentList "terminal_reader.py" `
        -WorkingDirectory "C:\jarvis-voice" `
        -WindowStyle Hidden `
        -RedirectStandardOutput "C:\jarvis-voice\tr_out.log" `
        -RedirectStandardError  "C:\jarvis-voice\tr_err.log"
}

# 2d) Russian Magellan (order flow MOEX) — если не на :8127
$mup = $false
try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', 8127); $c.Close(); $mup = $true } catch {}
if (-not $mup) {
    Start-Process -FilePath "C:\jarvis-voice\.venv\Scripts\python.exe" `
        -ArgumentList "magellan_reader.py" `
        -WorkingDirectory "C:\jarvis-voice" `
        -WindowStyle Hidden `
        -RedirectStandardOutput "C:\jarvis-voice\mag_out.log" `
        -RedirectStandardError  "C:\jarvis-voice\mag_err.log"
}

# 3) GUI (release — собран без консоли)
Start-Process -FilePath "C:\jarvis-lab\target\release\jarvis-gui.exe" `
    -WorkingDirectory "C:\jarvis-lab\target\release"

# 4) Watchdog — сам следит за стеком и перезапускает упавшее (singleton через порт 8130)
$wup = $false
try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', 8130); $c.Close(); $wup = $true } catch {}
if (-not $wup) {
    Start-Process -FilePath "C:\jarvis-voice\.venv\Scripts\python.exe" `
        -ArgumentList "nox_watchdog.py" `
        -WorkingDirectory "C:\jarvis-voice" `
        -WindowStyle Hidden `
        -RedirectStandardOutput "C:\jarvis-voice\wd_out.log" `
        -RedirectStandardError  "C:\jarvis-voice\wd_err.log"
}
