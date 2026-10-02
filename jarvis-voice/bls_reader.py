# -*- coding: utf-8 -*-
"""
BLS reader/monitor для Nox — макростатистика США (нонфарм, CPI, PPI, безработица, JOLTS).
Два режима:
  1) КАЛЕНДАРЬ — заранее голосом предупреждает о приближающихся релизах (по расписанию BLS на 2026).
  2) ФАКТ — при выходе данных зачитывает фактическое значение (через официальный api.bls.gov).
Только ЧТЕНИЕ и УВЕДОМЛЕНИЯ. Прогнозов/консенсуса нет (BLS их не публикует). Сделок не совершает.

Лимит: без ключа BLS API ~25 запросов/сутки, поэтому ВСЕ индикаторы берём ОДНИМ батч-запросом
и опрашиваем в основном в окне релиза, а не по кругу. Опциональный ключ (bls_key.txt) поднимает лимит.
Запуск: python bls_reader.py  ->  http://127.0.0.1:8128
"""
import threading, time, json, os, socket, base64, struct
import urllib.request
from datetime import datetime, timedelta, date
from fastapi import FastAPI
import uvicorn

IPC_HOST, IPC_PORT = "127.0.0.1", 9712
API_URL   = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
STATE_FILE = r"C:\jarvis-voice\bls_state.json"
MON_FLAG   = r"C:\jarvis-voice\bls_mon_on.txt"
SCHED_FILE = r"C:\jarvis-voice\bls_schedule.json"   # необязательное переопределение расписания
KEY_FILE   = r"C:\jarvis-voice\bls_key.txt"          # необязательный ключ BLS (поднимает лимит)

app = FastAPI()
_state = {"ok": False, "err": "", "ts": 0, "last_poll": 0}

# ------------------------------------------------------------------ таймзоны ET -> MSK
_TZ = False
try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York"); _MSK = ZoneInfo("Europe/Moscow")
    datetime(2026, 7, 1, tzinfo=_ET).astimezone(_MSK)  # проверка, что база tz есть
    _TZ = True
except Exception:
    _TZ = False

def _is_edt(dt):
    """Грубое правило летнего времени США: 2-е вс марта .. 1-е вс ноября."""
    y = dt.year
    mar = date(y, 3, 1); mar += timedelta(days=(6 - mar.weekday()) % 7 + 7)   # 2-е воскресенье
    nov = date(y, 11, 1); nov += timedelta(days=(6 - nov.weekday()) % 7)      # 1-е воскресенье
    d = dt.date()
    return mar <= d < nov

def et_to_msk(dt_et_naive):
    """datetime (наивный, по Нью-Йорку) -> datetime (наивный, по Москве)."""
    if _TZ:
        return dt_et_naive.replace(tzinfo=_ET).astimezone(_MSK).replace(tzinfo=None)
    off = -4 if _is_edt(dt_et_naive) else -5   # EDT/EST
    return dt_et_naive + timedelta(hours=(3 - off))   # MSK = UTC+3

def now_msk():
    if _TZ:
        return datetime.now(_MSK).replace(tzinfo=None)
    return datetime.utcnow() + timedelta(hours=3)

# ------------------------------------------------------------------ индикаторы и серии
# kind: как презентовать. series — какие ряды BLS тянуть для этого показателя.
INDICATORS = {
    "nonfarm": {
        "ru": "Отчёт по занятости США (нонфарм)", "short": "нонфарм",
        "trigger": "CES0000000001",
        "match": ["нонфарм", "nonfarm", "занятост", "payroll", "заняты"],
    },
    "cpi": {
        "ru": "Инфляция США, CPI", "short": "инфляция (CPI)",
        "trigger": "CUSR0000SA0",
        "match": ["инфляц", "cpi", "потребительск"],
    },
    "ppi": {
        "ru": "Цены производителей США, PPI", "short": "цены производителей (PPI)",
        "trigger": "WPSFD4",
        "match": ["ppi", "производител", "цены производ"],
    },
    "jolts": {
        "ru": "Вакансии США, JOLTS", "short": "вакансии (JOLTS)",
        "trigger": "JTS000000000000000JOL",
        "match": ["jolts", "ваканс", "openings"],
    },
}
# все уникальные ряды для одного батч-запроса
ALL_SERIES = [
    "CES0000000001",        # нонфарм, всего занятых (тыс), SA
    "LNS14000000",          # безработица, %, SA
    "CES0500000003",        # средняя часовая оплата, $, SA
    "CUSR0000SA0",          # CPI все статьи, SA (для MoM)
    "CUUR0000SA0",          # CPI все статьи, NSA (для YoY)
    "CUSR0000SA0L1E",       # CPI базовый (без еды/энергии), SA (MoM)
    "CUUR0000SA0L1E",       # CPI базовый, NSA (YoY)
    "WPSFD4",               # PPI конечный спрос, SA (MoM)
    "WPUFD4",               # PPI конечный спрос, NSA (YoY)
    "JTS000000000000000JOL",# JOLTS вакансии (тыс)
]

