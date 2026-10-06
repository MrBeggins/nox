//! Multi-brain — conversational backends with runtime-switchable routing.
//!
//! Backends:
//!   * **claude** — shells out to the local `claude` CLI (`claude -p`), billed to
//!     the Claude **subscription** (after a one-time `claude login`). No API key.
//!   * **openai** — OpenAI-compatible Chat Completions over HTTP. Works with
//!     ChatGPT (needs an API key) *and* with local OpenAI-compatible servers
//!     (Ollama, LM Studio) by pointing the base URL at them (no key needed).
//!
//! The active backend can be switched at runtime (e.g. by voice) via
//! [`set_backend`], or fixed per launch with `JARVIS_BRAIN_BACKEND`.
//!
//! Env vars (all optional, no rebuild needed):
//!   JARVIS_BRAIN          "0"/"false"/"off"/"no" disables the brain (default: on)
//!   JARVIS_BRAIN_BACKEND  claude | openai | auto            (default: claude)
//!   JARVIS_BRAIN_SYSTEM   system prompt (shared by all backends)
//!   JARVIS_BRAIN_TIMEOUT  seconds before giving up          (default: 60)
//!   -- claude --
//!   JARVIS_CLAUDE_BIN     full path to the claude launcher   (default: auto-detect)
//!   JARVIS_BRAIN_MODEL    model id passed to claude `--model`(default: CLI default)
//!   -- openai / local --
//!   JARVIS_OPENAI_KEY     API key (empty for local servers)
//!   JARVIS_OPENAI_URL     base URL (default: https://api.openai.com/v1)
//!   JARVIS_OPENAI_MODEL   model id (default: gpt-4o-mini)

use std::io::Read;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use once_cell::sync::Lazy;
use parking_lot::Mutex;

const DEFAULT_SYSTEM: &str = "Ты — Джарвис, голосовой ассистент на ПК. Отвечай кратко (1–3 предложения), по-русски, разговорным тоном. Без markdown, списков и заголовков — только чистый текст, который удобно произнести вслух.\n\nТы работаешь с терминалом Т-Инвестиций и умеешь голосом: читать портфель, котировки любых инструментов (акции, облигации, фонды, фьючерсы, валюты, индексы), стакан, статус торгов и аукционы, а также готовить лимитные заявки с тейк-профитом. Важно: сам ты сделки НЕ совершаешь и деньги не двигаешь — заявки выставляет пользователь.\n\nТы знаешь популярные расширения для терминала Т-Инвестиций: Trading Tools — скальперские кнопки, быстрый ввод объёма и цены, кнопки обновления/удаления заявок в один клик, виджет статистики позиций; в премиуме — лента сделок, сканеры цен и объёмов, лента корпоративных новостей, стаканы рынка США. Stocksi Ultimate — расширенные виджеты и аналитика. Mind Stocks — торговые виджеты и новости MOEX. Если спрашивают про их функции — отвечай по существу.";

/// Which conversational backend handles requests.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Backend {
    Claude,
    OpenAI,
    /// Token-saving: prefer the cheaper OpenAI/local backend, fall back to Claude.
    Auto,
}

impl Backend {
    fn from_str(s: &str) -> Option<Backend> {
        match s.trim().to_lowercase().as_str() {
            "claude" | "клод" | "клауд" => Some(Backend::Claude),
            "openai" | "gpt" | "chatgpt" | "гпт" | "чатгпт" | "джипити" => Some(Backend::OpenAI),
            "auto" | "авто" => Some(Backend::Auto),
            _ => None,
        }
    }
    pub fn name(self) -> &'static str {
        match self {
            Backend::Claude => "claude",
            Backend::OpenAI => "openai",
            Backend::Auto => "auto",
        }
    }
}

/// Read a persisted setting (GUI-controlled) from the shared settings DB.
fn db_get(key: &str) -> Option<String> {
    crate::DB.get().and_then(|db| db.read().get(key))
}

