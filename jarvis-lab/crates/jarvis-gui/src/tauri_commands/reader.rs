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

#[tauri::command(async)]
pub fn reader_filter_get() -> Result<String, String> {
    get("/filter", &[])
}

#[tauri::command(async)]
pub fn reader_filter_add(kind: String, value: String) -> Result<String, String> {
    get("/filter_add", &[("kind", &kind), ("q", &value)])
}

#[tauri::command(async)]
pub fn reader_filter_remove(kind: String, value: String) -> Result<String, String> {
    get("/filter_remove", &[("kind", &kind), ("q", &value)])
}

#[tauri::command(async)]
pub fn reader_filter_clear(kind: String) -> Result<String, String> {
    get("/filter_clear", &[("kind", &kind)])
}

#[tauri::command(async)]
pub fn reader_mon(on: i32) -> Result<String, String> {
    get("/mon", &[("on", &on.to_string())])
}

#[tauri::command(async)]
pub fn reader_news(n: i32) -> Result<String, String> {
    get("/news", &[("n", &n.to_string())])
}

#[tauri::command(async)]
pub fn reader_smart(on: i32) -> Result<String, String> {
    get("/smart", &[("on", &on.to_string())])
}

// ---- Сценарии авто-клика (:8126) ----
#[tauri::command(async)]
pub fn scn_list() -> Result<String, String> { get("/scn_list", &[]) }

#[tauri::command(async)]
pub fn scn_parse(text: String) -> Result<String, String> { get("/scn_parse", &[("text", &text)]) }

#[tauri::command(async)]
pub fn scn_save(json_data: String) -> Result<String, String> { get("/scn_save", &[("json_data", &json_data)]) }

#[tauri::command(async)]
pub fn scn_del(id: String) -> Result<String, String> { get("/scn_del", &[("id", &id)]) }

#[tauri::command(async)]
pub fn scn_toggle(id: String) -> Result<String, String> { get("/scn_toggle", &[("id", &id)]) }

#[tauri::command(async)]
pub fn scn_capture() -> Result<String, String> { get("/scn_capture", &[]) }

#[tauri::command(async)]
pub fn scn_schedule(id: String, dt: String, prewarm: String) -> Result<String, String> {
    get("/scn_schedule", &[("id", &id), ("dt", &dt), ("prewarm", &prewarm)])
}

#[tauri::command(async)]
pub fn scn_show(x: String, y: String) -> Result<String, String> {
    get("/scn_show", &[("x", &x), ("y", &y)])
}

#[tauri::command(async)]
pub fn scn_show_all(id: String) -> Result<String, String> {
    get("/scn_show_all", &[("id", &id)])
}

#[tauri::command(async)]
pub fn scn_test(id: String, value: String) -> Result<String, String> {
    get("/scn_test", &[("id", &id), ("value", &value)])
}

/// Показать терминал в окне Нокса (без новых вкладок; если терминала нет — ридер откроет один).
#[tauri::command(async)]
pub fn terminal_show() -> Result<String, String> { get_long("/terminal_show", &[], 30) }

/// Страница «Мозг»: модели Ollama на этом ПК и каталог облака (по ключу).
#[tauri::command(async)]
pub fn brain_models() -> Result<String, String> { get_long("/brain_models", &[], 20) }

#[tauri::command(async)]
pub fn scn_brain(brain: String, claude_key: String, openai_key: String) -> Result<String, String> {
    get("/scn_brain", &[("brain", &brain), ("claude_key", &claude_key), ("openai_key", &openai_key)])
}

// ---- Наблюдатели «мультимозг» (:8126) ----
#[tauri::command(async)]
pub fn watch_list() -> Result<String, String> { get("/watch_list", &[]) }

#[tauri::command(async)]
pub fn watch_add(name: String, keywords: String, source: String, brain: String,
                 scenario_id: String, until: String, once: i32) -> Result<String, String> {
    get("/watch_add", &[("name", &name), ("keywords", &keywords), ("source", &source),
                        ("brain", &brain), ("scenario_id", &scenario_id), ("until", &until),
                        ("once", &once.to_string())])
}

#[tauri::command(async)]
pub fn watch_voice(text: String) -> Result<String, String> { get("/watch_voice", &[("text", &text)]) }

/// Разобрать описание наблюдения для формы (без сохранения) — мозг думает несколько секунд.
#[tauri::command(async)]
pub fn watch_parse(text: String) -> Result<String, String> { get_long("/watch_parse", &[("text", &text)], 40) }

#[tauri::command(async)]
pub fn watch_del(id: String) -> Result<String, String> { get("/watch_del", &[("id", &id)]) }

#[tauri::command(async)]
pub fn watch_toggle(id: String) -> Result<String, String> { get("/watch_toggle", &[("id", &id)]) }

// Наблюдатели с ветками: операции в терминале идут дольше 8с (раскладка виджетов — до минут)
fn get_long(path: &str, query: &[(&str, &str)], secs: u64) -> Result<String, String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(secs))
        .build()
        .map_err(|e| e.to_string())?;
    let url = reqwest::Url::parse_with_params(&format!("{}{}", READER, path), query)
        .map_err(|e| e.to_string())?;
    client.get(url).send().map_err(|e| format!("reader offline: {e}"))?
        .text().map_err(|e| e.to_string())
}

