# Удаление проверочной установки C:\noxtest и проверка, что рабочий Nox не тронут.
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$R   = "C:\noxtest"
$OUT = "C:\nox-release\tests\results"
function Say($m) { $t = "[{0}] {1}" -f (Get-Date -Format HH:mm:ss), $m; Write-Host $t; Add-Content "$OUT\test.log" $t -Encoding UTF8 }
$key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{3B7D9E14-2C55-4F0A-8A11-7E6C4D2B9F01}_is1"
$un = "$R\jarvis-lab\unins000.exe"
if (-not (Test-Path $un)) { Say "нет деинсталлятора $un"; exit 2 }
Say "удаление: $un"
Start-Process $un -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' | Out-Null
# деинсталлятор Inno перезапускает себя из %TEMP% — ждём, пока исчезнет запись в «Приложениях»
for ($i = 0; $i -lt 120 -and (Test-Path $key); $i++) { Start-Sleep -Seconds 2 }
Start-Sleep -Seconds 3
$left = @()
if (Test-Path $R) { $left = @(Get-ChildItem $R -Recurse -Force -File -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName.Substring($R.Length + 1) }) }
Say ("запись в «Приложениях»: {0}" -f $(if (Test-Path $key) { "ОСТАЛАСЬ" } else { "удалена" }))
Say (".venv: {0}; models: {1}" -f $(if (Test-Path "$R\jarvis-voice\.venv") { "ОСТАЛСЯ" } else { "удалён" }), $(if (Test-Path "$R\jarvis-voice\models") { "ОСТАЛИСЬ" } else { "удалены" }))
Say ("осталось файлов: {0} {1}" -f $left.Count, (($left | Select-Object -First 8) -join ", "))
# рабочий Nox
$before = Get-Content "$OUT\live_before.json" -Raw | ConvertFrom-Json
$xtts = (Get-Item "$env:LOCALAPPDATA\tts\tts_models--multilingual--multi-dataset--xtts_v2\model.pth" -ErrorAction SilentlyContinue).Length
Say ("рабочий Nox: .venv={0}, модель XTTS={1} (было {2}), app.db={3}" -f (Test-Path "C:\jarvis-voice\.venv\Scripts\python.exe"), $xtts, $before.xtts, (Test-Path "$env:APPDATA\com.priler.jarvis\app.db"))
Say "UNINSTALL-DONE"
