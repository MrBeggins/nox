# -*- coding: utf-8 -*-
"""
«Мультимозг» — наблюдатели Nox.
Каждый наблюдатель прикован к ОДНОЙ цели: весь день (или до заданного часа) ждёт
свою новость и больше ничего не делает. У каждого — свой мозг (модель). При срабатывании:
озвучивает новость и запускает привязанный сценарий (открытие стакана «взведённой заявки»).
Несколько наблюдателей работают параллельно — отсюда «мультимозг».

Хранилище: C:\\jarvis-voice\\nox_watchers.json
Вызывается из terminal_reader (монитор ленты) и по HTTP (GUI/голос).

Наблюдатель с ВЕТКАМИ (2026-10-08): своя вкладка терминала `tab` с виджетами «Заявка»,
`event` — какое число брать из новости, `branches` — условия и заявки ветки:
  {"type":"gt|ge|lt|le|range|none|any","min":..,"max":..,"orders":[{"ticker","n","side","lots","price"}]}
При новости: число -> ветка -> EXECUTOR нажимает кнопки виджетов ветки (nox_widgets.press_orders).
Заявка с "venue":"cscalp" (2026-10-12) — не виджет Т-Терминала, а стакан CScalp по тикеру (SILVM-12.26):
горячая клавиша CScalp, только по рынку, объём — тот, что стоит в стакане (nox_cscalp).
Тип "rate" (2026-10-12) — РЕШЕНИЕ ПО СТАВКЕ ЦБ/ФРС: заголовок разбирается ПРАВИЛАМИ (nox_rates, ~0.1 мс, без мозга),
значение для веток = изменение ставки в б.п. (снижение — минус, сохранение — 0). Поля: bank cbr|fed,
date ГГГГ-ММ-ДД, from ЧЧ:ММ:СС (МСК; раньше — новости не принимаются), rate_now (текущая ставка, запасной
вариант для заголовков без «на N б.п.»). Ключевые слова этому типу не нужны.
Ветка без заявок = «ничего не делать». lots/price в заявке — только для утренней раскладки Ноксом.
"""
import json, os, re, time, uuid, threading
import urllib.request, urllib.parse
import nox_scenarios  # переиспользуем озвучку, извлечение числа, условия веток и время МСК

STORE = r"C:\jarvis-voice\nox_watchers.json"

# Подключаются terminal_reader'ом (там живёт браузер терминала):
#   EXECUTOR(w, branch, news_text) -> {"text": отчёт}   — нажать кнопки ветки
#   LAYOUT(w) -> {"text": отчёт}                         — разложить виджеты на вкладке
EXECUTOR = None
LAYOUT = None

# общая рабочая вкладка терминала (лента новостей + заготовки «Заявка»): все наблюдатели — на ней,
# если явно не сказано другое. Раньше под каждого наблюдателя создавалась своя вкладка.
DEFAULT_TAB = "нокс"

def _now():
    try:
        return nox_scenarios._now_msk()
    except Exception:
        import datetime as dt
        return dt.datetime.utcnow() + dt.timedelta(hours=3)

def load():
    try:
        d = json.load(open(STORE, encoding="utf-8"))
        if "watchers" not in d:
            d = {"watchers": []}
    except Exception:
        d = {"watchers": []}
    return d

