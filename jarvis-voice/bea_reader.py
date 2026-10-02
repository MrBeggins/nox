# -*- coding: utf-8 -*-
"""
BEA reader/monitor для Nox — ВВП и PCE (инфляция по расходам) США.
Данные: официальный api BEA (https://apps.bea.gov/api/data), набор NIPA.
  ВВП    — таблица T10101, строка «Gross domestic product» = % изменение реального ВВП (годовых).
  PCE    — таблица T20804 (месячная): «Personal consumption expenditures» (общий индекс цен)
           и «PCE excluding food and energy» (БАЗОВЫЙ PCE — ключевой показатель для ФРС).
Два режима: календарь (заранее предупреждает) + факт (при выходе зачитывает).
ОСОБЕННОСТЬ: опрос API стартует ЗА 1 МИНУТУ до запланированного релиза (просьба пользователя).
Только ЧТЕНИЕ+УВЕДОМЛЕНИЯ, сделок не совершает. Прогнозов нет (BEA их не публикует).

ТРЕБУЕТСЯ UserID BEA (36 символов) в файле C:\\jarvis-voice\\bea_key.txt — регистрация:
  https://apps.bea.gov/api/signup/  (ключ приходит на почту). Без ключа календарь работает,
  а фактические значения — нет.
Запуск: python bea_reader.py  ->  http://127.0.0.1:8129
"""
import threading, time, json, os, socket, base64, struct
import urllib.request, urllib.parse
from datetime import datetime, timedelta, date
from fastapi import FastAPI
import uvicorn

IPC_HOST, IPC_PORT = "127.0.0.1", 9712
API_URL    = "https://apps.bea.gov/api/data"
STATE_FILE = r"C:\jarvis-voice\bea_state.json"
MON_FLAG   = r"C:\jarvis-voice\bea_mon_on.txt"
SCHED_FILE = r"C:\jarvis-voice\bea_schedule.json"
KEY_FILE   = r"C:\jarvis-voice\bea_key.txt"

app = FastAPI()
_state = {"ok": False, "err": "", "ts": 0, "last_poll": 0}

# ------------------------------------------------------------------ таймзоны ET -> MSK
_TZ = False
try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York"); _MSK = ZoneInfo("Europe/Moscow")
    datetime(2026, 7, 1, tzinfo=_ET).astimezone(_MSK)
    _TZ = True
except Exception:
    _TZ = False

def _is_edt(dt):
    y = dt.year
    mar = date(y, 3, 1); mar += timedelta(days=(6 - mar.weekday()) % 7 + 7)
    nov = date(y, 11, 1); nov += timedelta(days=(6 - nov.weekday()) % 7)
    return mar <= dt.date() < nov

def et_to_msk(dt_et_naive):
    if _TZ:
        return dt_et_naive.replace(tzinfo=_ET).astimezone(_MSK).replace(tzinfo=None)
    off = -4 if _is_edt(dt_et_naive) else -5
    return dt_et_naive + timedelta(hours=(3 - off))

def now_msk():
    if _TZ:
        return datetime.now(_MSK).replace(tzinfo=None)
    return datetime.utcnow() + timedelta(hours=3)

# ------------------------------------------------------------------ показатели
# pick — как найти нужную строку в таблице (по LineDescription, без привязки к номеру строки).
INDICATORS = {
    "gdp": {
        "ru": "ВВП США", "short": "ВВП",
        "table": "T10101", "freq": "Q",
        "pick": lambda d: d.strip().lower() == "gross domestic product",
        "match": ["ввп", "gdp", "валовой", "валовый"],
    },
    "pce": {
        "ru": "Инфляция PCE США", "short": "PCE (инфляция)",
        "table": "T20804", "freq": "M",
        "pick": lambda d: (d.strip().lower().startswith("personal consumption expenditures")
                           and "excluding" not in d.lower()),
        "match": ["pce", "пи-си", "пи си", "пи-си-и", "расходы потребител", "потребительск расход"],
    },
    "core_pce": {
        "ru": "Базовый PCE США", "short": "базовый PCE",
        "table": "T20804", "freq": "M",
        "pick": lambda d: "excluding food and energy" in d.lower(),
        "match": ["базов", "core", "базовый pce", "базовая инфляц"],
    },
}
# группы релизов (для расписания/детекта факта): что выходит вместе
GROUPS = {
    "gdp": {"ru": "ВВП США", "inds": ["gdp"]},
    "pce": {"ru": "Личные доходы и расходы США (PCE)", "inds": ["pce", "core_pce"]},
}
TABLES = {("T10101", "Q"), ("T20804", "M")}   # что тянуть одним опросом

