# ============================================================================
#  Nox — донастройка после распаковки (вызывается установщиком Nox-Setup.exe).
#  Можно запускать повторно: ярлык «Nox — починить установку» в меню Пуск.
#  Ставит то, что нельзя положить в установщик: Python 3.11, библиотеки (PyPI),
#  по желанию Ollama + модель и Яндекс.Браузер; создаёт стартовые настройки.
# ============================================================================
param(
    [switch]$Ollama,    # локальный мозг Ollama + qwen2.5:3b
    [switch]$Yandex,    # Яндекс.Браузер (окно Т-Терминала)
    [switch]$F5,        # голос-клон Джарвиса F5-TTS (видеокарта NVIDIA)
    [switch]$Xtts,      # голос-клон Джарвиса XTTS v2 (видеокарта NVIDIA)
    [switch]$NoPause,   # не ждать Enter в конце (тихая установка /VERYSILENT)
    [string]$Root = "C:\",          # корень установки (меняется только для проверочной сборки)
    [string]$AppDataDir = ""         # папка настроек вместо %APPDATA%\com.priler.jarvis (проверочная сборка)
)
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Host.UI.RawUI.WindowTitle = "Nox — установка компонентов"

$VO   = Join-Path $Root "jarvis-voice"
$LAB  = Join-Path $Root "jarvis-lab"
$LOG  = "$VO\install.log"
$TMP  = Join-Path $env:TEMP "nox-setup"
$VPY  = "$VO\.venv\Scripts\python.exe"
$problems = New-Object System.Collections.Generic.List[string]
New-Item -ItemType Directory -Force $TMP | Out-Null

function Log($m, $color = "Gray") {
    $t = "[{0}] {1}" -f (Get-Date -Format HH:mm:ss), $m
    Write-Host $t -ForegroundColor $color
    Add-Content -Path $LOG -Value $t -Encoding UTF8
}
function Step($m) { Write-Host ""; Log ("=== " + $m + " ===") "Cyan" }
function Problem($m) { Log ("ВНИМАНИЕ: " + $m) "Yellow"; $problems.Add($m) }

function Download($url, $out) {
    # curl.exe есть в Windows 10 1803+; докачивает при обрыве (-C -), повторяет при сбоях
    $curl = Join-Path $env:SystemRoot "System32\curl.exe"
    if (Test-Path $curl) {
        & $curl -L --fail --retry 5 --retry-delay 3 --connect-timeout 30 -C - -o $out $url
        if ($LASTEXITCODE -eq 0 -and (Test-Path $out)) { return $true }
    }
    try { Invoke-WebRequest $url -OutFile $out -UseBasicParsing; return (Test-Path $out) } catch { return $false }
}

function Test-Port($p) {
    try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', $p); $c.Close(); return $true } catch { return $false }
}

function Fetch($url, $out, $size, $sha) {
    # большие файлы — кусками с проверкой SHA-256 (nox_fetch.py), повторный запуск продолжает
    # Start-Process, а не «&»: вывод (прогресс) идёт в окно, а не в результат функции
    $a = @("-I", "$VO\installer\nox_fetch.py", $url, $out)
    if ($size) { $a += @("--size", "$size") }
    if ($sha)  { $a += @("--sha256", $sha) }
    $line = ($a | ForEach-Object { '"' + $_ + '"' }) -join " "
    for ($i = 1; $i -le 3; $i++) {
        $p = Start-Process -FilePath $BASEPY -ArgumentList $line -NoNewWindow -Wait -PassThru
        if ($p.ExitCode -eq 0) { return $true }
    }
    return $false
}

Log "Старт донастройки Nox. Журнал: $LOG" "Green"

# выбранные в установщике компоненты запоминаем: «Починить установку» (без параметров) чинит их же
$OPT = "$VO\installer\options.txt"
if (-not ($Ollama -or $Yandex -or $F5 -or $Xtts) -and (Test-Path $OPT)) {
    $o = Get-Content $OPT -Raw
    $Ollama = $o -match "ollama"; $Yandex = $o -match "yandex"; $F5 = $o -match "f5"; $Xtts = $o -match "xtts"
} else {
    $sel = @(); if ($Ollama) { $sel += "ollama" }; if ($Yandex) { $sel += "yandex" }
    if ($F5) { $sel += "f5" }; if ($Xtts) { $sel += "xtts" }
    Set-Content $OPT ($sel -join " ") -Encoding ascii
}
$NVIDIA = Test-Path "$env:SystemRoot\System32\nvidia-smi.exe"
$wantGpu = ($F5 -or $Xtts)
if ($wantGpu -and -not $NVIDIA) {
    Problem "голоса-клоны Джарвиса (F5/XTTS) работают только на видеокарте NVIDIA — пропускаю их"
    $F5 = $false; $Xtts = $false; $wantGpu = $false
}
$DL = "$VO\downloads"   # недокачанное остаётся здесь до следующего запуска

