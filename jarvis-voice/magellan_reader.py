# -*- coding: utf-8 -*-
"""
Russian Magellan PRO (order flow MOEX) reader/monitor для Nox.
Читает открытые JSON сайта (https://russianmagellan.pro/data/*.json) — БЕЗ парсинга DOM.
Считает по каждой бумаге: оборот, дисбаланс (покупатели/продавцы, %), дельту, движение цены,
долю крупных сделок. Следит за выбранными пользователем тикерами и уведомляет голосом (IPC).
Только ЧТЕНИЕ и УВЕДОМЛЕНИЯ — сделок не совершает.
"""
import threading, time, json, os, socket, base64, struct, re
import urllib.request
from fastapi import FastAPI
import uvicorn

BASE = "https://russianmagellan.pro/data"
IPC_HOST, IPC_PORT = "127.0.0.1", 9712
FILTER_FILE = r"C:\jarvis-voice\magellan_filter.json"
MON_FLAG = r"C:\jarvis-voice\magellan_mon_on.txt"

app = FastAPI()

_names = {}          # ticker -> name
_names_ts = 0
_last_alert = {}     # (ticker, kind) -> ts (антиспам)
_state = {"ok": False, "err": "", "ts": 0}

# ------------------------------------------------------------------ конфиг фильтра
_DEFAULTS = {
    "tickers": [],            # за какими бумагами следить (пусто = ни за кем)
    "min_imbalance": 15,      # |дисбаланс| в % для алерта
    "min_price": 2.0,         # |движение цены| в % для алерта
    "big_ratio": 0.5,         # доля крупных сделок в обороте для алерта
    "min_turnover": 300,      # млн ₽ — минимальный оборот, чтобы вообще реагировать
    "cooldown": 1200,         # сек между повторными алертами по одной бумаге/типу
}

def _load_filter():
    try:
        d = json.load(open(FILTER_FILE, encoding="utf-8"))
    except Exception:
        d = {}
    for k, v in _DEFAULTS.items():
        d.setdefault(k, v)
    d["tickers"] = [str(x).upper().strip() for x in d.get("tickers", []) if str(x).strip()]
    return d

