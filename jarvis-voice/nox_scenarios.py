# -*- coding: utf-8 -*-
"""
Сценарии Nox: по новости — автоклик в нужный стакан (CScalp и т.п.) по заранее
заданным координатам. Nox ТОЛЬКО открывает/переключает стакан (один клик по
координате пользователя). Купить/Продать жмёт пользователь сам.

- Координаты задаются наведением мыши + F2 (захват точки под курсором).
- Сценарий: инструмент + событие + ветки с условиями (нет/диапазон/больше/меньше) -> координата.
- Мозг (разбор NL и извлечение числа из новости): Ollama (по умолч.) / Claude API / OpenAI API.
"""
import ctypes, time, threading, json, os, re, socket, base64, struct
import urllib.request
from ctypes import wintypes

_user32 = ctypes.windll.user32
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
VK_F2 = 0x71

STORE = r"C:\jarvis-voice\nox_scenarios.json"
IPC_HOST, IPC_PORT = "127.0.0.1", 9712

# ------------------------------------------------------------------ Win32: курсор/клик
def cursor_pos():
    pt = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    return (int(pt.x), int(pt.y))

def click_at(x, y, restore=True):
    try:
        back = cursor_pos() if restore else None
        _user32.SetCursorPos(int(x), int(y)); time.sleep(0.04)
        _user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0); time.sleep(0.04)
        _user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0); time.sleep(0.04)
        if back:
            _user32.SetCursorPos(back[0], back[1])
        return True
    except Exception:
        return False

# ------------------------------------------------------------------ F2-захват координаты
_captured = {"x": None, "y": None, "ts": 0.0}
def last_captured():
    return dict(_captured)

def _f2_loop():
    prev = False
    while True:
        try:
            down = (_user32.GetAsyncKeyState(VK_F2) & 0x8000) != 0
            if down and not prev:
                x, y = cursor_pos()
                _captured.update(x=x, y=y, ts=time.time())
            prev = down
        except Exception:
            pass
        time.sleep(0.03)

_f2_started = False
def start_capture():
    global _f2_started
    if not _f2_started:
        _f2_started = True
        threading.Thread(target=_f2_loop, daemon=True).start()

# ------------------------------------------------------------------ хранилище
_DEF = {"brain": "ollama", "claude_key": "", "openai_key": "", "scenarios": []}
def load():
    try:
        d = json.load(open(STORE, encoding="utf-8"))
    except Exception:
        d = {}
    for k, v in _DEF.items():
        d.setdefault(k, v if not isinstance(v, list) else [])
    return d
