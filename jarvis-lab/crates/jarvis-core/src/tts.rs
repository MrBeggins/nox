//! Text-to-speech — gives Jarvis a voice.
//!
//! Speaks text aloud using the modern Windows (WinRT) speech engine, which
//! exposes high-quality voices such as "Microsoft Pavel" (ru-RU male). The
//! actual synthesis is done by an embedded PowerShell script (speak.ps1) that
//! renders the text to a WAV and plays it. Text is passed through a temp UTF-8
//! file so Cyrillic survives intact.
//!
//! Tunable via env vars:
//!   JARVIS_TTS       "0"/"false"/"off"/"no" disables speaking (default: on)
//!   JARVIS_TTS_VOICE WinRT voice name substring (default: "Pavel", ru-RU male)
//!   JARVIS_TTS_RATE  WinRT speaking rate 0.5..6.0 (default: natural, ~1.0)

use std::process::{Command, Stdio};

const SPEAK_PS1: &str = include_str!("speak.ps1");
const SPEAK_F5_PS1: &str = include_str!("speak_f5.ps1");

/// Whether the local F5 voice engine is enabled (default: on). Set
/// JARVIS_F5="0"/"false"/"off"/"no" to force the built-in WinRT (Pavel) voice.
fn f5_enabled() -> bool {
    match std::env::var("JARVIS_F5") {
        Ok(v) => {
            let v = v.trim().to_lowercase();
            !(v == "0" || v == "false" || v == "off" || v == "no")
        }
        Err(_) => true,
    }
}

/// Try to speak via the local F5 TTS server (cloned Jarvis voice). Returns true
/// on success; false if the server is unavailable or errored (caller falls back).
fn speak_f5(txt_path: &std::path::Path, tmp: &std::path::Path, nfe: Option<u32>, speed: Option<f32>) -> bool {
    let ps_path = tmp.join("jarvis_speak_f5.ps1");
    if std::fs::write(&ps_path, SPEAK_F5_PS1.as_bytes()).is_err() {
        return false;
    }
    let mut cmd = Command::new("powershell");
    cmd.args([
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        &ps_path.to_string_lossy(),
    ])
    .env("JARVIS_TTS_FILE", txt_path)
    .stdin(Stdio::null())
    .stdout(Stdio::null())
    .stderr(Stdio::null());
    if let Some(n) = nfe {
        cmd.env("JARVIS_TTS_NFE", n.to_string());
    }
    if let Some(s) = speed {
        cmd.env("JARVIS_TTS_SPEED", format!("{}", s));
    }
    matches!(cmd.status(), Ok(s) if s.success())
}

fn enabled() -> bool {
    // env var wins; otherwise read the persisted setting (GUI toggle); default on.
    if let Ok(v) = std::env::var("JARVIS_TTS") {
        let v = v.trim().to_lowercase();
        return !(v == "0" || v == "false" || v == "off" || v == "no");
    }
    if let Some(db) = crate::DB.get() {
        if let Some(v) = db.read().get("tts_enabled") {
            return v == "true";
        }
    }
    true
}

/// Whether TTS is currently enabled (public wrapper for callers).
pub fn is_enabled() -> bool {
    enabled()
}

/// Speak the given text aloud. Blocks until speech finishes. Never panics;
/// on any failure it just logs and returns (TTS is best-effort).
pub fn speak(text: &str) {
    speak_nfe(text, None, None);
}

/// Speak short/time-critical answers faster: lower nfe_step (быстрее синтез) и
/// повышенный темп речи (движок «тараторит») — только для зачитки терминала/
/// календаря. Обычный чат (speak) остаётся в нормальном темпе.
pub fn speak_fast(text: &str) {
    let n = std::env::var("JARVIS_TTS_NFE_FAST")
        .ok()
        .and_then(|v| v.trim().parse::<u32>().ok())
        .filter(|n| *n > 0)
        .unwrap_or(10);
    let speed = std::env::var("JARVIS_TTS_SPEED_FAST")
        .ok()
        .and_then(|v| v.trim().parse::<f32>().ok())
        .filter(|s| *s > 0.0)
        .unwrap_or(2.0);
    // Для зачитки цифр убираем разряд из ОЗВУЧКИ (на экране «-0.64 млн» остаётся) —
    // короче фраза = быстрее. Затрагивает только сокращения из календаря.
    let spoken = text
        .replace(" млрд", "")
        .replace(" млн", "")
        .replace(" трлн", "")
        .replace(" тыс", "");
    speak_nfe(&spoken, Some(n), Some(speed));
}