def _save_filter(d):
    try:
        json.dump(d, open(FILTER_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass

def _mon_enabled():
    try:
        return open(MON_FLAG, encoding="utf-8").read().strip() != "0"
    except Exception:
        return False  # по умолчанию монитор Магеллана ВЫКЛ (включает пользователь)

def _set_mon(on):
    try:
        open(MON_FLAG, "w", encoding="utf-8").write("1" if on else "0")
    except Exception:
        pass

# ------------------------------------------------------------------ IPC-озвучка
def _ipc_announce(text):
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

# ------------------------------------------------------------------ данные
def _get_json(path):
    req = urllib.request.Request(f"{BASE}/{path}", headers={"User-Agent": "Nox/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))

def _names_map():
    global _names, _names_ts
    if _names and time.time() - _names_ts < 3600:
        return _names
    try:
        j = _get_json("securities.json")
        _names = {k: (v.get("name") or k) for k, v in j.get("data", {}).items()}
        _names_ts = time.time()
    except Exception:
        pass
    return _names

# Чистые произносимые имена для основных тикеров (иначе TTS читает «Татнфт 3ао» и т.п.).
TICKER_SPOKEN = {
    "SBER": "Сбербанк", "SBERP": "Сбербанк преф", "GAZP": "Газпром", "LKOH": "Лукойл",
    "GMKN": "Норникель", "ROSN": "Роснефть", "NVTK": "Новатэк", "SNGS": "Сургутнефтегаз",
    "SNGSP": "Сургут преф", "TATN": "Татнефть", "TATNP": "Татнефть преф", "PLZL": "Полюс",
    "PIKK": "Пик", "MGNT": "Магнит", "MTSS": "Эм тэ эс", "VTBR": "Вэ тэ бэ", "YDEX": "Яндекс",
    "OZON": "Озон", "OZPH": "Озон Фармацевтика", "MOEX": "Московская биржа", "AFLT": "Аэрофлот",
    "CHMF": "Северсталь", "NLMK": "Эн эл эм ка", "MAGN": "Магнитка", "ALRS": "Алроса",
    "PHOR": "Фосагро", "RUAL": "Русал", "AFKS": "Система", "FEES": "Россети", "HYDR": "Русгидро",
    "IRAO": "Интер РАО", "LENT": "Лента", "X5": "Икс пять", "SMLT": "Самолёт", "T": "Т Технологии",
    "BSPB": "Банк Санкт-Петербург", "POSI": "Позитив", "SIBN": "Газпромнефть",
    "TRNFP": "Транснефть преф", "VKCO": "Вэ Ка", "HEAD": "Хэдхантер", "SVCB": "Совкомбанк",
    "FLOT": "Совкомфлот", "MTLR": "Мечел", "RASP": "Распадская", "SPBE": "Эс Пэ Бэ биржа",
    "DOMRF": "Дом Эр Эф", "CBOM": "Эм Ка Бэ", "WUSH": "Вуш", "AQUA": "Инарктика",
    "ENPG": "Эн плюс", "UNAC": "Объединённая авиастроительная",
}
_NAME_SUFFIX_RE = re.compile(r"[\s\-]*(?:\d*а[оп]|-п|\bао\b|\bап\b|\bгдр\b|\bojsc\b)\s*$", re.I)

def _clean_name(nm):
    """«Самолет ао»->«Самолет», «Татнфт 3ао»->«Татнфт», без хвостов ао/ап/гдр."""
    return _NAME_SUFFIX_RE.sub("", nm or "").strip() or nm

def _name(tk):
    tk = tk.upper()
    if tk in TICKER_SPOKEN:
        return TICKER_SPOKEN[tk]
    return _clean_name(_names_map().get(tk, tk))

def _metrics(row):
    bv = float(row.get("bv", 0) or 0)
    sv = float(row.get("sv", 0) or 0)
    turn = bv + sv
    imb = ((bv - sv) / turn * 100.0) if turn else 0.0
    delta = bv - sv
    big = float(row.get("bigBv", 0) or 0) + float(row.get("bigSv", 0) or 0)
    big_ratio = (big / turn) if turn else 0.0
    days = row.get("days") or []
    closes = [d.get("close") for d in days if d.get("close")]
    price = closes[-1] if closes else 0.0
    price_chg = ((closes[-1] - closes[0]) / closes[0] * 100.0) if len(closes) >= 2 and closes[0] else 0.0
    return {"turnover": turn, "imbalance": imb, "delta": delta,
            "big_ratio": big_ratio, "price": price, "price_chg": price_chg}

_today_cache = {"data": {}, "ts": 0}

def _fetch_today():
    j = _get_json("today.json")
    out = {}
    for row in j.get("data", []):
        tk = (row.get("ticker") or "").upper()
        if tk:
            out[tk] = _metrics(row)
    _state.update(ok=True, err="", ts=time.time())
    _today_cache.update(data=out, ts=time.time())
    return out

def _today(max_age=60):
    """Данные потока из кэша (обновляет монитор каждые 45с); тянет сеть только если кэш устарел."""
    if _today_cache["data"] and time.time() - _today_cache["ts"] < max_age:
        return _today_cache["data"]
    return _fetch_today()

# ------------------------------------------------------------------ озвучка чисел
def _plural(n, forms):
    """forms=(1,2,5): 1 миллиард / 2 миллиарда / 5 миллиардов."""
    n = abs(int(n)); w = n % 100; d = n % 10
    if 11 <= w <= 14: return forms[2]
    if d == 1: return forms[0]
    if 2 <= d <= 4: return forms[1]
    return forms[2]

def _turn_words(v):
    # Округляем и согласуем слово с числом (TTS прочитает цифру словом):
    # 3.2 млрд -> «3 миллиарда рублей» (натуральнее, чем «три целых две десятых»).
    if v >= 1e9:
        n = round(v / 1e9)
        return f"{n} {_plural(n, ('миллиард', 'миллиарда', 'миллиардов'))} рублей"
    if v >= 1e6:
        n = round(v / 1e6)
        return f"{n} {_plural(n, ('миллион', 'миллиона', 'миллионов'))} рублей"
    return f"{int(v)} рублей"

def _pct_words(p):
    sign = "плюс" if p >= 0 else "минус"
    return f"{sign} {abs(p):.1f} процента"

def _alert_text(tk, m, kinds):
    parts = [f"Магеллан: {_name(tk)}"]
    if "imbalance" in kinds:
        who = "покупатели" if m["imbalance"] > 0 else "продавцы"
        parts.append(f"{who} {abs(round(m['imbalance']))} процентов")
    if "price" in kinds:
        parts.append(_pct_words(m["price_chg"]))
    if "big" in kinds:
        parts.append("крупный игрок в потоке")
    parts.append(f"оборот {_turn_words(m['turnover'])}.")
    return ", ".join(parts[:-1]) + ", " + parts[-1] if len(parts) > 1 else parts[0]

def _check_and_alert(data, cfg):
    now = time.time()
    watch = set(cfg["tickers"])
    if not watch:
        return
    for tk in watch:
        m = data.get(tk)
        if not m:
            continue
        if m["turnover"] < cfg["min_turnover"] * 1e6:
            continue
        kinds = []
        if abs(m["imbalance"]) >= cfg["min_imbalance"]:
            kinds.append("imbalance")
        if abs(m["price_chg"]) >= cfg["min_price"]:
            kinds.append("price")
        if m["big_ratio"] >= cfg["big_ratio"]:
            kinds.append("big")
        if not kinds:
            continue
        key = (tk, ",".join(kinds))
        if now - _last_alert.get(key, 0) < cfg["cooldown"]:
            continue
        _last_alert[key] = now
        _ipc_announce(_alert_text(tk, m, kinds))

def _monitor_loop():
    while True:
        try:
            # Кэш обновляем ВСЕГДА (чтобы /pulse и /flow были мгновенными),
            # а уведомляем только когда слежка включена.
            data = _fetch_today()
            if _mon_enabled():
                _check_and_alert(data, _load_filter())
        except Exception as e:
            _state.update(ok=False, err=str(e))
        time.sleep(45)

# ------------------------------------------------------------------ HTTP API
@app.on_event("startup")
def _startup():
    _names_map()
    try: _fetch_today()   # прогреть кэш сразу
    except Exception: pass
    threading.Thread(target=_monitor_loop, daemon=True).start()

@app.get("/health")
def health():
    return {"status": "ok" if _state["ok"] else "starting", "err": _state["err"], "mon": _mon_enabled()}

@app.get("/pulse")
def pulse(n: int = 5):
    """Кратко: топ бумаг по обороту + перекос рынка (для голоса «магеллан/поток сделок»)."""
    try:
        data = _today()
    except Exception as e:
        return {"text": f"Не смог прочитать поток, сэр. {type(e).__name__}"}
    rows = sorted(data.items(), key=lambda kv: kv[1]["turnover"], reverse=True)[:max(1, min(n, 8))]
    parts = []
    for tk, m in rows:
        who = "покупают" if m["imbalance"] > 0 else "продают"
        parts.append(f"{_name(tk)} {who}, {_pct_words(m['price_chg'])}")
    return {"text": "Поток сделок. " + ". ".join(parts) + ".", "items": [
        {"ticker": tk, **m} for tk, m in rows]}

def _resolve_ticker(q, data):
    """Тикер по запросу: точный тикер, иначе фаззи по русскому имени (в т.ч. склонения)."""
    qn = (q or "").strip().lower()
    if not qn:
        return None
    up = qn.upper().replace(" ", "")
    if up in data:
        return up
    names = _names_map()
    stem = qn[:4]  # «сберу»->«сбер», «озону»->«озон», «самолету»->«само»
    best, best_turn = None, -1.0
    for tk, name in names.items():
        if tk not in data:
            continue
        nl = (name or "").lower()
        first = nl.split()[0] if nl else ""
        if nl.startswith(stem) or (first and (first.startswith(stem) or qn.startswith(first[:5]))):
            turn = data[tk]["turnover"]
            if turn > best_turn:
                best, best_turn = tk, turn
    return best

@app.get("/flow")
def flow(q: str = ""):
    if not q.strip():
        return {"text": "Уточните бумагу, сэр."}
    try:
        data = _today()
    except Exception as e:
        return {"text": f"Не смог прочитать поток, сэр. {type(e).__name__}"}
    tk = _resolve_ticker(q, data)
    m = data.get(tk) if tk else None
    if not m:
        # не распознали конкретную бумагу — отдаём общую сводку, чтобы не молчать
        return pulse(5)
    who = "покупатели" if m["imbalance"] > 0 else "продавцы"
    return {"text": f"{_name(tk)}: {who} {abs(round(m['imbalance']))} процентов, "
                    f"{_pct_words(m['price_chg'])}, оборот {_turn_words(m['turnover'])}.",
            "metrics": m}

@app.get("/filter")
def filter_show():
    cfg = _load_filter()
    return {"filter": cfg, "mon": _mon_enabled(),
            "text": ("Слежу за: " + ", ".join(cfg["tickers"])) if cfg["tickers"] else "Список слежки Магеллана пуст, сэр."}

@app.get("/filter_add")
def filter_add(kind: str = "ticker", q: str = ""):
    v = q.strip().upper().replace(" ", "")
    if not v:
        return {"text": "Что добавить, сэр?"}
    cfg = _load_filter()
    if v not in cfg["tickers"]:
        cfg["tickers"].append(v)
    _save_filter(cfg)
    return {"text": f"Слежу за {_name(v)} в Магеллане.", "filter": cfg}

@app.get("/filter_remove")
def filter_remove(kind: str = "ticker", q: str = ""):
    v = q.strip().upper().replace(" ", "")
    cfg = _load_filter()
    cfg["tickers"] = [x for x in cfg["tickers"] if x != v]
    _save_filter(cfg)
    return {"text": f"Убрал {v}.", "filter": cfg}

@app.get("/filter_clear")
def filter_clear():
    cfg = _load_filter()
    cfg["tickers"] = []
    _save_filter(cfg)
    return {"text": "Список слежки Магеллана очищен, сэр.", "filter": cfg}

@app.get("/settings")
def settings(min_imbalance: float = -1, min_price: float = -1, big_ratio: float = -1,
            min_turnover: float = -1, cooldown: float = -1):
    cfg = _load_filter()
    if min_imbalance >= 0: cfg["min_imbalance"] = min_imbalance
    if min_price >= 0: cfg["min_price"] = min_price
    if big_ratio >= 0: cfg["big_ratio"] = big_ratio
    if min_turnover >= 0: cfg["min_turnover"] = min_turnover
    if cooldown >= 0: cfg["cooldown"] = cooldown
    _save_filter(cfg)
    return {"filter": cfg, "text": "Настройки Магеллана сохранены, сэр."}

@app.get("/mon")
def mon(on: int = -1):
    if on in (0, 1):
        _set_mon(bool(on))
    return {"text": ("Слежу за Магелланом, сэр." if _mon_enabled() else "Слежка за Магелланом выключена, сэр."),
            "on": _mon_enabled()}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8127, log_level="warning")
