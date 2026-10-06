use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::fs;
use std::time::Duration;
use std::process::{Child, Command};
use std::sync::{Mutex, OnceLock};
use std::sync::atomic::{AtomicBool, Ordering};

use seqdiff::ratio;

// Принудительная отмена запущенных внешних команд.
static CANCEL_REQUESTED: AtomicBool = AtomicBool::new(false);
static RUNNING_CHILDREN: OnceLock<Mutex<Vec<Child>>> = OnceLock::new();

fn running_children() -> &'static Mutex<Vec<Child>> {
    RUNNING_CHILDREN.get_or_init(|| Mutex::new(Vec::new()))
}

/// Прерывает озвучку и внешние процессы текущей команды.
pub fn request_cancel() {
    CANCEL_REQUESTED.store(true, Ordering::SeqCst);
    if let Ok(mut children) = running_children().lock() {
        for child in children.iter_mut() {
            let _ = child.kill();
        }
        children.clear();
    }
}

fn clear_cancel() {
    CANCEL_REQUESTED.store(false, Ordering::SeqCst);
}

fn spawn_tracked(mut child: Child) -> std::io::Result<()> {
    if CANCEL_REQUESTED.load(Ordering::SeqCst) {
        let _ = child.kill();
        return Ok(());
    }
    let mut children = running_children().lock().map_err(|_| std::io::Error::other("command lock poisoned"))?;
    children.push(child);
    Ok(())
}

mod structs;
pub use structs::*;

use crate::{config, i18n, APP_DIR};

#[cfg(feature = "lua")]
use crate::lua::{self, SandboxLevel, CommandContext};

pub fn parse_commands() -> Result<Vec<JCommandsList>, String> {
    let mut commands: Vec<JCommandsList> = Vec::new();

    let commands_path = APP_DIR.join(config::COMMANDS_PATH);
    let cmd_dirs = fs::read_dir(&commands_path)
        .map_err(|e| format!("Error reading commands directory {:?}: {}", commands_path, e))?;

    for entry in cmd_dirs.flatten() {
        let cmd_path = entry.path();
        let toml_file = cmd_path.join("command.toml");
        
        if !toml_file.exists() {
            continue;
        }
        
        let content = match fs::read_to_string(&toml_file) {
            Ok(c) => c,
            Err(e) => {
                warn!("Failed to read {}: {}", toml_file.display(), e);
                continue;
            }
        };

        let file: JCommandsList = match toml::from_str(&content) {
            Ok(f) => f,
            Err(e) => {
                warn!("Failed to parse {}: {}", toml_file.display(), e);
                continue;
            }
        };

        commands.push(JCommandsList {
            path: cmd_path,
            commands: file.commands,
        });
    }

    if commands.is_empty() {
        Err("No commands found".into())
    } else {
        info!("Loaded {} command pack(s)", commands.len());
        Ok(commands)
    }
}

// ### GUI-EDITABLE COMMANDS (create / update / delete)

/// Payload for creating or updating a command from the GUI. Mirrors the
/// `[[commands]]` TOML entry but without runtime-only cache fields.
#[derive(serde::Serialize, serde::Deserialize, Debug, Default)]
pub struct CommandInput {
    pub id: String,
    #[serde(rename = "type", default)]
    pub cmd_type: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub exe_path: String,
    #[serde(default)]
    pub exe_args: Vec<String>,
    #[serde(default)]
    pub cli_cmd: String,
    #[serde(default)]
    pub cli_args: Vec<String>,
    #[serde(default)]
    pub script: String,
    #[serde(default)]
    pub sandbox: String,
    #[serde(default)]
    pub phrases: HashMap<String, Vec<String>>,
    #[serde(default)]
    pub sounds: HashMap<String, Vec<String>>,
    #[serde(default)]
    pub modes: Vec<String>,
}

/// Активна ли команда в данном режиме (пустой список modes = во всех режимах).
pub fn command_in_mode(cmd: &JCommand, active_mode: &str) -> bool {
    cmd.modes.is_empty() || active_mode.is_empty() || cmd.modes.iter().any(|m| m == active_mode)
}

fn sanitize_id(id: &str) -> Result<String, String> {
    let s: String = id
        .trim()
        .to_lowercase()
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == '_' || c == '-' { c } else { '_' })
        .collect();
    if s.is_empty() {
        return Err("Command id is empty".into());
    }
    Ok(s)
}

