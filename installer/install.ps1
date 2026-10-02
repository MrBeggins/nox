# ============================================================================
#  Nox — онлайн-установщик (ставит всё сам, как «игру из Steam»)
#  Запускать через Установить-Nox.bat (он поднимает права администратора).
#  Определяет видеокарту: NVIDIA -> голос F5 на GPU; иначе -> лёгкий Silero на CPU.
# ============================================================================
$ErrorActionPreference = "Stop"
$ROOT   = Split-Path -Parent $MyInvocation.MyCommand.Path   # папка установщика
$LOG    = Join-Path $env:TEMP "nox-install.log"
$LABDST = "C:\jarvis-lab"
$VODST  = "C:\jarvis-voice"
$RELEASE = "$LABDST\target\release"

function Log($m){ $t="[{0}] {1}" -f (Get-Date -Format HH:mm:ss), $m; Write-Host $t; Add-Content -Path $LOG -Value $t -Encoding UTF8 }
function Step($m){ Write-Host ""; Log ("=== " + $m + " ===") }

Log "Старт установки Nox. Лог: $LOG"

# --- 0. Определить GPU -------------------------------------------------------
Step "Проверка оборудования"
$hasNvidia = $false
try { if (Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "NVIDIA" }) { $hasNvidia = $true } } catch {}
$profile = if ($hasNvidia) { "GPU" } else { "CPU" }
Log "Профиль: $profile (NVIDIA: $hasNvidia). Голос: $(if($hasNvidia){'F5 на GPU'}else{'Silero на CPU'})"

# --- 1. Системные пакеты через winget ---------------------------------------
Step "Установка системного ПО (winget)"
function Winget-Ensure($id, $name){
    try {
        $installed = winget list --id $id -e 2>$null | Select-String $id
        if ($installed) { Log "уже стоит: $name"; return }
    } catch {}
    Log "ставлю: $name ..."
    winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
}
Winget-Ensure "Python.Python.3.11" "Python 3.11"
Winget-Ensure "Ollama.Ollama"      "Ollama"
Winget-Ensure "Gyan.FFmpeg"        "FFmpeg"
# обновить PATH текущей сессии
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

# найти python 3.11
$PY = $null
foreach($c in @("py -3.11 -V","python -V")){ try { iex $c 2>$null | Out-Null; $PY = ($c -split " ")[0..($c.Split(' ').Count-2)] -join " "; break } catch {} }
if (-not $PY) { $PY = "py -3.11" }
Log "python: $PY"

# --- 2. Скопировать код в C:\jarvis-lab и C:\jarvis-voice -------------------
Step "Копирование программы"
# ВАЖНО: пути только ASCII (Vosk ломается на кириллице)
robocopy "$ROOT\jarvis-lab"   $LABDST /E /NFL /NDL /NJH /NJS /R:1 /W:1 | Out-Null
robocopy "$ROOT\jarvis-voice" $VODST  /E /NFL /NDL /NJH /NJS /R:1 /W:1 | Out-Null
Log "код скопирован в $LABDST и $VODST"

