# -*- coding: utf-8 -*-
"""
Сценарии Nox: по новости — автоклик в нужный стакан (CScalp и т.п.) по заранее
заданным координатам. Nox ТОЛЬКО открывает/переключает стакан (один клик по
координате пользователя). Купить/Продать жмёт пользователь сам.

- Координаты задаются наведением мыши + F2 (захват точки под курсором).
- Сценарий: инструмент + событие + ветки с условиями (нет/диапазон/больше/меньше) -> координата.
- Мозг (разбор NL и извлечение числа из новости): Ollama (по умолч.) / Claude API / OpenAI API.
"""
import ctypes, time, threading, json, os, re, socket, base64, struct, subprocess
import urllib.request
from ctypes import wintypes

_CREATE_NO_WINDOW = 0x08000000  # не плодить консольные окна

_user32 = ctypes.windll.user32
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
VK_F2 = 0x71

# DPI-осознанность: без неё на мониторах с разным масштабом Windows ВИРТУАЛИЗИРУЕТ координаты,
# и GetCursorPos/SetCursorPos врут -> клик уходит не туда. PER_MONITOR_AWARE_V2 = -4.
def _enable_dpi_awareness():
    try:
        if _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per_monitor_v2"
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR
        return "per_monitor"
    except Exception:
        pass
    try:
        _user32.SetProcessDPIAware()                     # system-DPI (хуже, но лучше, чем ничего)
        return "system"
    except Exception:
        return "none"
_DPI_MODE = _enable_dpi_awareness()

STORE = r"C:\jarvis-voice\nox_scenarios.json"
IPC_HOST, IPC_PORT = "127.0.0.1", 9712

# ------------------------------------------------------------------ Win32: курсор/клик
def cursor_pos():
    pt = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    return (int(pt.x), int(pt.y))

def show_point(x, y):
    """Переместить курсор в точку (БЕЗ клика) — чтобы пользователь увидел, куда поставил."""
    try:
        _user32.SetCursorPos(int(x), int(y)); return True
    except Exception:
        return False

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
_DEF = {"brain": "ollama", "claude_key": "", "openai_key": "",
        "claude_cli_enabled": False, "claude_cli_model": "haiku", "scenarios": []}
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

# Claude через локальный CLI (ПОДПИСКА пользователя, без API-ключа). Нужен разовый `claude login`
# (+ VPN из РФ). За ~минуты вокруг события расход токенов копеечный, качество выше Ollama.
_CLAUDE_EXE = None
def _claude_cli_bin():
    global _CLAUDE_EXE
    if _CLAUDE_EXE is not None:
        return _CLAUDE_EXE
    ad = os.environ.get("APPDATA", "")
    cand = os.path.join(ad, "npm", "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe")
    _CLAUDE_EXE = cand if os.path.exists(cand) else "claude"
    return _CLAUDE_EXE

def _claude_cli(system, user, max_tokens, model="haiku"):
    p = subprocess.run(
        [_claude_cli_bin(), "-p", user, "--append-system-prompt", system, "--model", (model or "haiku")],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        creationflags=_CREATE_NO_WINDOW)
    out = (p.stdout or "").strip()
    if (not out) or ("Not logged in" in out) or ("Please run /login" in out):
        raise RuntimeError("claude cli: " + (out or (p.stderr or "")[:120] or "empty"))
    return out

_force_claude_until = 0.0   # до этого времени (unix) в окне события предпочитаем Claude

