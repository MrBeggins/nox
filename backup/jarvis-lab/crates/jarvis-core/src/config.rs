pub mod structs;
use structs::AudioType;
use structs::RecorderType;
use structs::SpeechToTextEngine;
use structs::WakeWordEngine;

use once_cell::sync::Lazy;
use std::env;
use std::fs;
use std::path::PathBuf;

use platform_dirs::AppDirs;

#[cfg(feature="jarvis_app")]
use rustpotter::{
    AudioFmt, BandPassConfig, DetectorConfig, FiltersConfig, GainNormalizationConfig,
    RustpotterConfig, ScoreMode,
};

use crate::config::structs::NoiseSuppressionBackend;
use crate::{APP_CONFIG_DIR, APP_DIRS, APP_LOG_DIR};

#[allow(dead_code)]
pub fn init_dirs() -> Result<(), String> {
    // infer app dirs
    if APP_DIRS.get().is_some() {
        return Ok(());
    }

    // cache_dir, config_dir, data_dir, state_dir
    APP_DIRS
        .set(AppDirs::new(Some(BUNDLE_IDENTIFIER), false).unwrap())
        .unwrap();

    // setup directories
    let mut config_dir = PathBuf::from(&APP_DIRS.get().unwrap().config_dir);
    let mut log_dir = PathBuf::from(&APP_DIRS.get().unwrap().config_dir);

    // create dirs, if required
    if !config_dir.exists() {
        if fs::create_dir_all(&config_dir).is_err() {
            config_dir = env::current_dir().expect("Cannot infer the config directory");
            fs::create_dir_all(&config_dir)
                .expect("Cannot create config directory, access denied?");
        }
    }

    if !log_dir.exists() {
        if fs::create_dir_all(&log_dir).is_err() {
            log_dir = env::current_dir().expect("Cannot infer the log directory");
            fs::create_dir_all(&log_dir).expect("Cannot create log directory, access denied?");
        }
    }

    // store inferred paths
    APP_CONFIG_DIR.set(config_dir).unwrap();
    APP_LOG_DIR.set(log_dir).unwrap();

    Ok(())
}

/*
   Defaults.
*/
pub const DEFAULT_AUDIO_TYPE: AudioType = AudioType::Kira;
pub const DEFAULT_RECORDER_TYPE: RecorderType = RecorderType::PvRecorder;
pub const DEFAULT_WAKE_WORD_ENGINE: WakeWordEngine = WakeWordEngine::Vosk;
pub const DEFAULT_SPEECH_TO_TEXT_ENGINE: SpeechToTextEngine = SpeechToTextEngine::Vosk;

// backend defaults (string IDs)
pub const DEFAULT_INTENT_BACKEND: &str = "intent-classifier";
pub const DEFAULT_SLOTS_BACKEND: &str = "none";
pub const DEFAULT_VAD_BACKEND: &str = "energy";

pub const DEFAULT_VOICE: &str = "jarvis-remaster";
pub const SOUND_PATH: &str = "resources/sound"; // extended from SOUND_DIR (resources/sound)
pub const VOICES_PATH: &str = "voices"; // extended from SOUND_PATH (resources/sound)

pub const BUNDLE_IDENTIFIER: &str = "com.priler.jarvis";
pub const DB_FILE_NAME: &str = "app.db";
pub const LOG_FILE_NAME: &str = "log.txt";
pub const APP_VERSION: Option<&str> = option_env!("CARGO_PKG_VERSION");
pub const AUTHOR_NAME: Option<&str> = option_env!("CARGO_PKG_AUTHORS");
pub const REPOSITORY_LINK: Option<&str> = option_env!("CARGO_PKG_REPOSITORY");
pub const TG_OFFICIAL_LINK: Option<&str> = Some("https://t.me/howdyho_official");
pub const FEEDBACK_LINK: Option<&str> = Some("https://t.me/jarvis_feedback_bot");
pub const SUPPORT_BOOSTY_LINK: Option<&str> = Some("https://boosty.to/howdyho");
pub const SUPPORT_PATREON_LINK: Option<&str> = Some("https://www.patreon.com/c/priler");

/*
   Tray.
*/
pub const TRAY_ICON: &str = "32x32.png";
pub const TRAY_TOOLTIP: &str = "Jarvis Voice Assistant";

