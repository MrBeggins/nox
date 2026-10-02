# Jarvis F5 voice: POST текста на локальный TTS-сервер, проигрывание WAV.
# Вход:  $env:JARVIS_TTS_FILE  — UTF-8 файл с текстом
#        $env:JARVIS_F5_URL    — URL сервера (по умолч. http://127.0.0.1:8123/tts)
# Код возврата: 0 — озвучено (или хотя бы получено аудио); !=0 — сервер недоступен
#              (тогда Rust откатится на Pavel). ВАЖНО: после того как аудио получено,
#              всегда exit 0 — иначе будет двойная озвучка (F5 + Pavel).

$url = $env:JARVIS_F5_URL
if ([string]::IsNullOrWhiteSpace($url)) { $url = 'http://127.0.0.1:8123/tts' }
$wav = Join-Path $env:TEMP ("jarvis_f5_{0}.wav" -f $PID)

# --- Этап 1: получить аудио с сервера. 3 попытки (сервер может быть занят
#     генерацией предыдущей фразы). Только если все провалились -> Pavel (exit 1). ---
try {
    $text = [System.IO.File]::ReadAllText($env:JARVIS_TTS_FILE, [System.Text.Encoding]::UTF8)
} catch { exit 1 }
if ([string]::IsNullOrWhiteSpace($text)) { exit 2 }

$body = @{ text = $text }
# необязательный nfe (качество/скорость синтеза) — для коротких ответов терминала пониже
if (-not [string]::IsNullOrWhiteSpace($env:JARVIS_TTS_NFE)) {
    $n = 0
    if ([int]::TryParse($env:JARVIS_TTS_NFE, [ref]$n) -and $n -gt 0) { $body.nfe = $n }
}
# необязательная скорость речи — для зачитки новостей/календаря «тараторит» (1.8)
if (-not [string]::IsNullOrWhiteSpace($env:JARVIS_TTS_SPEED)) {
    $sp = 0.0
    if ([double]::TryParse($env:JARVIS_TTS_SPEED, [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$sp) -and $sp -gt 0) { $body.speed = $sp }
}
$payload = $body | ConvertTo-Json -Compress
$bytes = [System.Text.Encoding]::UTF8.GetBytes($payload)

$got = $false
for ($try = 1; $try -le 3; $try++) {
    try {
        Invoke-WebRequest -Uri $url -Method Post `
            -ContentType 'application/json; charset=utf-8' `
            -Body $bytes -OutFile $wav -TimeoutSec 90 -UseBasicParsing | Out-Null
        if ((Test-Path $wav) -and (Get-Item $wav).Length -ge 128) { $got = $true; break }
    } catch {
        Start-Sleep -Milliseconds 800
    }
}
if (-not $got) { exit 1 }

# --- Этап 2: воспроизвести. Аудио уже есть -> любые ошибки глушим и всё равно exit 0. ---
try {
    $player = New-Object System.Media.SoundPlayer $wav
    $player.PlaySync()
    $player.Dispose()
} catch {}
try { Remove-Item -LiteralPath $wav -Force -ErrorAction SilentlyContinue } catch {}
exit 0
