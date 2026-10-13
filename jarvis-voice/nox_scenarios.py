# -*- coding: utf-8 -*-
"""
Сценарии Nox: по новости — автоклик в нужный стакан (CScalp и т.п.) по заранее
заданным координатам. Nox ТОЛЬКО открывает/переключает стакан (один клик по
координате пользователя). Купить/Продать жмёт пользователь сам.

- Координаты задаются наведением мыши + F2 (захват точки под курсором).
- Сценарий: инструмент + событие + ветки с условиями (нет/диапазон/больше/меньше) -> координата.
- Мозг (разбор NL и извлечение числа из новости): облако по API-ключу (APIMIRA и т.п.) или Ollama —
  выбор один, на странице «Мозг» (app.db). Подписка Claude убрана.
"""
import ctypes, time, threading, json, os, re, socket, base64, struct
import urllib.request
from ctypes import wintypes


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
_DEF = {"scenarios": []}

# общий конфиг GUI (app.db) — чтобы ключ/URL/модель OpenAI задавались в ОДНОМ месте
_APPDB = r"C:\Users\Дамир\AppData\Roaming\com.priler.jarvis\app.db"
def _appdb():
    try:
        return json.loads(open(_APPDB, "rb").read().decode("utf-8-sig"))
    except Exception:
        return {}
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

# Правило речи для ВСЕХ ответов, которые Нокс произносит вслух (дописывается к системным подсказкам).
SPEECH_RULES = (
    " Пиши грамотным литературным русским языком: правильные падежи, склонения и окончания; "
    "согласуй числа со словами (одна акция, две акции, пять акций; до двадцати одного процента; "
    "в две тысячи двадцать шестом году). Иностранные названия и компании — по-русски (Рейтер, Блумберг, "
    "Сбер, Новатэк), без латиницы. Без markdown, списков, эмодзи и ссылок — только связный текст для озвучки."
)

# ------------------------------------------------------------------ мозг: облако по ключу или Ollama
# Выбор ОДИН — страница «Мозг» (app.db): brain_backend "openai" = облако (openai_url/openai_model/
# api_key__openai) или "ollama" = локально и бесплатно (ollama_model). Подписка Claude убрана.
def _http_json(url, body, headers, timeout=25):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))

_OLLAMA_URL = "http://127.0.0.1:11434"
_OLLAMA_DEFAULT = "qwen2.5:3b"
_CLOUD_DEFAULT_URL = "https://apimira.com/v1"

def _ollama_model():
    return (_appdb().get("ollama_model") or "").strip() or _OLLAMA_DEFAULT

def brain_choice():
    """'openai' (облако по ключу) или 'ollama'. Старый «локальный» выбор (openai + адрес Ollama) -> ollama."""
    adb = _appdb()
    if (adb.get("brain_backend") or "").strip().lower() == "ollama" or "11434" in (adb.get("openai_url") or ""):
        return "ollama"
    return "openai"

def _cloud():
    """(ключ, адрес, модель) облака из app.db."""
    adb = _appdb()
    url = (adb.get("openai_url") or "").strip()
    if not url or "11434" in url:
        url = _CLOUD_DEFAULT_URL
    return (adb.get("api_keys", {}).get("openai", "") or "", url,
            (adb.get("openai_model") or "").strip() or "openai/gpt-5.4-mini")

def _ollama(system, user, max_tokens, model=None):
    r = _http_json(_OLLAMA_URL + "/v1/chat/completions",
                   {"model": model or _ollama_model(), "messages": [{"role": "system", "content": system},
                    {"role": "user", "content": user}], "stream": False, "temperature": 0,
                    "max_tokens": max_tokens, "keep_alive": -1},   # -1: держать в VRAM всегда (без холодного старта)
                   {"Content-Type": "application/json"})
    return r["choices"][0]["message"]["content"]

def warm_ollama():
    """Загрузить модель в VRAM заранее (чтобы на событии не было холодного старта ~5с)."""
    try:
        _http_json(_OLLAMA_URL + "/api/generate",
                   {"model": _ollama_model(), "prompt": "ок", "stream": False, "keep_alive": -1},
                   {"Content-Type": "application/json"}, timeout=60)
    except Exception:
        pass

def _openai(system, user, max_tokens, key, url=None, model=None):
    base = (url or _CLOUD_DEFAULT_URL).rstrip("/")
    r = _http_json(base + "/chat/completions",
                   {"model": (model or "openai/gpt-5.4-mini"), "messages": [{"role": "system", "content": system},
                    {"role": "user", "content": user}], "temperature": 0, "max_tokens": max_tokens},
                   {"Content-Type": "application/json", "Authorization": "Bearer " + key})
    return r["choices"][0]["message"]["content"]

