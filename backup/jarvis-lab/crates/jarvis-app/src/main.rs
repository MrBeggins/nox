// В release — оконное приложение без консоли (иначе при запуске мигает/висит cmd).
// В debug консоль оставляем для логов.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use jarvis_core::slots;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;

// include core
use jarvis_core::{
    audio, audio_processing, commands, config, db, listener, recorder, stt, intent,
    ipc::{self, IpcAction},
    i18n, voices, models,
    APP_CONFIG_DIR, APP_LOG_DIR, COMMANDS_LIST, DB,
};

// include log
#[macro_use]
extern crate simple_log;
mod log;

// include app
mod app;

// include tray
// @TODO. macOS currently not supported for tray functionality.
#[cfg(not(target_os = "macos"))]
mod tray;

static SHOULD_STOP: AtomicBool = AtomicBool::new(false);
/// Микрофон отключён (mute): аудио читается, но не обрабатывается.
pub static MUTED: AtomicBool = AtomicBool::new(false);
/// «Не беспокоить»: проактивные алерты не озвучиваются (но пишутся в ленту).
pub static DND: AtomicBool = AtomicBool::new(false);
/// Запрос активации «кнопкой» (глобальный хоткей / трей / IPC). Основной цикл
/// это подхватывает и начинает слушать команду без слова-активации.
pub static ACTIVATE: AtomicBool = AtomicBool::new(false);

/// Глобальный хоткей активации: поток опрашивает GetAsyncKeyState (ловит и клавиши,
/// и боковые кнопки мыши X1/X2), по фронту нажатия ставит ACTIVATE. Клавиша берётся
/// из настройки `activation_hotkey` и перечитывается на лету (меняется из трея).
#[cfg(target_os = "windows")]
fn spawn_hotkey_listener() {
    std::thread::spawn(|| {
        let mut prev = false;
        loop {
            if should_stop() { break; }
            let combo = DB.get()
                .and_then(|db| db.read().get("activation_hotkey"))
                .unwrap_or_else(|| "ctrl_alt_j".to_string());
            let down = hotkey_is_down(&combo);
            if down && !prev {
                info!("Activation hotkey pressed: {}", combo);
                ACTIVATE.store(true, Ordering::SeqCst);
            }
            prev = down;
            std::thread::sleep(std::time::Duration::from_millis(35));
        }
    });
}

#[cfg(target_os = "windows")]
fn key_down(vk: i32) -> bool {
    unsafe { (winapi::um::winuser::GetAsyncKeyState(vk) as u16 & 0x8000u16) != 0 }
}

#[cfg(target_os = "windows")]
fn hotkey_is_down(combo: &str) -> bool {
    use winapi::um::winuser::*;
    // Произвольная клавиша/кнопка мыши, заданная захватом в настройках: "vk:<код>"
    if let Some(n) = combo.strip_prefix("vk:") {
        if let Ok(vk) = n.trim().parse::<i32>() {
            return key_down(vk);
        }
    }
    match combo {
        "ctrl_alt_j"     => key_down(VK_CONTROL) && key_down(VK_MENU) && key_down('J' as i32),
        "ctrl_alt_space" => key_down(VK_CONTROL) && key_down(VK_MENU) && key_down(VK_SPACE),
        "right_ctrl"     => key_down(VK_RCONTROL),
        "right_shift"    => key_down(VK_RSHIFT),
        "pause"          => key_down(VK_PAUSE),
        "f8"             => key_down(VK_F8),
        "f9"             => key_down(VK_F9),
        "mouse_x1"       => key_down(VK_XBUTTON1),
        "mouse_x2"       => key_down(VK_XBUTTON2),
        // по умолчанию — Ctrl+Alt+J
        _                => key_down(VK_CONTROL) && key_down(VK_MENU) && key_down('J' as i32),
    }
}