# ------------------------------------------------------------------ расписание BEA (ET), остаток 2026
# Только для заблаговременных предупреждений; факт детектируется по появлению данных в API.
_DEFAULT_SCHEDULE = [
    # ВВП Q3-2026: предв./вторая/третья оценки
    ("gdp", "2026-10-29", "08:30", "предварительная оценка"),
    ("gdp", "2026-11-25", "08:30", "вторая оценка"),
    ("gdp", "2026-12-23", "08:30", "третья оценка"),
    # Личные доходы и расходы (PCE), месячные
    ("pce", "2026-10-29", "08:30", "сентябрь"),
    ("pce", "2026-11-25", "08:30", "октябрь"),
    ("pce", "2026-12-23", "08:30", "ноябрь"),
]

def _load_schedule():
    try:
        raw = json.load(open(SCHED_FILE, encoding="utf-8"))
        rows = [(r[0], r[1], r[2], (r[3] if len(r) > 3 else "")) for r in raw]
    except Exception:
        rows = list(_DEFAULT_SCHEDULE)
    out = []
    for grp, dstr, tstr, note in rows:
        try:
            dt_et = datetime.strptime(dstr + " " + tstr, "%Y-%m-%d %H:%M")
            out.append({"grp": grp, "et": dt_et, "msk": et_to_msk(dt_et),
                        "note": note, "id": f"{grp}:{dstr}"})
        except Exception:
            pass
    out.sort(key=lambda r: r["msk"])
    return out

# ------------------------------------------------------------------ состояние
def _load_state():
    try:
        return json.load(open(STATE_FILE, encoding="utf-8"))
    except Exception:
        return {"seen": {}, "announced": {}}

