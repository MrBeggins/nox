// Выключатели функций Нокса — вкладка «Фичи» в Настройках.
// Общий файл с Python-службами: C:\jarvis-voice\nox_features.json {"ключ": true/false};
// ключа нет — функция включена. Пишется атомарно (временный файл + rename).

use std::path::Path;

pub const FEATURES_FILE: &str = r"C:\jarvis-voice\nox_features.json";

fn load() -> serde_json::Map<String, serde_json::Value> {
    std::fs::read(FEATURES_FILE)
        .ok()
        .and_then(|b| {
            let s = String::from_utf8_lossy(&b);
            serde_json::from_str::<serde_json::Value>(s.trim_start_matches('\u{feff}')).ok()
        })
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default()
}

/// Функция включена? Нет файла / нет ключа — включена.
pub fn enabled(key: &str) -> bool {
    load().get(key).and_then(|v| v.as_bool()).unwrap_or(true)
}

/// Все записанные выключатели (JSON-объект строкой) — для окна настроек.
pub fn get_json() -> String {
    serde_json::Value::Object(load()).to_string()
}

/// Включить/выключить функцию.
pub fn set(key: &str, on: bool) -> Result<(), String> {
    let mut m = load();
    m.insert(key.to_string(), serde_json::Value::Bool(on));
    let text = serde_json::to_string_pretty(&serde_json::Value::Object(m)).map_err(|e| e.to_string())?;
    let tmp = format!("{}.tmp", FEATURES_FILE);
    std::fs::write(&tmp, text).map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, Path::new(FEATURES_FILE)).map_err(|e| e.to_string())
}