# ------------------------------------------------------------------ расписание BLS 2026 (ET)
# индикатор, дата, время(ET). Используется ТОЛЬКО для заблаговременных предупреждений;
# факт детектируется по реальному продвижению данных в API (само-корректируется).
_DEFAULT_SCHEDULE = (
    [("nonfarm", d, "08:30") for d in (
        "2026-01-09","2026-02-11","2026-03-06","2026-04-03","2026-05-08","2026-06-05",
        "2026-07-02","2026-08-07","2026-09-04","2026-10-02","2026-11-06","2026-12-04")]
  + [("cpi", d, "08:30") for d in (
        "2026-01-13","2026-02-13","2026-03-11","2026-04-10","2026-05-12","2026-06-10",
        "2026-07-14","2026-08-12","2026-09-11","2026-10-14","2026-11-10","2026-12-10")]
  + [("ppi", d, "08:30") for d in (
        "2026-01-14","2026-01-30","2026-02-27","2026-03-18","2026-04-14","2026-05-13",
        "2026-06-11","2026-07-15","2026-08-13","2026-09-10","2026-10-15","2026-11-13","2026-12-15")]
  + [("jolts", d, "10:00") for d in (
        "2026-03-13","2026-03-31","2026-05-05","2026-06-02","2026-06-30","2026-08-04",
        "2026-09-01","2026-09-29","2026-11-03","2026-12-01")]
)

def _load_schedule():
    try:
        raw = json.load(open(SCHED_FILE, encoding="utf-8"))
        rows = [(r[0], r[1], r[2]) for r in raw]
    except Exception:
        rows = list(_DEFAULT_SCHEDULE)
    out = []
    for ind, dstr, tstr in rows:
        try:
            dt_et = datetime.strptime(dstr + " " + tstr, "%Y-%m-%d %H:%M")
            out.append({"ind": ind, "et": dt_et, "msk": et_to_msk(dt_et),
                        "id": f"{ind}:{dstr}"})
        except Exception:
            pass
    out.sort(key=lambda r: r["msk"])
    return out

# ------------------------------------------------------------------ состояние (persist)
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
        return True   # для BLS монитор по умолчанию ВКЛ (пользователь явно просил календарь+факт)

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

# ------------------------------------------------------------------ BLS API (батч)
_DATA = {}   # series_id -> список точек [{year, period, periodName, value(float)}], новейшие первыми
_DATA_TS = 0

