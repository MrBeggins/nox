//! Обращение к пользователю: «сэр» → имя из профиля (окно «Регистрация», поле user_name).
//! Применяется на выходе — в озвучке (tts::speak_piece) и в ленте (ipc AssistantReply),
//! поэтому работает для любых фраз: своих, от мозга и от Python-сервисов.
//! Пустое имя — всё как было («сэр»).

use once_cell::sync::Lazy;
use parking_lot::RwLock;

static NAME: Lazy<RwLock<String>> = Lazy::new(|| RwLock::new(String::new()));

/// Задать имя (при старте и после reload_settings).
pub fn set_name(name: &str) {
    *NAME.write() = name.trim().to_string();
}

pub fn name() -> String {
    NAME.read().clone()
}

/// Заменить отдельное слово «сэр» (любой регистр) на имя. Внутри слов («сэра»,
/// «пэсэр») не трогает.
pub fn personalize(text: &str) -> String {
    let name = NAME.read();
    if name.is_empty() {
        return text.to_string();
    }
    let chars: Vec<char> = text.chars().collect();
    let mut out = String::with_capacity(text.len() + name.len());
    let mut i = 0;
    while i < chars.len() {
        let is_sir = i + 3 <= chars.len()
            && chars[i..i + 3].iter().map(|c| c.to_lowercase().next().unwrap_or(*c)).eq("сэр".chars())
            && (i == 0 || !chars[i - 1].is_alphanumeric())
            && (i + 3 == chars.len() || !chars[i + 3].is_alphanumeric());
        if is_sir {
            out.push_str(&name);
            i += 3;
        } else {
            out.push(chars[i]);
            i += 1;
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn replaces_only_whole_word() {
        set_name("Дамир");
        assert_eq!(personalize("Готово, сэр."), "Готово, Дамир.");
        assert_eq!(personalize("Сэр, вышла новость"), "Дамир, вышла новость");
        assert_eq!(personalize("СЭР!"), "Дамир!");
        assert_eq!(personalize("сэра нет, пэсэр"), "сэра нет, пэсэр");
        set_name("");
        assert_eq!(personalize("Готово, сэр."), "Готово, сэр.");
    }
}
