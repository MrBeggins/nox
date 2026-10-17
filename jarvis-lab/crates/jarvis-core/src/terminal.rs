//! T-terminal reader bridge — голосовые запросы про портфель, котировки, стакан,
//! статус торгов/аукционы и подготовку лимитных заявок.
//!
//! Обращается к локальному сервису `invest_reader` (http://127.0.0.1:8124),
//! который читает данные через официальный T-Invest API (**только чтение**).
//! Джарвис СМОТРИТ, ОЗВУЧИВАЕТ и ГОТОВИТ заявки — но сделок НЕ совершает
//! (выставляет их пользователь сам).

use std::time::Duration;

const READER_URL: &str = "http://127.0.0.1:8124";
const CAL_URL: &str = "http://127.0.0.1:8125";
const MIND_URL: &str = "http://127.0.0.1:8126"; // ридер терминала: новости ТТ + календарь MindStocks
const MAG_URL: &str = "http://127.0.0.1:8127"; // Russian Magellan PRO: order flow MOEX
const BLS_URL: &str = "http://127.0.0.1:8128"; // BLS: макростатистика США (нонфарм, CPI, PPI, JOLTS)
const BEA_URL: &str = "http://127.0.0.1:8129"; // BEA: ВВП и PCE США
const SYS_URL: &str = "http://127.0.0.1:8132"; // монитор нагрузки + открытие программ/сайтов/поиск
const NOTES_URL: &str = "http://127.0.0.1:8133"; // заметки и напоминания

/// Голосовые регрессионные проверки (`cargo test -p jarvis-core --lib voice_cases`): в тестовой сборке
/// сетевые вызовы не уходят, а записываются строкой «<адрес службы> <путь>?k=v&…» — тест сверяет,
/// куда и с какими параметрами ушла бы фраза. Ничего не исполняется. Кейсы: C:\jarvis-voice\voice_tests\rust_cases.json
#[cfg(test)]
pub(crate) mod test_hook {
    use std::cell::RefCell;
    thread_local! {
        pub static CALLS: RefCell<Vec<String>> = RefCell::new(Vec::new());
        pub static INSTRUMENTS: RefCell<Vec<String>> = RefCell::new(Vec::new());
    }
    pub fn record(svc: &str, path: &str, q: &[(&str, &str)]) -> String {
        let qs: Vec<String> = q.iter().map(|(k, v)| format!("{}={}", k, v)).collect();
        CALLS.with(|c| c.borrow_mut().push(format!("{} {}?{}", svc, path, qs.join("&"))));
        String::new()
    }
}

fn enabled() -> bool {
    #[cfg(test)]
    { return true; }
    #[allow(unreachable_code)]
    crate::DB
        .get()
        .and_then(|db| db.read().get("tterminal_enabled"))
        .map(|v| v == "true")
        .unwrap_or(false)
}

/// Текст после первого встреченного ключевого слова (для извлечения инструмента).
fn after_any(t: &str, keys: &[&str]) -> String {
    for k in keys {
        if let Some(idx) = t.find(k) {
            return t[idx + k.len()..]
                .trim()
                .trim_start_matches(|c: char| {
                    c == 'а' || c == 'и' || c == 'у' || c == 'е' || c == 'о' || c.is_whitespace()
                })
                .trim()
                .to_string();
        }
    }
    String::new()
}

/// Текст после ключевого слова КАК ЕСТЬ — для заметок, напоминаний и календаря. after_any срезает
/// гласные в начале (нужно для «стакана газпрома» -> «газпрома»), а в тексте заметки это портило
/// первое слово: «запиши в календарь отчёт сбера» -> «тчёт сбера».
fn after_any_raw(t: &str, keys: &[&str]) -> String {
    for k in keys {
        if let Some(idx) = t.find(k) {
            return t[idx + k.len()..].trim().trim_start_matches(|c: char| c == ':' || c == ',').trim().to_string();
        }
    }
    String::new()
}

/// Извлечь число, идущее после слова-маркера (например "по" -> цена).
fn number_after(words: &[&str], marker: &str) -> Option<f64> {
    for (i, w) in words.iter().enumerate() {
        if *w == marker {
            for n in words.iter().skip(i + 1) {
                if let Some(v) = parse_num(n) {
                    return Some(v);
                }
            }
        }
    }
    None
}

fn parse_num(s: &str) -> Option<f64> {
    let cleaned: String = s.chars().filter(|c| c.is_ascii_digit() || *c == '.' || *c == ',').collect();
    if cleaned.is_empty() {
        return None;
    }
    cleaned.replace(',', ".").parse::<f64>().ok()
}