# Облако (APIMIRA) отвечает с большим разбросом: тот же запрос — то 2 с, то 8 с, иногда виснет до таймаута.
# Поэтому: не ответило за CLOUD_HEDGE с — шлём тот же запрос ещё раз и берём первый ответ;
# не ответило за CLOUD_DEADLINE с — считаем недоступным (вызывающий откатывается на Ollama, ~1 с).
CLOUD_HEDGE = 3.0
CLOUD_DEADLINE = 9.0


def _openai_fast(system, user, max_tokens, key, url=None, model=None):
    import queue
    q = queue.Queue()
    t0 = time.time()

    def run(tag):
        try:
            q.put((tag, _openai(system, user, max_tokens, key, url, model), None))
        except Exception as e:
            q.put((tag, None, e))

    threading.Thread(target=run, args=("1",), daemon=True).start()
    started, errors, err = 1, 0, None
    while True:
        left = CLOUD_DEADLINE - (time.time() - t0)
        if left <= 0:
            break
        wait = min(left, CLOUD_HEDGE - (time.time() - t0)) if started == 1 else left
        try:
            tag, res, e = q.get(timeout=max(0.05, wait))
        except queue.Empty:
            if started == 1:                       # медленно — дублируем запрос
                threading.Thread(target=run, args=("2",), daemon=True).start(); started = 2
            continue
        if res:
            if started == 2:
                _log(f"ОБЛАКО: ответ запроса {tag} за {time.time()-t0:.1f}s (был дубль)")
            return res
        errors += 1; err = e
        if started == 1:                           # первый упал — сразу второй
            threading.Thread(target=run, args=("2",), daemon=True).start(); started = 2
        elif errors >= started:
            break
    _log(f"ОБЛАКО не ответило за {time.time()-t0:.1f}s ({err or 'таймаут'}) — откат на Ollama")
    raise TimeoutError(f"облако не ответило за {CLOUD_DEADLINE:.0f} с")


_race_until = 0.0   # до этого времени (unix) окно события -> ГОНКА облака и Ollama
_last_warm = 0.0
def arm_window(seconds=360):
    """Активировать окно ГОНКИ источников на N секунд (вызывают BLS/BEA в окне релиза) + прогрев.
    Так US-события (нонфарм/CPI/ВВП/PCE) авто-получают гонку облако+Ollama без ручной настройки."""
    global _race_until, _last_warm
    _race_until = max(_race_until, time.time() + seconds)
    if time.time() - _last_warm > 120:   # прогрев не чаще раза в 2 мин
        _last_warm = time.time()
        threading.Thread(target=warm_ollama, daemon=True).start()

# -------- лог (чтобы «по логам посмотреть», кто быстрее и что извлекли) --------
_LOG = r"C:\jarvis-voice\nox_scenarios.log"
def _log(msg):
    try:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg) + "\n")
    except Exception:
        pass

def _sources(racing):
    """Источники (имя, функция). Выбрана Ollama или нет ключа — только Ollama.
    Облако: вне окна события — облако, откат Ollama; в окне — гонка обоих."""
    key, url, model = _cloud()
    oll = ("ollama", lambda s, u, m: _ollama(s, u, m))
    oai = ("openai", lambda s, u, m: _openai_fast(s, u, m, key, url, model))
    if brain_choice() == "ollama" or not key:
        return [oll]
    return [oll, oai] if racing else [oai, oll]

