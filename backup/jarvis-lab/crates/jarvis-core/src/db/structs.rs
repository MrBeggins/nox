use crate::config;
use serde::{Deserialize, Serialize};

use crate::config::structs::SpeechToTextEngine;
use crate::config::structs::WakeWordEngine;
use crate::config::structs::NoiseSuppressionBackend;

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct Settings {
    pub microphone: i32,
    pub voice: String,

    pub wake_word_engine: WakeWordEngine,

    // backend selections (string IDs matching model or code backend IDs)
    #[serde(default = "default_intent_backend")]
    pub intent_backend: String,
    #[serde(default = "default_slots_backend")]
    pub slots_backend: String,
    #[serde(default = "default_vad_backend")]
    pub vad_backend: String,

    pub gliner_model: String,

    pub speech_to_text_engine: SpeechToTextEngine,
    pub vosk_model: String,

    // audio processing
    pub noise_suppression: NoiseSuppressionBackend,
    pub gain_normalizer: bool,

    #[serde(default = "default_language")]
    pub language: String,

    // ### ИМЯ / РЕЖИМЫ-ПРОФИЛИ
    #[serde(default = "default_wake_word")]
    pub wake_word: String,
    /// id активного режима-профиля
    #[serde(default)]
    pub active_mode: String,
    /// JSON-массив режимов: [{id,name,wake_word,brain_backend,openai_model}]
    #[serde(default = "default_modes")]
    pub modes: String,

    // ### MODES (toggles)
    #[serde(default = "default_true")]
    pub brain_enabled: bool,
    #[serde(default = "default_true")]
    pub tts_enabled: bool,
    #[serde(default)] // false
    pub vision_enabled: bool,

    // ### AI BRAIN (multi-model)
    #[serde(default = "default_brain_backend")]
    pub brain_backend: String,
    #[serde(default)]
    pub openai_url: String,
    #[serde(default)]
    pub openai_model: String,
    /// JSON array of user-defined AI models (name, backend, url, model, key).
    #[serde(default = "default_ai_models")]
    pub ai_models: String,

    // ### T-TERMINAL
    #[serde(default = "default_tterminal_url")]
    pub tterminal_url: String,
    #[serde(default)] // false
    pub tterminal_enabled: bool,
    /// T-Invest API токен (только чтение) для чтения портфеля/котировок.
    #[serde(default)]
    pub invest_token: String,
    /// Голосовое исполнение заявок: Нокс сам жмёт Купить/Продать в виджете. По умолчанию выключено.
    /// Проверяется в terminal_reader.py прямо перед нажатием (читает app.db с диска).
    #[serde(default)]
    pub tterminal_exec_enabled: bool,
    /// Лимиты одной голосовой заявки: лотов и рублей (0 = без лимита).
    #[serde(default = "default_exec_max_lots")]
    pub tterminal_exec_max_lots: u32,
    #[serde(default = "default_exec_max_rub")]
    pub tterminal_exec_max_rub: u64,

    // ### АКТИВАЦИЯ
    /// Реагировать на слово-активацию (wake word). false -> только хоткей/кнопка/текст.
    #[serde(default = "default_true")]
    pub wake_word_enabled: bool,
    /// Пресет клавиши активации: ctrl_alt_j | ctrl_alt_space | right_ctrl | right_shift
    /// | pause | f8 | f9 | mouse_x1 | mouse_x2
    #[serde(default = "default_activation_hotkey")]
    pub activation_hotkey: String,

    pub api_keys: ApiKeys,
}

fn default_intent_backend() -> String { config::DEFAULT_INTENT_BACKEND.to_string() }
fn default_slots_backend() -> String { config::DEFAULT_SLOTS_BACKEND.to_string() }
fn default_vad_backend() -> String { config::DEFAULT_VAD_BACKEND.to_string() }
fn default_language() -> String { crate::i18n::detect_system_language().to_string() }
fn default_true() -> bool { true }
fn default_brain_backend() -> String { "claude".to_string() }
fn default_ai_models() -> String { "[]".to_string() }
fn default_tterminal_url() -> String { "https://www.tbank.ru/invest/terminal/".to_string() }
fn default_exec_max_lots() -> u32 { 10 }
fn default_exec_max_rub() -> u64 { 50_000 }
fn default_wake_word() -> String { "джарвис".to_string() }
fn default_modes() -> String { "[]".to_string() }
fn default_activation_hotkey() -> String { "ctrl_alt_j".to_string() }

fn parse_bool(val: &str) -> Result<bool, String> {
    match val.trim().to_lowercase().as_str() {
        "true" | "1" | "on" | "yes"  => Ok(true),
        "false" | "0" | "off" | "no" => Ok(false),
        _ => Err(format!("expected boolean, got: '{}'", val)),
    }
}

