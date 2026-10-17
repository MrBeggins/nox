//! Исправления распознавания речи (Vosk) по словарю C:\jarvis-voice\nox_speech_fix.json —
//! применяются сразу после распознавания, до команд и мозга: «пять плотов пикапа» -> «пять лотов пика»,
//! «выставили лимит» -> «выставь лимит», «индексом у с биржи» -> «индекс мосбиржи».
//! Разделы: phrases — словосочетания целиком, words — отдельные слова, start — только первое слово команды
//! (после «нокс»/«джарвис»). Файл перечитывается при изменении — перезапуск не нужен.

use std::sync::Mutex;
use std::time::SystemTime;

const FIX_FILE: &str = r"C:\jarvis-voice\nox_speech_fix.json";

#[derive(Default, Clone)]
struct Fix {
    phrases: Vec<(String, String)>,
    words: Vec<(String, String)>,
    start: Vec<(String, String)>,
}

static CACHE: Mutex<Option<(SystemTime, Fix)>> = Mutex::new(None);

fn pairs(v: &serde_json::Value) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = v.as_object().map(|m| m.iter()
        .filter_map(|(k, x)| x.as_str().map(|s| (k.trim().to_lowercase(), s.trim().to_lowercase())))
        .filter(|(k, _)| !k.is_empty())
        .collect()).unwrap_or_default();
    out.sort_by(|a, b| b.0.len().cmp(&a.0.len()));     // длинные словосочетания — первыми
    out
}

fn parse(s: &str) -> Fix {
    let v: serde_json::Value = serde_json::from_str(s).unwrap_or(serde_json::Value::Null);
    Fix { phrases: pairs(&v["phrases"]), words: pairs(&v["words"]), start: pairs(&v["start"]) }
}

fn load() -> Fix {
    let mt = std::fs::metadata(FIX_FILE).and_then(|m| m.modified()).ok();
    let mut c = CACHE.lock().unwrap();
    if let (Some(mt), Some((t, f))) = (mt, c.as_ref()) {
        if *t == mt {
            return f.clone();
        }
    }
    let f = std::fs::read_to_string(FIX_FILE).map(|s| parse(&s)).unwrap_or_default();
    if let Some(mt) = mt {
        *c = Some((mt, f.clone()));
    }
    f
}

fn apply_with(text: &str, f: &Fix) -> String {
    let mut s = format!(" {} ", text.split_whitespace().collect::<Vec<_>>().join(" "));
    for (a, b) in &f.phrases {
        s = s.replace(&format!(" {} ", a), &format!(" {} ", b));
    }
    let mut toks: Vec<String> = s.split_whitespace().map(|w| {
        f.words.iter().find(|(a, _)| a == w).map(|(_, b)| b.clone()).unwrap_or_else(|| w.to_string())
    }).collect();
    let first = if toks.first().map(|w| w == "нокс" || w == "джарвис").unwrap_or(false) { 1 } else { 0 };
    if let Some(w) = toks.get(first).cloned() {
        if let Some((_, b)) = f.start.iter().find(|(a, _)| *a == w) {
            toks[first] = b.clone();
        }
    }
    toks.join(" ")
}

/// Исправить распознанную фразу (нижний регистр на входе).
pub fn apply(text: &str) -> String {
    apply_with(text, &load())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn speechfix_dictionary() {
        let f = parse(&std::fs::read_to_string(FIX_FILE).expect("нет nox_speech_fix.json"));
        assert_eq!(apply_with("подготовь заявку пять плотов пикапа пятьсот сорок пять", &f),
                   "подготовь заявку пять лотов пика пятьсот сорок пять");
        assert_eq!(apply_with("джарвис выставили лимит на заявку на продажу пик", &f),
                   "джарвис выставь лимит на заявку на продажу пик");
        assert_eq!(apply_with("котировки индексом у с биржи", &f), "котировки индекс мосбиржи");
        assert_eq!(apply_with("сообщить цену газпрома и стакана ты терминала", &f),
                   "сообщить цену газпрома и стакана т-терминала");
        assert_eq!(apply_with("купи десять вэ тэ бэ", &f), "купи десять втб");
        // «выставили» не в начале — не трогаем: «мы вчера выставили заявку»
        assert_eq!(apply_with("мы вчера выставили заявку", &f), "мы вчера выставили заявку");
        assert_eq!(apply_with("купи сбер", &f), "купи сбер");
    }
}