def save(d):
    try:
        json.dump(d, open(STORE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass

# ------------------------------------------------------------------ CRUD
def add(name, keywords, source="all", brain="", scenario_id="", until="", once=True):
    d = load()
    kws = [k.lower().strip() for k in keywords if k and k.strip()]
    w = {"id": uuid.uuid4().hex[:8],
         "name": (name or " ".join(kws) or "наблюдатель").strip(),
         "keywords": kws, "source": source or "all", "brain": brain or "",
         "scenario_id": scenario_id or "", "until": until or "",
         "once": bool(once), "enabled": True,
         "created": time.time(), "last_fired": 0, "fired_day": ""}
    d["watchers"].append(w); save(d)
    return w

def remove(wid):
    d = load(); n = len(d["watchers"])
    d["watchers"] = [w for w in d["watchers"] if str(w.get("id")) != str(wid)]
    save(d); return len(d["watchers"]) < n

def toggle(wid):
    d = load(); st = None
    for w in d["watchers"]:
        if str(w.get("id")) == str(wid):
            w["enabled"] = not w.get("enabled", True); st = w["enabled"]
            # повторное включение «once»-наблюдателя сбрасывает отметку срабатывания
            if st and w.get("once"): w["fired_day"] = ""
    save(d); return st

def _scn_name(sid):
    if not sid:
        return ""
    try:
        for s in nox_scenarios.load().get("scenarios", []):
            if str(s.get("id")) == str(sid):
                return s.get("name", "")
    except Exception:
        pass
    return ""

def list_public():
    d = load()
    out = []
    for w in d["watchers"]:
        out.append({"id": w["id"], "name": w["name"], "keywords": w["keywords"],
                    "source": w.get("source", "all"), "brain": w.get("brain", ""),
                    "scenario_id": w.get("scenario_id", ""), "scenario_name": _scn_name(w.get("scenario_id", "")),
                    "until": w.get("until", ""), "once": w.get("once", True),
                    "enabled": w.get("enabled", True), "fired_day": w.get("fired_day", ""),
                    "tab": w.get("tab", ""), "event": w.get("event", ""),
                    "branches": w.get("branches", []), "last_result": w.get("last_result", ""),
                    "kind": w.get("kind", ""), "bank": w.get("bank", ""), "date": w.get("date", ""),
                    "from": w.get("from", ""), "rate_now": w.get("rate_now")})
    return {"watchers": out}

def get(wid):
    return next((w for w in load()["watchers"] if str(w.get("id")) == str(wid)), None)

_TYPES = {"gt", "ge", "lt", "le", "range", "none", "any"}

def _clean_branches(branches):
    """Нормализовать ветки из GUI/голоса: типы, числа, заявки (тикер в верхнем регистре, № >= 1, сторона)."""
    out = []
    for b in branches or []:
        t = b.get("type") if b.get("type") in _TYPES else "any"
        num = lambda v: (None if v in (None, "") else float(str(v).replace(",", ".")))
        orders = []
        for o in b.get("orders") or []:
            cs = str(o.get("venue") or "").lower() in ("cscalp", "скальп", "cs")
            tk = re.sub(r"\s+", "", str(o.get("ticker") or "")).upper()
            if not tk:
                continue
            side = "sell" if str(o.get("side")).lower() in ("sell", "продать", "short", "шорт") else "buy"
            if cs:   # стакан CScalp: только тикер и сторона (по рынку, объём — в стакане)
                tk = re.sub(r"^([A-Z]+)-?(\d{1,2}\.\d{2})$", r"\1-\2", tk)   # «SILVM 3.27» -> SILVM-3.27
                orders.append({"ticker": tk, "n": 1, "side": side, "lots": None, "price": None, "venue": "cscalp"})
                continue
            orders.append({"ticker": tk, "n": max(1, int(o.get("n") or 1)), "side": side,
                           "lots": (int(o["lots"]) if str(o.get("lots") or "").strip().isdigit() else None),
                           "price": num(o.get("price"))})
        mn, mx = num(b.get("min")), num(b.get("max"))
        # порог в «правильное» поле: больше/не меньше -> min, меньше/не больше -> max (мозг их путает)
        if t in ("gt", "ge") and mn is None and mx is not None:
            mn, mx = mx, None
        if t in ("lt", "le") and mx is None and mn is not None:
            mn, mx = None, mn
        if t == "range" and mn is not None and mx is not None and mn > mx:
            mn, mx = mx, mn
        out.append({"type": t, "min": mn, "max": mx, "orders": orders})
    return out

def upsert(data):
    """Создать/обновить наблюдателя целиком (GUI). data — dict с полями наблюдателя."""
    d = load()
    kws = data.get("keywords") or []
    if isinstance(kws, str):
        kws = re.split(r"[,; ]+", kws)
    kws = [k.lower().strip() for k in kws if k and k.strip()]
    kind = "rate" if data.get("kind") == "rate" else ""
    if kind and not kws:
        kws = ["ставка"]          # для подписи: тип «ставка» ловит решения парсером, не словами
    if not kws:
        return {"ok": False, "text": "Укажите, что ловить (ключевые слова)."}
    bank = data.get("bank") if data.get("bank") in ("cbr", "fed") else ""
    if kind and not bank:
        return {"ok": False, "text": "Для решения по ставке выберите банк: ЦБ или ФРС."}
    try:
        rate_now = float(str(data.get("rate_now")).replace(",", ".")) if str(data.get("rate_now") or "").strip() else None
    except ValueError:
        rate_now = None
    t_from = (data.get("from") or "").strip()
    if t_from and re.fullmatch(r"\d{1,2}:\d{2}", t_from):
        t_from += ":00"
    if t_from and not re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", t_from):
        return {"ok": False, "text": "Время «с» — в формате ЧЧ:ММ:СС (МСК), например 13:30:00."}
    if t_from and len(t_from) == 7:
        t_from = "0" + t_from
    if kind and not t_from:
        t_from = "13:30:00" if bank == "cbr" else "21:00:00"
    date = (data.get("date") or "").strip()
    if date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return {"ok": False, "text": "Дата — в формате ГГГГ-ММ-ДД."}
    fields = {"name": (data.get("name") or " ".join(kws)).strip(), "keywords": kws,
              "source": data.get("source") or "all", "brain": data.get("brain") or "",
              "scenario_id": data.get("scenario_id") or "", "until": (data.get("until") or "").strip(),
              "once": bool(data.get("once", True)), "tab": (data.get("tab") or "").strip(),
              "event": (data.get("event") or "").strip(), "branches": _clean_branches(data.get("branches")),
              "group": (data.get("group") or "").strip(), "delete_after": bool(data.get("delete_after", False)),
              "kind": kind, "bank": bank, "date": date, "from": t_from, "rate_now": rate_now}
    if fields["branches"] and not fields["tab"]:
        fields["tab"] = DEFAULT_TAB
    w = next((x for x in d["watchers"] if str(x.get("id")) == str(data.get("id") or "")), None)
    if w:
        w.update(fields)
    else:
        w = {"id": uuid.uuid4().hex[:8], "enabled": data.get("enabled", True) is not False, "created": time.time(),
             "last_fired": 0, "fired_day": "", **fields}
        d["watchers"].append(w)
    save(d)
    return {"ok": True, "id": w["id"], "text": f"Наблюдатель «{w['name']}» сохранён."}

# ------------------------------------------------------------------ голосовой разбор
_STOP = {"следи", "следить", "наблюдай", "наблюдать", "последи", "смотри", "весь", "день",
         "сегодня", "за", "новостью", "новостями", "новости", "новость", "про", "о", "об",
         "нокс", "пожалуйста", "а", "и", "также", "тоже", "только", "лишь", "будет", "выйдет"}

def parse_watch(text):
    """«следи весь день за дивидендами Сбера до 19 сценарий Сбер» -> keywords/until/scenario."""
    low = (text or "").strip().lower()
    scenario_name = ""
    m = re.search(r"сценари[йяю]\s+(.+)$", low)
    if m:
        scenario_name = m.group(1).strip(); low = low[:m.start()].strip()
    until = ""
    m = re.search(r"до\s+(\d{1,2})(?:[:.](\d{2}))?\s*(?:час\w*)?", low)
    if m:
        hh = min(23, int(m.group(1))); mm = min(59, int(m.group(2) or 0))
        until = f"{hh:02d}:{mm:02d}"; low = (low[:m.start()] + " " + low[m.end():]).strip()
    words = re.findall(r"[a-zа-яё0-9\-]+", low)
    kws = [w for w in words if w not in _STOP and len(w) >= 3]
    return {"keywords": kws, "scenario_name": scenario_name, "until": until, "name": " ".join(kws)}

def _find_scenario(name):
    if not name:
        return None
    nl = name.lower().strip()
    try:
        for s in nox_scenarios.load().get("scenarios", []):
            if nl in (s.get("name", "").lower()) or (s.get("name", "").lower()) in nl:
                return s
    except Exception:
        pass
    return None

_WATCH_SYS = (
    "Пользователь голосом ставит задачу «следить за новостью в биржевой ленте» и, возможно, "
    "описывает ветки: при каком значении из новости какие заявки исполнить. "
    "Извлеки СТРОГИЙ JSON без пояснений:\n"
    "{\"name\":\"<короткое название 2-4 слова, напр. «Лукойл · дивиденды»>\","
    "\"keywords\":[\"<ключевые слова темы в начальной форме>\"],\"until\":\"<ЧЧ:ММ или пусто>\","
    "\"scenario\":\"<название сценария или пусто>\",\"event\":\"<какое число брать из новости: дивиденд/ставка/... или пусто>\","
    "\"tab\":\"<вкладка терминала, если сказано «на вкладке X», иначе пусто>\","
    "\"branches\":[{\"type\":\"gt|ge|lt|le|range|none|any\",\"min\":число|null,\"max\":число|null,"
    "\"orders\":[{\"name\":\"<компания или тикер>\",\"kind\":\"share|future\",\"month\":число|null,"
    "\"side\":\"buy|sell\",\"lots\":число|null,\"price\":число|null,\"venue\":\"cscalp или пусто\"}]}]}\n"
    "type: gt — больше min; lt — меньше max; range — от min до max (min — меньшее число); "
    "none — выплаты/события нет; any — любое.\n"
    "kind=future — если сказано «фьючерс»; month — месяц экспирации фьючерса числом "
    "(«фьючерс Лукойла 12» / «декабрьский» -> 12; не назван -> null). Иначе kind=share, month=null.\n"
    "«купить/лонг» = buy; «продать/шорт/открыть шорт» = sell. «одну акцию / один лот / один контракт» -> lots 1. "
    "price=null — по рынку. Ветка «ничего не делать» — orders: []. Если веток не описано — branches: [].\n"
    "Если для «меньше X» и «не выплатит» действие ОДИНАКОВОЕ — это ОДНА ветка lt X (нулевая выплата тоже меньше X).\n"
    "«следи весь день за дивидендами Сбера» => {\"keywords\":[\"дивиденд\",\"сбер\"],\"until\":\"\",\"scenario\":\"\",\"event\":\"дивиденд\",\"branches\":[]}\n"
    "«наблюдай дивиденды Новатэка: больше 35 рублей купи 10 лотов по рынку, от 0 до 35 ничего, "
    "не выплатит — продай 5 лотов Новатэка по 1057 и 20 лотов Газпрома по рынку» => "
    "{\"keywords\":[\"дивиденд\",\"новатэк\"],\"until\":\"\",\"scenario\":\"\",\"event\":\"дивиденд\",\"branches\":["
    "{\"type\":\"gt\",\"min\":35,\"max\":null,\"orders\":[{\"name\":\"новатэк\",\"side\":\"buy\",\"lots\":10,\"price\":null}]},"
    "{\"type\":\"range\",\"min\":0,\"max\":35,\"orders\":[]},"
    "{\"type\":\"none\",\"min\":null,\"max\":null,\"orders\":[{\"name\":\"новатэк\",\"side\":\"sell\",\"lots\":5,\"price\":1057},"
    "{\"name\":\"газпром\",\"side\":\"sell\",\"lots\":20,\"price\":null}]}]}"
)

_RATE_HINT = (
    "\nРЕШЕНИЕ ПО СТАВКЕ ЦБ РФ или ФРС (ключевая ставка, заседание ЦБ/ФРС/FOMC): добавь поля "
    "\"kind\":\"rate\",\"bank\":\"cbr|fed\",\"date\":\"ГГГГ-ММ-ДД или пусто\",\"from\":\"ЧЧ:ММ:СС или пусто\". "
    "Ветки описывай СЛОВАМИ, без чисел со знаком: вместо type/min/max у ветки поля "
    "\"move\":\"cut|hold|hike|cut_or_hold|hold_or_hike\",\"bp\":число|null,\"bp_mode\":\"exact|atleast|null\". "
    "cut — снизят, hold — сохранят, hike — повысят; «снизят или сохранят» — cut_or_hold; «сохранят или повысят» — "
    "hold_or_hike. bp — размер в базисных пунктах БЕЗ знака (1 п.п. = 100): «на 50 и больше / от 50» — bp 50, "
    "bp_mode atleast; «ровно на 25 / на 25» — bp 25, bp_mode exact; «меньше чем на 100» — bp 100, bp_mode under; "
    "размер не назван — bp null. Каждая заявка — в той ветке, ПОСЛЕ условия которой она названа. "
    "Обычные ЦБ — 13:30:00, ФРС — 21:00:00 (МСК), если не сказано иначе. keywords — [\"ставка\"], event пусто.\n"
    "«ЦБ 23 октября: снизят на 50 и больше — купи Сбер, на 25 — ничего, сохранят — продай Сбер» => "
    "{\"name\":\"ЦБ · ставка 23.10\",\"keywords\":[\"ставка\"],\"until\":\"\",\"scenario\":\"\",\"event\":\"\","
    "\"tab\":\"\",\"kind\":\"rate\",\"bank\":\"cbr\",\"date\":\"2026-10-23\",\"from\":\"13:30:00\",\"branches\":["
    "{\"move\":\"cut\",\"bp\":50,\"bp_mode\":\"atleast\",\"orders\":[{\"name\":\"сбер\",\"side\":\"buy\",\"lots\":null,\"price\":null}]},"
    "{\"move\":\"cut\",\"bp\":25,\"bp_mode\":\"exact\",\"orders\":[]},"
    "{\"move\":\"hold\",\"bp\":null,\"bp_mode\":null,\"orders\":[{\"name\":\"сбер\",\"side\":\"sell\",\"lots\":null,\"price\":null}]}]}\n"
    "«ФРС: снизят — купи Сбер, сохранят или повысят — продай Сбер» => ветки: "
    "{\"move\":\"cut\",\"bp\":null,...buy...}, {\"move\":\"hold_or_hike\",\"bp\":null,...sell...}")


def _rate_move_to_branch(b):
    """Ветка решения по ставке «словами» (move/bp/bp_mode от мозга) -> пороги изменения в б.п. со знаком.
    Знак ставит КОД, не мозг: мозг путал «снизят на 50 и больше» (≤ −50) с «больше −50»."""
    move = str(b.get("move") or "").lower()
    try:
        bp = abs(int(round(float(b.get("bp"))))) if b.get("bp") not in (None, "") else None
    except (TypeError, ValueError):
        bp = None
    mode = str(b.get("bp_mode") or "").lower()
    if move == "hold":
        return {"type": "range", "min": 0, "max": 0}
    if move == "cut_or_hold":
        return ({"type": "range", "min": -(bp - 1), "max": 0} if (bp and mode == "under")
                else {"type": "le", "min": None, "max": 0})
    if move == "hold_or_hike":
        return ({"type": "range", "min": 0, "max": bp - 1} if (bp and mode == "under")
                else {"type": "ge", "min": 0, "max": None})
    if move == "cut":
        if not bp:
            return {"type": "lt", "min": None, "max": 0}
        if mode == "under":
            return {"type": "range", "min": -(bp - 1), "max": -1}
        return ({"type": "range", "min": -bp, "max": -bp} if mode == "exact"
                else {"type": "le", "min": None, "max": -bp})
    if move == "hike":
        if not bp:
            return {"type": "gt", "min": 0, "max": None}
        if mode == "under":
            return {"type": "range", "min": 1, "max": bp - 1}
        return ({"type": "range", "min": bp, "max": bp} if mode == "exact"
                else {"type": "ge", "min": bp, "max": None})
    return None


def _cscalp_known():
    """Тикеры стаканов CScalp (видимые + скрытые вкладки) из сохранённой раскладки — без нового скана."""
    try:
        import nox_cscalp
        data = nox_cscalp.scan(float("inf"))
        return ([d["ticker"] for d in data.get("doms") or []]
                + [nox_cscalp.norm(h["ticker"]) for h in data.get("hidden") or []])
    except Exception:
        return []


def _cscalp_hint():
    """Добавка к промпту разбора: как узнать заявку в CScalp и какие там есть стаканы."""
    known = sorted(set(_cscalp_known()) - {"ВЫБРАТЬИНСТРУМЕНТ"})
    return ("\nCScalp — второй терминал (проп). Если про заявку сказано «в скальпе», «в CScalp», «в цискальпе», "
            "«в пропе» — у неё \"venue\":\"cscalp\", а \"name\" — ТИКЕР стакана CScalp"
            + (f" ТОЧНО из списка: {', '.join(known)}" if known else "") + ". "
            "Коды: серебро SILV, мини-серебро SILVM, золото GOLD, мини-золото GOLDM, платина PLT (мини PLTM), "
            "палладий PLD (мини PLDM), нефть Brent BR (мини BRM), евро-доллар ED, доллар Si, евро Eu, юань CNY, "
            "индекс Мосбиржи MIX (мини MXI), РТС RTS, газ NG, какао COCOA, кофе COFFEE; акции — тикер (SBER, LKOH). "
            "Месяц фьючерса: 12/декабрьский -> -12.26, 3/мартовский -> -3.27 (напр. SILVM-12.26). "
            "Для CScalp lots и price = null (по рынку, объём стоит в стакане). Иначе venue пусто.\n"
            "«слово X» / «если выйдет новость со словом X» без чисел — ОДНА ветка type any, event пусто.\n"
            "«слово санкции: в скальпе продай мини-серебро декабрь и март» => {\"name\":\"Санкции · серебро\","
            "\"keywords\":[\"санкции\"],\"until\":\"\",\"scenario\":\"\",\"event\":\"\",\"tab\":\"\",\"branches\":["
            "{\"type\":\"any\",\"min\":null,\"max\":null,\"orders\":["
            "{\"name\":\"SILVM-12.26\",\"side\":\"sell\",\"lots\":null,\"price\":null,\"venue\":\"cscalp\"},"
            "{\"name\":\"SILVM-3.27\",\"side\":\"sell\",\"lots\":null,\"price\":null,\"venue\":\"cscalp\"}]}]}")


def _cscalp_ticker(name):
    """Тикер стакана CScalp из ответа мозга: есть в раскладке — он; похож на тикер — как есть; иначе пусто."""
    s = re.sub(r"^([A-Z]+)-?(\d{1,2}\.\d{2})$", r"\1-\2", re.sub(r"\s+", "", str(name or "")).upper())
    known = {t.upper(): t for t in _cscalp_known()}
    if s in known:
        return known[s]
    return s if re.fullmatch(r"[A-Z]{1,8}(-\d{1,2}\.\d{2})?", s) else ""


def _ticker(name, kind="share", month=None):
    """Название компании -> тикер через invest_reader (:8124 /ticker). Уже тикер (латиница) — как есть.
    kind=future (+month) — фьючерс на эту бумагу (лукойл, 12 -> LKZ6)."""
    s = (name or "").strip()
    if kind != "future" and re.fullmatch(r"[A-Za-z0-9]{2,8}", s):
        return s.upper()
    try:
        q = {"q": s}
        if kind == "future":
            q.update(kind="future", month=int(month or 0))
        u = "http://127.0.0.1:8124/ticker?" + urllib.parse.urlencode(q)
        j = json.loads(urllib.request.urlopen(u, timeout=10).read().decode("utf-8"))
        return (j.get("ticker") or "").upper()
    except Exception:
        return ""

def _number_orders(branches):
    """Проставить № виджета: одинаковые заявки (тикер+лоты+цена) делят один виджет, разные — свои."""
    slots = {}
    for b in branches:
        for o in b.get("orders") or []:
            if o.get("venue") == "cscalp":   # стакан CScalp — не виджет, номер не нужен
                continue
            key = (o["ticker"], o.get("lots"), o.get("price"))
            per = slots.setdefault(o["ticker"], [])
            if key not in per:
                per.append(key)
            o["n"] = per.index(key) + 1
    return branches

def _brain_parse(text):
    """Понять задачу через API-мозг (APIMIRA/выбранный в «ИИ-модели»)."""
    try:
        adb = nox_scenarios._appdb()
        model = adb.get("openai_model") or None
        sys_prompt = _WATCH_SYS + _cscalp_hint() + _RATE_HINT + f"\nСегодня {_now().strftime('%Y-%m-%d')}."
        raw = (nox_scenarios._llm_model(sys_prompt, text, model, max_tokens=700)
               if model else nox_scenarios._llm(sys_prompt, text, max_tokens=700))
        j = nox_scenarios._json_from(raw) or {}
        kws = [str(k).lower().strip() for k in (j.get("keywords") or []) if str(k).strip()]
        if not kws:
            return None
        branches, missing = [], []
        for b in j.get("branches") or []:
            orders = []
            for o in b.get("orders") or []:
                if str(o.get("venue") or "").lower() == "cscalp":
                    tk = _cscalp_ticker(o.get("name"))
                    if not tk:
                        missing.append(f"{o.get('name')} (CScalp)"); continue
                    orders.append({"ticker": tk, "side": o.get("side"), "venue": "cscalp"}); continue
                fut = str(o.get("kind") or "").lower() == "future"
                tk = _ticker(o.get("name"), "future" if fut else "share", o.get("month"))
                if not tk:
                    missing.append(str(o.get("name")) + (" (фьючерс)" if fut else "")); continue
                orders.append({"ticker": tk, "side": o.get("side"), "lots": o.get("lots"), "price": o.get("price")})
            if j.get("kind") == "rate":
                # решение по ставке: условие только «словами» (move) -> пороги кодом; иначе ветку НЕ берём —
                # ветка «любое значение» с заявкой сработала бы на любое решение
                conv = _rate_move_to_branch(b) if b.get("move") else (
                    {"type": b["type"], "min": b.get("min"), "max": b.get("max")}
                    if b.get("type") in ("gt", "ge", "lt", "le", "range") else None)
                if not conv:
                    missing.append(f"условие ветки {len(branches) + 1} (впишите сами)")
                    continue
                branches.append({**conv, "orders": orders})
            else:
                branches.append({"type": b.get("type"), "min": b.get("min"), "max": b.get("max"), "orders": orders})
        rate = {"kind": "rate", "bank": j.get("bank") if j.get("bank") in ("cbr", "fed") else "",
                "date": str(j.get("date") or "").strip(), "from": str(j.get("from") or "").strip()} \
            if j.get("kind") == "rate" else {}
        return {**rate, "keywords": kws, "until": (j.get("until") or "").strip(),
                "scenario_name": (j.get("scenario") or "").strip(),
                "name": (str(j.get("name") or "").strip() or " ".join(kws)),
                "event": (j.get("event") or "").strip(), "tab": (j.get("tab") or "").strip(),
                "branches": _number_orders(_clean_branches(branches)), "missing": missing}
    except Exception:
        pass
    return None

_BRIEF_SYS = (
    "Пользователь описывает, за какой новостью следить в биржевой ленте. Извлеки СТРОГИЙ JSON без пояснений:\n"
    "{\"name\":\"<короткое название, 2-4 слова>\",\"keywords\":[\"<ключевые слова темы в начальной форме>\"],"
    "\"until\":\"<ЧЧ:ММ или пусто>\"}\n"
    "«следи до 19 за дивидендами Сбера» => {\"name\":\"Сбер · дивиденды\",\"keywords\":[\"дивиденд\",\"сбер\"],\"until\":\"19:00\"}\n"
    "«новость про дефолт ВТБ» => {\"name\":\"ВТБ · дефолт\",\"keywords\":[\"дефолт\",\"втб\"],\"until\":\"\"}"
)

def parse_brief(text):
    """Разбор описания наблюдения для формы GUI (без сохранения): название, что ловить, до скольки.
    Сначала мозг, при сбое — разбор по словам."""
    try:
        model = nox_scenarios._appdb().get("openai_model") or None
        raw = (nox_scenarios._llm_model(_BRIEF_SYS, text, model, max_tokens=120) if model
               else nox_scenarios._llm(_BRIEF_SYS, text, max_tokens=120))
        j = nox_scenarios._json_from(raw) or {}
        kws = [str(k).lower().strip() for k in (j.get("keywords") or []) if str(k).strip()]
        if kws:
            return {"name": (j.get("name") or " ".join(kws)).strip(), "keywords": kws,
                    "until": (j.get("until") or "").strip()}
    except Exception:
        pass
    p = parse_watch(text)
    return {"name": p["name"], "keywords": p["keywords"], "until": p["until"]}

def parse_full(text):
    """Разбор одной строки для формы GUI (без сохранения): поля наблюдения + ветки с заявками
    (тот же разбор, что у голоса). Мозг недоступен — только поля (parse_brief)."""
    p = _brain_parse(text)
    if p:
        return {"name": p["name"], "keywords": p["keywords"], "until": p["until"], "event": p.get("event", ""),
                "tab": p.get("tab", ""), "branches": p.get("branches", []), "missing": p.get("missing", []),
                "kind": p.get("kind", ""), "bank": p.get("bank", ""), "date": p.get("date", ""),
                "from": p.get("from", "")}
    b = parse_brief(text)
    return {**b, "event": "", "tab": "", "branches": [], "missing": [], "brain_failed": True}

def _speak_branches(branches):
    parts = []
    f = lambda v: "?" if v is None else ("%g" % v)
    for i, b in enumerate(branches, 1):
        mn, mx = f(b.get("min")), f(b.get("max"))
        cond = {"none": "нет выплаты", "gt": f"больше {mn}", "ge": f"от {mn}", "lt": f"меньше {mx}",
                "le": f"до {mx}", "range": f"от {mn} до {mx}", "any": "любое"}.get(b["type"], b["type"])
        acts = ", ".join(f"{'купить' if o['side'] == 'buy' else 'продать'} {o['ticker']}"
                         + (" в скальпе" if o.get("venue") == "cscalp" else "") for o in b["orders"]) or "ничего"
        parts.append(f"ветка {i}: {cond} — {acts}")
    return "; ".join(parts)

def _rate_cond_ru(b):
    """Ветка решения по ставке словами (как подпись в GUI): «снижение на 50 и больше», «сохранение»."""
    t, mn, mx = b.get("type"), b.get("min"), b.get("max")
    f = lambda v: int(v) if v is not None and float(v).is_integer() else v
    mn, mx = f(mn), f(mx)
    if t == "range" and mn == 0 and mx == 0:
        return "сохранение"
    if t == "lt" and mx == 0:
        return "любое снижение"
    if t == "gt" and mn == 0:
        return "любое повышение"
    if t == "le" and mx == 0:
        return "снижение или сохранение"
    if t == "ge" and mn == 0:
        return "сохранение или повышение"
    if t == "le" and mx is not None and mx < 0:
        return f"снижение на {-mx} б.п. и больше"
    if t == "ge" and mn is not None and mn > 0:
        return f"повышение на {mn} б.п. и больше"
    if t == "range" and mn == mx and mn is not None:
        return f"снижение на {-mn} б.п." if mn < 0 else f"повышение на {mn} б.п."
    return f"изменение {t} {mn if mn is not None else ''}{'…' + str(mx) if mx is not None else ''} б.п."


def _speak_rate_branches(branches):
    parts = []
    for i, b in enumerate(branches, 1):
        acts = ", ".join(f"{'купить' if o['side'] == 'buy' else 'продать'} {o['ticker']}"
                         + (" в скальпе" if o.get("venue") == "cscalp" else "") for o in b["orders"]) or "ничего"
        parts.append(f"ветка {i}: {_rate_cond_ru(b)} — {acts}")
    return "; ".join(parts)


def add_from_voice(text):
    p = _brain_parse(text) or parse_watch(text)   # сначала мозг, иначе разбор по словам
    if not p["keywords"]:
        return {"ok": False, "text": "Не понял, за какой новостью следить, сэр."}
    branches = p.get("branches") or []
    if branches:   # наблюдатель с ветками и своей вкладкой терминала
        name = (p.get("name") or "наблюдатель").strip()
        rate = p.get("kind") == "rate"
        # решение по ставке голосом — создаётся ВЫКЛЮЧЕННЫМ: мозг может перепутать ветки, включает пользователь
        r = upsert({"name": name, "keywords": p["keywords"], "until": p["until"], "once": True,
                    "tab": p.get("tab") or DEFAULT_TAB, "event": p.get("event", ""), "branches": branches,
                    "kind": p.get("kind", ""), "bank": p.get("bank", ""), "date": p.get("date", ""),
                    "from": p.get("from", ""), "enabled": not rate})
        if not r.get("ok"):
            return r
        if rate:
            txt = (f"Наблюдатель «{name}» создан выключенным: {_speak_rate_branches(branches)}. "
                   "Проверьте ветки на странице «Наблюдатели» и включите.")
        else:
            txt = f"Наблюдатель «{name}»: {_speak_branches(branches)}."
        if p.get("missing"):
            txt += " Не нашёл инструменты: " + ", ".join(p["missing"]) + "."
        if any(o.get("venue") != "cscalp" for b in branches for o in b["orders"]) and LAYOUT:
            w = get(r["id"])
            threading.Thread(target=_layout_and_announce, args=(w,), daemon=True).start()
            txt += " Раскладываю заявки во вкладке терминала."
        return {"ok": True, "id": r["id"], "text": txt}
    sid, sname = "", ""
    if p["scenario_name"]:
        sc = _find_scenario(p["scenario_name"])
        if sc:
            sid = sc.get("id"); sname = sc.get("name")
    w = add(p["name"], p["keywords"], source="all", brain="", scenario_id=sid, until=p["until"], once=True)
    txt = "Наблюдаю за: " + ", ".join(p["keywords"])
    txt += (f", до {p['until']}" if p["until"] else ", весь день")
    txt += (f". Сработает сценарий «{sname}»." if sname else ". Только озвучу новость.")
    return {"ok": True, "id": w["id"], "text": txt}

def _layout_and_announce(w):
    try:
        res = LAYOUT(w) or {}
        nox_scenarios._announce(res.get("text") or "Раскладка завершена, сэр.")
    except Exception as e:
        nox_scenarios._announce(f"Не смог разложить заявки: {e}")

# ------------------------------------------------------------------ движок: новость -> наблюдатель
def choose_branch(branches, val):
    """Первая подходящая ветка. 0 («не выплачивает») сначала ищем среди веток type=none."""
    if val == 0:
        b = next((b for b in branches if b.get("type") == "none"), None)
        if b:
            return b
    return next((b for b in branches if b.get("type") != "none" and nox_scenarios._branch_match(b, val)), None)

def _vtxt(val):
    return "нет" if val == 0 else "не названо" if val is None else ("%g" % val)

def _fire_branches(w, news_text, meta=None):
    """Наблюдатель с ветками: число из новости -> ветка -> нажать её кнопки. Возвращает текст отчёта.
    СЛОВЕСНЫЙ режим (все ветки type=any): сработало само слово — нажимаем сразу, без мозга и без фильтра
    прогнозов (это нужно для скорости: «санкции» -> продать, «переговоры» -> купить)."""
    branches = w.get("branches") or []
    if w.get("kind") == "rate":
        return _fire_rate(w, news_text, meta)
    ev = w.get("event") or " ".join(w.get("keywords") or [])
    if branches and all(b.get("type") == "any" for b in branches):
        b = branches[0]
        if not b.get("orders"):
            return f"сработало слово «{ev}» — ничего не делаем."
        if not EXECUTOR:
            return "исполнитель терминала не подключён."
        res = EXECUTOR(w, b, news_text, meta=meta) or {}
        nox_scenarios._log(f"НАБЛЮДАТЕЛЬ «{w.get('name')}» (слово): {res.get('text', '')}")
        return res.get("text", "")
    # прогнозы/слухи — не решение: число из них НЕ берём (мозг упорно его достаёт) -> ничего не нажимаем
    if re.search(r"ожида|прогноз|\bможет\b|источник|аналитик|оценк|слух|вероятн|планиру|рассмотр|обсуд",
                 (news_text or "").lower()):
        nox_scenarios._log(f"НАБЛЮДАТЕЛЬ «{w.get('name')}»: прогноз/анонс, не решение — пропуск: {news_text[:80]}")
        return f"{ev}: это прогноз или анонс, а не решение — ничего не нажимаю, жду официальную новость."
    val = nox_scenarios._extract_value(news_text, ev, model=(w.get("brain") or None))
    b = choose_branch(branches, val)
    if not b:
        nox_scenarios._log(f"НАБЛЮДАТЕЛЬ «{w.get('name')}»: значение={val}, ветки нет")
        return f"{ev}: {_vtxt(val)} — подходящей ветки нет, ничего не нажимаю."
    i = branches.index(b) + 1
    if not b.get("orders"):
        nox_scenarios._log(f"НАБЛЮДАТЕЛЬ «{w.get('name')}»: значение={val} -> ветка {i}, без действий")
        return f"{ev}: {_vtxt(val)} — ветка {i}, ничего не делаем."
    if not EXECUTOR:
        return f"{ev}: {_vtxt(val)} — ветка {i}, но исполнитель терминала не подключён."
    res = EXECUTOR(w, b, news_text, meta=meta) or {}
    nox_scenarios._log(f"НАБЛЮДАТЕЛЬ «{w.get('name')}»: значение={val} -> ветка {i}: {res.get('text', '')}")
    return f"{ev}: {_vtxt(val)} — ветка {i}. {res.get('text', '')}"

def _rate_branch(branches, bp):
    """Ветка по изменению ставки (б.п.). 0 — обычное значение «сохранение» (не «не выплачивает»)."""
    return next((b for b in branches if b.get("type") != "none" and nox_scenarios._branch_match(b, bp)), None)


def _fire_rate(w, news_text, meta=None):
    """Решение по ставке: правила (nox_rates) -> изменение в б.п. -> ветка -> нажать. Без мозга, ~0.1 мс."""
    import nox_rates
    bank_ru = {"cbr": "ЦБ", "fed": "ФРС"}.get(w.get("bank"), "банк")
    d = nox_rates.parse_decision(news_text, rate_now=w.get("rate_now"))
    if not d or d.get("bank") != w.get("bank"):
        return f"{bank_ru}: не решение по ставке — жду."
    what = nox_rates.describe(d)
    if d.get("change_bp") is None:
        nox_scenarios._log(f"СТАВКА «{w.get('name')}»: {what} — размер не назван, жду следующий заголовок: {news_text[:90]}")
        return f"{what}: размер не назван — жду следующий заголовок."
    bp = d["change_bp"]
    branches = w.get("branches") or []
    b = _rate_branch(branches, bp)
    if not b:
        nox_scenarios._log(f"СТАВКА «{w.get('name')}»: {what} ({bp:+d} б.п.) — ветки нет")
        return f"{what} — подходящей ветки нет, ничего не нажимаю."
    i = branches.index(b) + 1
    if not b.get("orders"):
        nox_scenarios._log(f"СТАВКА «{w.get('name')}»: {what} ({bp:+d} б.п.) -> ветка {i}, без действий")
        return f"{what} — ветка {i}, ничего не делаем."
    if not EXECUTOR:
        return f"{what} — ветка {i}, но исполнитель терминала не подключён."
    res = EXECUTOR(w, b, news_text, meta=meta) or {}
    nox_scenarios._log(f"СТАВКА «{w.get('name')}»: {what} ({bp:+d} б.п.) -> ветка {i}: {res.get('text', '')}")
    return f"{what} — ветка {i}. {res.get('text', '')}"


def fast_window():
    """Сейчас окно решения по ставке у включённого наблюдателя (от «с» −5 с до +3 мин)? Тогда ридер
    читает ленту чаще (0.1 с вместо 0.5) — только эти минуты."""
    try:
        now = _now()
        today = now.strftime("%Y-%m-%d")
        secs = now.hour * 3600 + now.minute * 60 + now.second
        for w in load()["watchers"]:
            if not (w.get("enabled") and w.get("kind") == "rate" and w.get("from")):
                continue
            if w.get("date") and w["date"] != today:
                continue
            h, m, s = (int(x) for x in w["from"].split(":"))
            if -5 <= secs - (h * 3600 + m * 60 + s) <= 180:
                return True
    except Exception:
        pass
    return False


def dry_run(wid, value):
    """Проверка без нажатий: какая ветка сработает при значении и что именно нажмётся."""
    w = get(wid)
    if not w:
        return {"ok": False, "text": "Наблюдатель не найден."}
    if w.get("kind") == "rate":
        import nox_rates
        txt = str(value or "").strip()
        if re.fullmatch(r"[-+−]?\s*\d+(?:[.,]\d+)?", txt):
            bp, what = int(round(float(txt.replace("−", "-").replace(" ", "").replace(",", ".")))), ""
        else:
            d = nox_rates.parse_decision(txt, rate_now=w.get("rate_now"))
            if not d or d.get("bank") != w.get("bank"):
                return {"ok": True, "text": "Не решение по ставке этого банка — наблюдатель пропустит заголовок."}
            if d.get("change_bp") is None:
                return {"ok": True, "text": nox_rates.describe(d) + " — размер не назван: наблюдатель подождёт следующий заголовок."}
            bp, what = d["change_bp"], nox_rates.describe(d) + ". "
        b = _rate_branch(w.get("branches") or [], bp)
        if not b:
            return {"ok": True, "text": f"{what}Изменение {bp:+d} б.п.: подходящей ветки нет."}
        i = (w.get("branches") or []).index(b) + 1
        if not b.get("orders"):
            return {"ok": True, "branch": i, "text": f"{what}Изменение {bp:+d} б.п. → ветка {i}: ничего не делать."}
        if not EXECUTOR:
            return {"ok": False, "text": "Исполнитель терминала не подключён."}
        res = EXECUTOR(w, b, "", dry=True) or {}
        return {"ok": True, "branch": i, "text": f"{what}Изменение {bp:+d} б.п. → ветка {i}. {res.get('text', '')}"}
    try:
        val = float(str(value).replace(",", "."))
    except Exception:
        val = None
    b = choose_branch(w.get("branches") or [], val)
    if not b:
        return {"ok": True, "text": f"Значение {_vtxt(val)}: подходящей ветки нет."}
    i = (w.get("branches") or []).index(b) + 1
    if not b.get("orders"):
        return {"ok": True, "branch": i, "text": f"Значение {_vtxt(val)} → ветка {i}: ничего не делать."}
    if not EXECUTOR:
        return {"ok": False, "text": "Исполнитель терминала не подключён."}
    res = EXECUTOR(w, b, "", dry=True) or {}
    return {"ok": True, "branch": i, "text": f"Значение {_vtxt(val)} → ветка {i}. {res.get('text', '')}"}

def _fire(w, news_text, meta=None):
    name = w.get("name", "")
    short = (news_text or "").strip()
    if len(short) > 170:
        short = short[:167] + "…"
    if w.get("branches"):
        # сначала нажимаем (каждая секунда на счету), озвучиваем после
        try:
            report = _fire_branches(w, news_text, meta)
        except Exception as e:
            report = f"сбой исполнения: {e}"
        w["last_result"] = time.strftime("%H:%M:%S ") + report
        try:
            nox_scenarios._announce(f"{short}. {report}")   # без вступлений — сразу новость и что сделано
        except Exception:
            pass
        # прогноз/анонс/число не названо — решения ещё не было: продолжаем ждать (не выключаемся)
        return not any(k in report for k in ("прогноз или анонс", "не названо", "ветки нет",
                                              "не решение по ставке", "размер не назван"))
    # обычный наблюдатель (без веток): озвучить новость + открыть/подсветить стакан сценария
    try:
        nox_scenarios._announce(short)
    except Exception:
        pass
    sid = w.get("scenario_id", "")
    if sid:
        try:
            res = nox_scenarios.fire_by_id(sid, news_text, model=(w.get("brain") or None))
            if res.get("text"):
                nox_scenarios._announce(res["text"])
        except Exception as e:
            try: nox_scenarios._log(f"НАБЛЮДАТЕЛЬ «{name}» сценарий-ошибка: {e}")
            except Exception: pass
    try:
        nox_scenarios._log(f"НАБЛЮДАТЕЛЬ «{name}» СРАБОТАЛ: {news_text[:70]}")
    except Exception:
        pass
    return True

_VOW = "аеёиоуыэюяьъй"
def _stem(w):
    """Грубый корень слова (для сопоставления с учётом склонений): первые ~6 букв без окончания."""
    w = (w or "").lower().replace("ё", "е")
    r = w[:max(6, len(w) - 2)]
    while len(r) > 3 and r[-1] in _VOW:
        r = r[:-1]
    return r

def on_news(items, source="trading"):
    """items: [{text, body, tickers, seen, _detect}] — свежие новости (новые первыми). Проверяет наблюдателей.
    Новости разбираются В ПОРЯДКЕ ВЫХОДА (старые -> новые): «кто первым совпал — тот и сработал».
    Группа (`group`): первый сработавший наблюдатель выключает остальных из своей группы."""
    if not items:
        return
    d = load(); changed = False
    remove_ids = []; remove_scn = []     # «один раз»: наблюдатель и его сценарий удаляются после срабатывания
    now = _now(); today = now.strftime("%Y-%m-%d"); cur_hm = now.strftime("%H:%M")
    for w in d["watchers"]:
        until = w.get("until", "")
        if w.get("enabled") and until and cur_hm > until:   # окно наблюдения закрылось
            w["enabled"] = False; changed = True

    def eligible(w):
        if not w.get("enabled") or w["id"] in remove_ids or not w.get("keywords"):
            return False
        if w.get("source", "all") not in ("all", source):
            return False
        if w.get("once") and w.get("fired_day") == today:
            return False
        if (not w.get("once")) and (time.time() - w.get("last_fired", 0) < 900):
            return False
        if w.get("date") and w["date"] != today:            # не тот день
            return False
        if w.get("from") and cur_hms < w["from"]:          # ещё рано: анонсы и прогнозы до решения не берём
            return False
        return True

    cur_hms = now.strftime("%H:%M:%S")
    for it in reversed(items):                  # по порядку выхода
        blob = ((it.get("text", "") or "") + " " + (it.get("body", "") or "")).lower().replace("ё", "е")
        for w in d["watchers"]:
            if not eligible(w):
                continue
            if w.get("kind") == "rate":   # решение по ставке: каждую новость в окне — в парсер (слова не нужны)
                if not re.search(r"ставк|\brates?\b|target range|fed funds", blob):
                    continue
            else:
                roots = [_stem(k) for k in w["keywords"]]
                if not all(r in blob for r in roots):   # ВСЕ корни цели -> попадание (с учётом склонений)
                    continue
            seen, det = it.get("seen"), it.get("_detect")
            if seen and det:
                nox_scenarios._log(f"СОВПАЛ «{w.get('name')}»: новость появилась на странице -> ридер увидел "
                                   f"за {(det - seen / 1000) * 1000:.0f} мс")
            acted = _fire(w, it.get("text", "") or it.get("body", ""), it)
            changed = True
            if acted is False:
                continue                        # не решение — ждём следующую новость
            w["last_fired"] = time.time(); w["fired_day"] = today
            if w.get("delete_after"):
                # «исполнил — удалился»: сам наблюдатель и вся его группа (второй вариант теста больше не нужен)
                grp = w.get("group")
                remove_ids.extend(o["id"] for o in d["watchers"]
                                  if o is w or (grp and o.get("group") == grp))
                nox_scenarios._log(f"НАБЛЮДАТЕЛЬ «{w.get('name')}» исполнен и удалён" + (f" вместе с группой «{grp}»" if grp else ""))
                continue
            if w.get("once") and w.get("branches"):
                w["enabled"] = False            # с ветками — не удаляем: остаётся отчёт и разметка вкладки
            elif w.get("once"):
                remove_ids.append(w["id"])                      # удалить наблюдателя
                if w.get("scenario_id"):
                    remove_scn.append(str(w["scenario_id"]))    # и привязанный сценарий
            grp = w.get("group")
            if grp:                             # первый из группы сработал — остальные больше не ждут
                for o in d["watchers"]:
                    if o is not w and o.get("group") == grp and o.get("enabled"):
                        o["enabled"] = False
                        o["last_result"] = time.strftime("%H:%M:%S ") + f"выключен: первым сработал «{w.get('name')}»"
    if remove_ids:
        d["watchers"] = [x for x in d["watchers"] if x["id"] not in remove_ids]
    if remove_scn:
        try:
            scfg = nox_scenarios.load()
            scfg["scenarios"] = [s for s in scfg.get("scenarios", []) if str(s.get("id")) not in remove_scn]
            nox_scenarios.save(scfg)
            nox_scenarios._log(f"Сценарии удалены после однократного срабатывания: {remove_scn}")
        except Exception:
            pass
    if changed:
        save(d)
