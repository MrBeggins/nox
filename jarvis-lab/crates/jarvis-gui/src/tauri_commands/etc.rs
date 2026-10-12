use jarvis_core::{config, APP_LOG_DIR};
use tauri::Manager;

/// Полностью остановить Nox: сначала watchdog (8130, иначе поднимет назад),
/// затем сервисы по портам, голосовой движок и GUI. Запускается отдельным
/// скрытым процессом, чтобы довести дело до конца даже после закрытия GUI.
#[tauri::command]
pub fn stop_nox() -> Result<(), String> {
    // общая реализация с трея: метка «остановлен» + watchdog + все сервисы + движок + GUI
    jarvis_core::shutdown::spawn_full_stop()
}

/// Разовый вход в Telegram: открыть ВИДИМОЕ окно консоли для ввода телефона и кода
/// (это единственное место, где консоль нужна — для интерактивного ввода).
#[tauri::command]
pub fn tg_login() -> Result<(), String> {
    let mut cmd = std::process::Command::new("cmd");
    cmd.args([
        "/c", "start", "Nox: vhod v Telegram",
        r"C:\jarvis-voice\.venv\Scripts\python.exe",
        r"C:\jarvis-voice\tg_login.py",
    ]);
    cmd.spawn().map_err(|e| e.to_string())?;
    Ok(())
}

/// Показать/скрыть оверлей-шар (окно label="overlay").
#[tauri::command]
pub fn set_overlay_visible(app: tauri::AppHandle, visible: bool) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("overlay") {
        if visible { w.show().map_err(|e| e.to_string())?; }
        else { w.hide().map_err(|e| e.to_string())?; }
        Ok(())
    } else {
        Err("overlay window not found".into())
    }
}

// Learn more about Tauri commands at https://tauri.app/v1/guides/features/command

#[tauri::command]
pub fn get_app_version() -> String {
    if let Some(res) = config::APP_VERSION {
        res.to_string()
    } else {
        String::from("error")
    }
}

#[tauri::command]
pub fn get_author_name() -> String {
    if let Some(res) = config::AUTHOR_NAME {
        res.to_string()
    } else {
        String::from("error")
    }
}

#[tauri::command]
pub fn get_repository_link() -> String {
    if let Some(res) = config::REPOSITORY_LINK {
        res.to_string()
    } else {
        String::from("error")
    }
}

#[tauri::command]
pub fn get_tg_official_link() -> String {
    if let Some(ver) = config::TG_OFFICIAL_LINK {
        ver.to_string()
    } else {
        String::from("error")
    }
}

#[tauri::command]
pub fn get_boosty_link() -> String {
    if let Some(ver) = config::SUPPORT_BOOSTY_LINK {
        ver.to_string()
    } else {
        String::from("error")
    }
}

#[tauri::command]
pub fn get_patreon_link() -> String {
    if let Some(ver) = config::SUPPORT_PATREON_LINK {
        ver.to_string()
    } else {
        String::from("error")
    }
}

#[tauri::command]
pub fn get_feedback_link() -> String {
    if let Some(res) = config::FEEDBACK_LINK {
        res.to_string()
    } else {
        String::from("error")
    }
}

#[tauri::command]
pub fn get_log_file_path() -> String {
    APP_LOG_DIR.get()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|| "unknown".to_string())
}