// RUSPOTTER
pub const RUSPOTTER_MIN_SCORE: f32 = 0.62;

#[cfg(feature="jarvis_app")]
pub const RUSTPOTTER_DEFAULT_CONFIG: Lazy<RustpotterConfig> = Lazy::new(|| {
    RustpotterConfig {
        fmt: AudioFmt::default(),
        detector: DetectorConfig {
            avg_threshold: 0.,
            threshold: 0.5,
            min_scores: 15,
            score_ref: 0.22,
            band_size: 5,
            vad_mode: None,
            score_mode: ScoreMode::Max,
            eager: false,
            // comparator_band_size: 5,
            // comparator_ref: 0.22
        },
        filters: FiltersConfig {
            gain_normalizer: GainNormalizationConfig {
                // enabled: true,
                // gain_ref: None,
                // min_gain: 0.7,
                // max_gain: 1.0,
                enabled: false, // disable, now we have separate gain normalizer implementation
                gain_ref: None,
                min_gain: 0.7,
                max_gain: 1.0,
            },
            band_pass: BandPassConfig {
                enabled: true,
                low_cutoff: 80.,
                high_cutoff: 400.,
            },
        },
    }
});

// PICOVOICE
pub const COMMANDS_PATH: &str = "resources/commands/";
pub const KEYWORDS_PATH: &str = "resources/picovoice/keywords/";
pub const DEFAULT_KEYWORD: &str = "jarvis_windows.ppn";
pub const DEFAULT_SENSITIVITY: f32 = 1.0;

// VOSK
// pub const VOSK_MODEL_PATH: &str = const_concat!(PUBLIC_PATH, "/vosk/model_small");
pub const VOSK_MODELS_PATH: &str = "resources/vosk";
pub const VOSK_MODEL_PATH: &str = "resources/vosk/model_small";
pub const VOSK_FETCH_PHRASE: &str = "джарвис";
pub const VOSK_MIN_RATIO: f64 = 70.0;

// 0.7 lenient, expect false positives
// 0.8 balanced
// 0.9 strict
// etc
pub const VOSK_WAKE_CONFIDENCE: f32 = 0.9;

pub const VOSK_SPEECH_RECOGNIZER_MAX_ALTERNATIVES: u16 = 3;
pub const VOSK_SPEECH_RECOGNIZER_WORDS: bool = false;
pub const VOSK_SPEECH_PARTIAL_WORDS: bool = false;

// IRE (intents recognition)
pub const INTENT_CLASSIFIER_MIN_CONFIDENCE: f64 = 0.75;


// embedding classifier
pub const EMBEDDING_MIN_CONFIDENCE: f64 = 0.70;

// AUDIO PROCESSING DEFAULTS
pub const DEFAULT_NOISE_SUPPRESSION: NoiseSuppressionBackend = NoiseSuppressionBackend::None;
pub const DEFAULT_GAIN_NORMALIZER: bool = false;

// VAD settings
pub const VAD_ENERGY_THRESHOLD: f32 = 100.0;  // RMS threshold for energy-based VAD
pub const VAD_NNNOISELESS_THRESHOLD: f32 = 0.8;  // probability threshold for nnnoiseless
pub const VAD_SILENCE_FRAMES: u32 = 15;  // frames of silence before speech end (~480ms)

// gain normalizer settings
pub const GAIN_TARGET_RMS: f32 = 3000.0;  // target RMS level
pub const GAIN_MIN: f32 = 0.5;  // minimum gain multiplier
pub const GAIN_MAX: f32 = 3.0;  // maximum gain multiplier

// nnnoiseless frame size (fixed by library)
pub const NNNOISELESS_FRAME_SIZE: usize = 480;

// LUA
pub const DEFAULT_LUA_SANDBOX: &str = "standard";
pub const DEFAULT_LUA_TIMEOUT: u64 = 10000; // ms

// ETC
pub const CMD_RATIO_THRESHOLD: f64 = 75f64;
pub const CMS_WAIT_DELAY: std::time::Duration = std::time::Duration::from_secs(15);