/// Достать название инструмента из фразы: выкидываем слова-действия, служебные
/// слова, цену и числа — остаётся тикер/имя. Работает и для «пик», и для
/// «пик специализированный застройщик».
fn extract_instrument(t: &str) -> String {
    const DROP: &[&str] = &[
        // активация
        "нокс", "джарвис",
        // действия
        "подготовь", "подготовить", "подготовьте", "готовь", "готова", "готово",
        "купи", "купить", "куплю", "покупку", "покупка",
        "продай", "продать", "продам", "продажу", "продажа",
        "закрой", "закрыть", "закрытие", "закрывай", "закрою", "закрытия",
        "выстави", "выставь", "поставь", "отправь", "исполни", "соверши", "открой", "сделай",
        "всё", "все", "сейчас", "срочно",
        // структура/шум
        "заявку", "заявка", "заявки", "ордер", "на", "по", "в", "и",
        "позицию", "позиции", "позиция", "позу", "сделку", "сделки", "сделка",
        "лот", "лота", "лотов", "лотами", "плотов", "плота", "плот", // «плотов» — так Vosk слышит «лотов»
        "акций", "акции", "акцию", "акция",
        "штук", "штуки", "штука", "бумаг", "бумаги", "бумагу",
        "тейк", "тейком", "тейкпрофит", "тейк-профит", "профит",
        "стоп", "стопом", "стопа", "стоп-лосс", "лосс",
        "с", "около", "примерно", "цене", "цену", "цена", "по-рыночной", "рыночной",
        "рынку", "рынок", "рыночную", "маркет", "лимит", "лимитную", "лимитка", "лимиткой", "лимитной",
        "всей", "всю", "весь", "целиком", "полностью",
        "рублей", "рубль", "рубля", "руб", "рублям",
        "копеек", "копейки", "копейка", "коп",
    ];
    t.split_whitespace()
        .filter(|w| !DROP.contains(w) && parse_num(w).is_none())
        .collect::<Vec<_>>()
        .join(" ")
}

/// Разбор команды заявки: "купи 10 сбер по 280 тейк 300".
/// `execute = false` — только заполнить виджет; `true` — заполнить и нажать Купить/Продать
/// (ридер жмёт, только если в Настройках включено «Исполнять заявки голосом» и заявка в лимитах).
fn parse_order(t: &str, execute: bool) -> Option<String> {
    let side = if t.contains("прод") { "sell" } else { "buy" };
    let words: Vec<&str> = t.split_whitespace().collect();

    let price = number_after(&words, "по").unwrap_or(0.0);
    let tp = ["тейк", "тейком", "профит", "тейк-профит"]
        .iter()
        .find_map(|m| number_after(&words, m))
        .unwrap_or(0.0);

    // Vosk иногда теряет «по»: «пять плотов пика пятьсот сорок пять» — два числа без маркера цены:
    // первое — объём, последнее — цена.
    let nums: Vec<f64> = words.iter().filter_map(|w| parse_num(w)).filter(|v| *v != tp).collect();
    let price = if price == 0.0 && nums.len() >= 2 { *nums.last().unwrap() } else { price };

    // количество: первое число (не равное цене/тейку)
    let mut qty = 1i64;
    for w in &words {
        if let Some(v) = parse_num(w) {
            if v != price && v != tp {
                qty = v as i64;
                break;
            }
        }
    }
    if qty <= 0 {
        qty = 1;
    }

    // инструмент: единый экстрактор (выкидывает действия/шум/числа/цену)
    let inst = extract_instrument(t);
    if inst.trim().is_empty() {
        return Some("Уточните инструмент для заявки, сэр.".to_string());
    }

    let _ = tp; // тейк в заполнение виджета v1 переносим
    let price_s = format!("{}", price);
    let qty_s = format!("{}", qty);
    // Заполняем виджет «Заявка» (тикер/объём/цена); при execute ридер ещё и жмёт кнопку.
    // said — исходная фраза, ридер пишет её в журнал заявок.
    Some(fetch_mind(
        "/fill_order",
        &[("q", inst.as_str()), ("side", side), ("qty", qty_s.as_str()),
          ("price", price_s.as_str()), ("close", "0"),
          ("exec", if execute { "1" } else { "0" }), ("said", t)],
    ))
}

