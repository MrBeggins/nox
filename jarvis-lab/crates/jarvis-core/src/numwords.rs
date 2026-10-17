//! Русские числительные словами -> цифры для голосовых команд.
//!
//! Vosk пишет числа СЛОВАМИ: «купи пять пик по пятьсот сорок пять» — а разбор заявки понимает только
//! цифры, поэтому заявка выходила на 1 лот по рынку, а «пять … пятьсот сорок пять» попадало в название
//! инструмента. `digits()` превращает фразу в «купи 5 пик по 545».
//! Понимает: падежи («пяти», «двухсот», «сорока»), тысячи/миллионы, «полтора/полторы», «с половиной»,
//! «N целых M десятых/сотых», «N запятая/точка M», «N рублей M копеек» -> N.MM.

fn unit(w: &str) -> Option<f64> {
    let v = match w {
        "ноль" | "нуля" | "нулю" | "нулём" | "нулем" => 0.0,
        "один" | "одна" | "одно" | "одного" | "одной" | "одному" | "одним" | "одну" | "одною" => 1.0,
        "два" | "две" | "двух" | "двум" | "двумя" => 2.0,
        "три" | "трёх" | "трех" | "трём" | "трем" | "тремя" => 3.0,
        "четыре" | "четырёх" | "четырех" | "четырём" | "четырем" | "четырьмя" => 4.0,
        "пять" | "пяти" | "пятью" => 5.0,
        "шесть" | "шести" | "шестью" => 6.0,
        "семь" | "семи" | "семью" => 7.0,
        "восемь" | "восьми" | "восемью" | "восьмью" => 8.0,
        "девять" | "девяти" | "девятью" => 9.0,
        "десять" | "десяти" | "десятью" => 10.0,
        "одиннадцать" | "одиннадцати" => 11.0,
        "двенадцать" | "двенадцати" => 12.0,
        "тринадцать" | "тринадцати" => 13.0,
        "четырнадцать" | "четырнадцати" => 14.0,
        "пятнадцать" | "пятнадцати" => 15.0,
        "шестнадцать" | "шестнадцати" => 16.0,
        "семнадцать" | "семнадцати" => 17.0,
        "восемнадцать" | "восемнадцати" => 18.0,
        "девятнадцать" | "девятнадцати" => 19.0,
        _ => return None,
    };
    Some(v)
}

fn tens(w: &str) -> Option<f64> {
    let v = match w {
        "двадцать" | "двадцати" | "двадцатью" => 20.0,
        "тридцать" | "тридцати" | "тридцатью" => 30.0,
        "сорок" | "сорока" => 40.0,
        "пятьдесят" | "пятидесяти" | "пятьюдесятью" => 50.0,
        "шестьдесят" | "шестидесяти" | "шестьюдесятью" => 60.0,
        "семьдесят" | "семидесяти" | "семьюдесятью" => 70.0,
        "восемьдесят" | "восьмидесяти" | "восемьюдесятью" => 80.0,
        "девяносто" | "девяноста" => 90.0,
        _ => return None,
    };
    Some(v)
}

fn hundreds(w: &str) -> Option<f64> {
    let v = match w {
        "сто" | "ста" | "стам" => 100.0,
        "двести" | "двухсот" | "двумстам" => 200.0,
        "триста" | "трёхсот" | "трехсот" | "трёмстам" | "тремстам" => 300.0,
        "четыреста" | "четырёхсот" | "четырехсот" | "четырёмстам" | "четыремстам" => 400.0,
        "пятьсот" | "пятисот" | "пятистам" => 500.0,
        "шестьсот" | "шестисот" | "шестистам" => 600.0,
        "семьсот" | "семисот" | "семистам" => 700.0,
        "восемьсот" | "восьмисот" | "восьмистам" => 800.0,
        "девятьсот" | "девятисот" | "девятистам" => 900.0,
        _ => return None,
    };
    Some(v)
}

fn scale(w: &str) -> Option<f64> {
    if w.starts_with("тысяч") {
        return Some(1e3);
    }
    if w.starts_with("миллион") {
        return Some(1e6);
    }
    if w.starts_with("миллиард") {
        return Some(1e9);
    }
    None
}

fn frac_div(w: &str) -> Option<f64> {
    if w.starts_with("десят") && w != "десять" && w != "десяти" && w != "десятью" {
        return Some(10.0);
    }
    if w.starts_with("сотых") || w.starts_with("сотая") || w.starts_with("сотую") {
        return Some(100.0);
    }
    if w.starts_with("тысячн") {
        return Some(1000.0);
    }
    None
}

fn is_num_word(w: &str) -> bool {
    unit(w).is_some() || tens(w).is_some() || hundreds(w).is_some()
        || (scale(w).is_some() && !w.starts_with("тысячн"))
}

fn fmt(v: f64) -> String {
    if (v - v.round()).abs() < 1e-9 {
        format!("{}", v.round() as i64)
    } else {
        let s = format!("{:.4}", v);
        s.trim_end_matches('0').trim_end_matches('.').to_string()
    }
}