// pub const ASSISTANT_GREET_PHRASES: [&str; 3] = ["greet1", "greet2", "greet3"];
// pub const ASSISTANT_PHRASES_TBR: [&str; 17] = [
//     "джарвис",
//     "сэр",
//     "слушаю сэр",
//     "всегда к услугам",
//     "произнеси",
//     "ответь",
//     "покажи",
//     "скажи",
//     "давай",
//     "да сэр",
//     "к вашим услугам сэр",
//     "всегда к вашим услугам сэр",
//     "запрос выполнен сэр",
//     "выполнен сэр",
//     "есть",
//     "загружаю сэр",
//     "очень тонкое замечание сэр",
// ];



/// Активное имя-обращение (wake word) из настроек; по умолчанию "джарвис".
pub fn active_wake_word() -> String {
    crate::DB
        .get()
        .and_then(|db| db.read().get("wake_word"))
        .map(|w| w.trim().to_lowercase())
        .filter(|w| !w.is_empty())
        .unwrap_or_else(|| "джарвис".to_string())
}

fn is_default_jarvis(w: &str) -> bool {
    matches!(w, "джарвис" | "jarvis" | "джарвіс")
}

fn is_nox(w: &str) -> bool {
    matches!(w, "нокс" | "nox" | "нокс," | "ноx")
}

/// true, если активно слово по умолчанию (Джарвис) — для него используем grammar-распознавание.
/// Для кастомных слов используем СВОБОДНОЕ распознавание + фаззи-сверку (grammar молча
/// выкидывает слова вне словаря модели, из-за чего короткое «нокс» могло не срабатывать).
pub fn wake_is_default() -> bool {
    is_default_jarvis(&active_wake_word())
}

/// Фонетические варианты «нокс» для фаззи-сверки (модель может расслышать близко).
fn nox_variants() -> Vec<String> {
    ["нокс", "ноукс", "нокис", "нокус", "ноктс", "нокз", "ногс", "нокс с", "окс", "нок", "ноксь", "нохс"]
        .iter().map(|s| s.to_string()).collect()
}

pub fn get_wake_phrases(_lang: &str) -> Vec<String> {
    let w = active_wake_word();
    if is_default_jarvis(&w) {
        return ["джарвис", "джервис", "гарвис", "джарви", "гарви", "jarvis", "jervis"]
            .iter().map(|s| s.to_string()).collect();
    }
    if is_nox(&w) {
        return nox_variants();
    }
    vec![w]
}

pub fn get_phrases_to_remove(lang: &str) -> Vec<String> {
    let w = active_wake_word();
    // ВАЖНО: для ВЫРЕЗАНИЯ из команды берём только канонические формы, без коротких
    // фрагментов (иначе «окс»/«нок» испортят слова внутри самой команды).
    let mut v: Vec<String> = if is_default_jarvis(&w) {
        ["джарвис", "джервис", "гарвис", "джарви", "гарви"]
            .iter().map(|s| s.to_string()).collect()
    } else if is_nox(&w) {
        ["нокс", "ноукс", "нокис", "нокус"].iter().map(|s| s.to_string()).collect()
    } else {
        vec![w]
    };
    let fillers: &[&str] = match lang {
        "ru" => &["сэр", "слушаю сэр", "всегда к услугам", "произнеси", "ответь",
            "покажи", "скажи", "давай", "да сэр", "к вашим услугам сэр", "загружаю сэр"],
        "ua" => &["сер", "слухаю сер", "завжди до послуг", "скажи", "покажи",
            "відповідай", "давай", "так сер", "до ваших послуг сер"],
        "en" => &["sir", "yes sir", "at your service", "please", "say", "show", "tell", "hey"],
        _ => &[],
    };
    v.extend(fillers.iter().map(|s| s.to_string()));
    v
}

pub fn get_wake_grammar(_lang: &str) -> Vec<String> {
    let w = active_wake_word();
    if is_default_jarvis(&w) {
        return ["джарвис", "[unk]", "джон", "джони", "джей", "джонстон", "привет", "давай"]
            .iter().map(|s| s.to_string()).collect();
    }
    let mut g: Vec<String> = w.split_whitespace().map(|s| s.to_string()).collect();
    for f in ["[unk]", "привет", "давай"] {
        g.push(f.to_string());
    }
    g
}