def save(d):
    try:
        json.dump(d, open(STORE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass

# ------------------------------------------------------------------ IPC-озвучка
def _announce(text):
    try:
        s = socket.create_connection((IPC_HOST, IPC_PORT), timeout=3)
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall((f"GET / HTTP/1.1\r\nHost: {IPC_HOST}:{IPC_PORT}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        s.recv(4096)
        data = json.dumps({"action": "announce", "text": text}, ensure_ascii=False).encode("utf-8")
        mask = os.urandom(4); hdr = bytearray([0x81]); n = len(data)
        if n < 126: hdr.append(0x80 | n)
        else: hdr.append(0x80 | 126); hdr += struct.pack(">H", n)
        hdr += mask
        s.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
        time.sleep(0.15); s.close()
    except Exception:
        pass

# ------------------------------------------------------------------ мозг (роутер)
def _http_json(url, body, headers, timeout=25):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))

def _ollama(system, user, max_tokens):
    r = _http_json("http://127.0.0.1:11434/v1/chat/completions",
                   {"model": "qwen2.5:3b", "messages": [{"role": "system", "content": system},
                    {"role": "user", "content": user}], "stream": False, "temperature": 0,
                    "max_tokens": max_tokens, "keep_alive": "30m"},
                   {"Content-Type": "application/json"})
    return r["choices"][0]["message"]["content"]

def _claude(system, user, max_tokens, key):
    r = _http_json("https://api.anthropic.com/v1/messages",
                   {"model": "claude-haiku-4-5-20251001", "max_tokens": max_tokens,
                    "system": system, "messages": [{"role": "user", "content": user}]},
                   {"Content-Type": "application/json", "x-api-key": key,
                    "anthropic-version": "2023-06-01"})
    return r["content"][0]["text"]

def _openai(system, user, max_tokens, key):
    r = _http_json("https://api.openai.com/v1/chat/completions",
                   {"model": "gpt-4o-mini", "messages": [{"role": "system", "content": system},
                    {"role": "user", "content": user}], "temperature": 0, "max_tokens": max_tokens},
                   {"Content-Type": "application/json", "Authorization": "Bearer " + key})
    return r["choices"][0]["message"]["content"]

_force_claude_until = 0.0   # до этого времени (unix) в окне события предпочитаем Claude

def _llm(system, user, max_tokens=120):
    cfg = load()
    b = cfg.get("brain", "ollama")
    key = cfg.get("claude_key")
    # В окне ожидаемого события — Claude (точнее выбор стакана), если ключ есть; иначе обычный роутер.
    if time.time() < _force_claude_until and key:
        b = "claude"
    try:
        if b == "claude" and key:
            return _claude(system, user, max_tokens, key)
        if b == "openai" and cfg.get("openai_key"):
            return _openai(system, user, max_tokens, cfg["openai_key"])
    except Exception:
        pass  # при сбое платного API — падаем на Ollama
    return _ollama(system, user, max_tokens)

def _json_from(text):
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0) if m else text)
    except Exception:
        return None

# ------------------------------------------------------------------ разбор сценария из речи
_PARSE_SYS = (
    "Преобразуй описание торгового сценария в СТРОГИЙ JSON без пояснений.\n"
    "Формат: {\"instrument\":\"<бумага>\",\"event\":\"<событие, одно слово>\",\"branches\":["
    "{\"type\":\"none|range|gt|lt|any\",\"min\":число|null,\"max\":число|null,\"label\":\"<куда>\"}]}\n"
    "type: none — события/выплаты нет; range — min..max; gt — больше min; lt — меньше max; any — любое.\n"
    "Пример: «Новатэк дивиденды: нет -> стакан 1; 1-30 руб -> стакан 2; больше 30 -> стакан 3» ->\n"
    "{\"instrument\":\"новатэк\",\"event\":\"дивиденд\",\"branches\":["
    "{\"type\":\"none\",\"min\":null,\"max\":null,\"label\":\"стакан 1\"},"
    "{\"type\":\"range\",\"min\":1,\"max\":30,\"label\":\"стакан 2\"},"
    "{\"type\":\"gt\",\"min\":30,\"max\":null,\"label\":\"стакан 3\"}]}"
)
def parse_scenario(text):
    out = _json_from(_llm(_PARSE_SYS, text, max_tokens=300))
    if not out or "branches" not in out:
        return None
    out["instrument"] = str(out.get("instrument", "")).lower().strip()
    out["event"] = str(out.get("event", "")).lower().strip()
    for b in out["branches"]:
        b["label"] = b.get("label") or b.get("minLabel") or b.get("maxLabel") or "стакан"
        b.pop("minLabel", None); b.pop("maxLabel", None)
        b.setdefault("x", None); b.setdefault("y", None)
    return out

