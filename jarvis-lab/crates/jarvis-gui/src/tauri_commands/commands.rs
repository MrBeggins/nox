use jarvis_core::commands::{self, CommandInput, JCommand};

#[tauri::command]
pub fn get_commands_count() -> usize {
    commands::parse_commands()
        .unwrap_or_default()
        .iter()
        .map(|list| list.commands.len())
        .sum()
}

#[tauri::command]
pub fn get_commands_list() -> Vec<JCommand> {
    // Re-parse on every call so newly added/edited commands show up immediately.
    commands::parse_commands()
        .unwrap_or_default()
        .into_iter()
        .flat_map(|list| list.commands)
        .collect()
}

#[tauri::command]
pub fn save_command(input: CommandInput) -> Result<(), String> {
    commands::save_command(&input)
}

#[tauri::command]
pub fn delete_command(id: String) -> Result<(), String> {
    commands::delete_command(&id)
}