# --- 3. Python-окружение ----------------------------------------------------
Step "Python-окружение (venv + зависимости)"
Push-Location $VODST
if (-not (Test-Path "$VODST\.venv")) { iex "$PY -m venv `"$VODST\.venv`"" }
$VPY = "$VODST\.venv\Scripts\python.exe"
& $VPY -m pip install --upgrade pip 2>&1 | Out-Null
# torch: CPU или CUDA 12.8
if ($profile -eq "GPU") {
    Log "ставлю torch (CUDA 12.8)..."
    & $VPY -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128 2>&1 | Out-Null
} else {
    Log "ставлю torch (CPU)..."
    & $VPY -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu 2>&1 | Out-Null
}
# остальные зависимости без torch-пинов (torch уже поставлен нужной сборкой)
$req = Get-Content "$VODST\requirements.txt" | Where-Object { $_ -notmatch "^(torch|torchaudio|torchcodec|torchvision)" }
$reqTmp = Join-Path $env:TEMP "nox-req.txt"; $req | Set-Content $reqTmp -Encoding UTF8
& $VPY -m pip install -r $reqTmp 2>&1 | Out-Null
Log "ставлю Chromium для Playwright..."
& $VPY -m playwright install chromium 2>&1 | Out-Null
Pop-Location

# --- 4. Готовые бинарники + ресурсы -----------------------------------------
Step "Бинарники и ресурсы"
New-Item -ItemType Directory -Force "$RELEASE" | Out-Null
Copy-Item "$ROOT\bin\*" "$RELEASE\" -Recurse -Force    # jarvis-app.exe, jarvis-gui.exe, *.dll
# ресурсы и нативные либы рядом с exe (как делает post_build.py)
robocopy "$LABDST\resources"        "$RELEASE\resources" /E /NFL /NDL /NJH /NJS /R:1 /W:1 | Out-Null
robocopy "$LABDST\lib\windows\amd64" "$RELEASE" /NFL /NDL /NJH /NJS /R:1 /W:1 | Out-Null
robocopy "$LABDST\lib\common"        "$RELEASE" /NFL /NDL /NJH /NJS /R:1 /W:1 | Out-Null
Log "бинарники в $RELEASE"

# --- 5. Модели --------------------------------------------------------------
Step "Модели"
$MODELS = "$RELEASE\resources\models"; $VOSK = "$RELEASE\resources\vosk"
New-Item -ItemType Directory -Force $MODELS,$VOSK,"$MODELS\gliner_multi-v2.1","$VODST\models" | Out-Null
# 5a. из комплекта установщика (эмбеддинги + GLiNER — их неудобно качать онлайн)
if (Test-Path "$ROOT\models") {
    Log "копирую модели из комплекта..."
    robocopy "$ROOT\models" $MODELS /E /NFL /NDL /NJH /NJS /R:1 /W:1 | Out-Null
}
# 5b. Vosk (русский) — онлайн
if (-not (Test-Path "$VOSK\vosk-model-small-ru-0.22")) {
    Log "качаю Vosk (русский)..."
    $z = Join-Path $env:TEMP "vosk-ru.zip"
    try {
        Invoke-WebRequest "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip" -OutFile $z -UseBasicParsing
        Expand-Archive $z -DestinationPath $VOSK -Force; Remove-Item $z -Force
    } catch { Log "Vosk не скачался: $($_.Exception.Message)" }
}
# 5c. Голос
if ($profile -eq "GPU") {
    Log "качаю русскую модель F5-TTS (может занять время, ~1.3 ГБ)..."
    & $VPY -m pip install huggingface_hub 2>&1 | Out-Null
    & $VPY -c "from huggingface_hub import snapshot_download; snapshot_download('Misha24-10/F5-TTS_RUSSIAN', local_dir=r'$VODST\models\ru')" 2>&1 | Out-Null
} else {
    Log "Silero скачается автоматически при первом запуске голоса (~60 МБ)."
}

# --- 6. Ollama (мозг) -------------------------------------------------------
Step "Мозг (Ollama + модель)"
try {
    Start-Process -FilePath "ollama" -ArgumentList "pull qwen2.5:3b" -NoNewWindow -Wait
    [System.Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE","-1","User")
    Log "qwen2.5:3b готов"
} catch { Log "Ollama/модель: $($_.Exception.Message). Докачайте вручную: ollama pull qwen2.5:3b" }

# --- 7. Настройки (app.db) + выбор голоса -----------------------------------
Step "Настройки"
$APPDIR = "$env:APPDATA\com.priler.jarvis"
New-Item -ItemType Directory -Force $APPDIR | Out-Null
if (-not (Test-Path "$APPDIR\app.db")) { Copy-Item "$ROOT\app.db.template" "$APPDIR\app.db" -Force; Log "app.db создан (токен T-Invest введёте в интерфейсе)" }
# движок голоса
Set-Content "$VODST\tts_engine.txt" ($(if($hasNvidia){"f5"}else{"silero"})) -Encoding UTF8 -NoNewline
Log "движок голоса: $(if($hasNvidia){'f5'}else{'silero'})"

# --- 8. Ярлык на рабочий стол -----------------------------------------------
Step "Ярлык"
try {
    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut("$env:USERPROFILE\Desktop\Nox.lnk")
    $lnk.TargetPath = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    $lnk.Arguments  = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$LABDST\start-jarvis.ps1`""
    $lnk.WorkingDirectory = $LABDST
    if (Test-Path "$LABDST\nox.ico") { $lnk.IconLocation = "$LABDST\nox.ico" }
    $lnk.Save()
    Log "ярлык Nox создан на рабочем столе"
} catch { Log "ярлык не создан: $($_.Exception.Message)" }

Step "ГОТОВО"
Log "Nox установлен. Запуск — ярлык «Nox» на рабочем столе."
Log "Осталось (один раз, вручную): войти в терминал T-Bank в окне и вставить токен T-Invest в настройках."
Write-Host ""
Write-Host "Нажмите Enter для выхода..." -ForegroundColor Green
[void](Read-Host)