# --- 1. Microsoft Visual C++ Runtime (нужен голосовому движку и onnxruntime) ---
Step "Visual C++ Runtime"
$vc = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" -ErrorAction SilentlyContinue
if ($vc -and $vc.Installed -eq 1) {
    Log "уже установлен ($($vc.Version))"
} else {
    $f = "$TMP\vc_redist.x64.exe"
    if (Download "https://aka.ms/vs/17/release/vc_redist.x64.exe" $f) {
        Log "ставлю (Windows спросит разрешение администратора)..."
        try { Start-Process $f -ArgumentList "/install /quiet /norestart" -Verb RunAs -Wait } catch { Problem "Visual C++ Runtime не установлен: $($_.Exception.Message)" }
    } else { Problem "не удалось скачать Visual C++ Runtime (https://aka.ms/vs/17/release/vc_redist.x64.exe)" }
}

# --- 2. WebView2 (окно Nox) — в Windows 11 уже есть ---------------------------
Step "WebView2 Runtime"
$wvKey = "SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
$wv = (Get-ItemProperty "HKLM:\$wvKey" -ErrorAction SilentlyContinue).pv
if (-not $wv) { $wv = (Get-ItemProperty "HKCU:\$($wvKey -replace 'WOW6432Node\\','')" -ErrorAction SilentlyContinue).pv }
if ($wv -and $wv -ne "0.0.0.0") {
    Log "уже установлен ($wv)"
} else {
    $f = "$TMP\MicrosoftEdgeWebview2Setup.exe"
    if (Download "https://go.microsoft.com/fwlink/p/?LinkId=2124703" $f) {
        Log "ставлю WebView2..."
        Start-Process $f -ArgumentList "/silent /install" -Wait
    } else { Problem "не удалось скачать WebView2 (https://developer.microsoft.com/microsoft-edge/webview2/)" }
}

# --- 3. Python 3.11 -----------------------------------------------------------
Step "Python 3.11"
function Find-Python311 {
    try {
        $p = (& py -3.11 -c "import sys; print(sys.executable)" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $p -and (Test-Path $p.Trim())) { return $p.Trim() }
    } catch {}
    foreach ($c in @("$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
                     "C:\Program Files\Python311\python.exe", "C:\Python311\python.exe")) {
        if (Test-Path $c) { return $c }
    }
    return $null
}
$BASEPY = Find-Python311
if (-not $BASEPY) {
    $f = "$TMP\python-3.11.9-amd64.exe"
    Log "Python 3.11 не найден — скачиваю с python.org (~25 МБ)..."
    if (Download "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe" $f) {
        Log "ставлю Python 3.11 (для текущего пользователя)..."
        Start-Process $f -ArgumentList "/quiet InstallAllUsers=0 PrependPath=0 Include_test=0 Include_doc=0 Shortcuts=0 Include_launcher=1 InstallLauncherAllUsers=0" -Wait
        $BASEPY = Find-Python311
    }
}
if (-not $BASEPY) {
    Problem "Python 3.11 не установлен. Поставьте его вручную: https://www.python.org/downloads/release/python-3119/ и запустите «Nox — починить установку»."
} else {
    Log "Python: $BASEPY"
}