fn main() -> Result<(), String> {
    // initialize directories
    config::init_dirs()?;

    // initialize logging
    log::init_logging()?;

    // log some base info
    info!("Starting Jarvis v{} ...", config::APP_VERSION.unwrap());
    info!("Config directory is: {}", APP_CONFIG_DIR.get().unwrap().display());
    info!("Log directory is: {}", APP_LOG_DIR.get().unwrap().display());

    // initialize settings
    let settings = db::init();

    // set global DB (for core modules that read settings at init time)
    DB.set(settings.arc().clone())
            .expect("DB already initialized");

    // init voices
    let voice_id = settings.lock().voice.clone();
    let language = settings.lock().language.clone();
    if let Err(e) = voices::init(&voice_id, &language) {
        warn!("Failed to init voices: {}", e);
    }

    // init i18n
    i18n::init(&settings.lock().language);

    // init recorder
    if recorder::init().is_err() {
        app::close(1);
    }

    // init models registry (scans available AI models)
    if let Err(e) = models::init() {
        warn!("Models registry init failed: {}", e);
    }

    // init stt engine
    if stt::init().is_err() {
        // @TODO. Allow continuing even without STT, if commands is using keywords or smthng?
        app::close(1); // cannot continue without stt
    }

    // init commands
    info!("Initializing commands.");
    let cmds = match commands::parse_commands() {
        Ok(c) => c,
        Err(e) => {
            warn!("Failed to parse commands: {}. Starting with empty command list.", e);
            Vec::new()
        }
    };
    info!("Commands initialized. Count: {}, List: {:?}", cmds.len(), commands::list_paths(&cmds));
    COMMANDS_LIST.set(cmds).unwrap();

    // init audio
    if audio::init().is_err() {
        // @TODO. Allow continuing even without audio?
        app::close(1); // cannot continue without audio
    }

    // init wake-word engine
    if let Err(e) = listener::init() {
        error!("Wake-word engine init failed: {}", e);
        app::close(1);
    }

    // shared async runtime for intent classification, IPC, etc.
    let rt = Arc::new(
        tokio::runtime::Runtime::new().expect("Failed to create tokio runtime")
    );

    // init intent-recognition engine
    rt.block_on(async {
        if let Err(e) = intent::init(COMMANDS_LIST.get().unwrap()).await {
            error!("Failed to initialize intent classifier: {}", e);
            app::close(1);
        }
    });

    // init slots parsing engine
    slots::init().map_err(|e| error!("Slot extraction init failed: {}", e)).ok();

    // init audio processing
    info!("Initializing audio processing...");
    if let Err(e) = audio_processing::init() {
        warn!("Audio processing init failed: {}", e);
    }

    // init IPC
    info!("Initializing IPC...");
    ipc::init();

    // channel for text commands (manually written in the GUI)
    let (text_cmd_tx, text_cmd_rx) = mpsc::channel::<String>();

    // channel for proactive announcements (news monitor) — озвучиваются ПОСЛЕДОВАТЕЛЬНО
    let (ann_tx, ann_rx) = mpsc::channel::<String>();
    std::thread::spawn(move || {
        while let Ok(text) = ann_rx.recv() {
            // Проактивные алерты (боевые слежки) озвучиваются ДАЖЕ при mute:
            // mute гасит реакцию на пользователя, но не важные оповещения.
            // В ленту событие пишем всегда — даже в «Не беспокоить».
            ipc::send(ipc::IpcEvent::AssistantReply { text: text.clone() });
            if DND.load(Ordering::Relaxed) {
                continue; // тихий журнал: без голоса
            }
            // Новости/алерты — быстрым дикторским темпом (динамическая скорость),
            // блокирует до конца проигрывания -> без наложений.
            jarvis_core::tts::speak_reply(&text);
        }
    });

    ipc::set_action_handler(move |action| {
        match action {
            IpcAction::Stop => {
                info!("Received stop command from GUI");
                SHOULD_STOP.store(true, Ordering::SeqCst);
            }
            IpcAction::ReloadCommands => {
                info!("Received reload commands request");
                // TODO: implement reload
            }
            IpcAction::ReloadSettings => {
                info!("Reloading settings from disk (live apply)");
                jarvis_core::db::reload_global();
                if let Some(b) = jarvis_core::DB.get().and_then(|db| db.read().get("brain_backend")) {
                    jarvis_core::brain::set_backend(&b);
                }
            }
            IpcAction::AbortSpeech => {
                info!("Received abort-speech (stop button)");
                jarvis_core::tts::request_stop();
                jarvis_core::commands::request_cancel();
            }
            IpcAction::SetMuted { muted } => {
                info!("Mic mute set to: {}", muted);
                MUTED.store(muted, Ordering::SeqCst);
            }
            IpcAction::TextCommand { text } => {
                info!("Received text command: {}", text);
                if let Err(e) = text_cmd_tx.send(text) {
                    error!("Failed to send text command to app: {}", e);
                }
            }
            IpcAction::Announce { text } => {
                let _ = ann_tx.send(text); // проактивная озвучка новостей (очередь)
            }
            IpcAction::SetDnd { on } => {
                info!("Do-not-disturb set to: {}", on);
                DND.store(on, Ordering::SeqCst);
            }
            IpcAction::Ping => {
                // handled internally by server
            }
            _ => {}
        }
    });

    // start WebSocket server on the shared runtime
    let ipc_rt = Arc::clone(&rt);
    std::thread::spawn(move || {
        ipc_rt.block_on(ipc::start_server());
    });
    
    // start the app (in the background thread)
    let app_rt = Arc::clone(&rt);
    std::thread::spawn(move || {
        let _ = app::start(text_cmd_rx, &app_rt);
    });

    // start screen-vision loop (periodic screenshot -> Claude description) if enabled
    if jarvis_core::vision::is_enabled() {
        let interval = std::time::Duration::from_secs(jarvis_core::vision::interval_secs());
        info!("Screen vision enabled (interval {}s)", jarvis_core::vision::interval_secs());
        std::thread::spawn(move || {
            loop {
                if crate::should_stop() {
                    break;
                }
                match jarvis_core::vision::capture_and_describe() {
                    Ok(desc) => {
                        info!("Vision: {}", desc);
                        ipc::send(ipc::IpcEvent::AssistantReply { text: format!("👁 {}", desc) });
                    }
                    Err(e) => warn!("Vision error: {}", e),
                }
                std::thread::sleep(interval);
            }
        });
    }

    // глобальный хоткей активации (Windows)
    #[cfg(target_os = "windows")]
    spawn_hotkey_listener();

    tray::init_blocking(settings);

    Ok(())
}

pub fn should_stop() -> bool {
    SHOULD_STOP.load(Ordering::SeqCst)
}