static BACKEND: Lazy<Mutex<Backend>> = Lazy::new(|| {
    // env wins; else the persisted GUI setting; else Claude.
    let init = std::env::var("JARVIS_BRAIN_BACKEND")
        .ok()
        .and_then(|v| Backend::from_str(&v))
        .or_else(|| db_get("brain_backend").and_then(|v| Backend::from_str(&v)))
        .unwrap_or(Backend::Claude);
    Mutex::new(init)
});

/// Switch the active backend at runtime. Accepts names/aliases (e.g. "gpt",
/// "claude", "auto"). Returns the resolved backend name on success.
pub fn set_backend(name: &str) -> Option<&'static str> {
    let b = Backend::from_str(name)?;
    *BACKEND.lock() = b;
    Some(b.name())
}

/// Name of the currently active backend.
pub fn current_backend() -> &'static str {
    BACKEND.lock().name()
}

fn enabled() -> bool {
    if let Ok(v) = std::env::var("JARVIS_BRAIN") {
        let v = v.trim().to_lowercase();
        return !(v == "0" || v == "false" || v == "off" || v == "no");
    }
    if let Some(v) = db_get("brain_enabled") {
        return v == "true";
    }
    true
}

fn system_prompt() -> String {
    std::env::var("JARVIS_BRAIN_SYSTEM").unwrap_or_else(|_| DEFAULT_SYSTEM.to_string())
}

fn timeout_secs() -> u64 {
    std::env::var("JARVIS_BRAIN_TIMEOUT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(60)
}

/// Ask the active brain a question. Returns the plain-text reply on success.
pub fn ask(prompt: &str) -> Result<String, String> {
    if !enabled() {
        return Err("brain disabled (JARVIS_BRAIN=0)".to_string());
    }
    let prompt = prompt.trim();
    if prompt.is_empty() {
        return Err("empty prompt".to_string());
    }

    match *BACKEND.lock() {
        Backend::Claude => ask_claude(prompt),
        Backend::OpenAI => ask_openai(prompt),
        Backend::Auto => {
            // Token-saving: try the cheaper OpenAI/local backend first (if
            // configured), escalate to Claude on any failure.
            if openai_configured() {
                match ask_openai(prompt) {
                    Ok(a) => Ok(a),
                    Err(e) => {
                        log::warn!("openai backend failed ({}); falling back to claude", e);
                        ask_claude(prompt)
                    }
                }
            } else {
                ask_claude(prompt)
            }
        }
    }
}

// ----------------------------- Claude backend -----------------------------

/// Resolve the `claude` launcher. On Windows npm installs a `claude.cmd` shim.
fn claude_bin() -> String {
    if let Ok(p) = std::env::var("JARVIS_CLAUDE_BIN") {
        if !p.trim().is_empty() {
            return p;
        }
    }
    #[cfg(target_os = "windows")]
    {
        if let Ok(appdata) = std::env::var("APPDATA") {
            let shim = format!("{}\\npm\\claude.cmd", appdata);
            if std::path::Path::new(&shim).exists() {
                return shim;
            }
        }
    }
    "claude".to_string()
}

fn ask_claude(prompt: &str) -> Result<String, String> {
    let system = system_prompt();
    let timeout = timeout_secs();

    let bin = claude_bin();
    let mut cmd = Command::new(&bin);
    cmd.arg("-p")
        .arg(prompt)
        .arg("--append-system-prompt")
        .arg(&system);
    // Модель Claude: env -> DB -> по умолчанию Sonnet (Haiku намеренно не используем).
    let claude_model = std::env::var("JARVIS_BRAIN_MODEL")
        .ok()
        .filter(|m| !m.trim().is_empty())
        .or_else(|| db_get("brain_model").filter(|m| !m.trim().is_empty()))
        .unwrap_or_else(|| "sonnet".to_string());
    cmd.arg("--model").arg(claude_model.trim());
    cmd.stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("failed to launch '{}': {}", bin, e))?;

    let mut out_pipe = child.stdout.take().unwrap();
    let mut err_pipe = child.stderr.take().unwrap();
    let out_handle = std::thread::spawn(move || {
        let mut s = String::new();
        let _ = out_pipe.read_to_string(&mut s);
        s
    });
    let err_handle = std::thread::spawn(move || {
        let mut s = String::new();
        let _ = err_pipe.read_to_string(&mut s);
        s
    });

    let deadline = Instant::now() + Duration::from_secs(timeout);
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(format!("claude timed out after {}s", timeout));
                }
                std::thread::sleep(Duration::from_millis(100));
            }
            Err(e) => return Err(format!("wait failed: {}", e)),
        }
    };

    let stdout = out_handle.join().unwrap_or_default().trim().to_string();
    let stderr = err_handle.join().unwrap_or_default().trim().to_string();

    if status.success() && !stdout.is_empty() {
        Ok(stdout)
    } else if !stderr.is_empty() {
        Err(stderr)
    } else if !stdout.is_empty() {
        Err(stdout)
    } else {
        Err("claude returned an empty response".to_string())
    }
}