/// Create or overwrite a command pack directory `<commands>/<id>/command.toml`.
pub fn save_command(input: &CommandInput) -> Result<(), String> {
    let id = sanitize_id(&input.id)?;
    if input.cmd_type.trim().is_empty() {
        return Err("Command type is empty".into());
    }

    let dir = APP_DIR.join(config::COMMANDS_PATH).join(&id);
    fs::create_dir_all(&dir).map_err(|e| format!("create dir: {}", e))?;

    #[derive(serde::Serialize)]
    struct Wrap<'a> {
        commands: Vec<&'a CommandInput>,
    }
    let toml_str = toml::to_string_pretty(&Wrap { commands: vec![input] })
        .map_err(|e| format!("serialize toml: {}", e))?;

    fs::write(dir.join("command.toml"), toml_str).map_err(|e| format!("write file: {}", e))?;
    info!("Saved command '{}'", id);
    Ok(())
}

/// Delete a command pack directory by id.
pub fn delete_command(id: &str) -> Result<(), String> {
    let id = sanitize_id(id)?;
    let dir = APP_DIR.join(config::COMMANDS_PATH).join(&id);
    if dir.exists() {
        fs::remove_dir_all(&dir).map_err(|e| format!("remove dir: {}", e))?;
        info!("Deleted command '{}'", id);
    }
    Ok(())
}


pub fn commands_hash(commands: &[JCommandsList]) -> String {
    use sha2::{Sha256, Digest};
    
    let mut hasher = Sha256::new();
    
    let lang = i18n::get_language();
    hasher.update(lang.as_bytes());
    hasher.update(b"|");

    // collect all command ids and phrases for current language, sorted
    let mut all_data: Vec<(&str, _)> = commands.iter()
        .flat_map(|ac| ac.commands.iter().map(|c| (c.id.as_str(), c.get_phrases(&lang))))
        .collect();
    all_data.sort_by_key(|(id, _)| *id);
    
    for (id, phrases) in all_data {
        hasher.update(id.as_bytes());
        for phrase in phrases.iter() {
            hasher.update(phrase.as_bytes());
        }
    }
    
    format!("{:x}", hasher.finalize())
}


pub fn fetch_command<'a>(
    phrase: &str,
    commands: &'a [JCommandsList],
) -> Option<(&'a PathBuf, &'a JCommand)> {
    let lang = i18n::get_language();

    let phrase = phrase.trim().to_lowercase();
    if phrase.is_empty() {
        return None;
    }

    let phrase_chars: Vec<char> = phrase.chars().collect();
    let phrase_words: Vec<&str> = phrase.split_whitespace().collect();

    let mut result: Option<(&PathBuf, &JCommand)> = None;
    let mut best_score = config::CMD_RATIO_THRESHOLD;

    for cmd_list in commands {
        for cmd in &cmd_list.commands {
            let cmd_phrases = cmd.get_phrases(&lang);
            
            for cmd_phrase in cmd_phrases.iter() {
                let cmd_phrase_lower = cmd_phrase.trim().to_lowercase();
                let cmd_phrase_chars: Vec<char> = cmd_phrase_lower.chars().collect();
                
                // character-level similarity
                let char_ratio = ratio(&phrase_chars, &cmd_phrase_chars);
                
                // word-level similarity
                let cmd_words: Vec<&str> = cmd_phrase_lower.split_whitespace().collect();
                let word_score = word_overlap_score(&phrase_words, &cmd_words);
                
                // combined score
                let score = (char_ratio * 0.6) + (word_score * 0.4);
                
                // early exit on perfect match
                if score >= 99.0 {
                    debug!("Perfect match: '{}' -> '{}'", phrase, cmd_phrase_lower);
                    return Some((&cmd_list.path, cmd));
                }
                
                if score > best_score {
                    best_score = score;
                    result = Some((&cmd_list.path, cmd));
                }
            }
        }
    }

    if let Some((_, cmd)) = result {
        info!("Fuzzy match: '{}' -> cmd '{}' (score: {:.1}%)", phrase, cmd.id, best_score);
    } else {
        debug!("No match for '{}' (best: {:.1}%)", phrase, best_score);
    }
    
    result
}


fn word_overlap_score(input_words: &[&str], cmd_words: &[&str]) -> f64 {
    if input_words.is_empty() || cmd_words.is_empty() {
        return 0.0;
    }

    let mut matched = 0.0;
    
    // pre-compute cmd word chars to avoid repeated allocations
    let cmd_word_chars: Vec<Vec<char>> = cmd_words
        .iter()
        .map(|w| w.chars().collect())
        .collect();
    
    for input_word in input_words {
        let input_chars: Vec<char> = input_word.chars().collect();
        
        let best_word_match = cmd_word_chars
            .iter()
            .map(|cw| ratio(&input_chars, cw))
            .fold(0.0_f64, f64::max);
        
        if best_word_match > 70.0 {
            matched += best_word_match / 100.0;
        }
    }

    let max_words = input_words.len().max(cmd_words.len()) as f64;
    (matched / max_words) * 100.0
}




