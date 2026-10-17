# Проверка установщика на этом ПК без вмешательства в рабочий Nox:
# проверочная сборка (dist-test) ставится в C:\noxtest — тот же мастер, те же скрипты, другой корень.
#   1) тихая установка  2) проверка файлов/окружения/настроек  3) запуск голоса и тестов  4) удаление
param([string]$Version = "2026.10.18", [switch]$SkipInstall)
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$R    = "C:\noxtest"
$EXE  = "C:\nox-release\dist-test\Nox-Setup-TEST-$Version.exe"
$OUT  = "C:\nox-release\tests\results"
New-Item -ItemType Directory -Force $OUT | Out-Null
function Say($m) { $t = "[{0}] {1}" -f (Get-Date -Format HH:mm:ss), $m; Write-Host $t; Add-Content "$OUT\test.log" $t -Encoding UTF8 }

# что у рабочего Nox до проверки — потом сверим, что его не тронули
$live = @{
    procs = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like "C:\jarvis-*" } | ForEach-Object { $_.ProcessId }) | Sort-Object
    venv  = Test-Path "C:\jarvis-voice\.venv\Scripts\python.exe"
    xtts  = (Get-Item "$env:LOCALAPPDATA\tts\tts_models--multilingual--multi-dataset--xtts_v2\model.pth" -ErrorAction SilentlyContinue).Length
}
$live | ConvertTo-Json | Set-Content "$OUT\live_before.json" -Encoding UTF8
Say "рабочий Nox: процессов $($live.procs.Count), .venv=$($live.venv), модель XTTS=$($live.xtts)"

if (-not $SkipInstall) {
    if (Test-Path $R) { Say "СТОП: $R уже существует"; exit 2 }
    Say "установка: $EXE"
    $t0 = Get-Date
    $p = Start-Process $EXE -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/TASKS=""', "/LOG=`"$OUT\inno-install.log`"" -Wait -PassThru
    Say ("установка завершена: код {0}, {1:N1} мин" -f $p.ExitCode, ((Get-Date) - $t0).TotalMinutes)
}
Say "INSTALL-DONE"