def _save_state(st):
    try:
        json.dump(st, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass

_ST = _load_state()

def _mon_enabled():
    try:
        return open(MON_FLAG, encoding="utf-8").read().strip() != "0"
    except Exception:
        return True

def _set_mon(on):
    try:
        open(MON_FLAG, "w", encoding="utf-8").write("1" if on else "0")
    except Exception:
        pass

def _key():
    try:
        k = open(KEY_FILE, encoding="utf-8").read().strip()
        return k or None
    except Exception:
        return None

# ------------------------------------------------------------------ IPC-озвучка
def _announce(text):
    try:
        s = socket.create_connection((IPC_HOST, IPC_PORT), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall((f"GET / HTTP/1.1\r\nHost: {IPC_HOST}:{IPC_PORT}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        s.recv(4096)
        data = json.dumps({"action": "announce", "text": text}, ensure_ascii=False).encode("utf-8")
        mask = os.urandom(4)
        hdr = bytearray([0x81]); n = len(data)
        if n < 126: hdr.append(0x80 | n)
        elif n < 65536: hdr.append(0x80 | 126); hdr += struct.pack(">H", n)
        else: hdr.append(0x80 | 127); hdr += struct.pack(">Q", n)
        hdr += mask
        s.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
        time.sleep(0.15); s.close(); return True
    except Exception:
        return False

# ------------------------------------------------------------------ BEA API
_DATA = {}   # table -> {desc_lower: [points]}  (points новейшие первыми)
_DATA_TS = 0

def _pt_key(period):
    """'2026Q2'->(2026,2); '2026M08'->(2026,8); '2026'->(2026,0)."""
    try:
        if "Q" in period:
            y, q = period.split("Q"); return (int(y), int(q))
        if "M" in period:
            y, m = period.split("M"); return (int(y), int(m))
        return (int(period), 0)
    except Exception:
        return (0, 0)

def _parse_val(s):
    try:
        return float(str(s).replace(",", "").strip())
    except Exception:
        return None

def _api_get(table, freq):
    key = _key()
    if not key:
        raise RuntimeError("NO_KEY")
    y = now_msk().year
    params = urllib.parse.urlencode({
        "UserID": key, "method": "GetData", "datasetname": "NIPA",
        "TableName": table, "Frequency": freq, "Year": f"{y-1},{y}",
        "ResultFormat": "JSON",
    })
    req = urllib.request.Request(f"{API_URL}?{params}", headers={"User-Agent": "Nox/1.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        j = json.loads(r.read().decode("utf-8"))
    bea = j.get("BEAAPI", {})
    if "Error" in bea:
        raise RuntimeError(str(bea["Error"])[:160])
    res = bea.get("Results", {})
    if isinstance(res, dict) and res.get("Error"):
        raise RuntimeError(str(res["Error"])[:160])
    rows = res.get("Data", []) if isinstance(res, dict) else []
    by_desc = {}
    for row in rows:
        desc = (row.get("LineDescription") or "").strip()
        val = _parse_val(row.get("DataValue"))
        per = row.get("TimePeriod") or ""
        if not desc or val is None or not per:
            continue
        by_desc.setdefault(desc.lower(), []).append(
            {"desc": desc, "period": per, "pt": _pt_key(per), "value": val})
    for d in by_desc.values():
        d.sort(key=lambda x: x["pt"], reverse=True)
    return by_desc

def _api_poll():
    global _DATA, _DATA_TS
    out = {}
    for table, freq in TABLES:
        out[table] = _api_get(table, freq)
    _DATA = out; _DATA_TS = time.time()
    _state.update(ok=True, err="", ts=time.time(), last_poll=time.time())
    return out

def _points(ind):
    meta = INDICATORS[ind]
    tbl = _DATA.get(meta["table"], {})
    pick = meta["pick"]
    for desc_l, pts in tbl.items():
        if pick(pts[0]["desc"]):
            return pts
    return []

# ------------------------------------------------------------------ числа для TTS
_MONTHS = {1:"январь",2:"февраль",3:"март",4:"апрель",5:"май",6:"июнь",7:"июль",
           8:"август",9:"сентябрь",10:"октябрь",11:"ноябрь",12:"декабрь"}
_QUARTERS = {1:"первый",2:"второй",3:"третий",4:"четвёртый"}

def _num(x, nd=1):
    return f"{x:.{nd}f}".replace(".", ",")

def _pct(x, nd=1):
    return f"{abs(x):.{nd}f}".replace(".", ",") + " процента"

def _signed_pct(x, nd=1):
    return ("плюс " if x >= 0 else "минус ") + _pct(x, nd)

def _mom(ind):
    p = _points(ind)
    if len(p) >= 2 and p[1]["value"]:
        return (p[0]["value"] - p[1]["value"]) / p[1]["value"] * 100.0
    return None

def _yoy(ind):
    p = _points(ind)
    if not p:
        return None
    cur = p[0]
    for q in p[1:]:
        if q["pt"][1] == cur["pt"][1] and q["pt"][0] == cur["pt"][0] - 1 and q["value"]:
            return (cur["value"] - q["value"]) / q["value"] * 100.0
    return None

# ------------------------------------------------------------------ тексты
def _text_gdp():
    p = _points("gdp")
    if not p:
        return "ВВП США: нет данных."
    cur = p[0]; q = cur["pt"][1]
    return (f"ВВП США за {_QUARTERS.get(q, str(q))} квартал: "
            f"{_signed_pct(cur['value'])} в годовом выражении.")

def _text_pce():
    mom = _mom("pce"); yoy = _yoy("pce")
    cmom = _mom("core_pce"); cyoy = _yoy("core_pce")
    p = _points("pce"); mon = _MONTHS.get(p[0]["pt"][1], "") if p else ""
    parts = [f"Инфляция PCE США за {mon}:"]
    if mom is not None: parts.append(f"{_pct(mom)} за месяц,")
    if yoy is not None: parts.append(f"{_pct(yoy)} за год.")
    if cmom is not None or cyoy is not None:
        c = "Базовый PCE:"
        if cmom is not None: c += f" {_pct(cmom)} за месяц,"
        if cyoy is not None: c += f" {_pct(cyoy)} за год."
        parts.append(c)
    return " ".join(parts)

_GROUP_TEXT = {"gdp": _text_gdp, "pce": _text_pce}

def _group_tag(grp):
    """Метка последнего периода + значения группы (ловит и новый период, и пересмотр оценки)."""
    ind = GROUPS[grp]["inds"][0]
    p = _points(ind)
    if not p:
        return ""
    return f"{p[0]['period']}:{p[0]['value']}"

# ------------------------------------------------------------------ подача в сценарии (авто-клик CScalp)
def _feed_scenarios(grp, text):
    try:
        import nox_scenarios
        matches = []
        for ind in GROUPS[grp]["inds"]:
            matches += INDICATORS[ind]["match"]
        nox_scenarios.on_news([{"text": text, "body": "", "tickers": matches}])
    except Exception:
        pass

# ------------------------------------------------------------------ детект факта
def _check_facts(announce=True):
    fired = []
    for grp in GROUPS:
        tag = _group_tag(grp)
        if not tag:
            continue
        prev = _ST.get("seen", {}).get(grp)
        if prev == tag:
            continue
        same_period = bool(prev) and prev.split(":")[0] == tag.split(":")[0]
        _ST.setdefault("seen", {})[grp] = tag
        text = _GROUP_TEXT[grp]()
        if same_period:
            text = "Уточнённая оценка. " + text   # пересмотр того же периода (вторая/третья оценка)
        fired.append((grp, text))
        if announce:
            _announce("Вышли данные. " + text)
            _feed_scenarios(grp, text)
    if fired:
        _save_state(_ST)
    return fired

# ------------------------------------------------------------------ предупреждения
_PREWARN = [24, 1, 0.2]

def _when_label(r, now):
    delta_h = (r["msk"] - now).total_seconds() / 3600.0
    hhmm = r["msk"].strftime("%H:%M")
    if delta_h <= 0.35:
        return f"совсем скоро, в {hhmm} по Москве,"
    if delta_h <= 1.2:
        return f"примерно через час, в {hhmm} по Москве,"
    day = "сегодня" if r["msk"].date() == now.date() else \
          "завтра" if r["msk"].date() == (now.date() + timedelta(days=1)) else \
          r["msk"].strftime("%d.%m")
    return f"{day} в {hhmm} по Москве"

def _warn_name(r):
    g = GROUPS[r["grp"]]["ru"]
    return f"{g} ({r['note']})" if r.get("note") else g

def _check_calendar():
    now = now_msk()
    for r in _load_schedule():
        delta_h = (r["msk"] - now).total_seconds() / 3600.0
        if delta_h <= -0.1:
            continue
        for thr in _PREWARN:
            if 0 < delta_h <= thr:
                akey = f"{r['id']}:{thr}"
                if _ST.get("announced", {}).get(akey):
                    continue
                ok = _announce(f"Напоминание, сэр: {_when_label(r, now)} — {_warn_name(r)}.")
                if ok:
                    _ST.setdefault("announced", {})[akey] = True
                    _save_state(_ST)
                break

def _in_release_window():
    """Опрос API стартует ЗА 1 МИНУТУ до релиза и держится до +40 мин."""
    now = now_msk()
    for r in _load_schedule():
        if (r["msk"] - timedelta(minutes=1)) <= now <= (r["msk"] + timedelta(minutes=40)):
            return True
    return False

# ------------------------------------------------------------------ монитор
def _monitor_loop():
    if _key():
        try:
            _api_poll(); _check_facts(announce=False)   # тихая синхронизация
        except Exception as e:
            _state.update(ok=False, err=str(e))
    while True:
        try:
            if _mon_enabled():
                _check_calendar()
                if _key():
                    in_win = _in_release_window()
                    since = time.time() - _state.get("last_poll", 0)
                    # в окне релиза — раз в ~10с (лимит BEA ~100/мин), иначе — раз в 6ч
                    if (in_win and since > 10) or (not in_win and since > 6 * 3600):
                        _api_poll()
                        _check_facts(announce=True)
        except Exception as e:
            _state.update(ok=False, err=str(e))
        time.sleep(8)

# ------------------------------------------------------------------ HTTP API
@app.on_event("startup")
def _startup():
    threading.Thread(target=_monitor_loop, daemon=True).start()

@app.get("/health")
def health():
    return {"status": "ok" if _state["ok"] else ("no_key" if not _key() else "starting"),
            "err": _state["err"], "mon": _mon_enabled(), "tz": _TZ, "key": bool(_key())}

@app.get("/mon")
def mon(on: int = -1):
    if on in (0, 1):
        _set_mon(bool(on))
    return {"on": _mon_enabled(),
            "text": ("Слежу за ВВП и PCE США, сэр." if _mon_enabled()
                     else "Слежка за ВВП и PCE США выключена, сэр.")}

@app.get("/calendar")
def calendar(days: int = 90):
    now = now_msk(); lim = now + timedelta(days=max(1, days))
    rows = [r for r in _load_schedule() if now <= r["msk"] <= lim]
    if not rows:
        return {"text": "Ближайших релизов ВВП/PCE в этом окне нет, сэр.", "items": []}
    items = [{"grp": r["grp"], "name": _warn_name(r), "msk": r["msk"].strftime("%Y-%m-%d %H:%M")} for r in rows]
    spoken = "; ".join(f"{r['msk'].strftime('%d.%m в %H:%M')} — {_warn_name(r)}" for r in rows[:6])
    return {"text": "Календарь ВВП и PCE США: " + spoken + ".", "items": items}

@app.get("/next")
def nxt():
    now = now_msk()
    fut = [r for r in _load_schedule() if r["msk"] > now]
    if not fut:
        return {"text": "Ближайших релизов ВВП/PCE не нашёл, сэр."}
    r = fut[0]; dh = (r["msk"] - now).total_seconds() / 3600.0
    when = (f"через {int(round(dh/24))} дн" if dh >= 36
            else f"через {int(round(dh))} ч" if dh >= 1.5
            else f"через {int(round(dh*60))} мин")
    return {"text": f"Следующий релиз — {_warn_name(r)}, {r['msk'].strftime('%d.%m в %H:%M')} "
                    f"по Москве ({when}).", "grp": r["grp"], "msk": r["msk"].strftime("%Y-%m-%d %H:%M")}

def _need_key_text():
    return ("Для данных ВВП и PCE нужен ключ BEA, сэр. Зарегистрируйтесь на apps.bea.gov "
            "и дайте мне UserID — я его подключу.")

@app.get("/latest")
def latest(force: int = 0):
    if not _key():
        return {"text": _need_key_text()}
    try:
        if force or not _DATA or time.time() - _DATA_TS > 6 * 3600:
            _api_poll()
    except Exception as e:
        if not _DATA:
            return {"text": f"Не смог прочитать данные BEA, сэр. {type(e).__name__}"}
    return {"text": _text_gdp() + " " + _text_pce(),
            "items": {"gdp": _text_gdp(), "pce": _text_pce()}}

@app.get("/value")
def value(q: str = ""):
    ql = (q or "").lower()
    if not _key():
        return {"text": _need_key_text()}
    try:
        if not _DATA or time.time() - _DATA_TS > 6 * 3600:
            _api_poll()
    except Exception as e:
        if not _DATA:
            return {"text": f"Не смог прочитать данные BEA, сэр. {type(e).__name__}"}
    # ВВП?
    if any(m in ql for m in INDICATORS["gdp"]["match"]):
        return {"text": _text_gdp(), "grp": "gdp"}
    # PCE (общий/базовый) -> единый текст по группе
    if any(m in ql for m in INDICATORS["pce"]["match"] + INDICATORS["core_pce"]["match"]):
        return {"text": _text_pce(), "grp": "pce"}
    return latest()

@app.get("/check")
def check():
    if not _key():
        return {"ok": False, "err": "NO_KEY", "text": _need_key_text()}
    try:
        _api_poll()
    except Exception as e:
        return {"ok": False, "err": str(e)}
    fired = _check_facts(announce=True)
    return {"ok": True, "fired": [f[0] for f in fired], "seen": _ST.get("seen", {})}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8129, log_level="warning")