# ------------------------------------------------------------------ извлечение значения из новости
# Возвращаем ОДНО число: >0 размер; 0 — события/выплаты нет; None — размер не назван (неоднозначно).
_EXTRACT_SYS_TMPL = (
    "Извлеки размер события «{ev}» из новости. Ответь РОВНО одним числом, без слов:\n"
    "- размер в рублях, если он указан (например 35)\n"
    "- 0 — если выплаты/события нет (отказ, не выплачивать, отменили, не рекомендовал)\n"
    "- -1 — если событие есть, но конкретный размер не назван\n"
    "Примеры:\n"
    "«Совет директоров рекомендовал дивиденды 35 рублей на акцию» => 35\n"
    "«дивиденды 20 руб на акцию» => 20\n"
    "«рекомендовал не выплачивать дивиденды» => 0\n"
    "«отказались от дивидендов» => 0\n"
    "«совет директоров обсудит дивиденды в пятницу» => -1"
)
def _extract_value(news_text, event):
    raw = _llm(_EXTRACT_SYS_TMPL.format(ev=event), news_text, max_tokens=12) or ""
    m = re.search(r"-?\d+(?:[.,]\d+)?", raw)
    if not m:
        return None
    v = float(m.group(0).replace(",", "."))
    return None if v == -1 else v   # -1 -> неоднозначно (None)

def _branch_match(b, val):
    t = b.get("type")
    if t == "any":
        return True
    if t == "none":
        return val == 0
    if val is None:
        return False
    mn, mx = b.get("min"), b.get("max")
    if t == "range":
        return (mn is None or val >= mn) and (mx is None or val <= mx)
    if t == "gt":
        return mn is not None and val > mn
    if t == "lt":
        return mx is not None and val < mx
    if t == "ge":
        return mn is not None and val >= mn
    if t == "le":
        return mx is not None and val <= mx
    return False

# ------------------------------------------------------------------ движок: новость -> клик
_COOLDOWN = 6 * 3600  # не повторять один сценарий чаще раза в 6 часов

def on_news(items):
    """items: [{text, tickers, ...}] — свежие новости (newest first). Проверяет активные сценарии."""
    cfg = load()
    changed = False
    for sc in cfg.get("scenarios", []):
        if not sc.get("enabled"):
            continue
        branches = sc.get("branches", [])
        if not branches or any(b.get("x") is None for b in branches):
            continue  # координаты не заданы
        if time.time() - sc.get("fired_ts", 0) < _COOLDOWN:
            continue
        inst = (sc.get("instrument") or "").lower()
        ev = (sc.get("event") or "").lower()
        for it in items:
            low = (it.get("text", "") + " " + it.get("body", "")).lower()
            tks = [t.lower() for t in (it.get("tickers") or [])]
            if inst and not (inst in low or inst in tks):
                continue
            if ev and ev not in low:
                continue
            val = _extract_value(it.get("text", ""), ev)
            branch = next((b for b in branches if _branch_match(b, val)), None)
            if not branch:
                continue
            click_at(branch["x"], branch["y"])
            sc["fired_ts"] = time.time(); changed = True
            vtxt = ("нет" if val == 0 else "не указан" if val is None
                    else (str(int(val)) if val == int(val) else str(val)))
            _announce(f"Сценарий {sc.get('name','')}: открыл {branch.get('label','стакан')}. "
                      f"{ev} {vtxt}. Проверьте и жмите, сэр.")
            break
    if changed:
        save(cfg)

# ------------------------------------------------------------------ планировщик ожидаемых событий
# У сценария могут быть поля: schedule_dt "YYYY-MM-DD HH:MM" (МСК) и prewarm_min (по умолч. 3).
# За prewarm_min до времени: анонс + прогрев Claude + окно, в котором мозг = Claude (с откатом).
# Сам клик по стакану срабатывает как обычно — по факту новости (США: BLS/BEA; РФ: лента терминала).
import datetime as _dt
try:
    from zoneinfo import ZoneInfo as _ZI
    _MSK_TZ = _ZI("Europe/Moscow"); _dt.datetime.now(_MSK_TZ)
    def _now_msk(): return _dt.datetime.now(_MSK_TZ).replace(tzinfo=None)
except Exception:
    def _now_msk(): return _dt.datetime.utcnow() + _dt.timedelta(hours=3)

