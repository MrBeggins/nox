//! Полная остановка Nox — одна реализация для трея («Выход») и GUI («Остановить Nox»).
//!
//! Порядок важен: сначала метка «остановлен» и watchdog (иначе он через ~20 с поднимет
//! всё назад), затем сервисы по портам, голосовой движок и GUI. Выполняется отдельным
//! скрытым процессом, чтобы довести дело до конца даже после выхода вызывающего.
//! Метку снимает лаунчер (start-jarvis.ps1) при следующем запуске Nox.

/// Файл-метка «пользователь остановил Nox»: watchdog видит её и не перезапускает стек.
pub const STOPPED_FLAG: &str = r"C:\jarvis-voice\nox_stopped.flag";

const STOP_SCRIPT: &str = "\
Set-Content -Path 'C:\\jarvis-voice\\nox_stopped.flag' -Value 'stopped' -Encoding ascii; \
$w=Get-NetTCPConnection -LocalPort 8130 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; \
if($w){ Stop-Process -Id $w.OwningProcess -Force -ErrorAction SilentlyContinue }; \
Start-Sleep -Milliseconds 300; \
foreach($p in 8123,8124,8126,8127,8128,8129,8131,8132,8133,9712){ \
$c=Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; \
if($c){ Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue } }; \
taskkill /F /IM jarvis-app.exe 2>$null; Start-Sleep -Seconds 1; taskkill /F /IM jarvis-gui.exe 2>$null";

/// Запустить полную остановку в фоне (скрыто, без окна консоли).
pub fn spawn_full_stop() -> Result<(), String> {
    let mut cmd = std::process::Command::new("powershell");
    cmd.args(["-NoProfile", "-WindowStyle", "Hidden", "-Command", STOP_SCRIPT]);
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    cmd.spawn().map(|_| ()).map_err(|e| e.to_string())
}