# --- 4. Окружение .venv и библиотеки ------------------------------------------
if ($BASEPY) {
    Step "Окружение Python и библиотеки (это самый долгий шаг)"
    $venvOk = $false
    if (Test-Path $VPY) {
        $v = (& $VPY -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null)
        if ($LASTEXITCODE -eq 0 -and "$v".Trim() -eq "3.11") { $venvOk = $true; Log "окружение .venv уже есть" }
        else { Log "окружение .venv повреждено — пересоздаю"; Remove-Item "$VO\.venv" -Recurse -Force -ErrorAction SilentlyContinue }
    }
    if (-not $venvOk) { & $BASEPY -m venv "$VO\.venv"; Log "создано окружение $VO\.venv" }

    if (-not (Test-Path $VPY)) {
        Problem "не удалось создать окружение Python в $VO\.venv"
    } else {
        $pipArgs = @("--disable-pip-version-check", "--retries", "10", "--timeout", "60")
        & $VPY -m pip install --upgrade pip @pipArgs

        $tv = "$(& $VPY -c "import torch; print(torch.__version__)" 2>$null)".Trim()
        if ($wantGpu -and $tv -notmatch "cu128") {
            # голоса-клоны: torch с CUDA 12.8 (~3 ГБ). Качаем кусками — pip не умеет докачивать
            Log "ставлю torch для видеокарты (CUDA 12.8, ~3 ГБ — это долго)..."
            $whl = "https://download-r2.pytorch.org/whl/cu128"
            $t1 = "$DL\torch-2.11.0+cu128-cp311-cp311-win_amd64.whl"
            $t2 = "$DL\torchaudio-2.11.0+cu128-cp311-cp311-win_amd64.whl"
            $ok1 = Fetch "$whl/torch-2.11.0%2Bcu128-cp311-cp311-win_amd64.whl" $t1 0 "90ef0c2454e5296a9fb021ddd42252e4ce1abe2c0a4988a173ef90a6cded0bf5"
            $ok2 = Fetch "$whl/torchaudio-2.11.0%2Bcu128-cp311-cp311-win_amd64.whl" $t2 0 "0f2995101f526a2220dc348d35b153f327ed59b247fa249adddc4c478cece566"
            if ($ok1 -and $ok2) {
                & $VPY -m pip install $t1 $t2 "torchcodec==0.16.0" @pipArgs
                if ($LASTEXITCODE -eq 0) { Remove-Item $t1, $t2 -Force -ErrorAction SilentlyContinue }
            }
            $tv = "$(& $VPY -c "import torch; print(torch.__version__)" 2>$null)".Trim()
            if ($tv -notmatch "cu128") {
                Problem "torch для видеокарты не установлен — голоса F5/XTTS пропущены (запустите «Nox — починить установку»)"
                $F5 = $false; $Xtts = $false
            }
        }
        # torch: CPU-сборка (голос Silero быстрый и без видеокарты)
        & $VPY -c "import torch" 2>$null
        if ($LASTEXITCODE -ne 0) {
            Log "ставлю torch (CPU, ~200 МБ)..."
            & $VPY -m pip install "torch==2.11.0" "torchaudio==2.11.0" --index-url https://download.pytorch.org/whl/cpu @pipArgs
            if ($LASTEXITCODE -ne 0) {
                Log "сервер pytorch.org недоступен — пробую PyPI..."
                & $VPY -m pip install "torch==2.11.0" "torchaudio==2.11.0" @pipArgs
            }
        } else { Log "torch уже стоит" }

        Log "ставлю остальные библиотеки..."
        & $VPY -m pip install -r "$VO\installer\requirements-nox.txt" @pipArgs
        if ($LASTEXITCODE -ne 0) {
            Log "повтор установки библиотек..."
            & $VPY -m pip install -r "$VO\installer\requirements-nox.txt" @pipArgs
        }

        # модели ударений RUAccent лежат в комплекте — кладём туда, где их ищет библиотека
        $ra = "$VO\.venv\Lib\site-packages\ruaccent"
        if ((Test-Path "$VO\models\ruaccent") -and (Test-Path $ra)) {
            robocopy "$VO\models\ruaccent" $ra /E /MOVE /NFL /NDL /NJH /NJS /R:1 /W:1 | Out-Null
            Log "модели ударений RUAccent на месте"
        }

        # проверка
        & $VPY -c "import fastapi, uvicorn, torch, numpy, soundfile, num2words, pymorphy3, ruaccent, playwright, psutil, comtypes; print('ok')"
        if ($LASTEXITCODE -eq 0) { Log "библиотеки Python: ОК" "Green" }
        else { Problem "часть библиотек Python не поставилась (см. сообщения выше). Проверьте интернет и запустите «Nox — починить установку»." }
    }
}

