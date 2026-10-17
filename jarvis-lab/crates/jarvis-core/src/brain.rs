//! Мозг Нокса — разговорные ответы. Ровно ДВА варианта (подписка Claude убрана):
//!
//!   * **openai** — облако по API-ключу: любой OpenAI-совместимый сервис (APIMIRA и т.п.).
//!     Настройки: `openai_url`, `openai_model`, `api_key__openai` (страница «Мозг»).
//!   * **ollama** — локально и бесплатно (Ollama на этом ПК, OpenAI-совместимый /v1).
//!     Настройка: `ollama_model` (напр. qwen2.5:3b).
//!
//! Облако не ответило — подстраховка локальной Ollama (если запущена), чтобы Нокс не молчал.
//! Переключение: страница «Мозг» (app.db + ReloadSettings) или голосом через [`set_backend`].
//!
//! Env (необязательно): JARVIS_BRAIN=0 — выключить; JARVIS_BRAIN_SYSTEM — системный промпт;
//! JARVIS_BRAIN_TIMEOUT — таймаут, с (по умолчанию 60).

use std::time::Duration;

use once_cell::sync::Lazy;
use parking_lot::Mutex;

const DEFAULT_SYSTEM: &str = "Ты — Нокс, голосовой ассистент на ПК. Отвечай кратко (1–3 предложения), разговорным тоном, грамотным литературным русским языком: правильные падежи, склонения и окончания, числа согласованы со словами (одна акция, две акции, пять акций; до двадцати одного процента). Иностранные названия и компании — по-русски, без латиницы. Без markdown, списков, заголовков, эмодзи и ссылок — только чистый текст, который удобно произнести вслух.\n\nТы работаешь с терминалом Т-Инвестиций и умеешь голосом: читать портфель, котировки любых инструментов (акции, облигации, фонды, фьючерсы, валюты, индексы), стакан, статус торгов и аукционы, готовить заявки, а по явной команде пользователя — и выставлять их (если это разрешено в настройках). Наблюдатели следят за новостями и нажимают заранее подготовленные заявки. Деньги ты не переводишь.\n\nТы знаешь популярные расширения для терминала Т-Инвестиций: Trading Tools — скальперские кнопки, быстрый ввод объёма и цены, кнопки обновления/удаления заявок в один клик, виджет статистики позиций; в премиуме — лента сделок, сканеры цен и объёмов, лента корпоративных новостей, стаканы рынка США. Stocksi Ultimate — расширенные виджеты и аналитика. Mind Stocks — торговые виджеты и новости MOEX. Если спрашивают про их функции — отвечай по существу.";

/// Адрес локальной Ollama (OpenAI-совместимый).
pub const OLLAMA_URL: &str = "http://127.0.0.1:11434/v1";
const DEFAULT_OLLAMA_MODEL: &str = "qwen2.5:3b";
const DEFAULT_CLOUD_URL: &str = "https://apimira.com/v1";

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Backend {
    /// облако по API-ключу
    Cloud,
    /// локальная Ollama
    Local,
}

impl Backend {
    fn from_str(s: &str) -> Option<Backend> {
        match s.trim().to_lowercase().as_str() {
            "openai" | "cloud" | "api" | "gpt" | "chatgpt" | "облако" | "гпт" => Some(Backend::Cloud),
            "ollama" | "local" | "локальный" | "олама" | "оллама" => Some(Backend::Local),
            // старые значения из прежних версий (подписка Claude / авто) -> облако по ключу
            "claude" | "auto" => Some(Backend::Cloud),
            _ => None,
        }
    }
    pub fn name(self) -> &'static str {
        match self {
            Backend::Cloud => "openai",
            Backend::Local => "ollama",
        }
    }
}

fn db_get(key: &str) -> Option<String> {
    crate::DB.get().and_then(|db| db.read().get(key))
}

fn non_empty(v: Option<String>) -> Option<String> {
    v.map(|s| s.trim().to_string()).filter(|s| !s.is_empty())
}

/// Из настроек: старый «локальный» выбор хранился как openai + адрес Ollama — тоже считаем локальным.
fn backend_from_db() -> Backend {
    let b = db_get("brain_backend").and_then(|v| Backend::from_str(&v)).unwrap_or(Backend::Cloud);
    let url = db_get("openai_url").unwrap_or_default();
    if b == Backend::Cloud && url.contains("11434") {
        Backend::Local
    } else {
        b
    }
}

static BACKEND: Lazy<Mutex<Backend>> = Lazy::new(|| Mutex::new(backend_from_db()));

/// Переключить мозг на лету («облако» / «ollama» и синонимы). Возвращает имя или None.
pub fn set_backend(name: &str) -> Option<&'static str> {
    let b = Backend::from_str(name)?;
    *BACKEND.lock() = b;
    Some(b.name())
}

/// Перечитать выбор из настроек (после ReloadSettings).
pub fn reload_backend() -> &'static str {
    let b = backend_from_db();
    *BACKEND.lock() = b;
    b.name()
}