/// Если фраза — запрос к терминалу, обработать и вернуть текст для озвучки.
pub fn try_handle(text: &str) -> Option<String> {
    if !enabled() {
        return None;
    }
    // Vosk пишет числа словами: «купи пять пик по пятьсот сорок пять» -> «купи 5 пик по 545»
    let t = crate::numwords::digits(&text.to_lowercase());

    // Явная команда ИСПОЛНИТЬ -> заполнить виджет и нажать Купить/Продать.
    // Проверяем ДО «закрытия» и подготовки, иначе фразу перехватят ветки, которые только заполняют.
    // «купи всё» намеренно не здесь: объёма у него нет, а close=1 по длинной позиции дал бы ПРОДАЖУ.
    let exec_intent = t.contains("выстави") || t.contains("выставь") || t.contains("поставь заявк")
        || t.contains("отправь заявк") || t.contains("исполни заявк") || t.contains("соверши сделк")
        || t.contains("открой сделк") || t.contains("продай всё") || t.contains("продай все")
        || t.contains("сделай сделк");
    // «на продажу всей позиции», «позицию целиком» — это закрытие (объём и сторона — из портфеля)
    let whole_pos = t.contains("всей позиц") || t.contains("всю позиц") || t.contains("позицию целиком")
        || t.contains("позицию полностью");
    if exec_intent {
        // «продай всё X» / «выставь закрытие X» -> закрыть всю позицию (объём и сторона — из портфеля)
        let close_all = t.contains("продай всё") || t.contains("продай все") || t.contains("закр") || whole_pos;
        if close_all {
            let q = extract_instrument(&t);
            if q.trim().is_empty() {
                return Some("По какому инструменту закрыть позицию, сэр?".to_string());
            }
            let words: Vec<&str> = t.split_whitespace().collect();
            let price_s = format!("{}", number_after(&words, "по").unwrap_or(0.0));
            return Some(fetch_mind(
                "/fill_order",
                &[("q", q.as_str()), ("close", "1"), ("price", price_s.as_str()),
                  ("exec", "1"), ("said", t.as_str())],
            ));
        }
        return parse_order(&t, true);
    }

    // Закрытие позиции -> ГОТОВИМ заявку (объём берём из портфеля), не исполняем
    if whole_pos || (t.contains("закр") && (t.contains("позиц") || t.contains("сделк")
        || t.contains("закрой ") || t.contains("закрыт")))
    {
        let q = extract_instrument(&t);
        if q.trim().is_empty() {
            return Some("По какому инструменту закрыть позицию, сэр?".to_string());
        }
        // цена, если названа («по 560»); иначе рыночная
        let words: Vec<&str> = t.split_whitespace().collect();
        let price = number_after(&words, "по").unwrap_or(0.0);
        let price_s = format!("{}", price);
        return Some(fetch_mind(
            "/fill_order",
            &[("q", q.as_str()), ("close", "1"), ("price", price_s.as_str())],
        ));
    }

    // «объясни / про что / откуда последняя новость» -> берём текст из ленты и просим мозг объяснить
    if t.contains("новост")
        && (t.contains("объясн") || t.contains("поясни") || t.contains("про что")
            || t.contains("о чём") || t.contains("о чем") || t.contains("откуда")
            || t.contains("что за") || t.contains("расскажи") || t.contains("подробн"))
    {
        let news = fetch_mind("/last_news", &[]);
        if news.trim().is_empty() {
            return Some("Пока не вижу новостей в ленте, сэр.".to_string());
        }
        let prompt = format!(
            "Объясни простыми словами по-русски эту биржевую новость и почему она важна, 2-3 коротких предложения: {}",
            news
        );
        return Some(crate::brain::ask(&prompt).unwrap_or(news));
    }

    // ===================== ПОМОЩНИК: монитор ПК / открытие / заметки / напоминания =====================
    // Нагрузка/производительность компьютера
    if t.contains("нагрузк") || t.contains("загрузк компьютер") || t.contains("загружен компьютер")
        || t.contains("производительн") || t.contains("что грузит") || t.contains("загрузка цп")
        || (t.contains("сколько") && (t.contains("процессор") || t.contains("память") || t.contains("оператив")))
    {
        if (t.contains("выключи") || t.contains("отключи") || t.contains("не следи")) && t.contains("нагрузк") {
            return Some(fetch_at(SYS_URL, "/mon", &[("on", "0")], "монитор"));
        }
        if (t.contains("следи") || t.contains("включи")) && t.contains("нагрузк") {
            return Some(fetch_at(SYS_URL, "/mon", &[("on", "1")], "монитор"));
        }
        return Some(fetch_at(SYS_URL, "/stats", &[], "монитор"));
    }

    // Наблюдатели «мультимозг»: «наблюдай …», «следи весь день за …», «следи за новостью про …».
    // (Нагрузка/магеллан/монитор новостей сюда не попадают: нужен явный маркёр наблюдателя.)
    let watch_trigger = t.starts_with("наблюдай") || t.starts_with("последи") || t.starts_with("отслеживай")
        || t.contains("создай наблюдател") || t.contains("добавь наблюдател")
        || (t.starts_with("следи") && t.contains("весь день"))
        || ((t.contains("следи") || t.contains("лови") || t.contains("жди")
             || t.contains("сообщи когда") || t.contains("дай знать когда"))
            && t.contains("новост") && t.contains("про"));
    if watch_trigger {
        return Some(fetch_at(MIND_URL, "/watch_voice", &[("text", t.as_str())], "наблюдатель"));
    }

    // Интернет: живые данные и факты -> поиск через Яндекс-браузер + ответ мозгом
    let web_ask = t.contains("погод")
        || t.contains("курс ") || t.contains("курс доллар") || t.contains("курс евро")
        || t.contains("в интернете") || t.contains("в сети") || t.contains("поищи ")
        || t.contains("узнай ") || t.contains("посмотри в интернете") || t.contains("посмотри в сети")
        || t.contains("кто такой") || t.contains("кто такая") || t.contains("что такое");
    if web_ask {
        return Some(fetch_at(MIND_URL, "/web_answer", &[("q", t.as_str())], "интернет"));
    }

    // Заметки: «запомни …» / «мои заметки»
    if t.starts_with("запомни") || t.contains("запомни что") {
        let body = after_any_raw(&t, &["запомни что", "запомни"]);
        return Some(fetch_at(NOTES_URL, "/note_add", &[("text", body.trim())], "заметки"));
    }
    if (t.contains("мои заметк") || t.contains("покажи заметк") || t.contains("какие заметк")
        || t.contains("что я просил запомнить") || t.contains("прочитай заметк")) {
        return Some(fetch_at(NOTES_URL, "/notes", &[], "заметки"));
    }
    // Напоминания: «напомни …» / «мои напоминания»
    if t.contains("мои напоминани") || t.contains("покажи напоминани") || t.contains("какие напоминани") {
        return Some(fetch_at(NOTES_URL, "/reminders", &[], "напоминания"));
    }
    if t.starts_with("напомни") || t.contains("напомни мне") {
        let body = after_any_raw(&t, &["напомни мне", "напомни"]);
        return Some(fetch_at(NOTES_URL, "/remind", &[("text", body.trim())], "напоминания"));
    }

    // Открытие программ / сайтов / веб-поиск: «открой …», «запусти …», «найди …», «загугли …»
    // «открой стакан газпрома / портфель» — это терминал, а не программа (раньше: «не нашёл программу»)
    let terminal_word = t.contains("стакан") || t.contains("портфел") || t.contains("позици") || t.contains("заявк");
    if (t.starts_with("открой") || t.starts_with("запусти") || t.starts_with("найди")
        || t.starts_with("загугли") || t.starts_with("поиск ") || t.starts_with("открыть"))
        && !terminal_word
    {
        return Some(fetch_at(SYS_URL, "/open", &[("q", t.as_str())], "система"));
    }
    // ===================================================================================================

    // Календарь-заметки: «запиши / запланируй / добавь событие …» (GUI-календарь).
    // Пишем в общий nox_agenda.json (его читает GUI). Только запись, без исполнения.
    let agenda_trigger = t.contains("напоминание")
        || t.contains("запиши") || t.contains("заметк")
        || t.contains("в календарь") || t.contains("в календаре")
        || t.contains("запланируй") || t.contains("план на день") || t.contains("план на неделю")
        || t.contains("добавь событие") || t.contains("добавь напоминание")
        || t.contains("добавь команду") || t.contains("добавь задачу") || t.contains("добавь заметку");
    // не перехватываем настройку фильтра новостей
    let is_filter_cmd = t.contains("тикер") || t.contains("фраз") || t.contains("источник")
        || t.contains("следи за новост");
    if agenda_trigger && !is_filter_cmd {
        let body = after_any_raw(&t, &[
            "добавь событие", "добавь напоминание", "добавь команду", "добавь задачу", "добавь заметку",
            "запиши в календарь", "в календарь", "в календаре",
            "напомни мне", "напомни", "запиши", "запланируй",
            "напоминание", "заметку", "заметка", "событие",
        ]);
        let body = if body.trim().is_empty() { t.clone() } else { body };
        let kind = if t.contains("команд") || t.contains("заявк") || t.contains("ордер")
            || t.contains("стоп ") || t.contains("купи") || t.contains("продай") { "cmd" }
            else if t.contains("событ") || t.contains("отчёт") || t.contains("отчет")
                || t.contains("дивиденд") || t.contains("оферт") || t.contains("собрани")
                || t.contains("экспирац") || t.contains("заседани") || t.contains("ставк") { "evt" }
            else { "rem" };
        return Some(save_agenda(kind, body.trim()));
    }

    // Сценарии авто-клика: «настрой сценарий …» -> разобрать (координаты задаются в форме)
    if t.contains("настрой сценарий") || t.contains("добавь сценарий")
        || t.contains("создай сценарий") || (t.contains("сценарий") && t.contains("стакан"))
    {
        return Some(fetch_mind("/scn_parse", &[("text", t.as_str())]));
    }

    // Russian Magellan PRO — поток сделок / order flow
    if t.contains("магеллан") || t.contains("поток") || t.contains("order flow")
        || t.contains("ордер флоу") || t.contains("ордерфлоу")
    {
        // «магеллан следи за сбер» / «добавь в магеллан сбер» -> в список слежки
        if t.contains("следи") || t.contains("добавь") || t.contains("отслеживай") {
            let q = extract_instrument(&t);
            if !q.trim().is_empty() {
                return Some(fetch_mag("/filter_add", &[("kind", "ticker"), ("q", q.as_str())]));
            }
        }
        // по конкретной бумаге? (магеллан сам фаззи-резолвит имя, в т.ч. склонения;
        // если не распознает — вернёт общую сводку)
        let after = after_any(&t, &["поток сделок по", "поток по", "магеллан по", "магеллан",
            "поток сделок", "order flow", "ордер флоу"]);
        let after = after.trim();
        if after.len() >= 4 && after.split_whitespace().count() <= 3 {
            return Some(fetch_mag("/flow", &[("q", after)]));
        }
        // общая сводка потока
        return Some(fetch_mag("/pulse", &[("n", "5")]));
    }

    // Макростатистика США (BLS): нонфарм, CPI, PPI, безработица, JOLTS.
    // Должно идти РАНЬШЕ общего макро-блока, чтобы «инфляция в США» ушла сюда,
    // а «инфляция» без США — на русский календарь событий.
    let us_kw = t.contains("сша") || t.contains("америк") || t.contains("штат");
    let us_ind = t.contains("нонфарм") || t.contains("nonfarm") || t.contains("jolts")
        || t.contains("джолтс");
    // BEA: ВВП и PCE (отдельный сервис 8129)
    let bea_kw = t.contains("ввп") || t.contains("gdp") || t.contains("валов")
        || t.contains("pce") || t.contains("пи-си") || t.contains("пи си")
        || t.contains("личные доход") || t.contains("личных доход")
        || t.contains("личные расход") || t.contains("потребительск расход")
        || t.contains("доходы населени") || t.contains("расходы населени");
    if us_kw || us_ind || bea_kw {
        // вкл/выкл слежки за статистикой США (оба сервиса — BLS и BEA)
        if (t.contains("выключи") || t.contains("отключи") || t.contains("не следи")
            || t.contains("хватит") || t.contains("прекрати"))
            && (t.contains("сша") || t.contains("статистик") || us_ind || bea_kw)
        {
            return Some(us_join(fetch_bls("/mon", &[("on", "0")]), fetch_bea("/mon", &[("on", "0")])));
        }
        if (t.contains("включи") || t.contains("следи") || t.contains("отслеживай"))
            && (t.contains("сша") || t.contains("статистик") || us_ind || bea_kw)
        {
            return Some(us_join(fetch_bls("/mon", &[("on", "1")]), fetch_bea("/mon", &[("on", "1")])));
        }
        // календарь/расписание релизов — объединяем BLS + BEA (оба по расписанию, без ключа)
        if t.contains("календар") || t.contains("расписани") || t.contains("релиз")
            || t.contains("событ") || t.contains("что по")
        {
            return Some(us_join(fetch_bls("/calendar", &[("days", "25")]),
                                fetch_bea("/calendar", &[("days", "90")])));
        }
        // когда ближайший релиз — объединяем ближайшие из обоих
        if t.contains("когда") || t.contains("следующ") || t.contains("ближайш") || t.contains("скоро") {
            return Some(us_join(fetch_bls("/next", &[]), fetch_bea("/next", &[])));
        }
        // конкретный показатель: ВВП/PCE -> BEA; остальное -> BLS
        if bea_kw && !us_ind && !t.contains("нонфарм") && !t.contains("cpi")
            && !t.contains("ppi") && !t.contains("безработиц")
        {
            return Some(fetch_bea("/value", &[("q", t.as_str())]));
        }
        return Some(fetch_bls("/value", &[("q", t.as_str())]));
    }

    // Фильтр новостей: показать / очистить / добавить (тикер / источник / фраза)
    if t.contains("покажи фильтр") || t.contains("какой фильтр") || t.contains("что отслеживаешь")
        || t.contains("за чем следишь") || t.contains("список слежки")
    {
        return Some(fetch_mind("/filter", &[]));
    }
    if (t.contains("очист") || t.contains("сброс")) && (t.contains("фильтр") || t.contains("слеж")) {
        return Some(fetch_mind("/filter_clear", &[]));
    }
    if t.contains("следи за") || t.contains("следить за") || t.contains("отслеживай")
        || t.contains("реагируй на") || t.contains("добавь тикер") || t.contains("добавь фраз")
        || t.contains("добавь источник") || t.contains("ключевое слово")
    {
        let kind = if t.contains("тикер") { "ticker" }
                   else if t.contains("источник") { "source" }
                   else { "phrase" };
        return Some(fetch_mind("/filter_add", &[("kind", kind), ("q", t.as_str())]));
    }
    // Вкл/выкл автоматического чтения ленты
    if t.contains("выключи новости") || t.contains("отключи новости") || t.contains("не читай новости")
        || t.contains("хватит новост") || t.contains("стоп новости") || t.contains("прекрати читать новост")
    {
        return Some(fetch_mind("/mon", &[("on", "0")]));
    }
    if t.contains("включи новости") || t.contains("читай новости") || t.contains("читай ленту")
        || t.contains("автоновости") || t.contains("следи за новост")
    {
        return Some(fetch_mind("/mon", &[("on", "1")]));
    }

    // НОВОСТИ (лента Trading Tools из терминала). "новости" без "дивиденд" -> свежие заголовки.
    if t.contains("новост") && !t.contains("дивиденд") {
        let n = if t.contains("одн") || t.contains("главн") || t.contains("последн") || t.contains("коротк") {
            "3"
        } else {
            "5"
        };
        return Some(fetch_mind("/news", &[("n", n)]));
    }

    // КАЛЕНДАРЬ СОБЫТИЙ (MindStocks из терминала): корп. события + макро.
    let event_kw = t.contains("календар") || t.contains("событ") || t.contains("дивиденд")
        || t.contains("отчёт") || t.contains("отчет") || t.contains("оферт") || t.contains("выкуп")
        || t.contains("допэмисс") || t.contains("доп эмисс") || t.contains("эмисси")
        || t.contains("экспирац") || t.contains("собрани") || t.contains("госа") || t.contains("воса")
        || t.contains("ipo") || t.contains("spo") || t.contains("размещени")
        || t.contains("купон") || t.contains("погашени");
    let macro_kw = ["ввп", "gdp", "инфляц", "цб", "заседани", "макроэконом"];
    let rate_kw = t.contains("ставк")
        && (t.contains("цб") || t.contains("фрс") || t.contains("ключев") || t.contains("ецб"));
    if event_kw || rate_kw || macro_kw.iter().any(|k| t.contains(k)) {
        return Some(fetch_mind("/events", &[("q", t.as_str())]));
    }

    // Подготовка заявки (самое специфичное — проверяем первым)
    let is_order = (t.contains("подготов") || t.contains("готов") || t.starts_with("купи")
        || t.starts_with("продай"))
        && (t.contains("куп") || t.contains("прод") || t.contains("заявк"));
    if is_order {
        return parse_order(&t, false);
    }

    // Стакан
    if t.contains("стакан") {
        let q = after_any(&t, &["стакан по", "стакан"]);
        return Some(fetch("/orderbook", &[("q", q.as_str())]));
    }

    // Статус торгов / аукцион
    if t.contains("статус торг") || t.contains("аукцион") || t.contains("идут ли торг")
        || t.contains("торги идут") || t.contains("торгуется")
    {
        let q = after_any(&t, &["статус торгов по", "статус торгов", "аукцион по",
            "аукцион", "торгуется", "торги по"]);
        return Some(fetch("/status", &[("q", q.as_str())]));
    }

    // Портфель / счёт (по корню «портфел» — ловит «портфель/портфеля/портфелю»)
    if t.contains("портфел") || t.contains("на счету") || t.contains("на счёте")
        || t.contains("на счете") || t.contains("по счёт") || t.contains("по счет")
        || t.contains("баланс") || t.contains("мои акции") || t.contains("мои активы")
        || t.contains("мои позиц") || t.contains("сколько у меня")
        || t.contains("состояние счет") || t.contains("состояние счёт")
    {
        return Some(fetch("/portfolio", &[]));
    }

    // Котировка
    for kw in ["котировк", "курс", "почём", "почем", "сколько стоит", "цена"] {
        if let Some(idx) = t.find(kw) {
            let rest = t[idx + kw.len()..]
                .trim()
                .trim_start_matches(|c: char| c == 'а' || c == 'и' || c == 'у' || c.is_whitespace())
                .trim()
                .to_string();
            if !rest.is_empty() {
                return Some(fetch("/quote", &[("q", rest.as_str())]));
            }
        }
    }

    // Голое короткое название инструмента без ключевого слова ("микс", "норка"):
    // строго проверяем в каталоге и, если это инструмент, отдаём котировку.
    let words = t.split_whitespace().count();
    if (1..=3).contains(&words) && !t.chars().any(|c| c.is_ascii_digit()) && is_instrument(&t) {
        return Some(fetch("/quote", &[("q", t.as_str())]));
    }

    None
}

