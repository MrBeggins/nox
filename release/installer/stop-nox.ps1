# Останавливает запущенный Nox (окно, голосовой движок, Python-службы, сторож) —
# перед обновлением и удалением, чтобы файлы не были заняты.
param([switch]$Uninstall)
$ErrorActionPreference = "SilentlyContinue"
if (Test-Path "C:\jarvis-voice") {
    # метка «остановлен»: сторож ничего не поднимает назад
    Set-Content -Path "C:\jarvis-voice\nox_stopped.flag" -Value "stopped" -Encoding ascii
}
Stop-Process -Name "jarvis-gui", "jarvis-app" -Force
$all   = Get-CimInstance Win32_Process
$roots = @($all | Where-Object { $_.ExecutablePath -and $_.ExecutablePath -like "C:\jarvis-voice\*" })
$ids   = @($roots | ForEach-Object { $_.ProcessId })
# python из .venv запускает дочерний python базовой установки — гасим и его
$kids  = @($all | Where-Object { $ids -contains $_.ParentProcessId -and $_.Name -like "python*" })
foreach ($p in ($roots + $kids)) { Stop-Process -Id $p.ProcessId -Force }
Start-Sleep -Milliseconds 800
if ($Uninstall -and (Test-Path "C:\jarvis-voice\.xtts-gpu")) {
    # ссылки (junction) на torch из .venv снимаем сами — rmdir удаляет ссылку, не содержимое
    Get-ChildItem "C:\jarvis-voice\.xtts-gpu" -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint } |
        ForEach-Object { cmd /c rmdir "$($_.FullName)" }
}
