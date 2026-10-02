// Прокси к terminal_reader.py (:8126): фильтр новостей, монитор, живая лента.
// Держим сеть в Rust, чтобы не упираться в CSP/CORS вебвью.
const READER: &str = "http://127.0.0.1:8126";

fn get(path: &str, query: &[(&str, &str)]) -> Result<String, String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(8))
        .build()
        .map_err(|e| e.to_string())?;
    let url = reqwest::Url::parse_with_params(&format!("{}{}", READER, path), query)
        .map_err(|e| e.to_string())?;
    let resp = client
        .get(url)
        .send()
        .map_err(|e| format!("reader offline: {e}"))?;
    resp.text().map_err(|e| e.to_string())
}

#[tauri::command]
pub fn reader_filter_get() -> Result<String, String> {
    get("/filter", &[])
}

#[tauri::command]
pub fn reader_filter_add(kind: String, value: String) -> Result<String, String> {
    get("/filter_add", &[("kind", &kind), ("q", &value)])
}

#[tauri::command]
pub fn reader_filter_remove(kind: String, value: String) -> Result<String, String> {
    get("/filter_remove", &[("kind", &kind), ("q", &value)])
}

#[tauri::command]
pub fn reader_filter_clear(kind: String) -> Result<String, String> {
    get("/filter_clear", &[("kind", &kind)])
}

#[tauri::command]
pub fn reader_mon(on: i32) -> Result<String, String> {
    get("/mon", &[("on", &on.to_string())])
}

#[tauri::command]
pub fn reader_news(n: i32) -> Result<String, String> {
    get("/news", &[("n", &n.to_string())])
}

#[tauri::command]
pub fn reader_smart(on: i32) -> Result<String, String> {
    get("/smart", &[("on", &on.to_string())])
}

// ---- Сценарии авто-клика (:8126) ----
#[tauri::command]
pub fn scn_list() -> Result<String, String> { get("/scn_list", &[]) }

#[tauri::command]
pub fn scn_parse(text: String) -> Result<String, String> { get("/scn_parse", &[("text", &text)]) }

#[tauri::command]
pub fn scn_save(json_data: String) -> Result<String, String> { get("/scn_save", &[("json_data", &json_data)]) }

#[tauri::command]
pub fn scn_del(id: String) -> Result<String, String> { get("/scn_del", &[("id", &id)]) }

#[tauri::command]
pub fn scn_toggle(id: String) -> Result<String, String> { get("/scn_toggle", &[("id", &id)]) }

#[tauri::command]
pub fn scn_capture() -> Result<String, String> { get("/scn_capture", &[]) }

#[tauri::command]
pub fn scn_schedule(id: String, dt: String, prewarm: String) -> Result<String, String> {
    get("/scn_schedule", &[("id", &id), ("dt", &dt), ("prewarm", &prewarm)])
}

#[tauri::command]
pub fn scn_show(x: String, y: String) -> Result<String, String> {
    get("/scn_show", &[("x", &x), ("y", &y)])
}

#[tauri::command]
pub fn scn_show_all(id: String) -> Result<String, String> {
    get("/scn_show_all", &[("id", &id)])
}

#[tauri::command]
pub fn scn_brain(brain: String, claude_key: String, openai_key: String) -> Result<String, String> {
    get("/scn_brain", &[("brain", &brain), ("claude_key", &claude_key), ("openai_key", &openai_key)])
}

// ---- Russian Magellan PRO (order flow, :8127) ----
const MAGELLAN: &str = "http://127.0.0.1:8127";

fn mget(path: &str, query: &[(&str, &str)]) -> Result<String, String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(12))
        .build()
        .map_err(|e| e.to_string())?;
    let url = reqwest::Url::parse_with_params(&format!("{}{}", MAGELLAN, path), query)
        .map_err(|e| e.to_string())?;
    client.get(url).send().map_err(|e| format!("magellan offline: {e}"))?
        .text().map_err(|e| e.to_string())
}

#[tauri::command]
pub fn magellan_filter_get() -> Result<String, String> { mget("/filter", &[]) }

#[tauri::command]
pub fn magellan_add(value: String) -> Result<String, String> {
    mget("/filter_add", &[("kind", "ticker"), ("q", &value)])
}

#[tauri::command]
pub fn magellan_remove(value: String) -> Result<String, String> {
    mget("/filter_remove", &[("kind", "ticker"), ("q", &value)])
}

#[tauri::command]
pub fn magellan_clear() -> Result<String, String> { mget("/filter_clear", &[]) }

#[tauri::command]
pub fn magellan_mon(on: i32) -> Result<String, String> { mget("/mon", &[("on", &on.to_string())]) }

#[tauri::command]
pub fn magellan_settings(min_imbalance: f64, min_price: f64, min_turnover: f64) -> Result<String, String> {
    mget("/settings", &[
        ("min_imbalance", &min_imbalance.to_string()),
        ("min_price", &min_price.to_string()),
        ("min_turnover", &min_turnover.to_string()),
    ])
}

#[tauri::command]
pub fn magellan_pulse() -> Result<String, String> { mget("/pulse", &[("n", "5")]) }

// ---- TTS speed (:8123) ----
const TTS: &str = "http://127.0.0.1:8123";

fn tget(path: &str, query: &[(&str, &str)]) -> Result<String, String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(8))
        .build().map_err(|e| e.to_string())?;
    let url = reqwest::Url::parse_with_params(&format!("{}{}", TTS, path), query)
        .map_err(|e| e.to_string())?;
    client.get(url).send().map_err(|e| format!("tts offline: {e}"))?
        .text().map_err(|e| e.to_string())
}

#[tauri::command]
pub fn tts_speed_get() -> Result<String, String> { tget("/speed", &[]) }

#[tauri::command]
pub fn tts_speed_set(v: f64) -> Result<String, String> { tget("/set_speed", &[("v", &v.to_string())]) }