#[tauri::command(async)]
pub fn watch_save(json_data: String) -> Result<String, String> { get("/watch_save", &[("json_data", &json_data)]) }

#[tauri::command(async)]
pub fn watch_scan(tab: String) -> Result<String, String> { get_long("/watch_scan", &[("tab", &tab)], 30) }

#[tauri::command(async)]
pub fn watch_layout(id: String) -> Result<String, String> { get_long("/watch_layout", &[("id", &id)], 200) }

#[tauri::command(async)]
pub fn watch_show(id: String) -> Result<String, String> { get_long("/watch_show", &[("id", &id)], 30) }

#[tauri::command(async)]
pub fn watch_dry(id: String, value: String) -> Result<String, String> {
    get_long("/watch_dry", &[("id", &id), ("value", &value)], 40)
}

// ---- Стрелки «Купить» / «Продать» на оверлее: ручная корзина заявок (:8126) ----
#[tauri::command(async)]
pub fn arrows_get() -> Result<String, String> { get("/arrows_get", &[]) }

#[tauri::command(async)]
pub fn arrows_save(json_data: String) -> Result<String, String> { get("/arrows_save", &[("json_data", &json_data)]) }

#[tauri::command(async)]
pub fn arrow_fire(side: String) -> Result<String, String> { get_long("/arrow_fire", &[("side", &side)], 40) }

#[tauri::command(async)]
pub fn arrow_dry(side: String) -> Result<String, String> { get_long("/arrow_dry", &[("side", &side)], 40) }

#[tauri::command(async)]
pub fn arrow_layout() -> Result<String, String> { get_long("/arrow_layout", &[], 200) }

// ---- Повтор сделок ProfitKing (nox_copytrade): свой объём, отдельно от лимитов наблюдателей ----
#[tauri::command(async)]
pub fn copy_get() -> Result<String, String> { get("/copy_get", &[]) }

#[tauri::command(async)]
pub fn copy_save(lots: i32, short: i32) -> Result<String, String> {
    get("/copy_save", &[("lots", &lots.to_string()), ("short", &short.to_string())])
}

// ---- Память Нокса: правила общения + факты о пользователе (вкладка «Память») ----
#[tauri::command(async)]
pub fn memory_get() -> Result<String, String> { get("/memory_get", &[]) }

#[tauri::command(async)]
pub fn memory_save(json_data: String) -> Result<String, String> { get("/memory_save", &[("json_data", &json_data)]) }

// Стаканы CScalp (проп без API) для редактора веток: скан окон CScalp — до ~5 с
#[tauri::command(async)]
pub fn cscalp_layout() -> Result<String, String> { get_long("/cscalp_layout", &[], 40) }

// ---- Выбор голоса: движок (Silero/XTTS) + диктор (:8126) ----
#[tauri::command(async)]
pub fn voice_list() -> Result<String, String> { get("/voice_list", &[]) }

#[tauri::command(async)]
pub fn voice_get() -> Result<String, String> { get("/voice_get", &[]) }

#[tauri::command(async)]
pub fn voice_set(engine: String, speaker: String) -> Result<String, String> {
    get("/voice_set", &[("engine", &engine), ("speaker", &speaker)])
}

#[tauri::command(async)]
pub fn voice_test(engine: String, speaker: String) -> Result<String, String> {
    get("/voice_test", &[("engine", &engine), ("speaker", &speaker)])
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

#[tauri::command(async)]
pub fn magellan_filter_get() -> Result<String, String> { mget("/filter", &[]) }

#[tauri::command(async)]
pub fn magellan_add(value: String) -> Result<String, String> {
    mget("/filter_add", &[("kind", "ticker"), ("q", &value)])
}

#[tauri::command(async)]
pub fn magellan_remove(value: String) -> Result<String, String> {
    mget("/filter_remove", &[("kind", "ticker"), ("q", &value)])
}

#[tauri::command(async)]
pub fn magellan_clear() -> Result<String, String> { mget("/filter_clear", &[]) }

#[tauri::command(async)]
pub fn magellan_mon(on: i32) -> Result<String, String> { mget("/mon", &[("on", &on.to_string())]) }

#[tauri::command(async)]
pub fn magellan_settings(min_imbalance: f64, min_price: f64, min_turnover: f64) -> Result<String, String> {
    mget("/settings", &[
        ("min_imbalance", &min_imbalance.to_string()),
        ("min_price", &min_price.to_string()),
        ("min_turnover", &min_turnover.to_string()),
    ])
}

#[tauri::command(async)]
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

#[tauri::command(async)]
pub fn tts_speed_get() -> Result<String, String> { tget("/speed", &[]) }

#[tauri::command(async)]
pub fn tts_speed_set(v: f64) -> Result<String, String> { tget("/set_speed", &[("v", &v.to_string())]) }

// громкость речи: 0.1…1.0 (общий файл tts_volume.txt для всех движков)
#[tauri::command(async)]
pub fn tts_volume_get() -> Result<String, String> { tget("/volume", &[]) }

#[tauri::command(async)]
pub fn tts_volume_set(v: f64) -> Result<String, String> { tget("/set_volume", &[("v", &v.to_string())]) }