/// Записать голосовую заметку в общий агенда-файл (его читает GUI-Календарь).
#[allow(unreachable_code)]
fn save_agenda(kind: &str, text: &str) -> String {
    #[cfg(test)]
    { return test_hook::record("agenda", kind, &[("text", text)]); }
    use std::time::{SystemTime, UNIX_EPOCH};
    let dir = crate::APP_CONFIG_DIR
        .get()
        .cloned()
        .unwrap_or_else(|| std::path::PathBuf::from("."));
    let path = dir.join("nox_agenda.json");
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0);
    let mut arr: Vec<serde_json::Value> = std::fs::read_to_string(&path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default();
    let item = serde_json::json!({
        "id": format!("v{}", now),
        "kind": kind,
        "text": text,
        "when": "",
        "done": false,
        "created": now,
        "source": "voice"
    });
    arr.insert(0, item);
    let _ = std::fs::write(&path, serde_json::to_string_pretty(&arr).unwrap_or_default());
    let label = match kind {
        "cmd" => "Команда",
        "evt" => "Событие",
        _ => "Напоминание",
    };
    format!("{} записано: {}, сэр.", label, text)
}

#[cfg(feature = "reqwest")]
#[allow(unreachable_code)]
fn fetch_cal(path: &str, q: &str, timeout_secs: u64) -> String {
    #[cfg(test)]
    { return test_hook::record(CAL_URL, path, &[("q", q)]); }
    let client = match reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(timeout_secs))
        .build()
    {
        Ok(c) => c,
        Err(_) => return "Сервис календаря недоступен, сэр.".to_string(),
    };
    let base = format!("{}{}", CAL_URL, path);
    let url = match reqwest::Url::parse_with_params(&base, &[("q", q)]) {
        Ok(u) => u,
        Err(_) => return "Ошибка адреса календаря, сэр.".to_string(),
    };
    match client.get(url).send().and_then(|r| r.json::<serde_json::Value>()) {
        Ok(v) => v["text"].as_str().unwrap_or("Пустой ответ календаря, сэр.").to_string(),
        Err(_) => "Календарь недоступен, сэр. Запущен ли сервис календаря?".to_string(),
    }
}

