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

fn enabled() -> bool {
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
        // структура/шум
        "заявку", "заявка", "заявки", "ордер", "на", "по", "в", "и",
        "позицию", "позиции", "позиция", "позу", "сделку", "сделки", "сделка",
        "лот", "лота", "лотов", "акций", "акции", "акцию", "акция",
        "штук", "штуки", "штука", "бумаг", "бумаги", "бумагу",
        "тейк", "тейком", "тейкпрофит", "тейк-профит", "профит",
        "стоп", "стопом", "стопа", "стоп-лосс", "лосс",
        "с", "около", "примерно", "цене", "цену", "цена", "по-рыночной", "рыночной",
        "рублей", "рубль", "рубля", "руб", "рублям",
        "копеек", "копейки", "копейка", "коп",
    ];
    t.split_whitespace()
        .filter(|w| !DROP.contains(w) && parse_num(w).is_none())
        .collect::<Vec<_>>()
        .join(" ")
}

/// Разбор команды подготовки заявки: "купи 10 сбер по 280 тейк 300".
fn parse_order(t: &str) -> Option<String> {
    let side = if t.contains("прод") { "sell" } else { "buy" };
    let words: Vec<&str> = t.split_whitespace().collect();

    let price = number_after(&words, "по").unwrap_or(0.0);
    let tp = ["тейк", "тейком", "профит", "тейк-профит"]
        .iter()
        .find_map(|m| number_after(&words, m))
        .unwrap_or(0.0);

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

    let _ = tp; // тейк в заполнение виджета v1 не переносим
    let price_s = format!("{}", price);
    let qty_s = format!("{}", qty);
    // Заполняем виджет «Заявка» в терминале (тикер/объём/цена). Кнопку жмёт пользователь.
    Some(fetch_mind(
        "/fill_order",
        &[("q", inst.as_str()), ("side", side), ("qty", qty_s.as_str()),
          ("price", price_s.as_str()), ("close", "0")],
    ))
}

/// Если фраза — запрос к терминалу, обработать и вернуть текст для озвучки.
pub fn try_handle(text: &str) -> Option<String> {
    if !enabled() {
        return None;
    }
    let t = text.to_lowercase();

    // Закрытие позиции -> ГОТОВИМ заявку (объём берём из портфеля), не исполняем
    if t.contains("закр") && (t.contains("позиц") || t.contains("сделк")
        || t.contains("закрой ") || t.contains("закрыт"))
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

    // Явная попытка ИСПОЛНИТЬ сделку -> честный отказ (чтобы мозг не выдумывал, что выставил)
    let exec_intent = t.contains("выстави") || t.contains("выставь") || t.contains("поставь заявк")
        || t.contains("отправь заявк") || t.contains("исполни заявк") || t.contains("соверши сделк")
        || t.contains("открой сделк") || t.contains("продай всё") || t.contains("продай все")
        || t.contains("купи всё") || t.contains("купи все") || t.contains("сделай сделк");
    if exec_intent {
        return Some("Я не выставляю заявки и не совершаю сделок, сэр. Но могу их подготовить — скажите, например: подготовь продажу десяти акций Сбербанка по двести восемьдесят, или закрой позицию по Сбербанку. Кнопку в терминале нажимаете вы.".to_string());
    }

    // Боевая слежка: купон ГК Самолёт (SMLT) — погашение / техдефолт
    if t.contains("самол") && (t.contains("купон") || t.contains("дефолт") || t.contains("облига")
        || t.contains("погас") || t.contains("выплат"))
    {
        return Some(fetch_mind("/task_samolet", &[("on", "1")]));
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

    // Заметки: «запомни …» / «мои заметки»
    if t.starts_with("запомни") || t.contains("запомни что") {
        let body = after_any(&t, &["запомни что", "запомни"]);
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
        let body = after_any(&t, &["напомни мне", "напомни"]);
        return Some(fetch_at(NOTES_URL, "/remind", &[("text", body.trim())], "напоминания"));
    }

    // Открытие программ / сайтов / веб-поиск: «открой …», «запусти …», «найди …», «загугли …»
    if t.starts_with("открой") || t.starts_with("запусти") || t.starts_with("найди")
        || t.starts_with("загугли") || t.starts_with("поиск ") || t.starts_with("открыть")
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
        let body = after_any(&t, &[
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
        return parse_order(&t);
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
fn save_agenda(kind: &str, text: &str) -> String {
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
fn fetch_cal(path: &str, q: &str, timeout_secs: u64) -> String {
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
fn is_instrument(q: &str) -> bool {
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
fn fetch(path: &str, query: &[(&str, &str)]) -> String {
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
fn fetch_mag(path: &str, query: &[(&str, &str)]) -> String {
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
fn fetch_bls(path: &str, query: &[(&str, &str)]) -> String {
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

/// Универсальный GET к локальному сервису-помощнику (монитор/заметки и т.п.): берём поле "text".
#[cfg(feature = "reqwest")]
fn fetch_at(base_url: &str, path: &str, query: &[(&str, &str)], label: &str) -> String {
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
fn fetch_bea(path: &str, query: &[(&str, &str)]) -> String {
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
fn fetch_mind(path: &str, query: &[(&str, &str)]) -> String {
    let client = match reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(30))
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
