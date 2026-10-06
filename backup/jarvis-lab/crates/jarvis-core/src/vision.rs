//! Screen vision — lets Jarvis "see" the screen.
//!
//! Captures a screenshot (via an embedded PowerShell script) and asks the local
//! `claude` CLI (subscription auth, vision-capable) to describe what's on screen.
//! `claude` reads the PNG with its Read tool, so we pass `--allowedTools Read`.
//!
//! Env vars:
//!   JARVIS_VISION          "1"/"true"/"on" enables the periodic vision loop (default: off)
//!   JARVIS_VISION_INTERVAL seconds between captures (default: 3)
//!   JARVIS_VISION_PROMPT   the instruction sent to Claude (default: short RU description)
//!   JARVIS_CLAUDE_BIN      path to claude launcher (default: auto-detect)

use std::io::Read;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

const CAPTURE_PS1: &str = include_str!("capture.ps1");

const DEFAULT_PROMPT: &str =
    "Опиши одним коротким предложением по-русски, что сейчас происходит на экране (какая программа/игра открыта). Только суть, без вступлений.";

fn truthy(v: &str) -> bool {
    let v = v.trim().to_lowercase();
    v == "1" || v == "true" || v == "on" || v == "yes"
}

pub fn is_enabled() -> bool {
    // env wins; otherwise the persisted GUI toggle; default off.
    if let Ok(v) = std::env::var("JARVIS_VISION") {
        return truthy(&v);
    }
    if let Some(db) = crate::DB.get() {
        if let Some(v) = db.read().get("vision_enabled") {
            return v == "true";
        }
    }
    false
}

pub fn interval_secs() -> u64 {
    std::env::var("JARVIS_VISION_INTERVAL")
        .ok()
        .and_then(|v| v.trim().parse().ok())
        .filter(|n| *n >= 1)
        .unwrap_or(3)
}

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

/// Capture the screen and return Claude's description of it.
pub fn capture_and_describe() -> Result<String, String> {
    let tmp = std::env::temp_dir();
    let shot = tmp.join("jarvis_vision.png");
    let ps = tmp.join("jarvis_capture.ps1");

    std::fs::write(&ps, CAPTURE_PS1.as_bytes())
        .map_err(|e| format!("write capture script failed: {}", e))?;

    // 1) capture the screen
    let cap = Command::new("powershell")
        .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", &ps.to_string_lossy()])
        .env("JARVIS_SHOT", &shot)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
    match cap {
        Ok(s) if s.success() => {}
        Ok(s) => return Err(format!("screen capture exited with {}", s)),
        Err(e) => return Err(format!("failed to run capture: {}", e)),
    }

    // 2) ask claude to read+describe the screenshot
    let prompt = std::env::var("JARVIS_VISION_PROMPT").unwrap_or_else(|_| DEFAULT_PROMPT.to_string());
    let full_prompt = format!("{} Изображение находится тут: {}", prompt, shot.to_string_lossy());

    // Vision uses a fast model (Haiku) by default; the main brain stays on Sonnet.
    let model = std::env::var("JARVIS_VISION_MODEL").unwrap_or_else(|_| "haiku".to_string());

    let bin = claude_bin();
    let mut cmd = Command::new(&bin);
    cmd.arg("-p")
        .arg(&full_prompt)
        .arg("--allowedTools")
        .arg("Read");
    if !model.trim().is_empty() {
        cmd.arg("--model").arg(model.trim());
    }
    cmd.stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let mut child = cmd.spawn().map_err(|e| format!("failed to launch '{}': {}", bin, e))?;

    let mut out_pipe = child.stdout.take().unwrap();
    let mut err_pipe = child.stderr.take().unwrap();
    let out_h = std::thread::spawn(move || {
        let mut s = String::new();
        let _ = out_pipe.read_to_string(&mut s);
        s
    });
    let err_h = std::thread::spawn(move || {
        let mut s = String::new();
        let _ = err_pipe.read_to_string(&mut s);
        s
    });

    let deadline = Instant::now() + Duration::from_secs(90);
    let status = loop {
        match child.try_wait() {
            Ok(Some(st)) => break st,
            Ok(None) => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err("vision: claude timed out".to_string());
                }
                std::thread::sleep(Duration::from_millis(100));
            }
            Err(e) => return Err(format!("wait failed: {}", e)),
        }
    };

    let stdout = out_h.join().unwrap_or_default().trim().to_string();
    let stderr = err_h.join().unwrap_or_default().trim().to_string();

    if status.success() && !stdout.is_empty() {
        Ok(stdout)
    } else if !stderr.is_empty() {
        Err(stderr)
    } else if !stdout.is_empty() {
        Err(stdout)
    } else {
        Err("vision: empty response".to_string())
    }
}