def _llm(system, user, max_tokens=120):
    """В окне события — ГОНКА источников (первый валидный ответ побеждает, остальные логируются).
    Вне окна — выбранный мозг с откатом на Ollama."""
    racing = time.time() < _race_until
    srcs = _sources(racing)
    if not racing or len(srcs) <= 1:
        for name, fn in srcs:
            try:
                return fn(system, user, max_tokens)
            except Exception:
                continue
        return _ollama(system, user, max_tokens)
    # --- гонка ---
    import queue
    q = queue.Queue(); t0 = time.time()
    def run(name, fn):
        try:
            r = fn(system, user, max_tokens)
            q.put((name, r, time.time() - t0, None))
        except Exception as e:
            q.put((name, None, time.time() - t0, str(e)[:70]))
    for nm, fn in srcs:
        threading.Thread(target=run, args=(nm, fn), daemon=True).start()
    winner, logs = None, []
    for _ in range(len(srcs)):
        name, res, dt, err = q.get()
        logs.append(f"{name} {dt:.2f}s" + ("" if res else f"✗({err})"))
        if res and winner is None:
            winner = (name, res, dt)
            break
    _log(f"RACE «{user[:45]}» -> {('ПОБЕДИЛ '+winner[0]+f' {winner[2]:.2f}s') if winner else 'НЕТ ОТВЕТА'} | " + ", ".join(logs))
    return winner[1] if winner else _ollama(system, user, max_tokens)

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
    "- 0 — ТОЛЬКО если официально решили/рекомендовали НЕ выплачивать (отказ, отменили)\n"
    "- NA — если событие есть, но число не названо\n"
    "- NA — если это НЕ официальное решение: прогноз, ожидания аналитиков, «может», «источники», "
    "слухи, оценка, анонс («рассмотрит», «обсудит», «назначил заседание»)\n"
    "Примеры:\n"
    "«дивиденды 35 рублей на акцию» => 35\n"
    "«нонфарм вырос на 180 тысяч» => 180\n"
    "«ЦБ повысил ключевую ставку до 21 процента» => 21\n"
    "«Минфин купит валюту на 50 млрд рублей» => 50\n"
    "«рекомендовал не выплачивать дивиденды» => 0\n"
    "«совет директоров обсудит вопрос в пятницу» => NA"
)
def _llm_model(system, user, model, max_tokens=120):
    """Конкретная облачная модель (свой мозг наблюдателя). Если это просто общая модель, а на странице
    «Мозг» выбрана Ollama, или ключа нет — обычный _llm (уважает общий выбор). Сбой — откат на _llm."""
    try:
        key, url, cloud_model = _cloud()
        own = model and not (brain_choice() == "ollama" and model == cloud_model)
        if own and key:
            try:
                return _openai_fast(system, user, max_tokens, key, url, model)
            except Exception:
                return _ollama(system, user, max_tokens)   # облако уже не успело — сразу локально, без повтора
    except Exception:
        pass
    return _llm(system, user, max_tokens)

def _extract_value(news_text, event, model=None):
    _sys = _EXTRACT_SYS_TMPL.format(ev=event)
    raw = ((_llm_model(_sys, news_text, model, max_tokens=12) if model
            else _llm(_sys, news_text, max_tokens=12)) or "").strip()
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

# ------------------------------------------------------------------ всегда-он: разбор ключевых новостей
# Для непредвиденных новостей: ловим тип события + тикер + размер. LLM зовём ТОЛЬКО если есть ключевое
# слово (дешёвая проверка подстрокой), чтобы не грузить мозг на каждой новости.
_EVENT_KW = {
    "дивиденд":   ["дивиденд"],
    "оферта":     ["оферт"],
    "допэмиссия": ["допэмисс", "доп. эмисс", "доп эмисс", "дополнительн эмисс", "spo", "размещени акц"],
    "дефолт":     ["дефолт", "не исполнил обязат", "техдефолт", "технический дефолт"],
    "выкуп":      ["выкуп", "buyback", "обратный выкуп"],
}
_EXTRACT_NEWS_SYS = (
    "Из новости извлеки СТРОГИЙ JSON без пояснений:\n"
    "{\"type\":\"<дивиденд|оферта|допэмиссия|дефолт|выкуп|другое>\","
    "\"ticker\":\"<тикер или короткое имя компании>\","
    "\"value\":\"<сумма/цена/размер с единицей, или пусто>\"}\n"
    "Примеры:\n"
    "«Совет директоров Новатэка рекомендовал дивиденды 35 руб/акция» => "
    "{\"type\":\"дивиденд\",\"ticker\":\"Новатэк\",\"value\":\"35 руб\"}\n"
    "«Сегежа объявила оферту по цене 1,8 руб» => {\"type\":\"оферта\",\"ticker\":\"Сегежа\",\"value\":\"1,8 руб\"}\n"
    "«ВТБ допэмиссия на 90 млрд руб» => {\"type\":\"допэмиссия\",\"ticker\":\"ВТБ\",\"value\":\"90 млрд руб\"}\n"
    "«Технический дефолт по облигациям Роснано» => {\"type\":\"дефолт\",\"ticker\":\"Роснано\",\"value\":\"\"}"
)
# Ключевые слова событий — С НАЧАЛА СЛОВА (регулярки), а не подстрокой: подстрока «spo» ловила
# «reSPOnse / SPOkesperson / corresPOndent / tranSPOrtation» — ~30 ложных «событий» в день из английских
# новостей, мозг называл их «дефолтом» → «Внимание: дефолт». «дополнительн эмисс» / «размещени акц»
# подстрокой не срабатывали вообще («дополнительнОЙ эмиссии»).
_W = r"(?<![а-яёa-z])"
_EVENT_RX = {
    "дивиденд":   re.compile(_W + r"(?:дивиденд|дивгэп|dividend)"),
    "оферта":     re.compile(_W + r"оферт"),
    "допэмиссия": re.compile(_W + r"(?:допэмисс|доп\.?\s?эмисс|дополнительн\w* (?:эмисс|выпуск\w* акц|акци)|"
                               r"размещени\w* (?:дополнительн\w* )?акций|spo(?![a-z]))"),
    "дефолт":     re.compile(_W + r"(?:дефолт|техдефолт|не исполнил\w* обязательств)"),
    "выкуп":      re.compile(_W + r"(?:выкуп|обратн\w* выкуп|buy-?back)"),
}