#[cfg(not(feature = "reqwest"))]
fn fetch_cal(_path: &str, _q: &str, _timeout_secs: u64) -> String {
    "Экономический календарь недоступен в этой сборке.".to_string()
}

#[cfg(feature = "reqwest")]
#[allow(unreachable_code)]
fn is_instrument(q: &str) -> bool {
    #[cfg(test)]
    { return test_hook::INSTRUMENTS.with(|i| i.borrow().iter().any(|x| x == q)); }
    let client = match reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(6))
        .build()
    {
        Ok(c) => c,
        Err(_) => return false,
    };
    let base = format!("{}/resolve", READER_URL);
    let url = match reqwest::Url::parse_with_params(&base, &[("q", q)]) {
        Ok(u) => u,
        Err(_) => return false,
    };
    match client.get(url).send().and_then(|r| r.json::<serde_json::Value>()) {
        Ok(v) => v["found"].as_bool().unwrap_or(false),
        Err(_) => false,
    }
}

#[cfg(not(feature = "reqwest"))]
fn is_instrument(_q: &str) -> bool {
    false
}

#[cfg(feature = "reqwest")]
#[allow(unreachable_code)]
fn fetch(path: &str, query: &[(&str, &str)]) -> String {
    #[cfg(test)]
    { return test_hook::record(READER_URL, path, query); }
    let client = match reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(30))
        .build()
    {
        Ok(c) => c,
        Err(_) => return "Сервис терминала недоступен, сэр.".to_string(),
    };
    let base = format!("{}{}", READER_URL, path);
    let url = match reqwest::Url::parse_with_params(&base, query) {
        Ok(u) => u,
        Err(_) => return "Ошибка адреса терминала, сэр.".to_string(),
    };
    match client.get(url).send().and_then(|r| r.json::<serde_json::Value>()) {
        Ok(v) => v["text"]
            .as_str()
            .unwrap_or("Пустой ответ терминала, сэр.")
            .to_string(),
        Err(_) => "Сервис терминала недоступен, сэр. Запущен ли читатель?".to_string(),
    }
}