/// Считать число, начиная с позиции i. Возвращает (значение, сколько слов съели).
fn read_int(ws: &[&str], i: usize) -> Option<(f64, usize)> {
    let (mut total, mut cur, mut j, mut any) = (0.0f64, 0.0f64, i, false);
    let mut last: u8 = 0; // 3 — сотни, 2 — десятки, 1 — единицы: «пятьсот сорок пять» идут по убыванию
    while j < ws.len() {
        let w = ws[j];
        if unit(w) == Some(0.0) {
            // «ноль» — всегда отдельное число: «в девять ноль пять» = 9 0 5 (09:05), а не 9 5
            if any { break; }
            return Some((0.0, 1));
        }
        if let Some(v) = hundreds(w) {
            if last != 0 && last <= 3 && cur > 0.0 { break; }
            cur += v; last = 3;
        } else if let Some(v) = tens(w) {
            if last != 0 && last <= 2 && cur > 0.0 { break; }
            cur += v; last = 2;
        } else if let Some(v) = unit(w) {
            if last != 0 && last <= 1 && cur > 0.0 { break; }
            if v >= 10.0 && last == 2 { break; }   // «двадцать двенадцать» — это два числа
            cur += v; last = 1;
        } else if let Some(s) = scale(w) {
            if w.starts_with("тысячн") { break; }
            if !any && cur == 0.0 { cur = 1.0; }   // «тысяча» = 1000
            total += cur.max(1.0) * s; cur = 0.0; last = 0;
        } else {
            break;
        }
        any = true;
        j += 1;
    }
    if !any { None } else { Some((total + cur, j - i)) }
}

/// Фраза с числами словами -> с цифрами. Остальные слова не трогаем.
pub fn digits(text: &str) -> String {
    let ws: Vec<&str> = text.split_whitespace().collect();
    let mut out: Vec<String> = Vec::with_capacity(ws.len());
    let mut i = 0;
    while i < ws.len() {
        let w = ws[i];
        if w == "полтора" || w == "полторы" || w == "полутора" {
            // «полторы тысячи» = 1500
            if i + 1 < ws.len() {
                if let Some(s) = scale(ws[i + 1]) {
                    out.push(fmt(1.5 * s)); i += 2; continue;
                }
            }
            out.push("1.5".into()); i += 1; continue;
        }
        if !is_num_word(w) {
            out.push(w.to_string()); i += 1; continue;
        }
        let (mut v, n) = read_int(&ws, i).unwrap();
        let mut j = i + n;
        // «с половиной»
        if j + 1 < ws.len() && ws[j] == "с" && ws[j + 1].starts_with("половин") {
            v += 0.5; j += 2;
        }
        // «N целых M десятых/сотых», «N запятая M», «N точка M»
        if j < ws.len() && (ws[j].starts_with("цел") || ws[j] == "запятая" || ws[j] == "точка") {
            if let Some((m, k)) = read_int(&ws, j + 1) {
                let after = j + 1 + k;
                if ws[j].starts_with("цел") {
                    if after < ws.len() {
                        if let Some(d) = frac_div(ws[after]) {
                            v += m / d; j = after + 1;
                        }
                    }
                } else {
                    let digits_m = fmt(m);
                    v = format!("{}.{}", fmt(v), digits_m).parse().unwrap_or(v);
                    j = after;
                }
            }
        }
        out.push(fmt(v));
        i = j;
    }
    // «545 рублей 50 копеек» -> «545.50 рублей»
    let joined = out.join(" ");
    let toks: Vec<&str> = joined.split_whitespace().collect();
    let mut res: Vec<String> = Vec::new();
    let mut k = 0;
    while k < toks.len() {
        if k + 3 < toks.len() && toks[k].parse::<f64>().is_ok() && toks[k + 1].starts_with("рубл")
            && toks[k + 2].parse::<u32>().is_ok() && toks[k + 3].starts_with("копе")
        {
            let kop: u32 = toks[k + 2].parse().unwrap();
            if kop < 100 && !toks[k].contains('.') {
                res.push(format!("{}.{:02}", toks[k], kop));
                res.push(toks[k + 1].to_string());
                k += 4;
                continue;
            }
        }
        res.push(toks[k].to_string());
        k += 1;
    }
    res.join(" ")
}

#[cfg(test)]
mod tests {
    use super::digits;

    #[test]
    fn orders_from_voice_log() {
        assert_eq!(digits("подготовь заявку купи пять пик по пятьсот сорок пять"),
                   "подготовь заявку купи 5 пик по 545");
        assert_eq!(digits("подготовь заявку на закрытие пик по пятьсот шестьдесят рублей"),
                   "подготовь заявку на закрытие пик по 560 рублей");
        assert_eq!(digits("выставь лимит на заявку на продажу пик по пятьсот сорок рублей"),
                   "выставь лимит на заявку на продажу пик по 540 рублей");
    }

    #[test]
    fn forms() {
        assert_eq!(digits("купи десять сбер по двести восемьдесят пять"), "купи 10 сбер по 285");
        assert_eq!(digits("продай две тысячи сто лотов"), "продай 2100 лотов");
        assert_eq!(digits("по триста двенадцать рублей пятьдесят копеек"), "по 312.50 рублей");
        assert_eq!(digits("по сто двадцать целых пять десятых"), "по 120.5");
        assert_eq!(digits("по сто двадцать запятая семьдесят пять"), "по 120.75");
        assert_eq!(digits("полторы тысячи"), "1500");
        assert_eq!(digits("шестнадцать с половиной процентов"), "16.5 процентов");
        assert_eq!(digits("купи один лот газпрома"), "купи 1 лот газпрома");
        assert_eq!(digits("тысяча лотов"), "1000 лотов");
        assert_eq!(digits("до пятисот рублей"), "до 500 рублей");
        assert_eq!(digits("купи сбер"), "купи сбер");
        assert_eq!(digits("в девять ноль пять"), "в 9 0 5");
        assert_eq!(digits("в пятнадцать ноль ноль"), "в 15 0 0");
        assert_eq!(digits("в пятнадцать тридцать"), "в 15 30");
    }

    #[test]
    fn separate_numbers_stay_separate() {
        // «пять лотов по сорок» — два разных числа
        assert_eq!(digits("купи пять лотов по сорок"), "купи 5 лотов по 40");
        assert_eq!(digits("купи пять сорок"), "купи 5 40");
    }
}