def _llm(system, user, max_tokens=120):
    cfg = load()
    b = cfg.get("brain", "ollama")
    key = cfg.get("claude_key")
    cli_on = (b == "claude_cli") or cfg.get("claude_cli_enabled", False)
    model = cfg.get("claude_cli_model", "haiku")
    in_window = time.time() < _force_claude_until
    # Выбран Claude ИЛИ окно ожидаемого события -> пробуем Claude: сначала подписку (CLI), потом ключ.
    if b in ("claude", "claude_cli") or in_window:
        if cli_on:
            try:
                return _claude_cli(system, user, max_tokens, model)
            except Exception:
                pass
        if key:
            try:
                return _claude(system, user, max_tokens, key)
            except Exception:
                pass
    if b == "openai" and cfg.get("openai_key"):
        try:
            return _openai(system, user, max_tokens, cfg["openai_key"])
        except Exception:
            pass
    return _ollama(system, user, max_tokens)   # универсальный откат

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
    "event — это НАЗВАНИЕ показателя (дивиденд, нонфарм, ставка), НЕ дата и НЕ число.\n"
    "Числа — БЕЗ единиц (к/тыс/млн/руб/%): «99к»->99, «от 75к до 99к»-> min 75 max 99.\n"
    "gt (больше X): порог в min, max=null. lt (меньше X): порог в max, min=null.\n"
    "Если для условия действие НЕ нужно — label = \"без действий\".\n"
    "Пример1: «Новатэк дивиденды: нет -> стакан 1; 1-30 руб -> стакан 2; больше 30 -> стакан 3» ->\n"
    "{\"instrument\":\"новатэк\",\"event\":\"дивиденд\",\"branches\":["
    "{\"type\":\"none\",\"min\":null,\"max\":null,\"label\":\"стакан 1\"},"
    "{\"type\":\"range\",\"min\":1,\"max\":30,\"label\":\"стакан 2\"},"
    "{\"type\":\"gt\",\"min\":30,\"max\":null,\"label\":\"стакан 3\"}]}\n"
    "Пример2: «нонфарм: больше 99к -> сценарий 1; меньше 75 -> сценарий 2; 75-99к -> без действий» ->\n"
    "{\"instrument\":\"нонфарм\",\"event\":\"нонфарм\",\"branches\":["
    "{\"type\":\"gt\",\"min\":99,\"max\":null,\"label\":\"сценарий 1\"},"
    "{\"type\":\"lt\",\"min\":null,\"max\":75,\"label\":\"сценарий 2\"},"
    "{\"type\":\"range\",\"min\":75,\"max\":99,\"label\":\"без действий\"}]}"
)
_DATE_RE = re.compile(r"\b\d{1,2}[.]\d{1,2}(?:[.]\d{2,4})?(?:\s+\d{1,2}:\d{2})?\b")

def parse_scenario(text):
    # дату/время убираем из текста ДО разбора (иначе модель путает её с событием);
    # если дата со временем — вернём её отдельно как schedule_dt (для поля «ожидается»).
    sched = None
    md = re.search(r"(\d{1,2})[.](\d{1,2})[.](\d{2,4})(?:\s+(\d{1,2}):(\d{2}))?", text)
    if md:
        dd, mo, yy = md.group(1), md.group(2), md.group(3)
        yy = ("20" + yy) if len(yy) == 2 else yy
        if md.group(4):
            sched = f"{yy}-{int(mo):02d}-{int(dd):02d} {int(md.group(4)):02d}:{md.group(5)}"
    clean = _DATE_RE.sub(" ", text)
    out = _json_from(_llm(_PARSE_SYS, clean, max_tokens=300))
    if not out or "branches" not in out:
        return None
    out["instrument"] = str(out.get("instrument", "")).lower().strip()
    ev = str(out.get("event", "")).lower().strip()
    # event не должен быть датой/числом -> откат на инструмент
    if (not ev) or re.fullmatch(r"[\d.\s:–-]+", ev):
        ev = out["instrument"]
    out["event"] = ev
    if sched:
        out["schedule_dt"] = sched
    for b in out["branches"]:
        b["label"] = b.get("label") or b.get("minLabel") or b.get("maxLabel") or "стакан"
        b.pop("minLabel", None); b.pop("maxLabel", None)
        b.setdefault("x", None); b.setdefault("y", None)
        # канонизация порогов: gt -> в min, lt -> в max
        t, mn, mx = b.get("type"), b.get("min"), b.get("max")
        if t == "gt" and mn is None and mx is not None:
            b["min"], b["max"] = mx, None
        if t == "lt" and mx is None and mn is not None:
            b["min"], b["max"] = None, mn
        lab = (b["label"] or "").lower()
        b["noop"] = any(k in lab for k in ("без действ", "ничего", "пропуск", "не трог", "skip"))
    return out