// -------------------------- OpenAI / local backend --------------------------

fn openai_url() -> String {
    std::env::var("JARVIS_OPENAI_URL")
        .ok()
        .filter(|s| !s.trim().is_empty())
        .or_else(|| db_get("openai_url").filter(|s| !s.trim().is_empty()))
        .unwrap_or_else(|| "https://api.openai.com/v1".to_string())
        .trim_end_matches('/')
        .to_string()
}

fn openai_key() -> String {
    std::env::var("JARVIS_OPENAI_KEY")
        .ok()
        .filter(|s| !s.trim().is_empty())
        .or_else(|| db_get("api_key__openai").filter(|s| !s.trim().is_empty()))
        .unwrap_or_default()
}

/// Considered usable if an API key is set (ChatGPT) or a custom base URL is set
/// (a local OpenAI-compatible server needs no key).
fn openai_configured() -> bool {
    !openai_key().trim().is_empty()
        || std::env::var("JARVIS_OPENAI_URL").is_ok()
        || db_get("openai_url").map(|s| !s.trim().is_empty()).unwrap_or(false)
}

#[cfg(not(feature = "reqwest"))]
fn ask_openai(_prompt: &str) -> Result<String, String> {
    Err("openai backend not built (reqwest feature disabled)".to_string())
}

#[cfg(feature = "reqwest")]
fn ask_openai(prompt: &str) -> Result<String, String> {
    let url = format!("{}/chat/completions", openai_url());
    let key = openai_key();
    let model = std::env::var("JARVIS_OPENAI_MODEL")
        .ok()
        .filter(|s| !s.trim().is_empty())
        .or_else(|| db_get("openai_model").filter(|s| !s.trim().is_empty()))
        .unwrap_or_else(|| "gpt-4o-mini".to_string());

    let body = serde_json::json!({
        "model": model,
        "messages": [
            { "role": "system", "content": system_prompt() },
            { "role": "user",   "content": prompt },
        ],
        "temperature": 0.7,
        "max_tokens": 180,      // короткие ответы -> быстрее ответ и короче озвучка
        "keep_alive": "30m",    // держать модель в VRAM (Ollama) -> без холодных стартов
    });

    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(timeout_secs()))
        .build()
        .map_err(|e| format!("http client error: {}", e))?;

    let mut req = client.post(&url).json(&body);
    if !key.trim().is_empty() {
        req = req.bearer_auth(key.trim());
    }

    let resp = req.send().map_err(|e| format!("openai request failed: {}", e))?;
    let status = resp.status();
    let val: serde_json::Value = resp
        .json()
        .map_err(|e| format!("openai: bad JSON response: {}", e))?;

    if !status.is_success() {
        let msg = val["error"]["message"].as_str().unwrap_or("unknown error");
        return Err(format!("openai HTTP {}: {}", status.as_u16(), msg));
    }

    let content = val["choices"][0]["message"]["content"]
        .as_str()
        .unwrap_or("")
        .trim()
        .to_string();

    if content.is_empty() {
        Err("openai returned an empty response".to_string())
    } else {
        Ok(content)
    }
}