// ### KEY-VALUE ACCESS

impl Settings {
    /// read a setting by key. returns None for unknown keys.
    pub fn get(&self, key: &str) -> Option<String> {
        match key {
            "selected_microphone"       => Some(self.microphone.to_string()),
            "assistant_voice"           => Some(self.voice.clone()),
            "selected_wake_word_engine" => Some(format!("{:?}", self.wake_word_engine)),
            "intent_backend"            => Some(self.intent_backend.clone()),
            "slots_backend"             => Some(self.slots_backend.clone()),
            "vad_backend"               => Some(self.vad_backend.clone()),
            "selected_gliner_model"     => Some(self.gliner_model.clone()),
            "selected_vosk_model"       => Some(self.vosk_model.clone()),
            "speech_to_text_engine"     => Some(format!("{:?}", self.speech_to_text_engine)),
            "noise_suppression"         => Some(format!("{:?}", self.noise_suppression)),
            "gain_normalizer"           => Some(self.gain_normalizer.to_string()),
            "language"                  => Some(self.language.clone()),
            "brain_enabled"             => Some(self.brain_enabled.to_string()),
            "tts_enabled"               => Some(self.tts_enabled.to_string()),
            "vision_enabled"            => Some(self.vision_enabled.to_string()),
            "brain_backend"             => Some(self.brain_backend.clone()),
            "openai_url"                => Some(self.openai_url.clone()),
            "openai_model"              => Some(self.openai_model.clone()),
            "ai_models"                 => Some(self.ai_models.clone()),
            "tterminal_url"             => Some(self.tterminal_url.clone()),
            "tterminal_enabled"         => Some(self.tterminal_enabled.to_string()),
            "invest_token"              => Some(self.invest_token.clone()),
            "tterminal_exec_enabled"    => Some(self.tterminal_exec_enabled.to_string()),
            "tterminal_exec_max_lots"   => Some(self.tterminal_exec_max_lots.to_string()),
            "tterminal_exec_max_rub"    => Some(self.tterminal_exec_max_rub.to_string()),
            "wake_word_enabled"         => Some(self.wake_word_enabled.to_string()),
            "activation_hotkey"         => Some(self.activation_hotkey.clone()),
            "wake_word"                 => Some(self.wake_word.clone()),
            "active_mode"               => Some(self.active_mode.clone()),
            "modes"                     => Some(self.modes.clone()),
            "api_key__picovoice"        => Some(self.api_keys.picovoice.clone()),
            "api_key__openai"           => Some(self.api_keys.openai.clone()),
            _ => None,
        }
    }

    /// write a setting by key. returns Err for unknown keys or invalid values.
    pub fn set(&mut self, key: &str, val: &str) -> Result<(), String> {
        match key {
            "selected_microphone" => {
                self.microphone = val.parse::<i32>()
                    .map_err(|_| format!("invalid integer: '{}'", val))?;
            }
            "assistant_voice" => {
                self.voice = val.to_string();
            }
            "selected_wake_word_engine" => {
                self.wake_word_engine = match val.to_lowercase().as_str() {
                    "rustpotter" => WakeWordEngine::Rustpotter,
                    "vosk"       => WakeWordEngine::Vosk,
                    "porcupine"  => WakeWordEngine::Porcupine,
                    _ => return Err(format!("unknown wake word engine: '{}'", val)),
                };
            }
            "intent_backend" => {
                self.intent_backend = val.to_string();
            }
            "slots_backend" => {
                self.slots_backend = val.to_string();
            }
            "vad_backend" => {
                self.vad_backend = val.to_string();
            }
            "selected_gliner_model" => {
                self.gliner_model = val.to_string();
            }
            "selected_vosk_model" => {
                self.vosk_model = val.to_string();
            }
            "noise_suppression" => {
                self.noise_suppression = match val.to_lowercase().as_str() {
                    "none"        => NoiseSuppressionBackend::None,
                    "nnnoiseless" => NoiseSuppressionBackend::Nnnoiseless,
                    _ => return Err(format!("unknown noise suppression backend: '{}'", val)),
                };
            }
            "gain_normalizer" => {
                self.gain_normalizer = match val.to_lowercase().as_str() {
                    "true"  => true,
                    "false" => false,
                    _ => return Err(format!("expected 'true' or 'false', got: '{}'", val)),
                };
            }
            "language" => {
                self.language = val.to_string();
            }
            "brain_enabled" => {
                self.brain_enabled = parse_bool(val)?;
            }
            "tts_enabled" => {
                self.tts_enabled = parse_bool(val)?;
            }
            "vision_enabled" => {
                self.vision_enabled = parse_bool(val)?;
            }
            "brain_backend" => {
                self.brain_backend = val.to_string();
            }
            "openai_url" => {
                self.openai_url = val.to_string();
            }
            "openai_model" => {
                self.openai_model = val.to_string();
            }
            "ai_models" => {
                self.ai_models = val.to_string();
            }
            "tterminal_url" => {
                self.tterminal_url = val.to_string();
            }
            "tterminal_enabled" => {
                self.tterminal_enabled = parse_bool(val)?;
            }
            "invest_token" => {
                self.invest_token = val.to_string();
            }
            "tterminal_exec_enabled" => {
                self.tterminal_exec_enabled = parse_bool(val)?;
            }
            "tterminal_exec_max_lots" => {
                self.tterminal_exec_max_lots = val.trim().parse::<u32>()
                    .map_err(|_| format!("invalid integer: '{}'", val))?;
            }
            "tterminal_exec_max_rub" => {
                self.tterminal_exec_max_rub = val.trim().replace(' ', "").parse::<u64>()
                    .map_err(|_| format!("invalid integer: '{}'", val))?;
            }
            "wake_word_enabled" => {
                self.wake_word_enabled = parse_bool(val)?;
            }
            "activation_hotkey" => {
                self.activation_hotkey = val.trim().to_string();
            }
            "wake_word" => {
                self.wake_word = val.trim().to_lowercase();
            }
            "active_mode" => {
                self.active_mode = val.to_string();
            }
            "modes" => {
                self.modes = val.to_string();
            }
            "api_key__picovoice" => {
                self.api_keys.picovoice = val.to_string();
            }
            "api_key__openai" => {
                self.api_keys.openai = val.to_string();
            }
            _ => return Err(format!("unknown setting: '{}'", key)),
        }
        Ok(())
    }