def _api_poll():
    """Один POST на все ряды (экономим дневной лимит). Возвращает dict series->points."""
    global _DATA, _DATA_TS
    y = now_msk().year
    payload = {"seriesid": ALL_SERIES, "startyear": str(y - 1), "endyear": str(y)}
    k = _key()
    if k:
        payload["registrationkey"] = k
    req = urllib.request.Request(
        API_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "Nox/1.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        j = json.loads(r.read().decode("utf-8"))
    if j.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError("; ".join(j.get("message", [])) or "BLS API error")
    out = {}
    for ser in j.get("Results", {}).get("series", []):
        pts = []
        for p in ser.get("data", []):
            if p.get("period", "").startswith("M") and p.get("value") not in (None, ""):
                try:
                    pts.append({"year": int(p["year"]), "period": p["period"],
                                "pname": p.get("periodName", ""), "value": float(p["value"])})
                except Exception:
                    pass
        pts.sort(key=lambda x: (x["year"], x["period"]), reverse=True)
        out[ser["seriesID"]] = pts
    _DATA = out; _DATA_TS = time.time()
    _state.update(ok=True, err="", ts=time.time(), last_poll=time.time())
    return out

def _series(sid):
    return _DATA.get(sid, [])

def _mom(sid):
    """Изменение месяц-к-месяцу в процентах (по SA-ряду)."""
    p = _series(sid)
    if len(p) >= 2 and p[1]["value"]:
        return (p[0]["value"] - p[1]["value"]) / p[1]["value"] * 100.0
    return None

def _yoy(sid):
    """Год-к-году в процентах (по NSA-ряду): тот же месяц год назад."""
    p = _series(sid)
    if not p:
        return None
    cur = p[0]
    for q in p[1:]:
        if q["period"] == cur["period"] and q["year"] == cur["year"] - 1:
            if q["value"]:
                return (cur["value"] - q["value"]) / q["value"] * 100.0
    return None

def _level_change(sid):
    """Абсолютное изменение уровня месяц-к-месяцу (для нонфарма — в тыс, для JOLTS — в тыс)."""
    p = _series(sid)
    if len(p) >= 2:
        return p[0]["value"] - p[1]["value"]
    return None

# ------------------------------------------------------------------ форматирование чисел для TTS
def _sg(x, nd=1):
    return ("плюс " if x >= 0 else "минус ") + f"{abs(x):.{nd}f}".replace(".", ",")

def _pct(x, nd=1):
    return f"{abs(x):.{nd}f}".replace(".", ",") + " процента"

def _plural(n, forms):
    """forms=(1,2,5): 1 тысяча / 2 тысячи / 5 тысяч."""
    n = abs(int(n)); w = n % 100; d = n % 10
    if 11 <= w <= 14: return forms[2]
    if d == 1: return forms[0]
    if 2 <= d <= 4: return forms[1]
    return forms[2]

def _thousands_signed(x):
    """x в тысячах рабочих мест -> «плюс 180 тысяч» (округляем до целых тысяч)."""
    n = int(round(x))
    return ("плюс " if n >= 0 else "минус ") + f"{abs(n)} " + _plural(n, ("тысяча", "тысячи", "тысяч"))

def _millions(x_thousands):
    """Уровень в тысячах -> «7,1 миллиона»."""
    return f"{x_thousands/1000.0:.1f}".replace(".", ",") + " миллиона"

# ------------------------------------------------------------------ тексты по показателям
def _month_ru(sid):
    p = _series(sid)
    if not p:
        return ""
    en = {"January":"январь","February":"февраль","March":"март","April":"апрель","May":"май",
          "June":"июнь","July":"июль","August":"август","September":"сентябрь",
          "October":"октябрь","November":"ноябрь","December":"декабрь"}
    return en.get(p[0]["pname"], p[0]["pname"])

def _text_nonfarm():
    chg = _level_change("CES0000000001")
    unemp = _series("LNS14000000")
    earn = _mom("CES0500000003")
    mon = _month_ru("CES0000000001")
    parts = [f"Нонфарм за {mon}: {_thousands_signed(chg)} рабочих мест." if chg is not None else "Нонфарм вышел."]
    if unemp:
        u = unemp[0]["value"]; ud = _level_change("LNS14000000")
        d = (f", {_sg(ud)} пункта" if ud is not None and abs(ud) >= 0.05
             else ", без изменений" if ud is not None else "")
        parts.append(f"Безработица {str(u).replace('.', ',')} процента{d}.")
    if earn is not None:
        parts.append(f"Средняя оплата {_sg(earn)} процента за месяц.")
    return " ".join(parts)

def _text_cpi():
    mom = _mom("CUSR0000SA0"); yoy = _yoy("CUUR0000SA0")
    cmom = _mom("CUSR0000SA0L1E"); cyoy = _yoy("CUUR0000SA0L1E")
    mon = _month_ru("CUSR0000SA0")
    parts = [f"Инфляция США за {mon}:"]
    if mom is not None: parts.append(f"{_pct(mom)} за месяц,")
    if yoy is not None: parts.append(f"{_pct(yoy)} за год.")
    if cmom is not None or cyoy is not None:
        c = "Базовая:"
        if cmom is not None: c += f" {_pct(cmom)} за месяц,"
        if cyoy is not None: c += f" {_pct(cyoy)} за год."
        parts.append(c)
    return " ".join(parts)

def _text_ppi():
    mom = _mom("WPSFD4"); yoy = _yoy("WPUFD4"); mon = _month_ru("WPSFD4")
    parts = [f"Цены производителей США, PPI, за {mon}:"]
    if mom is not None: parts.append(f"{_pct(mom)} за месяц,")
    if yoy is not None: parts.append(f"{_pct(yoy)} за год.")
    return " ".join(parts)

def _text_jolts():
    p = _series("JTS000000000000000JOL"); mon = _month_ru("JTS000000000000000JOL")
    if not p:
        return "Вакансии JOLTS вышли."
    lvl = p[0]["value"]; ch = _level_change("JTS000000000000000JOL")
    d = f", изменение {_thousands_signed(ch)}" if ch is not None else ""
    return f"Вакансии JOLTS за {mon}: {_millions(lvl)}{d}."

_TEXT = {"nonfarm": _text_nonfarm, "cpi": _text_cpi, "ppi": _text_ppi, "jolts": _text_jolts}

def _period_tag(ind):
    sid = INDICATORS[ind]["trigger"]; p = _series(sid)
    return f"{p[0]['year']}-{p[0]['period']}" if p else ""

# ------------------------------------------------------------------ подача факта в сценарии (авто-клик CScalp)
def _feed_scenarios(ind, text):
    try:
        import nox_scenarios
        item = {"text": text, "body": "", "tickers": INDICATORS[ind]["match"]}
        nox_scenarios.on_news([item])
    except Exception:
        pass

# ------------------------------------------------------------------ детект факта
def _check_facts(announce=True):
    """Сравнивает последний период каждого показателя с запомненным. Новое -> озвучивает."""
    fired = []
    for ind, meta in INDICATORS.items():
        tag = _period_tag(ind)
        if not tag:
            continue
        if _ST.get("seen", {}).get(ind) == tag:
            continue
        # новый период
        _ST.setdefault("seen", {})[ind] = tag
        text = _TEXT[ind]()
        fired.append((ind, text))
        if announce:
            _announce("Вышли данные. " + text)
            _feed_scenarios(ind, text)
    if fired:
        _save_state(_ST)
    return fired

# ------------------------------------------------------------------ заблаговременные предупреждения
# пороги предупреждений в часах до релиза (подпись выбирается динамически по факту)
_PREWARN = [24, 1, 0.2]

def _when_label(r, now):
    """Естественная подпись: «сегодня/завтра в HH:MM» или «примерно через час / совсем скоро»."""
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

def _check_calendar():
    now = now_msk()
    for r in _load_schedule():
        delta_h = (r["msk"] - now).total_seconds() / 3600.0
        if delta_h <= -0.1:
            continue   # уже прошло
        for thr in _PREWARN:
            if 0 < delta_h <= thr:
                akey = f"{r['id']}:{thr}"
                if _ST.get("announced", {}).get(akey):
                    continue
                ok = _announce(f"Напоминание, сэр: {_when_label(r, now)} — "
                               f"{INDICATORS[r['ind']]['ru']}.")
                if ok:   # помечаем отправленным только если реально озвучили (app мог быть недоступен)
                    _ST.setdefault("announced", {})[akey] = True
                    _save_state(_ST)
                break

def _in_release_window():
    """True, если сейчас в окне [релиз-2мин .. релиз+50мин] любого показателя (когда стоит опрашивать API)."""
    now = now_msk()
    for r in _load_schedule():
        if (r["msk"] - timedelta(minutes=2)) <= now <= (r["msk"] + timedelta(minutes=50)):
            return True
    return False

# ------------------------------------------------------------------ монитор
def _monitor_loop():
    # тихая синхронизация при старте: запомнить текущие периоды, НЕ озвучивая старое
    try:
        _api_poll(); _check_facts(announce=False)
    except Exception as e:
        _state.update(ok=False, err=str(e))
    while True:
        try:
            if _mon_enabled():
                _check_calendar()
                in_win = _in_release_window()
                since = time.time() - _state.get("last_poll", 0)
                # в окне релиза опрашиваем часто (раз в ~90с), иначе — раз в ~3 часа (контроль)
                if (in_win and since > 85) or (not in_win and since > 3 * 3600):
                    _api_poll()
                    _check_facts(announce=True)
        except Exception as e:
            _state.update(ok=False, err=str(e))
        time.sleep(30)

# ------------------------------------------------------------------ HTTP API
@app.on_event("startup")
def _startup():
    threading.Thread(target=_monitor_loop, daemon=True).start()

@app.get("/health")
def health():
    return {"status": "ok" if _state["ok"] else "starting", "err": _state["err"],
            "mon": _mon_enabled(), "tz": _TZ, "key": bool(_key())}

@app.get("/mon")
def mon(on: int = -1):
    if on in (0, 1):
        _set_mon(bool(on))
    return {"on": _mon_enabled(),
            "text": ("Слежу за статистикой США, сэр." if _mon_enabled()
                     else "Слежка за статистикой США выключена, сэр.")}

@app.get("/calendar")
def calendar(days: int = 21):
    now = now_msk(); lim = now + timedelta(days=max(1, days))
    rows = [r for r in _load_schedule() if now <= r["msk"] <= lim]
    if not rows:
        return {"text": "Ближайших релизов США в этом окне нет, сэр.", "items": []}
    items = [{"ind": r["ind"], "name": INDICATORS[r["ind"]]["ru"],
              "msk": r["msk"].strftime("%Y-%m-%d %H:%M")} for r in rows]
    spoken = "; ".join(f"{r['msk'].strftime('%d.%m в %H:%M')} — {INDICATORS[r['ind']]['short']}"
                       for r in rows[:6])
    return {"text": "Календарь США: " + spoken + ".", "items": items}

@app.get("/next")
def nxt():
    now = now_msk()
    fut = [r for r in _load_schedule() if r["msk"] > now]
    if not fut:
        return {"text": "Ближайших релизов США не нашёл, сэр."}
    r = fut[0]; dh = (r["msk"] - now).total_seconds() / 3600.0
    when = (f"через {int(round(dh))} ч" if dh >= 1.5 else f"через {int(round(dh*60))} мин")
    return {"text": f"Следующий релиз США — {INDICATORS[r['ind']]['ru']}, "
                    f"{r['msk'].strftime('%d.%m в %H:%M')} по Москве ({when}).",
            "ind": r["ind"], "msk": r["msk"].strftime("%Y-%m-%d %H:%M")}

@app.get("/latest")
def latest(force: int = 0):
    """Последние фактические значения (кэш; force=1 — опросить сейчас)."""
    try:
        if force or not _DATA or time.time() - _DATA_TS > 6 * 3600:
            _api_poll()
    except Exception as e:
        if not _DATA:
            return {"text": f"Не смог прочитать данные BLS, сэр. {type(e).__name__}"}
    texts = [_TEXT[i]() for i in ("nonfarm", "cpi", "ppi", "jolts")]
    return {"text": " ".join(texts), "items": {i: _TEXT[i]() for i in INDICATORS}}

@app.get("/value")
def value(q: str = ""):
    ql = (q or "").lower()
    try:
        if not _DATA or time.time() - _DATA_TS > 6 * 3600:
            _api_poll()
    except Exception as e:
        if not _DATA:
            return {"text": f"Не смог прочитать данные BLS, сэр. {type(e).__name__}"}
    for ind, meta in INDICATORS.items():
        if any(m in ql for m in meta["match"]):
            return {"text": _TEXT[ind](), "ind": ind}
    return latest()

@app.get("/check")
def check():
    """Принудительный опрос + детект факта (для отладки/ручной проверки)."""
    try:
        _api_poll()
    except Exception as e:
        return {"ok": False, "err": str(e)}
    fired = _check_facts(announce=True)
    return {"ok": True, "fired": [f[0] for f in fired],
            "seen": _ST.get("seen", {}), "last_poll": _state["last_poll"]}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8128, log_level="warning")
