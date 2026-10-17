# Останавливает запущенный Nox (окно, голосовой движок, Python-службы, сторож) —
# перед обновлением и удалением, чтобы файлы не были заняты.
# Гасит только процессы, запущенные из папок установки (по пути exe), — не по имени.
param(
    [switch]$Uninstall,
    [string]$Root = "C:\",    # корень установки (меняется только для проверочной сборки)
    [string]$AppDataDir = ""  # не используется; принимается для единообразия с setup-nox.ps1
)
$ErrorActionPreference = "SilentlyContinue"
$VO  = Join-Path $Root "jarvis-voice"
$LAB = Join-Path $Root "jarvis-lab"
if (Test-Path $VO) {
    # метка «остановлен»: сторож ничего не поднимает назад
    Set-Content -Path "$VO\nox_stopped.flag" -Value "stopped" -Encoding ascii
}
$all   = Get-CimInstance Win32_Process
$roots = @($all | Where-Object { $_.ExecutablePath -and ($_.ExecutablePath -like "$VO\*" -or $_.ExecutablePath -like "$LAB\*") })
$ids   = @($roots | ForEach-Object { $_.ProcessId })
# python из .venv запускает дочерний python базовой установки — гасим и его
$kids  = @($all | Where-Object { $ids -contains $_.ParentProcessId -and $_.Name -like "python*" })
foreach ($p in ($roots + $kids)) { Stop-Process -Id $p.ProcessId -Force }
Start-Sleep -Milliseconds 800
if ($Uninstall -and (Test-Path "$VO\.xtts-gpu")) {
    # ссылки (junction) на torch из .venv снимаем сами — rmdir удаляет ссылку, не содержимое
    Get-ChildItem "$VO\.xtts-gpu" -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint } |
        ForEach-Object { cmd /c rmdir "$($_.FullName)" }
}
