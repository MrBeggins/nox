// Хранилище голосовых/ручных записей календаря (команды/события/напоминания).
// Один JSON-файл в конфиг-директории; его же пишет jarvis-app при голосовом вводе.
use jarvis_core::APP_CONFIG_DIR;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Serialize, Deserialize, Clone)]
pub struct AgendaItem {
    pub id: String,
    pub kind: String, // cmd | evt | rem
    pub text: String,
    #[serde(default)]
    pub when: String,
    #[serde(default)]
    pub done: bool,
    #[serde(default)]
    pub created: u64,
    #[serde(default)]
    pub source: String, // voice | manual
}

fn agenda_path() -> PathBuf {
    let dir = APP_CONFIG_DIR
        .get()
        .cloned()
        .unwrap_or_else(|| PathBuf::from("."));
    dir.join("nox_agenda.json")
}

fn read_items() -> Vec<AgendaItem> {
    match fs::read_to_string(agenda_path()) {
        Ok(s) => serde_json::from_str(&s).unwrap_or_default(),
        Err(_) => Vec::new(),
    }
}

fn write_items(items: &[AgendaItem]) -> Result<(), String> {
    let s = serde_json::to_string_pretty(items).map_err(|e| e.to_string())?;
    fs::write(agenda_path(), s).map_err(|e| e.to_string())
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

#[tauri::command]
pub fn agenda_list() -> String {
    // отдаём сырым JSON, фронт распарсит
    fs::read_to_string(agenda_path()).unwrap_or_else(|_| "[]".to_string())
}

#[tauri::command]
pub fn agenda_add(kind: String, text: String, when: String) -> Result<String, String> {
    let text = text.trim().to_string();
    if text.is_empty() {
        return Err("empty text".into());
    }
    let k = match kind.as_str() {
        "cmd" | "evt" | "rem" => kind,
        _ => "rem".to_string(),
    };
    let mut items = read_items();
    let id = format!("a{}", now_ms());
    items.insert(
        0,
        AgendaItem {
            id: id.clone(),
            kind: k,
            text,
            when: when.trim().to_string(),
            done: false,
            created: now_ms(),
            source: "manual".into(),
        },
    );
    write_items(&items)?;
    Ok(id)
}

#[tauri::command]
pub fn agenda_set_done(id: String, done: bool) -> Result<(), String> {
    let mut items = read_items();
    for it in items.iter_mut() {
        if it.id == id {
            it.done = done;
        }
    }
    write_items(&items)
}

#[tauri::command]
pub fn agenda_delete(id: String) -> Result<(), String> {
    let mut items = read_items();
    items.retain(|it| it.id != id);
    write_items(&items)
}

#[tauri::command]
pub fn agenda_clear_done() -> Result<(), String> {
    let mut items = read_items();
    items.retain(|it| !it.done);
    write_items(&items)
}