_WINDOW_AFTER = 45 * 60   # сколько секунд после времени события держать Claude «тёплым»

def _parse_dt(s):
    for fmt in ("%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M", "%d.%m %H:%M"):
        try:
            d = _dt.datetime.strptime(s.strip(), fmt)
            if d.year == 1900:
                d = d.replace(year=_now_msk().year)
            return d
        except Exception:
            continue
    return None

def set_schedule(scenario_id, dt_str, prewarm_min=3):
    cfg = load()
    for sc in cfg.get("scenarios", []):
        if str(sc.get("id")) == str(scenario_id):
            d = _parse_dt(dt_str)
            if not d:
                return {"ok": False, "text": "Не понял время, сэр. Формат: 2026-10-29 16:30."}
            sc["schedule_dt"] = d.strftime("%Y-%m-%d %H:%M")
            sc["prewarm_min"] = int(prewarm_min or 3)
            sc["_prewarmed"] = False
            save(cfg)
            return {"ok": True, "text": f"Событие «{sc.get('name', sc.get('event',''))}» назначено на "
                                        f"{d.strftime('%d.%m в %H:%M')}, мозг подготовлю за {sc['prewarm_min']} мин."}
    return {"ok": False, "text": "Такой сценарий не найден, сэр."}

def _prime_claude(cfg):
    key = cfg.get("claude_key")
    if not key:
        return
    try:
        _claude("Ответь одним словом: готов", "прогрев", 5, key)
    except Exception:
        pass

def _scheduler_loop():
    global _force_claude_until
    while True:
        try:
            cfg = load(); changed = False
            now = _now_msk()
            for sc in cfg.get("scenarios", []):
                d = _parse_dt(sc.get("schedule_dt", "") or "")
                if not d:
                    continue
                pre = int(sc.get("prewarm_min", 3)) * 60
                secs = (d - now).total_seconds()
                # окно [T-pre .. T+45мин]: держим Claude наготове
                if -_WINDOW_AFTER <= secs <= pre:
                    _force_claude_until = max(_force_claude_until, time.time() + _WINDOW_AFTER + max(0, secs))
                # разовый прогрев + анонс в момент наступления T-pre
                if 0 < secs <= pre and not sc.get("_prewarmed"):
                    sc["_prewarmed"] = True; changed = True
                    _prime_claude(cfg)
                    mins = max(1, int(round(secs / 60)))
                    _announce(f"Готовлюсь, сэр: через {mins} мин — "
                              f"{sc.get('name') or sc.get('event') or 'событие'}. Мозг наготове.")
                # далёкое будущее (переназначили) — снять флаг, чтобы снова прогрелся
                if secs > pre + 120 and sc.get("_prewarmed"):
                    sc["_prewarmed"] = False; changed = True
            if changed:
                save(cfg)
        except Exception:
            pass
        time.sleep(20)

_sched_started = False
def start_scheduler():
    global _sched_started
    if not _sched_started:
        _sched_started = True
        threading.Thread(target=_scheduler_loop, daemon=True).start()

if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    print("parse:", json.dumps(parse_scenario(
        "Новатэк дивиденды: если нет дивидендов стакан 1, от 1 до 30 рублей стакан 2, больше 30 стакан 3"),
        ensure_ascii=False))
    for n in ["Совет директоров Новатэка рекомендовал дивиденды 35 рублей на акцию",
              "Новатэк рекомендовал не выплачивать дивиденды за год",
              "Новатэк дивиденды 20 рублей на акцию",
              "Совет директоров Новатэка обсудит дивиденды в пятницу"]:
        v = _extract_value(n, "дивиденд")
        sc = parse_scenario("Новатэк дивиденды: нет стакан 1, от 1 до 30 стакан 2, больше 30 стакан 3")
        br = next((b for b in sc["branches"] if _branch_match(b, v)), None) if sc else None
        print(f"val={v} -> {br['label'] if br else 'нет ветки'}  | {n[:40]}")