# --- 4b. Голос-клон Джарвиса F5-TTS (NVIDIA, по желанию) ----------------------
if ($F5 -and $BASEPY -and (Test-Path $VPY)) {
    Step "Голос Джарвиса F5-TTS (видеокарта)"
    & $VPY -m pip install "f5-tts==1.1.22" -c "$VO\installer\constraints-f5.txt" @pipArgs
    $hf = "https://huggingface.co"
    $m1 = Fetch "$hf/Misha24-10/F5-TTS_RUSSIAN/resolve/main/F5TTS_v1_Base_v2/model_last_inference.safetensors" `
                "$VO\models\ru\F5TTS_v1_Base_v2\model_last_inference.safetensors" 1348435761 "f11e788c8d2d86b8d6e3e2e7a12c583932d2494b015b544793c1924360b474d3"
    $m2 = Fetch "$hf/Misha24-10/F5-TTS_RUSSIAN/resolve/main/F5TTS_v1_Base/vocab.txt" "$VO\models\ru\F5TTS_v1_Base\vocab.txt" 13800 ""
    $m3 = Fetch "$hf/charactr/vocos-mel-24khz/resolve/main/config.yaml" "$VO\models\vocos-mel-24khz\config.yaml" 461 ""
    $m4 = Fetch "$hf/charactr/vocos-mel-24khz/resolve/main/pytorch_model.bin" "$VO\models\vocos-mel-24khz\pytorch_model.bin" 54365991 "97ec976ad1fd67a33ab2682d29c0ac7df85234fae875aefcc5fb215681a91b2a"
    & $VPY -c "import f5_tts.api" 2>$null
    if ($LASTEXITCODE -eq 0 -and $m1 -and $m2 -and $m3 -and $m4) {
        Log "голос F5 готов — выберите «F5 — голос Джарвиса» в Настройки → Голос" "Green"
    } else { Problem "голос F5 установлен не полностью (см. сообщения выше) — запустите «Nox — починить установку»" }
}

# --- 4c. Голос-клон Джарвиса XTTS v2 (NVIDIA, по желанию) ---------------------
if ($Xtts -and $BASEPY -and (Test-Path $VPY)) {
    Step "Голос Джарвиса XTTS v2 (видеокарта)"
    $XPY = "$VO\.venv-xtts\Scripts\python.exe"
    if (-not (Test-Path $XPY)) { & $BASEPY -m venv "$VO\.venv-xtts"; Log "создано окружение $VO\.venv-xtts" }
    & $XPY -m pip install --upgrade pip @pipArgs
    # --no-deps: точный набор без torch — torch для видеокарты берётся ссылками из основного .venv (без лишних 200+ МБ)
    & $XPY -m pip install --no-deps -r "$VO\installer\requirements-xtts.txt" @pipArgs
    # ссылки (junction) на torch cu128 из .venv — их подхватывает tts_xtts_server.py
    $sp = "$VO\.venv\Lib\site-packages"; $gpu = "$VO\.xtts-gpu"
    New-Item -ItemType Directory -Force $gpu | Out-Null
    $names = @("torch", "functorch", "torchgen", "torchaudio", "torchcodec") +
             @(Get-ChildItem $sp -Directory | Where-Object { $_.Name -match '^(torch|torchaudio|torchcodec)-[0-9].*\.dist-info$' } | ForEach-Object { $_.Name })
    foreach ($n in $names) {
        if ((Test-Path "$sp\$n") -and -not (Test-Path "$gpu\$n")) { cmd /c mklink /J "$gpu\$n" "$sp\$n" | Out-Null }
    }
    $md = "$env:LOCALAPPDATA\tts\tts_models--multilingual--multi-dataset--xtts_v2"
    $hf = "https://huggingface.co/coqui/XTTS-v2/resolve/main"
    $x = @(
        (Fetch "$hf/config.json"       "$md\config.json"       4368    ""),
        (Fetch "$hf/vocab.json"        "$md\vocab.json"        361219  ""),
        (Fetch "$hf/LICENSE.txt"       "$md\LICENSE.txt"       4014    ""),
        (Fetch "$hf/speakers_xtts.pth" "$md\speakers_xtts.pth" 7754818 "f0f6137c19a4eab0cbbe4c99b5babacf68b1746e50da90807708c10e645b943b"),
        (Fetch "$hf/model.pth"         "$md\model.pth"         1867929118 "c7ea20001c6a0a841c77e252d8409f6a74fb423e79b3206a0771ba5989776187"))
    if ($x -contains $false) {
        Problem "модель XTTS скачана не полностью — запустите «Nox — починить установку», докачает с места обрыва"
    } else {
        Log "самопроверка XTTS (загрузка модели и пробная фраза, 1–2 минуты)..."
        Remove-Item "$VO\xtts_ready.flag" -Force -ErrorAction SilentlyContinue
        Push-Location $VO; & $XPY "$VO\xtts_selftest.py"; Pop-Location
        if (Test-Path "$VO\xtts_ready.flag") { Log "голос XTTS готов — выберите «XTTS — голос Джарвиса» в Настройки → Голос" "Green" }
        else { Problem "самопроверка XTTS не прошла (см. сообщения выше)" }
    }
}
if (Test-Path $DL) { if (-not (Get-ChildItem $DL -Recurse -File)) { Remove-Item $DL -Recurse -Force } }

# --- 5. Ollama — локальный бесплатный мозг (по желанию) -----------------------
if ($Ollama) {
    Step "Локальный мозг Ollama"
    $oexe = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
    if (-not (Test-Path $oexe)) {
        $f = "$TMP\OllamaSetup.exe"
        Log "скачиваю Ollama (около 1 ГБ)..."
        if (Download "https://ollama.com/download/OllamaSetup.exe" $f) {
            Log "ставлю Ollama..."
            Start-Process $f -ArgumentList "/VERYSILENT /NORESTART /SUPPRESSMSGBOXES" -Wait
        }
    }
    if (Test-Path $oexe) {
        if (-not (Test-Port 11434)) { Start-Process $oexe -ArgumentList "serve" -WindowStyle Hidden; Start-Sleep -Seconds 5 }
        [System.Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", "-1", "User")
        Log "качаю модель qwen2.5:3b (~2 ГБ)..."
        & $oexe pull qwen2.5:3b
        if ($LASTEXITCODE -eq 0) { Log "мозг qwen2.5:3b готов" "Green" } else { Problem "модель не скачалась — потом выполните: ollama pull qwen2.5:3b" }
    } else { Problem "Ollama не установлена (https://ollama.com/download). Без неё работает облачный мозг по ключу (страница «Мозг»)." }
}

# --- 6. Яндекс.Браузер — окно Т-Терминала (по желанию) ------------------------
if ($Yandex) {
    Step "Яндекс.Браузер"
    $yb = @("C:\Program Files\Yandex\YandexBrowser\Application\browser.exe",
            "C:\Program Files (x86)\Yandex\YandexBrowser\Application\browser.exe",
            "$env:LOCALAPPDATA\Yandex\YandexBrowser\Application\browser.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($yb) {
        Log "уже установлен: $yb"
    } else {
        $ok = $false
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Log "ставлю через winget..."
            winget install --id Yandex.Browser -e --silent --accept-package-agreements --accept-source-agreements
            $ok = ($LASTEXITCODE -eq 0)
        }
        if (-not $ok) {
            Problem "Яндекс.Браузер не установлен автоматически — открываю страницу загрузки."
            Start-Process "https://browser.yandex.ru/"
        }
    }
}

# --- 7. Стартовые настройки ---------------------------------------------------
Step "Настройки"
$APPDIR = if ($AppDataDir) { $AppDataDir } else { Join-Path $env:APPDATA "com.priler.jarvis" }
New-Item -ItemType Directory -Force $APPDIR | Out-Null
if (-not (Test-Path "$APPDIR\app.db")) {
    Copy-Item "$VO\installer\app.db.template" "$APPDIR\app.db" -Force
    Log "созданы стартовые настройки $APPDIR\app.db"
} else { Log "настройки уже есть — не трогаю" }
if (-not (Test-Path "$VO\tts_engine.txt")) {
    [System.IO.File]::WriteAllText("$VO\tts_engine.txt", "silero")
}
Remove-Item "$VO\nox_stopped.flag" -Force -ErrorAction SilentlyContinue

# --- Итог ---------------------------------------------------------------------
Step "Готово"
Remove-Item $TMP -Recurse -Force -ErrorAction SilentlyContinue
if ($problems.Count -eq 0) {
    Log "Nox установлен. Запуск — ярлык «Nox» на рабочем столе." "Green"
    Log "При первом запуске откроется окно «Профиль»: имя и ключи (T-Invest, APIMIRA и др.)." "Green"
    if ($F5 -or $Xtts) { Log "Голос Джарвиса включается в Настройки → Голос (по умолчанию — Silero)." "Green" }
    Start-Sleep -Seconds 4
    exit 0
} else {
    Write-Host ""
    Log "Установка завершена с замечаниями:" "Yellow"
    foreach ($p in $problems) { Write-Host " - $p" -ForegroundColor Yellow }
    Write-Host ""
    Write-Host "Исправьте и запустите «Nox — починить установку» из меню Пуск." -ForegroundColor Yellow
    if (-not $NoPause) { Write-Host "Нажмите Enter..." -ForegroundColor Yellow; [void](Read-Host) }
    exit 1
}