pub fn execute_exe(exe: &str, args: &[String]) -> std::io::Result<Child> {
    Command::new(exe).args(args).spawn()
}

pub fn execute_cli(cmd: &str, args: &[String]) -> std::io::Result<Child> {
    debug!("Spawning: cmd /C {} {:?}", cmd, args);

    if cfg!(target_os = "windows") {
        Command::new("cmd").arg("/C").arg(cmd).args(args).spawn()
    } else {
        Command::new("sh").arg("-c").arg(cmd).args(args).spawn()
    }
}

pub fn execute_command(cmd_path: &PathBuf, cmd_config: &JCommand, phrase: Option<&str>, slots: Option<&HashMap<String, SlotValue>>) -> Result<bool, String> {
    clear_cancel();
    // execute command by the type
    match cmd_config.cmd_type.as_str() {

        // BRUH
        "voice" => Ok(true),
        
        // LUA command
        #[cfg(feature = "lua")]
        "lua" => {
            execute_lua_command(cmd_path, cmd_config, phrase, slots)
        }

        // AutoHotkey command
        // @TODO: Consider adding ahk source files execution?
        "ahk" => {
            let exe_path_absolute = Path::new(&cmd_config.exe_path);
            let exe_path_local = cmd_path.join(&cmd_config.exe_path);

            let exe_path = if exe_path_absolute.exists() {
                exe_path_absolute
            } else {
                exe_path_local.as_path()
            };

            execute_exe(exe_path.to_str().unwrap(), &cmd_config.exe_args)
                .and_then(|child| { spawn_tracked(child)?; Ok(()) })
                .map(|_| true)
                .map_err(|e| format!("AHK process spawn error: {}", e))
        }
        
        // CLI command type
        // @TODO: Consider security restrictions
        "cli" => {
            execute_cli(&cmd_config.cli_cmd, &cmd_config.cli_args)
                .and_then(|child| { spawn_tracked(child)?; Ok(()) })
                .map(|_| true)
                .map_err(|e| format!("CLI command error: {}", e))
        }
        
        // TERMINATOR command (T1000)
        "terminate" => {
            std::thread::sleep(Duration::from_secs(2));
            std::process::exit(0);
        }
        
        // STOP CHANING
        "stop_chaining" => Ok(false),

        // other
        _ => {
            error!("Command type unknown: {}", cmd_config.cmd_type);
            Err(format!("Command type unknown: {}", cmd_config.cmd_type).into())
        }
    }
}

// look up a command by its ID
pub fn get_command_by_id<'a>(
    commands: &'a [JCommandsList],
    id: &str,
) -> Option<(&'a PathBuf, &'a JCommand)> {
    for cmd_list in commands {
        for cmd in &cmd_list.commands {
            if cmd.id == id {
                return Some((&cmd_list.path, cmd));
            }
        }
    }
    None
}

pub fn list_paths(commands: &[JCommandsList]) -> Vec<&Path> {
    commands.iter().map(|x| x.path.as_path()).collect()
}

#[cfg(feature = "lua")]
fn execute_lua_command(
    cmd_path: &PathBuf,
    cmd_config: &JCommand,
    phrase: Option<&str>,
    slots: Option<&HashMap<String, SlotValue>>
) -> Result<bool, String> {
    // get script path

    let script_name = if cmd_config.script.is_empty() {
        "script.lua"
    } else {
        &cmd_config.script
    };
    
    let script_path = cmd_path.join(script_name);
    
    if !script_path.exists() {
        return Err(format!("Lua script not found: {}", script_path.display()));
    }
    
    // parse sandbox level
    let sandbox = SandboxLevel::from_str(&cmd_config.sandbox);

    // create context
    let context = CommandContext {
        phrase: phrase.unwrap_or("").to_string(),
        command_id: cmd_config.id.clone(),
        command_path: cmd_path.clone(),
        language: i18n::get_language(),
        slots: slots.map(|s| s.clone()),
    };
    
    // get timeout
    let timeout = Duration::from_millis(cmd_config.timeout);
    
    info!("Executing Lua command: {} (sandbox: {:?}, timeout: {:?})", 
          cmd_config.id, sandbox, timeout);
    
    // execute
    match lua::execute(&script_path, context, sandbox, timeout) {
        Ok(result) => {
            info!("Lua command {} completed (chain: {})", cmd_config.id, result.chain);
            Ok(result.chain)
        }
        Err(e) => {
            error!("Lua command {} failed: {}", cmd_config.id, e);
            Err(e.to_string())
        }
    }
}