#[cfg(not(feature = "reqwest"))]
fn fetch(_path: &str, _query: &[(&str, &str)]) -> String {
    "Чтение терминала недоступно в этой сборке.".to_string()
}

#[cfg(feature = "reqwest")]
#[allow(unreachable_code)]
fn fetch_mag(path: &str, query: &[(&str, &str)]) -> String {
    #[cfg(test)]
    { return test_hook::record(MAG_URL, path, query); }
    let client = match reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(15))
        .build()
    {
        Ok(c) => c,
        Err(_) => return "Сервис Магеллана недоступен, сэр.".to_string(),
    };
    let base = format!("{}{}", MAG_URL, path);
    let url = match reqwest::Url::parse_with_params(&base, query) {
        Ok(u) => u,
        Err(_) => return "Ошибка адреса Магеллана, сэр.".to_string(),
    };
    match client.get(url).send().and_then(|r| r.json::<serde_json::Value>()) {
        Ok(v) => v["text"].as_str().unwrap_or("Пустой ответ Магеллана, сэр.").to_string(),
        Err(_) => "Магеллан недоступен, сэр. Запущен ли magellan_reader?".to_string(),
    }
}

#[cfg(not(feature = "reqwest"))]
fn fetch_mag(_path: &str, _query: &[(&str, &str)]) -> String {
    "Магеллан недоступен в этой сборке.".to_string()
}