def event_type(text):
    """Тип ключевого события по словам (дешёво, без мозга) или None."""
    low = (text or "").lower().replace("ё", "е")
    return next((t for t, rx in _EVENT_RX.items() if rx.search(low)), None)


def event_confirmed(ev):
    """Мозг подтвердил ТОТ ЖЕ тип, что нашли слова? Иначе не озвучиваем («другое», «дефолт» вместо «spo»)."""
    return bool(ev) and ev.get("type") == ev.get("kw_type")


def extract_event(text):
    """Если в новости есть ключевое событие — вернуть {type,ticker,value,kw_type}, иначе None."""
    typ = event_type(text)
    if not typ:
        return None
    j = _json_from(_llm(_EXTRACT_NEWS_SYS, text, max_tokens=90)) or {}
    out = {"type": j.get("type") or typ, "ticker": (j.get("ticker") or "").strip(),
           "value": (j.get("value") or "").strip(), "kw_type": typ}
    _log(f"СОБЫТИЕ {out['type']}: тикер={out['ticker'] or '?'} размер={out['value'] or '?'} | {text[:60]}")
    return out

def speak_event(ev):
    t, tk, v = ev.get("type"), ev.get("ticker") or "", ev.get("value") or ""
    if t == "дефолт":
        return f"Внимание: дефолт, {tk}.".strip()
    head = {"дивиденд": "Дивиденды", "оферта": "Оферта", "допэмиссия": "Допэмиссия",
            "выкуп": "Выкуп"}.get(t, "Событие")
    return f"{head} {tk}: {v}.".replace(" :", ":").strip() if v else f"{head} {tk}.".strip()

# ------------------------------------------------------------------ движок: новость -> клик
_COOLDOWN = 6 * 3600  # не повторять один сценарий чаще раза в 6 часов

def on_news(items):
    """items: [{text, tickers, ...}] — свежие новости (newest first). Проверяет активные сценарии."""
    cfg = load()
    changed = False
    fired_ids = []     # «один раз»: сценарий удаляется после срабатывания (если не once=false)
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
            _t0 = time.time()
            val = _extract_value(it.get("text", ""), ev)
            branch = next((b for b in branches if _branch_match(b, val)), None)
            _dt = (time.time() - _t0) * 1000
            if not branch:
                _log(f"СЦЕНАРИЙ «{sc.get('name','')}»: совпало «{it.get('text','')[:50]}», значение={val}, "
                     f"ветки нет ({_dt:.0f}мс)")
                continue
            sc["fired_ts"] = time.time(); changed = True
            if sc.get("once", True):
                fired_ids.append(str(sc.get("id")))     # отработал один раз -> удалить
            vtxt = ("нет" if val == 0 else "не указан" if val is None
                    else (str(int(val)) if val == int(val) else str(val)))
            if branch.get("noop"):
                _log(f"СЦЕНАРИЙ «{sc.get('name','')}»: значение={val} -> «{branch['label']}» БЕЗ КЛИКА ({_dt:.0f}мс)")
                _announce(f"{ev} {vtxt} — в заданном диапазоне, действий не требуется, сэр.")
            else:
                click_at(branch["x"], branch["y"])
                _log(f"СЦЕНАРИЙ «{sc.get('name','')}»: значение={val} -> КЛИК «{branch['label']}» "
                     f"({branch['x']},{branch['y']}) за {_dt:.0f}мс от новости")
                _announce(f"Сценарий {sc.get('name','')}: открыл {branch.get('label','стакан')}. "
                          f"{ev} {vtxt}. Проверьте и жмите, сэр.")
            break
    if fired_ids:
        cfg["scenarios"] = [s for s in cfg.get("scenarios", []) if str(s.get("id")) not in fired_ids]
        _log(f"Сценарии удалены после однократного срабатывания: {fired_ids}")
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