pub fn current_backend() -> &'static str {
    BACKEND.lock().name()
}

fn enabled() -> bool {
    if let Ok(v) = std::env::var("JARVIS_BRAIN") {
        let v = v.trim().to_lowercase();
        return !(v == "0" || v == "false" || v == "off" || v == "no");
    }
    db_get("brain_enabled").map(|v| v == "true").unwrap_or(true)
}

/// Память Нокса (правила общения + факты о пользователе, вкладка «Память»): готовый текст пишет nox_memory.py.
const MEMORY_PROMPT_FILE: &str = r"C:\jarvis-voice\nox_memory_prompt.txt";

fn system_prompt() -> String {
    let base = std::env::var("JARVIS_BRAIN_SYSTEM").unwrap_or_else(|_| DEFAULT_SYSTEM.to_string());
    let memory = std::fs::read_to_string(MEMORY_PROMPT_FILE).unwrap_or_default();
    base + memory.trim_end()
}

fn timeout_secs() -> u64 {
    std::env::var("JARVIS_BRAIN_TIMEOUT").ok().and_then(|v| v.parse().ok()).unwrap_or(60)
}

fn ollama_model() -> String {
    non_empty(db_get("ollama_model")).unwrap_or_else(|| DEFAULT_OLLAMA_MODEL.to_string())
}

/// Облако: (адрес, ключ, модель). Если в адресе по старой памяти стоит Ollama — берём облако по умолчанию.
fn cloud() -> (String, String, String) {
    let url = non_empty(db_get("openai_url"))
        .filter(|u| !u.contains("11434"))
        .unwrap_or_else(|| DEFAULT_CLOUD_URL.to_string());
    let key = non_empty(db_get("api_key__openai")).unwrap_or_default();
    let model = non_empty(db_get("openai_model")).unwrap_or_else(|| "openai/gpt-5.4-mini".to_string());
    (url.trim_end_matches('/').to_string(), key, model)
}

/// Спросить выбранный мозг. Облако не ответило — подстраховка локальной Ollama.
pub fn ask(prompt: &str) -> Result<String, String> {
    if !enabled() {
        return Err("brain disabled".to_string());
    }
    let prompt = prompt.trim();
    if prompt.is_empty() {
        return Err("empty prompt".to_string());
    }
    let backend = *BACKEND.lock();
    match backend {
        Backend::Local => chat(OLLAMA_URL, "", &ollama_model(), prompt, timeout_secs()),
        Backend::Cloud => {
            let (url, key, model) = cloud();
            // облако иногда виснет: ждём максимум CLOUD_TIMEOUT с, потом отвечает локальная Ollama
            match chat(&url, &key, &model, prompt, CLOUD_TIMEOUT) {
                Ok(a) => Ok(a),
                Err(e) => {
                    log::warn!("cloud brain failed ({}); trying local Ollama", e);
                    chat(OLLAMA_URL, "", &ollama_model(), prompt, timeout_secs()).map_err(|e2| format!("{}; ollama: {}", e, e2))
                }
            }
        }
    }
}

/// Сколько ждать облако, прежде чем спросить локальную Ollama.
const CLOUD_TIMEOUT: u64 = 12;

#[cfg(not(feature = "reqwest"))]
fn chat(_url: &str, _key: &str, _model: &str, _prompt: &str, _timeout: u64) -> Result<String, String> {
    Err("brain not built (reqwest feature disabled)".to_string())
}

/// OpenAI-совместимый /chat/completions (облако или Ollama).
#[cfg(feature = "reqwest")]
fn chat(base: &str, key: &str, model: &str, prompt: &str, timeout: u64) -> Result<String, String> {
    let url = format!("{}/chat/completions", base.trim_end_matches('/'));
    let body = serde_json::json!({
        "model": model,
        "messages": [
            { "role": "system", "content": system_prompt() },
            { "role": "user",   "content": prompt },
        ],
        "temperature": 0.7,
        "max_tokens": 180,      // короткие ответы -> быстрее ответ и короче озвучка
        "keep_alive": "30m",    // Ollama: держать модель в VRAM -> без холодных стартов
    });

    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(timeout))
        .build()
        .map_err(|e| format!("http client error: {}", e))?;

    let mut req = client.post(&url).json(&body);
    if !key.trim().is_empty() {
        req = req.bearer_auth(key.trim());
    }

    let resp = req.send().map_err(|e| format!("request failed: {}", e))?;
    let status = resp.status();
    let val: serde_json::Value = resp.json().map_err(|e| format!("bad JSON response: {}", e))?;

    if !status.is_success() {
        let msg = val["error"]["message"].as_str().unwrap_or("unknown error");
        return Err(format!("HTTP {}: {}", status.as_u16(), msg));
    }

    let content = val["choices"][0]["message"]["content"].as_str().unwrap_or("").trim().to_string();
    if content.is_empty() {
        Err("empty response".to_string())
    } else {
        Ok(content)
    }
}