# ------------------------------------------------------------------ извлечение значения из новости
# Возвращаем ОДНО число: >0 размер; 0 — события/выплаты нет; None — размер не назван (неоднозначно).
_EXTRACT_SYS_TMPL = (
    "Из новости про «{ev}» извлеки главное число. Ответь РОВНО одним числом, без слов и единиц.\n"
    "Правила:\n"
    "- верни САМО число как в тексте, ОТБРОСив единицы (руб, %, тыс/тысяч/к, млн, млрд): "
    "«180 тысяч»->180, «35 рублей»->35, «21 процент»->21, «3 млрд»->3, «минус 20 тысяч»->-20\n"
    "- 0 — если события/выплаты нет (отказ, не выплачивать, отменили)\n"
    "- NA — если событие есть, но число не названо\n"
    "Примеры:\n"
    "«дивиденды 35 рублей на акцию» => 35\n"
    "«нонфарм вырос на 180 тысяч» => 180\n"
    "«ЦБ повысил ключевую ставку до 21 процента» => 21\n"
    "«Минфин купит валюту на 50 млрд рублей» => 50\n"
    "«рекомендовал не выплачивать дивиденды» => 0\n"
    "«совет директоров обсудит вопрос в пятницу» => NA"
)
def _extract_value(news_text, event):
    raw = (_llm(_EXTRACT_SYS_TMPL.format(ev=event), news_text, max_tokens=12) or "").strip()
    if ("na" in raw.lower()) and not re.search(r"\d", raw):
        return None   # число не названо
    m = re.search(r"-?\d+(?:[.,]\d+)?", raw)
    if not m:
        return None
    return float(m.group(0).replace(",", "."))

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
        # координаты нужны только для веток с действием (noop — без клика)
        if not branches or any(b.get("x") is None for b in branches if not b.get("noop")):
            continue
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
            sc["fired_ts"] = time.time(); changed = True
            vtxt = ("нет" if val == 0 else "не указан" if val is None
                    else (str(int(val)) if val == int(val) else str(val)))
            if branch.get("noop"):
                _announce(f"{ev} {vtxt} — в заданном диапазоне, действий не требуется, сэр.")
            else:
                click_at(branch["x"], branch["y"])
                _announce(f"Сценарий {sc.get('name','')}: открыл {branch.get('label','стакан')}. "
                          f"{ev} {vtxt}. Проверьте и жмите, сэр.")
            break
    if changed:
        save(cfg)

# ------------------------------------------------------------------ тест сценария (без ожидания новости)
def test_fire(scenario_id, value):
    """Прогнать сценарий с заданным значением: выбрать ветку и КЛИКНУТЬ (или noop). Для проверки координат."""
    cfg = load()
    sc = next((s for s in cfg.get("scenarios", []) if str(s.get("id")) == str(scenario_id)), None)
    if not sc:
        return {"ok": False, "text": "Сценарий не найден, сэр."}
    try:
        val = float(str(value).replace(",", "."))
    except Exception:
        val = None
    branch = next((b for b in sc.get("branches", []) if _branch_match(b, val)), None)
    if not branch:
        return {"ok": False, "text": f"Для значения {value} нет подходящей ветки."}
    if branch.get("noop"):
        return {"ok": True, "noop": True,
                "text": f"Тест: {value} → «{branch['label']}» (без действий, клика нет)."}
    if branch.get("x") is None:
        return {"ok": False, "text": f"У ветки «{branch['label']}» не задана координата."}
    click_at(branch["x"], branch["y"])
    return {"ok": True, "label": branch["label"], "x": branch["x"], "y": branch["y"],
            "text": f"Тест: {value} → клик по «{branch['label']}» ({branch['x']}, {branch['y']})."}

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
    # прогрев соединения/сессии: подписка (CLI) или ключ
    try:
        if cfg.get("brain") == "claude_cli" or cfg.get("claude_cli_enabled"):
            _claude_cli("Ответь одним словом: готов", "прогрев", 5, cfg.get("claude_cli_model", "haiku"))
            return
        if cfg.get("claude_key"):
            _claude("Ответь одним словом: готов", "прогрев", 5, cfg["claude_key"])
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
