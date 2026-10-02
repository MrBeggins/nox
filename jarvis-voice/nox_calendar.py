# -*- coding: utf-8 -*-
"""
Авто-календарь Nox: разбирает сырые события календаря MindStocks (из terminal_reader, CAL_JS),
нормализует в {date, time, ticker, type, text}, фильтрует ключевые, хранит в nox_calendar.json.
Каждое утро terminal_reader вызывает ingest() и озвучивает дайджест.
US-макро (нонфарм/CPI/ВВП/PCE) берётся из BLS/BEA (точнее), здесь — РФ-корпоративные + видимые макро.
"""
import json, os, re, datetime as _dt

STORE = r"C:\jarvis-voice\nox_calendar.json"

try:
    from zoneinfo import ZoneInfo
    _MSK = ZoneInfo("Europe/Moscow"); _dt.datetime.now(_MSK)
    def now_msk(): return _dt.datetime.now(_MSK).replace(tzinfo=None)
except Exception:
    def now_msk(): return _dt.datetime.utcnow() + _dt.timedelta(hours=3)

_MONTHS = {"январ":1,"феврал":2,"март":3,"апрел":4,"мая":5,"май":5,"июн":6,"июл":7,
           "август":8,"сентябр":9,"октябр":10,"ноябр":11,"декабр":12}

# ключевые типы событий (по подстроке в тексте)
_TYPES = [
    ("дивиденд",   ["дивиденд"]),
    ("оферта",     ["оферт"]),
    ("допэмиссия", ["допэмисс", "доп. эмисс", "доп эмисс", "дополнительн эмисс", "spo", "размещени акц"]),
    ("дефолт",     ["дефолт", "не исполнил обязат", "техдефолт"]),
    ("выкуп",      ["выкуп", "buyback"]),
    ("отчёт",      ["отчёт", "отчет", "мсфо", "рсбу", "финрезультат"]),
    ("макро",      ["ввп", "gdp", "инфляц", "cpi", "ставк", "фрс", "fomc", "цб", "нонфарм", "nonfarm",
                    "запас", "нефт", "газ", "pmi", "безработиц"]),
]

def _parse_date(date_title):
    low = (date_title or "").lower()
    now = now_msk()
    if "сегодня" in low: return now.date()
    if "завтра" in low:  return (now + _dt.timedelta(days=1)).date()
    m = re.search(r"(\d{1,2})\s+([а-яё]+)", low)
    if m:
        d = int(m.group(1))
        mon = next((v for k, v in _MONTHS.items() if m.group(2).startswith(k)), None)
        if mon:
            for yy in (now.year, now.year + 1):
                try:
                    dd = _dt.date(yy, mon, d)
                    if (dd - now.date()).days >= -45:
                        return dd
                except Exception:
                    pass
    return None

def _ticker(text):
    m = re.match(r"\s*([A-Z]{3,6}P?)\b", text or "")
    return m.group(1) if m else ""

def _etype(text):
    low = (text or "").lower()
    for name, kws in _TYPES:
        if any(k in low for k in kws):
            return name
    return "другое"

def parse_event(date_title, text):
    d = _parse_date(date_title)
    tm = None
    mt = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text or "")
    if mt:
        tm = f"{int(mt.group(1)):02d}:{mt.group(2)}"
    return {
        "date": d.strftime("%Y-%m-%d") if d else "",
        "time": tm or "",
        "ticker": _ticker(text),
        "type": _etype(text),
        "text": (text or "").strip()[:200],
    }

def ingest(raw_events, key_only=True):
    """raw_events: [{date, text}] из CAL_JS. Возвращает нормализованные (ключевые по умолчанию)."""
    out = []
    for e in (raw_events or []):
        ev = parse_event(e.get("date", ""), e.get("text", ""))
        if key_only and ev["type"] == "другое":
            continue
        out.append(ev)
    try:
        json.dump({"imported_at": now_msk().strftime("%Y-%m-%d %H:%M"), "events": out},
                  open(STORE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass
    return out

def load():
    try:
        return json.load(open(STORE, encoding="utf-8"))
    except Exception:
        return {"imported_at": "", "events": []}

def today_events():
    today = now_msk().strftime("%Y-%m-%d")
    return [e for e in load().get("events", []) if e.get("date") == today]

def digest_text(events):
    """Короткая сводка для озвучки: что сегодня/ближайшее важное."""
    if not events:
        return "Сегодня в календаре важных событий не вижу, сэр."
    by = {}
    for e in events:
        by.setdefault(e["type"], []).append(e)
    parts = []
    order = ["макро", "дивиденд", "оферта", "допэмиссия", "дефолт", "выкуп", "отчёт"]
    names = {"макро": "макро-события", "дивиденд": "дивиденды", "оферта": "оферты",
             "допэмиссия": "допэмиссии", "дефолт": "дефолты", "выкуп": "выкупы", "отчёт": "отчёты"}
    for t in order:
        lst = by.get(t)
        if not lst:
            continue
        tks = ", ".join(sorted({e["ticker"] for e in lst if e["ticker"]}))[:120]
        parts.append(f"{names[t]}: {tks}" if tks else names[t])
    return "В календаре сегодня — " + "; ".join(parts) + ", сэр." if parts else \
           "Сегодня в календаре важных событий не вижу, сэр."