    /// all valid setting keys (for enumeration, debugging, etc.)
    pub fn keys() -> &'static [&'static str] {
        &[
            "selected_microphone",
            "assistant_voice",
            "selected_wake_word_engine",
            "intent_backend",
            "slots_backend",
            "vad_backend",
            "selected_gliner_model",
            "selected_vosk_model",
            "speech_to_text_engine",
            "noise_suppression",
            "gain_normalizer",
            "language",
            "brain_enabled",
            "tts_enabled",
            "vision_enabled",
            "brain_backend",
            "openai_url",
            "openai_model",
            "ai_models",
            "tterminal_url",
            "tterminal_enabled",
            "invest_token",
            "tterminal_exec_enabled",
            "tterminal_exec_max_lots",
            "tterminal_exec_max_rub",
            "wake_word_enabled",
            "activation_hotkey",
            "wake_word",
            "active_mode",
            "modes",
            "api_key__picovoice",
            "api_key__openai",
        ]
    }
}

// ### DEFAULT

impl Default for Settings {
    fn default() -> Settings {
        Settings {
            microphone: -1,
            voice: String::from(""),

            wake_word_engine: config::DEFAULT_WAKE_WORD_ENGINE,

            intent_backend: config::DEFAULT_INTENT_BACKEND.to_string(),
            slots_backend: config::DEFAULT_SLOTS_BACKEND.to_string(),
            vad_backend: config::DEFAULT_VAD_BACKEND.to_string(),

            gliner_model: String::new(),
            speech_to_text_engine: config::DEFAULT_SPEECH_TO_TEXT_ENGINE,
            vosk_model: String::from(""),

            noise_suppression: config::DEFAULT_NOISE_SUPPRESSION,
            gain_normalizer: config::DEFAULT_GAIN_NORMALIZER,

            language: crate::i18n::detect_system_language().to_string(),

            brain_enabled: true,
            tts_enabled: true,
            vision_enabled: false,

            brain_backend: default_brain_backend(),
            openai_url: String::new(),
            openai_model: String::new(),
            ai_models: default_ai_models(),

            tterminal_url: default_tterminal_url(),
            tterminal_enabled: false,
            invest_token: String::new(),
            tterminal_exec_enabled: false,
            tterminal_exec_max_lots: default_exec_max_lots(),
            tterminal_exec_max_rub: default_exec_max_rub(),

            wake_word_enabled: true,
            activation_hotkey: default_activation_hotkey(),

            wake_word: default_wake_word(),
            active_mode: String::new(),
            modes: default_modes(),

            api_keys: ApiKeys {
                picovoice: String::from(""),
                openai: String::from(""),
            },
        }
    }
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct ApiKeys {
    pub picovoice: String,
    pub openai: String,
}
