//! Окно «Регистрация»: имя (как Нокс обращается к пользователю) + ключи сервисов.
//! Каждый ключ пишется туда, откуда его уже читает Нокс:
//!   app.db — invest_token (T-Invest), api_keys.openai (APIMIRA), fmp_key, tt_token, stocksi_key;
//!   C:\jarvis-voice — bls_key.txt, bea_key.txt, apimira_key.txt (Чат с Astra / агент).
//! Значения ключей назад в окно НЕ отдаются — только длина («задан, N символов»),
//! и в ошибки/логи не попадают.

use crate::AppState;
use serde::Deserialize;

const VOICE_DIR: &str = r"C:\jarvis-voice";

fn file_key(name: &str) -> String {
    std::fs::read_to_string(format!(r"{}\{}", VOICE_DIR, name))
        .map(|s| s.trim_start_matches('\u{feff}').trim().to_string())
        .unwrap_or_default()
}

fn write_key_file(name: &str, val: &str) -> Result<(), String> {
    std::fs::write(format!(r"{}\{}", VOICE_DIR, name), val.as_bytes())
        .map_err(|_| format!("не удалось записать {}", name))
}

#[tauri::command]
pub fn profile_get() -> String {
    let s = jarvis_core::db::init_settings();   // свежая копия с диска
    let len = |v: &str| v.trim().chars().count();
    let apimira = if s.api_keys.openai.trim().is_empty() { file_key("apimira_key.txt") } else { s.api_keys.openai.clone() };
    serde_json::json!({
        "name": s.user_name,
        "registered": !s.user_name.trim().is_empty(),
        "keys": {
            "tinvest": len(&s.invest_token),
            "apimira": len(&apimira),
            "bls": len(&file_key("bls_key.txt")),
            "bea": len(&file_key("bea_key.txt")),
            "fmp": len(&s.fmp_key),
            "tt": len(&s.tt_token),
            "stocksi": len(&s.stocksi_key),
        }
    })
    .to_string()
}

#[derive(Deserialize)]
pub struct ProfileInput {
    name: Option<String>,
    tinvest: Option<String>,
    apimira: Option<String>,
    bls: Option<String>,
    bea: Option<String>,
    fmp: Option<String>,
    tt: Option<String>,
    stocksi: Option<String>,
}

/// Сохранить профиль. Пустое поле ключа = не менять (окно не видит старых значений
/// и не должно их стирать). Имя сохраняется как есть (пустое — снова «сэр»).
#[tauri::command]
pub fn profile_save(state: tauri::State<'_, AppState>, p: ProfileInput) -> Result<String, String> {
    let filled = |o: &Option<String>| o.as_deref().map(str::trim).filter(|v| !v.is_empty()).map(str::to_string);
    let mut pairs: Vec<(&str, String)> = Vec::new();
    if let Some(n) = p.name.as_deref() {
        pairs.push(("user_name", n.trim().to_string()));
    }
    if let Some(v) = filled(&p.tinvest) { pairs.push(("invest_token", v)); }
    if let Some(v) = filled(&p.fmp) { pairs.push(("fmp_key", v)); }
    if let Some(v) = filled(&p.tt) { pairs.push(("tt_token", v)); }
    if let Some(v) = filled(&p.stocksi) { pairs.push(("stocksi_key", v)); }
    if let Some(v) = filled(&p.apimira) {
        let cur = jarvis_core::db::init_settings();
        if cur.openai_url.trim().is_empty() { pairs.push(("openai_url", "https://apimira.com/v1".into())); }
        if cur.openai_model.trim().is_empty() { pairs.push(("openai_model", "openai/gpt-5.4-mini".into())); }
        write_key_file("apimira_key.txt", &v)?;
        pairs.push(("api_key__openai", v));
    }
    if let Some(v) = filled(&p.bls) { write_key_file("bls_key.txt", &v)?; }
    if let Some(v) = filled(&p.bea) { write_key_file("bea_key.txt", &v)?; }

    let refs: Vec<(&str, &str)> = pairs.iter().map(|(k, v)| (*k, v.as_str())).collect();
    if !refs.is_empty() {
        state.settings.write_many(&refs).map_err(|_| "не удалось сохранить настройки".to_string())?;
    }
    Ok(profile_get())
}