#[cfg(feature = "reqwest")]
#[allow(unreachable_code)]
fn fetch_bls(path: &str, query: &[(&str, &str)]) -> String {
    #[cfg(test)]
    { return test_hook::record(BLS_URL, path, query); }
    let client = match reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(30))
        .build()
    {
        Ok(c) => c,
        Err(_) => return "Сервис статистики США недоступен, сэр.".to_string(),
    };
    let base = format!("{}{}", BLS_URL, path);
    let url = match reqwest::Url::parse_with_params(&base, query) {
        Ok(u) => u,
        Err(_) => return "Ошибка адреса статистики США, сэр.".to_string(),
    };
    match client.get(url).send().and_then(|r| r.json::<serde_json::Value>()) {
        Ok(v) => v["text"].as_str().unwrap_or("Пустой ответ, сэр.").to_string(),
        Err(_) => "Статистика США недоступна, сэр. Запущен ли bls_reader?".to_string(),
    }
}

/// Роутер «мозг-первым»: запрос -> /route (мозг решает действие). Возвращает ответ для
/// озвучки, либо None если это команда программе («__LOCAL__») или сервис недоступен —
/// тогда вызывающий идёт обычным путём (команды/терминал/мозг-фоллбэк).
#[cfg(feature = "reqwest")]
pub fn route(text: &str) -> Option<String> {
    if !enabled() {
        return None;
    }
    // роутер сам укладывается в ~10 с (облако 9 с с дублем, затем Ollama) — 30 с ожидания больше не нужно
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(15))
        .build()
        .ok()?;
    let url = reqwest::Url::parse_with_params(&format!("{}/route", MIND_URL), &[("text", text)]).ok()?;
    let v: serde_json::Value = client.get(url).send().ok()?.json().ok()?;
    let a = v["text"].as_str().unwrap_or("").trim().to_string();
    if a.is_empty() || a == "__LOCAL__" || a.contains("недоступен") {
        None
    } else {
        Some(a)
    }
}
#[cfg(not(feature = "reqwest"))]
pub fn route(_text: &str) -> Option<String> {
    None
}

/// Универсальный GET к локальному сервису-помощнику (монитор/заметки и т.п.): берём поле "text".
#[cfg(feature = "reqwest")]
#[allow(unreachable_code)]
fn fetch_at(base_url: &str, path: &str, query: &[(&str, &str)], label: &str) -> String {
    #[cfg(test)]
    { return test_hook::record(base_url, path, query); }
    let client = match reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(20))
        .build()
    {
        Ok(c) => c,
        Err(_) => return format!("Сервис ({}) недоступен, сэр.", label),
    };
    let url = match reqwest::Url::parse_with_params(&format!("{}{}", base_url, path), query) {
        Ok(u) => u,
        Err(_) => return format!("Ошибка адреса ({}), сэр.", label),
    };
    match client.get(url).send().and_then(|r| r.json::<serde_json::Value>()) {
        Ok(v) => v["text"].as_str().unwrap_or("Готово, сэр.").to_string(),
        Err(_) => format!("Сервис ({}) недоступен, сэр.", label),
    }
}

#[cfg(not(feature = "reqwest"))]
fn fetch_at(_b: &str, _p: &str, _q: &[(&str, &str)], _l: &str) -> String {
    "Недоступно в этой сборке.".to_string()
}

#[cfg(not(feature = "reqwest"))]
fn fetch_bls(_path: &str, _query: &[(&str, &str)]) -> String {
    "Статистика США недоступна в этой сборке.".to_string()
}

#[cfg(feature = "reqwest")]
#[allow(unreachable_code)]
fn fetch_bea(path: &str, query: &[(&str, &str)]) -> String {
    #[cfg(test)]
    { return test_hook::record(BEA_URL, path, query); }
    let client = match reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(30))
        .build()
    {
        Ok(c) => c,
        Err(_) => return "Сервис ВВП и PCE недоступен, сэр.".to_string(),
    };
    let base = format!("{}{}", BEA_URL, path);
    let url = match reqwest::Url::parse_with_params(&base, query) {
        Ok(u) => u,
        Err(_) => return "Ошибка адреса сервиса ВВП, сэр.".to_string(),
    };
    match client.get(url).send().and_then(|r| r.json::<serde_json::Value>()) {
        Ok(v) => v["text"].as_str().unwrap_or("Пустой ответ, сэр.").to_string(),
        Err(_) => "Данные ВВП и PCE недоступны, сэр. Запущен ли bea_reader?".to_string(),
    }
}