# ------------------------------------------------------------------ запуск сценария наблюдателем
def fire_by_id(scenario_id, news_text, model=None):
    """Наблюдатель нашёл свою новость -> прогнать привязанный сценарий по тексту новости.
    Значение извлекается мозгом наблюдателя (model). Клик открывает стакан (не Купить/Продать)."""
    cfg = load()
    sc = next((s for s in cfg.get("scenarios", []) if str(s.get("id")) == str(scenario_id)), None)
    if not sc:
        return {"ok": False, "text": "Сценарий не найден."}
    branches = sc.get("branches", [])
    if not branches or any(b.get("x") is None for b in branches if not b.get("noop")):
        return {"ok": False, "text": "У сценария не заданы координаты веток."}
    ev = (sc.get("event") or "").lower()
    val = _extract_value(news_text, ev, model)
    branch = next((b for b in branches if _branch_match(b, val)), None)
    vtxt = ("нет" if val == 0 else "не указан" if val is None
            else (str(int(val)) if val == int(val) else str(val)))
    if not branch:
        _log(f"НАБЛЮДАТЕЛЬ→«{sc.get('name','')}»: значение={val}, подходящей ветки нет")
        return {"ok": False, "text": f"{ev} {vtxt}: подходящей ветки нет."}
    sc["fired_ts"] = time.time(); save(cfg)
    if branch.get("noop"):
        _log(f"НАБЛЮДАТЕЛЬ→«{sc.get('name','')}»: {val} → «{branch['label']}» без клика")
        return {"ok": True, "noop": True, "text": f"{ev} {vtxt} — в заданном диапазоне, действий не требуется, сэр."}
    try: show_point(branch["x"], branch["y"])   # подсветка стакана (маркер) для наблюдателя
    except Exception: pass
    click_at(branch["x"], branch["y"])
    _log(f"НАБЛЮДАТЕЛЬ→«{sc.get('name','')}»: {val} → КЛИК «{branch['label']}» ({branch['x']},{branch['y']})")
    return {"ok": True, "label": branch.get("label"),
            "text": f"Открыл {branch.get('label','стакан')}. {ev} {vtxt}. Проверьте и жмите, сэр."}

# ------------------------------------------------------------------ планировщик ожидаемых событий
# У сценария могут быть поля: schedule_dt "YYYY-MM-DD HH:MM" (МСК) и prewarm_min (по умолч. 3).
# За prewarm_min до времени: анонс + прогрев Ollama + окно ГОНКИ (облако и Ollama, побеждает быстрый).
# Сам клик по стакану срабатывает как обычно — по факту новости (США: BLS/BEA; РФ: лента терминала).
import datetime as _dt
try:
    from zoneinfo import ZoneInfo as _ZI
    _MSK_TZ = _ZI("Europe/Moscow"); _dt.datetime.now(_MSK_TZ)
    def _now_msk(): return _dt.datetime.now(_MSK_TZ).replace(tzinfo=None)
except Exception:
    def _now_msk(): return _dt.datetime.utcnow() + _dt.timedelta(hours=3)

_WINDOW_AFTER = 45 * 60   # сколько секунд после времени события держать окно гонки

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

def _prime_all(cfg=None):
    """Прогреть перед событием: Ollama (модель в VRAM). Облаку прогрев не нужен."""
    threading.Thread(target=warm_ollama, daemon=True).start()

def _scheduler_loop():
    global _race_until
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
                # окно [T-pre .. T+45мин]: гонка облака и Ollama
                if -_WINDOW_AFTER <= secs <= pre:
                    _race_until = max(_race_until, time.time() + _WINDOW_AFTER + max(0, secs))
                # разовый прогрев + анонс в момент наступления T-pre
                if 0 < secs <= pre and not sc.get("_prewarmed"):
                    sc["_prewarmed"] = True; changed = True
                    _prime_all(cfg)   # прогреть Ollama (VRAM) перед событием
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
        threading.Thread(target=warm_ollama, daemon=True).start()   # прогреть модель сразу при старте
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