/// Speak a multi-item reply (новости/события) naturally: качество nfe 14 и
/// умеренный темп, КАЖДЫЙ пункт озвучивается ОТДЕЛЬНО (ровный темп + паузы,
/// без «разгона к концу» на длинной фразе). Пункты разделены '\n'.
pub fn speak_reply(text: &str) {
    if !enabled() {
        return;
    }
    // Базовая скорость для КОРОТКИХ ответов (настраивается).
    let fast = std::env::var("JARVIS_TTS_SPEED_REPLY")
        .ok()
        .and_then(|v| v.trim().parse::<f32>().ok())
        .filter(|s| *s > 0.0)
        .unwrap_or(1.5);
    // Единое чтение (F5 чётче на цельном тексте, чем на коротких обрывках).
    // Переносы строк между пунктами -> границы предложений.
    let joined = text.replace('\n', ". ");
    // Динамическая скорость: чем ДЛИННЕЕ текст, тем МЕДЛЕННЕЕ (иначе F5 «сжёвывает» слова).
    let n = joined.chars().count();
    let speed = if n > 500 {
        1.2
    } else if n > 300 {
        1.3
    } else if n > 180 {
        fast.min(1.4)
    } else {
        fast
    };
    speak_nfe(&joined, Some(14), Some(speed));
}

/// Speak with an optional explicit nfe_step (None = server default ~14) и
/// опциональной скоростью речи (None = серверная по умолчанию 1.2).
pub fn speak_nfe(text: &str, nfe: Option<u32>, speed: Option<f32>) {
    if !enabled() {
        return;
    }
    let text = text.trim();
    if text.is_empty() {
        return;
    }

    let tmp = std::env::temp_dir();

    // Text to speak (UTF-8 temp file).
    let txt_path = tmp.join(format!("jarvis_tts_{}.txt", std::process::id()));
    if let Err(e) = std::fs::write(&txt_path, text.as_bytes()) {
        error!("TTS: failed to write text file: {}", e);
        return;
    }

    // Preferred engine: local F5 server (cloned Jarvis voice). Falls back to
    // the built-in WinRT (Pavel) voice below if the server is unavailable.
    if f5_enabled() {
        if speak_f5(&txt_path, &tmp, nfe, speed) {
            let _ = std::fs::remove_file(&txt_path);
            return;
        }
        warn!("TTS: F5 voice failed after retries, falling back to Pavel");
    }

    // Embedded speaking script (written next to the text file).
    let ps_path = tmp.join("jarvis_speak.ps1");
    if let Err(e) = std::fs::write(&ps_path, SPEAK_PS1.as_bytes()) {
        error!("TTS: failed to write script file: {}", e);
        let _ = std::fs::remove_file(&txt_path);
        return;
    }

    let voice = std::env::var("JARVIS_TTS_VOICE").unwrap_or_else(|_| "Pavel".to_string());
    let rate = std::env::var("JARVIS_TTS_RATE").unwrap_or_default();

    let status = Command::new("powershell")
        .args([
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            &ps_path.to_string_lossy(),
        ])
        .env("JARVIS_TTS_FILE", &txt_path)
        .env("JARVIS_TTS_VOICE", voice)
        .env("JARVIS_TTS_RATE", rate)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();

    match status {
        Ok(s) if s.success() => {}
        Ok(s) => warn!("TTS: powershell exited with status {}", s),
        Err(e) => error!("TTS: failed to launch powershell: {}", e),
    }

    let _ = std::fs::remove_file(&txt_path);
}