#[cfg(not(feature = "reqwest"))]
fn fetch_bea(_path: &str, _query: &[(&str, &str)]) -> String {
    "Данные ВВП и PCE недоступны в этой сборке.".to_string()
}

/// Объединяет ответы двух сервисов США (BLS + BEA) в одну реплику, пропуская пустые/ошибочные.
fn us_join(a: String, b: String) -> String {
    let bad = |s: &str| s.contains("недоступ") || s.contains("Пустой ответ") || s.trim().is_empty();
    match (bad(&a), bad(&b)) {
        (false, false) => format!("{} {}", a.trim_end(), b.trim_start()),
        (false, true) => a,
        (true, false) => b,
        (true, true) => a, // оба недоступны — вернём первый (осмысленная ошибка BLS)
    }
}

#[cfg(feature = "reqwest")]
#[allow(unreachable_code)]
fn fetch_mind(path: &str, query: &[(&str, &str)]) -> String {
    #[cfg(test)]
    { return test_hook::record(MIND_URL, path, query); }
    // 70с > таймаутов ридера (заполнение 40с + нажатие 20с): иначе Нокс скажет «недоступен»,
    // а заявка в это время уйдёт
    let client = match reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(70))
        .build()
    {
        Ok(c) => c,
        Err(_) => return "Читатель терминала недоступен, сэр.".to_string(),
    };
    let base = format!("{}{}", MIND_URL, path);
    let url = match reqwest::Url::parse_with_params(&base, query) {
        Ok(u) => u,
        Err(_) => return "Ошибка адреса читателя, сэр.".to_string(),
    };
    match client.get(url).send().and_then(|r| r.json::<serde_json::Value>()) {
        Ok(v) => v["text"].as_str().unwrap_or("Пустой ответ, сэр.").to_string(),
        Err(_) => "Читатель терминала недоступен, сэр. Открыт ли терминал в окне Джарвиса?".to_string(),
    }
}

#[cfg(not(feature = "reqwest"))]
fn fetch_mind(_path: &str, _query: &[(&str, &str)]) -> String {
    "Чтение недоступно в этой сборке.".to_string()
}

/// Голосовые регрессионные проверки: фразы из C:\jarvis-voice\voice_tests\rust_cases.json прогоняются через
/// настоящий try_handle; сеть подменена test_hook. Кейс: {"say", "expect": [подстроки вызова/ответа],
/// "not": [чего быть не должно], "instruments": [что считать инструментом для голого названия], "none": true}.
/// Отчёт — C:\jarvis-voice\voice_tests\rust_report.json (его читает run_tests.py).
#[cfg(test)]
mod voice_cases {
    use super::*;

    fn run(say: &str, instruments: &[String]) -> (Option<String>, Vec<String>) {
        test_hook::CALLS.with(|c| c.borrow_mut().clear());
        test_hook::INSTRUMENTS.with(|i| *i.borrow_mut() = instruments.to_vec());
        let reply = try_handle(say);
        let calls = test_hook::CALLS.with(|c| c.borrow().clone());
        (reply, calls)
    }

    #[test]
    fn voice_cases() {
        let path = r"C:\jarvis-voice\voice_tests\rust_cases.json";
        let data = std::fs::read_to_string(path).expect("нет файла кейсов");
        let cases: Vec<serde_json::Value> = serde_json::from_str(&data).expect("кейсы — не JSON");
        let mut report = Vec::new();
        let mut fails = 0;
        for c in &cases {
            let say = c["say"].as_str().unwrap_or("");
            let inst: Vec<String> = c["instruments"].as_array().map(|a| a.iter()
                .filter_map(|x| x.as_str().map(|s| s.to_string())).collect()).unwrap_or_default();
            let (reply, calls) = run(say, &inst);
            let hay = format!("{} || {}", calls.join(" | "), reply.clone().unwrap_or_default());
            let mut problems: Vec<String> = Vec::new();
            for e in c["expect"].as_array().into_iter().flatten().filter_map(|x| x.as_str()) {
                if !hay.contains(e) { problems.push(format!("нет «{}»", e)); }
            }
            for n in c["not"].as_array().into_iter().flatten().filter_map(|x| x.as_str()) {
                if hay.contains(n) { problems.push(format!("лишнее «{}»", n)); }
            }
            if c["none"].as_bool() == Some(true) && (reply.is_some() || !calls.is_empty()) {
                problems.push("должно уйти дальше (None), а перехвачено".into());
            }
            if !problems.is_empty() { fails += 1; }
            report.push(serde_json::json!({"say": say, "ok": problems.is_empty(), "problems": problems,
                                           "calls": calls, "reply": reply}));
        }
        let _ = std::fs::write(r"C:\jarvis-voice\voice_tests\rust_report.json",
                               serde_json::to_string_pretty(&report).unwrap_or_default());
        println!("голосовые кейсы Rust: {} из {} прошли", cases.len() - fails, cases.len());
        assert_eq!(fails, 0, "провалено кейсов: {} (см. rust_report.json)", fails);
    }
}
