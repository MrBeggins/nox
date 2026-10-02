"""Jarvis Terminal Reader — читает НОВОСТИ (Trading Tools) и КАЛЕНДАРЬ (MindStocks)
прямо из уже открытого T-Терминала пользователя.

Подключается по CDP к запущенному Яндекс.Браузеру (порт отладки 9222), находит
вкладку терминала (tbank.ru/terminal) и снимает данные из готовых панелей —
«как читает пользователь глазами». Никаких токенов/WS: только отрисованный DOM.

Требуется, чтобы Яндекс был запущен с --remote-debugging-port=9222
(ярлык C:\jarvis-lab\Терминал-Яндекс.cmd) и терминал открыт с виджетами
«Все новости» (Trading Tools) и «Календарь событий» (MindStocks).

Запуск: python terminal_reader.py  ->  http://127.0.0.1:8126
"""
import threading, queue, time, json, re, os, socket, base64, struct
from fastapi import FastAPI
import uvicorn
try:
    import nox_scenarios
except Exception:
    nox_scenarios = None

CDP_URL = "http://127.0.0.1:9222"
TERMINAL_MATCH = "tbank.ru"
IPC_HOST, IPC_PORT = "127.0.0.1", 9712     # jarvis-app IPC (проактивная озвучка)
WATCH_FILE = r"C:\jarvis-voice\news_watch.json"
MON_STATE_FILE = r"C:\jarvis-voice\news_monitor_on.txt"  # "1"/"0" — монитор вкл/выкл

# --- извлечение новостей (Trading Tools: .news_row/.news_title) ---
NEWS_JS = r"""
() => {
  const rows = [...document.querySelectorAll('.news_row')];
  const out = [];
  const seen = new Set();
  for (const r of rows) {
    // только сам заголовок (.news_title_inner) — без источника (.news_source) и времени (.news_time)
    let t = (r.querySelector('.news_title_inner')?.innerText
             || r.querySelector('.news_title')?.innerText
             || r.innerText || '').replace(/\s+/g,' ').trim();
    if (!t) continue;
    const key = t.slice(0, 50);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(t.slice(0, 400));
    if (out.length >= 40) break;
  }
  return out;
}
"""

# --- монитор ленты: заголовок + тикеры (.ticker_change) + источник (.news_source) ---
MON_JS = r"""
() => {
  const rows = [...document.querySelectorAll('.news_row')];
  const out = [];
  for (const r of rows) {
    const inner = r.querySelector('.news_title_inner');
    if (!inner) continue;
    const text = (inner.innerText || '').replace(/\s+/g,' ').trim();
    if (!text) continue;
    // тикеры в шапке новости (LENT, $LNZ6 ...) — чистим $
    const tickers = [...r.querySelectorAll('.news_tickers .ticker_change')]
      .map(e => (e.textContent||'').replace(/\$/g,'').trim().toUpperCase())
      .filter(Boolean);
    // источник (ИНТЕРФАКС / НКЦ / e-disclosure / Московская биржа ...)
    const source = ((r.querySelector('.news_source')||{}).innerText||'').replace(/\s+/g,' ').trim();
    // подсвеченные слова и хэштеги (доп. сигналы)
    const hl = [...inner.querySelectorAll('b[style*="color"],span[style*="color"],font[color]')].map(e=>e.textContent.trim()).filter(Boolean);
    const hashtags = (text.match(/#[\wА-Яа-яЁё]+/g) || []);
    // текст тела (для распознавания события/цены)
    const body = ((r.querySelector('.news_preview_inner')||r.querySelector('.news_content')||{}).innerText||'').replace(/\s+/g,' ').trim().slice(0,300);
    out.push({ text: text.slice(0,400), tickers, source, hl, hashtags, body });
  }
  return out;   // newest first
}
"""

# --- извлечение календаря (MindStocks: .mind-events-date-group/.mind-event-item-new) ---
CAL_JS = r"""
() => {
  const out = [];
  for (const g of document.querySelectorAll('.mind-events-date-group')) {
    const date = (g.querySelector('.mind-events-date-title')?.innerText || '').replace(/\s+/g,' ').trim();
    for (const it of g.querySelectorAll('.mind-event-item-new')) {
      const txt = (it.innerText || '').replace(/\s+/g,' ').trim();
      if (txt) out.push({ date, text: txt.slice(0, 300) });
    }
  }
  return out;
}
"""

_cmd_q = queue.Queue()
_ready = threading.Event()
_state = {"ok": False, "err": "", "url": ""}
_worker_started = False

def _find_terminal_page(browser):
    pages = []
    for ctx in browser.contexts:
        for pg in ctx.pages:
            try:
                pages.append(pg)
            except Exception:
                pass
    # предпочесть именно вкладку терминала (не окно логина id.tbank.ru)
    for pg in pages:
        if "/terminal" in (pg.url or ""):
            return pg
    for pg in pages:
        u = pg.url or ""
        if "tbank.ru" in u and "id.tbank" not in u and "/auth" not in u:
            return pg
    for pg in pages:
        if TERMINAL_MATCH in (pg.url or ""):
            return pg
    return None

def _worker():
    from playwright.sync_api import sync_playwright
    attempt = 0
    while True:
        attempt += 1
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(CDP_URL, timeout=15000)
                pg = _find_terminal_page(browser)
                if pg is None:
                    _state["ok"] = False
                    _state["err"] = "Вкладка терминала не найдена (открыт ли tbank.ru/terminal?)"
                    _state["url"] = ""
                    _ready.set()
                    time.sleep(6)
                    continue
                _state["ok"] = True
                _state["err"] = ""
                _state["url"] = pg.url
                _ready.set()
                while True:
                    try:
                        fn, resp = _cmd_q.get(timeout=20)
                    except queue.Empty:
                        # проверка живости вкладки + повторный поиск, если закрылась
                        try:
                            if TERMINAL_MATCH not in (pg.url or ""):
                                np = _find_terminal_page(browser)
                                if np: pg = np
                        except Exception:
                            raise
                        continue
                    try:
                        # если вкладку сменили/закрыли — найти заново
                        if TERMINAL_MATCH not in (pg.url or ""):
                            np = _find_terminal_page(browser)
                            if np: pg = np
                        resp["result"] = fn(pg)
                    except Exception as e:
                        resp["error"] = str(e)
                        if _is_conn_lost(e):
                            resp["done"].set(); raise
                    resp["done"].set()
        except Exception as e:
            _state["ok"] = False
            _state["err"] = f"CDP не подключён: {e}"
            _state["url"] = ""
            _ready.set()
            time.sleep(6)

def _is_conn_lost(e):
    s = str(e).lower()
    return "closed" in s or "disconnect" in s or "target" in s or "connection" in s

def start_worker():
    global _worker_started
    if not _worker_started:
        _worker_started = True
        threading.Thread(target=_worker, daemon=True).start()
        threading.Thread(target=_monitor_loop, daemon=True).start()
        threading.Thread(target=_samolet_loop, daemon=True).start()
        threading.Thread(target=_terminal_health_loop, daemon=True).start()
        if nox_scenarios:
            nox_scenarios.start_capture()    # слушаем F2 для захвата координат стаканов
            nox_scenarios.start_scheduler()  # планировщик ожидаемых событий (прогрев Claude, анонс)

_PAGE_STATE_JS = ("()=>({n:document.querySelectorAll('.news_row').length,"
                  "blen:(document.body.innerText||'').length,"
                  "gate:/ертификат|инцифр|используйте .{0,4}ндекс/i.test(document.body.innerText||'')})")

def _page_gated(st):
    if not st:
        return True
    return bool(st.get("gate")) or (st.get("blen", 0) < 800 and st.get("n", 0) == 0)

def _terminal_health_loop():
    """Само-лечение страницы терминала: пустая/со шлюзом -> reload; если не помогло -> уведомить (раз в 10 мин)."""
    _ready.wait(60)
    last_announced = 0.0
    while True:
        try:
            if _state["ok"]:
                st, err = _submit(lambda pg: pg.evaluate(_PAGE_STATE_JS), timeout=20)
                if not err and _page_gated(st):
                    # 1) возможно просто не отрендерилось — перезагружаем
                    _submit(lambda pg: pg.reload(timeout=25000), timeout=30)
                    time.sleep(9)
                    st2, _e = _submit(lambda pg: pg.evaluate(_PAGE_STATE_JS), timeout=20)
                    # 2) всё ещё шлюз/пусто -> нужен вход пользователя
                    if _page_gated(st2) and time.time() - last_announced > 600:
                        _ipc_announce("Терминал не загружен или требует входа, сэр. "
                                      "Откройте окно терминала Nox и войдите в аккаунт.")
                        last_announced = time.time()
        except Exception:
            pass
        time.sleep(90)

def _submit(fn, timeout=30):
    resp = {"done": threading.Event()}
    _cmd_q.put((fn, resp))
    if not resp["done"].wait(timeout):
        return None, "timeout"
    return resp.get("result"), resp.get("error")

# ------------------------------------------------------------------ монитор ленты (автоозвучка)
FILTER_FILE = r"C:\jarvis-voice\news_filter.json"
# Источники по умолчанию (регуляторные/биржевые) — их читаем всегда.
_DEFAULT_SOURCES = ["нкц", "e-disclosure", "e disclosure", "московская бирж", "мосбирж", "moex"]

def _load_filter():
    try:
        d = json.load(open(FILTER_FILE, encoding="utf-8"))
    except Exception:
        d = {}
    d.setdefault("tickers", [])
    d.setdefault("phrases", [])
    d.setdefault("smart", False)   # умный отбор новостей через Ollama
    if "sources" not in d:
        d["sources"] = list(_DEFAULT_SOURCES)
    d["smart"] = bool(d.get("smart"))
    d["tickers"] = [str(x).upper().strip() for x in d["tickers"] if str(x).strip()]
    d["phrases"] = [str(x).lower().strip() for x in d["phrases"] if str(x).strip()]
    d["sources"] = [str(x).lower().strip() for x in d["sources"] if str(x).strip()]
    return d

def _save_filter(d):
    try:
        json.dump(d, open(FILTER_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass

def _mon_enabled():
    try:
        return open(MON_STATE_FILE, encoding="utf-8").read().strip() != "0"
    except Exception:
        return True  # по умолчанию монитор включён

def _set_mon(on):
    try:
        open(MON_STATE_FILE, "w", encoding="utf-8").write("1" if on else "0")
    except Exception:
        pass

def _ipc_announce(text):
    """Отправить проактивную озвучку в jarvis-app по WebSocket (9712)."""
    try:
        s = socket.create_connection((IPC_HOST, IPC_PORT), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall((f"GET / HTTP/1.1\r\nHost: {IPC_HOST}:{IPC_PORT}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        s.recv(4096)
        data = json.dumps({"action": "announce", "text": text}, ensure_ascii=False).encode("utf-8")
        mask = os.urandom(4)
        hdr = bytearray([0x81])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126); hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127); hdr += struct.pack(">Q", n)
        hdr += mask
        s.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
        time.sleep(0.15)
        s.close()
        return True
    except Exception:
        return False

_EVENT_WORDS = [("дивиденд", "дивиденды"), ("оферт", "оферта"), ("допэмисс", "допэмиссия"),
                ("выкуп", "выкуп"), ("buyback", "выкуп"), ("байбек", "выкуп"), ("сплит", "сплит"),
                ("делистинг", "делистинг"), ("редомицил", "редомициляция")]

# Тикер -> произносимое имя (латиница читается TTS криво). Расширяемо.
TICKER_NAME = {
    "SBER": "Сбербанк", "SBERP": "Сбербанк преф", "GAZP": "Газпром", "LKOH": "Лукойл",
    "GMKN": "Норникель", "ROSN": "Роснефть", "NVTK": "Новатэк", "SNGS": "Сургутнефтегаз",
    "SNGSP": "Сургут преф", "TATN": "Татнефть", "PLZL": "Полюс", "PIKK": "Пик", "MGNT": "Магнит",
    "MTSS": "Эм тэ эс", "VTBR": "Вэ тэ бэ", "YDEX": "Яндекс", "OZON": "Озон",
    "OZPH": "Озон Фармацевтика", "MOEX": "Московская биржа", "AFLT": "Аэрофлот", "CHMF": "Северсталь",
    "NLMK": "Эн эл эм ка", "MAGN": "Магнитка", "ALRS": "Алроса", "PHOR": "Фосагро", "RUAL": "Русал",
    "AFKS": "Система", "FEES": "Россети", "HYDR": "Русгидро", "IRAO": "Интер РАО", "LENT": "Лента",
    "FIVE": "Икс пять", "X5": "Икс пять", "FIXP": "Фикс прайс", "SMLT": "Самолёт",
    "BSPB": "Банк Санкт-Петербург", "T": "Т Технологии", "TCSG": "Т Технологии", "POSI": "Позитив",
    "SIBN": "Газпромнефть", "TRNFP": "Транснефть преф", "VSEH": "Все Инструменты", "MRKK": "Россети Кубань",
    "RTKM": "Ростелеком", "MTLR": "Мечел", "SELG": "Селигдар", "UPRO": "Юнипро", "BANE": "Башнефть",
}

def _tk_name(tk):
    return TICKER_NAME.get(tk.upper(), tk)

def _detect_event(text):
    low = text.lower()
    return next((v for k, v in _EVENT_WORDS if k in low), None)

def _concise(item):
    """Корпсобытие -> кратко 'ИМЯ [ЦЕНА руб] событие'. Иначе None (полный заголовок)."""
    tks = item.get("tickers") or []
    full = (item.get("text", "") + " " + item.get("body", ""))
    ev = _detect_event(full)
    if tks and ev:
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:руб|₽|р\.)", full, re.I)
        price = (m.group(1).replace(",", ".") + " руб ") if m else ""
        return f"{_tk_name(tks[0])} {price}{ev}".replace("  ", " ").strip()
    return None

# Хвостовые атрибуции («…, сообщает Рейтер», «… как заявил Х») — режем ТОЛЬКО после
# запятой/тире или «как» (иначе срежем главный глагол типа «Россия ЗАЯВЛЯЕТ …»).
_ATTR_RE = re.compile(
    r"(?:\s*[,—–]\s*|\s+как\s+)"
    r"(?:сообщ\w+|перед[аёе]\w+|пиш\w+|заяв\w+|расск\w+|отмет\w+|уточн\w+|"
    r"по\s+данным|по\s+словам|со\s+ссылкой\s+на|информир\w+|цитир\w+|пресс-служб\w+)\b.*$",
    re.I)
# Хвостовой источник-агентство: «… - REUTERS», «… · ТАСС».
_SRC_TAIL_RE = re.compile(
    r"\s*[—\-–·|]\s*(?:REUTERS|ТАСС|РИА(?:\s*НОВОСТИ)?|ИНТЕРФАКС|BLOOMBERG|РБК|"
    r"КОММЕРСАНТ|ВЕДОМОСТИ|ПРАЙМ|ФИНАМ|Reuters|Bloomberg|Ru)\s*$", re.I)

def _shorten(item):
    """Мгновенно сжать новость до сути: корпсобытие -> 'Имя [цена] событие';
    иначе — первое предложение без хвостовых атрибуций, тикеры -> имена, до ~120 симв."""
    c = _concise(item)
    if c:
        return c
    t = (item.get("text") or "").strip()
    # первое предложение
    t = re.split(r"(?<=[.!?…])\s", t)[0].strip()
    # убрать хвостовой источник-агентство и «… сообщает X»
    t = _SRC_TAIL_RE.sub("", t)
    t = _ATTR_RE.sub("", t).strip(" .,;—-–")
    # тикеры -> произносимые имена
    for tk in (item.get("tickers") or []):
        t = re.sub(r"(?<![A-Za-zА-Яа-яЁё])" + re.escape(tk) + r"(?![A-Za-zА-Яа-яЁё])",
                   _tk_name(tk), t, flags=re.I)
    if len(t) > 120:
        t = t[:120].rsplit(" ", 1)[0].rstrip(" .,;—-") + "…"
    return t or (item.get("text") or "")

# ------------------------------------------------------------------ умный отбор (Ollama, локально)
_OLLAMA_CHAT = "http://127.0.0.1:11434/v1/chat/completions"
_OLLAMA_MODEL = "qwen2.5:3b"
_SMART_SYS = (
    "Перескажи суть биржевой новости ОДНОЙ короткой фразой по-русски для озвучки "
    "(без кавычек: компания/рынок + что произошло + число). Если новость НЕ про экономику и рынок "
    "(спорт, погода, ДТП и происшествия, развлечения, поздравления, общая политика без влияния "
    "на рынок) — ответь ровно: ПРОПУСК."
)
_SMART_SHOTS = [
    ("Совет директоров Новатэка рекомендовал дивиденды 35 рублей на акцию", "Новатэк: дивиденды 35 рублей"),
    ("ЦБ повысил ключевую ставку до 18%", "ЦБ поднял ставку до 18 процентов"),
    ("Акции Газпрома упали на 5% после слабого отчёта", "Газпром упал на 5 процентов после отчёта"),
    ("ГК Самолет допустила технический дефолт по облигациям", "Самолёт: технический дефолт по облигациям"),
    ("Инфляция в России ускорилась до 9%", "Инфляция ускорилась до 9 процентов"),
    ("США ввели новые санкции против банков РФ", "США ввели санкции против банков России"),
    ("В Москве открылся новый парк развлечений", "ПРОПУСК"),
    ("Сборная России выиграла матч", "ПРОПУСК"),
    ("Президент поздравил нефтяников с профессиональным праздником", "ПРОПУСК"),
]

def _ollama_judge(text):
    """Умный отбор: важная новость -> краткая суть; иначе None. qwen2.5:3b, chat + few-shot."""
    import urllib.request
    text = (text or "").strip()
    if len(text) < 8:
        return None
    msgs = [{"role": "system", "content": _SMART_SYS}]
    for u, a in _SMART_SHOTS:
        msgs += [{"role": "user", "content": u}, {"role": "assistant", "content": a}]
    msgs.append({"role": "user", "content": text})
    body = json.dumps({
        "model": _OLLAMA_MODEL, "messages": msgs, "stream": False,
        "temperature": 0, "max_tokens": 40, "keep_alive": "30m",
    }).encode("utf-8")
    try:
        req = urllib.request.Request(_OLLAMA_CHAT, data=body, headers={"Content-Type": "application/json"})
        data = json.loads(urllib.request.urlopen(req, timeout=25).read().decode("utf-8"))
        resp = data["choices"][0]["message"]["content"]
    except Exception:
        return None
    resp = (resp or "").strip().strip('"«»').strip()
    if not resp or "пропуск" in resp.lower() or "propу" in resp.lower():
        return None
    return resp[:140]

def _match(item, cfg):
    """Реагировать только на заданное: тикер ИЛИ фраза ИЛИ источник из конфига."""
    tks = [t.upper() for t in (item.get("tickers") or [])]
    if cfg["tickers"] and any(t in cfg["tickers"] for t in tks):
        return True
    low = (item.get("text", "") + " " + item.get("body", "")).lower()
    if cfg["phrases"] and any(p in low for p in cfg["phrases"]):
        return True
    src = (item.get("source", "") or "").lower()
    if cfg["sources"] and any(s in src for s in cfg["sources"]):
        return True
    return False

# ------------------------------------------------------------------ точечная слежка: купон Самолёта
SAMOLET_FLAG = r"C:\jarvis-voice\task_samolet.txt"   # "1" активна, "done" отработала
_DEF_TERMS = ["дефолт", "тех. деф", "техдеф", "не выплат", "невыплат", "не исполн", "неисполн",
              "не погас", "просроч", "допустил деф", "объявил деф",
              "ненадлеж", "с нарушен", "нарушил", "ненадлежащим образом", "не в полном объ",
              "частично погас", "неполн"]
_PAID_TERMS = ["выплат", "погаси", "погаш", "исполнил", "исполнен", "перечисл", "выплачен", "выплатил"]
_SPEC_TERMS = ["может", "риск", "грозит", "вероят", "угроз", "опас", "предупре", "прогноз", "ожида", "если не"]

def _samolet_active():
    try:
        return open(SAMOLET_FLAG, encoding="utf-8").read().strip() == "1"
    except Exception:
        return False

def _samolet_set(v):
    try:
        open(SAMOLET_FLAG, "w", encoding="utf-8").write(v)
    except Exception:
        pass

def _samolet_verdict(item):
    """None — не по теме/неоднозначно (ждём дальше); строка — окончательный вердикт."""
    tks = [t.upper() for t in item.get("tickers", [])]
    low = (item.get("text", "") + " " + item.get("body", "")).lower()
    is_smlt = ("SMLT" in tks) or ("самол" in low)
    if not is_smlt:
        return None
    if not any(k in low for k in ["купон", "облига", "бонд", "2,5 млрд", "2.5 млрд", "выплат", "дефолт", "погаш", "исполн"]):
        return None
    if any(s in low for s in _SPEC_TERMS):
        return None  # спекуляция/прогноз — не выносим вердикт, ждём факт
    if any(k in low for k in _DEF_TERMS):
        return "тех дефолт"
    if any(k in low for k in _PAID_TERMS):
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*(млрд|миллиард|млн|миллион)", low)
        if m:
            unit = "миллиарда" if m.group(2).startswith(("млрд", "миллиард")) else "миллиона"
            summ = m.group(1).replace(".", ",") + " " + unit
        else:
            summ = "два и пять миллиарда"  # ожидаемый купон 2,5 млрд
        return f"выплатили {summ}"
    return None

def _samolet_loop():
    _ready.wait(90)
    while True:
        try:
            if not _samolet_active():
                time.sleep(5)
                continue
            items, err = _submit(lambda pg: pg.evaluate(MON_JS), timeout=25)
            if not err and items:
                for it in items:                 # newest first
                    v = _samolet_verdict(it)
                    if v:
                        _ipc_announce("Самолёт: " + v)
                        _samolet_set("done")
                        break
        except Exception:
            pass
        time.sleep(4)

_seen_news = set()

def _monitor_loop():
    _ready.wait(90)
    time.sleep(3)
    first = True
    while True:
        try:
            if not _state["ok"]:
                time.sleep(4)
                continue
            items, err = _submit(lambda pg: pg.evaluate(MON_JS), timeout=25)
            if err or not items:
                time.sleep(4)
                continue
            cfg = _load_filter()
            smart = cfg.get("smart")
            new_items = []
            for it in items:                    # newest first
                key = it["text"][:80]
                if key in _seen_news:
                    continue
                _seen_news.add(key)
                if not first:
                    new_items.append(it)
            first = False
            if len(_seen_news) > 5000:
                _seen_news.clear()
            # Сценарии (авто-клик стакана) работают ВСЕГДА, независимо от монитора озвучки.
            if new_items:
                try:
                    import nox_scenarios
                    nox_scenarios.on_news(new_items)
                except Exception:
                    pass
            # Озвучка новостей — только если монитор включён.
            if _mon_enabled():
                for it in reversed(new_items):  # в хронологическом порядке
                    if smart:
                        say = _ollama_judge(it.get("text", ""))
                        if say:
                            _ipc_announce(say)
                            time.sleep(0.4)
                    else:
                        if _match(it, cfg):
                            _ipc_announce(_shorten(it))
                            time.sleep(0.4)
        except Exception:
            pass
        time.sleep(2)

# ------------------------------------------------------------------ matching (календарь)
COUNTRY_TICKERS = {}  # события РФ по тикеру — сопоставление по подстроке

def match_cal(events, q):
    qn = re.sub(r"[^0-9a-zа-яё ]+", " ", (q or "").lower())
    words = [w for w in qn.split() if len(w) >= 3
             and w not in ("джарвис", "календарь", "событие", "события", "когда", "какие", "покажи", "что", "по")]
    if not words:
        return events
    scored = []
    for e in events:
        t = (e.get("text", "") + " " + e.get("date", "")).lower()
        score = sum(1 for w in words if w in t)
        if score:
            scored.append((score, e))
    scored.sort(key=lambda x: -x[0])
    return [e for _, e in scored]

# ------------------------------------------------------------------ HTTP
app = FastAPI(title="Jarvis Terminal Reader")

@app.on_event("startup")
def _startup():
    start_worker()

@app.get("/health")
def health():
    return {"status": "ok" if _state["ok"] else "starting", "err": _state["err"], "url": _state["url"]}

@app.get("/news")
def news(n: int = 5):
    """Последние n заголовков новостей (Trading Tools)."""
    if not _state["ok"]:
        return {"text": _state["err"] or "Терминал ещё не подключён, сэр."}
    items, err = _submit(lambda pg: pg.evaluate(NEWS_JS))
    if err or not items:
        return {"text": f"Не смог прочитать новости, сэр. {err or ''}".strip()}
    top = items[:max(1, min(n, 15))]
    return {"text": chr(10).join(top), "items": top}

@app.get("/events")
def events(q: str = ""):
    """События календаря (MindStocks). q — фильтр (тикер/тип/страна)."""
    if not _state["ok"]:
        return {"text": _state["err"] or "Терминал ещё не подключён, сэр."}
    evs, err = _submit(lambda pg: pg.evaluate(CAL_JS))
    if err or evs is None:
        return {"text": f"Не смог прочитать календарь, сэр. {err or ''}".strip()}
    if q.strip():
        m = match_cal(evs, q)
        if not m:
            return {"text": f"В календаре не нашёл «{q}», сэр."}
        top = m[:5]
        return {"text": chr(10).join(f"{e['date']}: {e['text']}" for e in top), "items": top}
    top = evs[:6]
    return {"text": chr(10).join(f"{e['date']}: {e['text']}" for e in top), "items": top}

_STOP_WORDS = ("джарвис", "следи", "следить", "за", "отслеживай", "реагируй", "на",
               "новости", "новостями", "новостей", "о", "об", "про", "по", "тикер",
               "тикеру", "фраза", "фразу", "источник", "добавь", "ключевое", "слово")

def _clean_q(q):
    q = (q or "").strip().lower()
    for w in _STOP_WORDS:
        q = re.sub(r"(?<![а-яёa-z0-9])" + re.escape(w) + r"(?![а-яёa-z0-9])", " ", q)
    return re.sub(r"\s+", " ", q).strip()

@app.get("/filter_add")
def filter_add(kind: str = "phrase", q: str = ""):
    val = _clean_q(q)
    if not val:
        return {"text": "Что добавить в фильтр, сэр?"}
    cfg = _load_filter()
    if kind.startswith("ticker"):
        v = val.upper().replace(" ", "")
        if v not in cfg["tickers"]:
            cfg["tickers"].append(v)
        say = f"Слежу за тикером {v}."
    elif kind.startswith("source"):
        if val not in cfg["sources"]:
            cfg["sources"].append(val)
        say = f"Добавил источник {val}."
    else:
        if val not in cfg["phrases"]:
            cfg["phrases"].append(val)
        say = f"Реагирую на «{val}»."
    _save_filter(cfg)
    return {"text": say}

@app.get("/filter_clear")
def filter_clear(kind: str = ""):
    cfg = _load_filter()
    if kind.startswith("ticker"):
        cfg["tickers"] = []
    elif kind.startswith("phrase"):
        cfg["phrases"] = []
    elif kind.startswith("source"):
        cfg["sources"] = []
    else:
        cfg["tickers"] = []; cfg["phrases"] = []
    _save_filter(cfg)
    return {"text": "Фильтр новостей очищен, сэр."}

@app.get("/filter_remove")
def filter_remove(kind: str = "phrase", q: str = ""):
    val = _clean_q(q)
    cfg = _load_filter()
    if kind.startswith("ticker"):
        v = val.upper().replace(" ", "")
        cfg["tickers"] = [x for x in cfg["tickers"] if x != v]
        say = f"Убрал тикер {v}."
    elif kind.startswith("source"):
        cfg["sources"] = [x for x in cfg["sources"] if x != val]
        say = f"Убрал источник {val}."
    else:
        cfg["phrases"] = [x for x in cfg["phrases"] if x != val]
        say = f"Больше не реагирую на «{val}»."
    _save_filter(cfg)
    return {"text": say, "filter": cfg}

@app.get("/smart")
def smart(on: int = -1):
    cfg = _load_filter()
    if on in (0, 1):
        cfg["smart"] = bool(on)
        _save_filter(cfg)
    return {"text": ("Умный отбор новостей включён, сэр." if cfg.get("smart")
                     else "Умный отбор выключен, фильтрую по вашим тикерам и фразам, сэр."),
            "smart": cfg.get("smart")}

@app.get("/filter")
def filter_show():
    cfg = _load_filter()
    parts = []
    if cfg["tickers"]:
        parts.append("тикеры: " + ", ".join(cfg["tickers"]))
    if cfg["phrases"]:
        parts.append("фразы: " + ", ".join(cfg["phrases"]))
    if cfg["sources"]:
        parts.append("источники: " + ", ".join(cfg["sources"]))
    return {"text": ("Фильтр — " + "; ".join(parts)) if parts else "Фильтр пуст, сэр.", "filter": cfg}

@app.get("/task_samolet")
def task_samolet(on: int = -1):
    if on == 1:
        _samolet_set("1")
    elif on == 0:
        _samolet_set("0")
    try:
        st = open(SAMOLET_FLAG, encoding="utf-8").read().strip()
    except Exception:
        st = "0"
    txt = {"1": "Слежу за купоном Самолёта, сэр.",
           "done": "Вердикт по Самолёту уже озвучен, сэр.",
           "0": "Слежка за Самолётом выключена, сэр."}.get(st, "Слежка за Самолётом выключена, сэр.")
    return {"text": txt, "state": st}

@app.get("/mon")
def mon(on: int = -1):
    if on in (0, 1):
        _set_mon(bool(on))
    return {"text": ("Чтение новостей включено, сэр." if _mon_enabled() else "Чтение новостей выключено, сэр."),
            "on": _mon_enabled()}

# ------------------------------------------------------------------ ЗАПОЛНЕНИЕ ВИДЖЕТА «ЗАЯВКА»
# Nox ГОТОВИТ заявку в терминале (тикер/цена/объём). Кнопку Купить/Продать жмёт пользователь.
# Кнопки исполнения НЕ нажимаются ни при каких условиях.

def _plan_from_invest(q, side, qty, price, close):
    import urllib.request, urllib.parse, json as _json
    params = urllib.parse.urlencode({"q": q, "side": side, "qty": qty, "price": price, "close": close})
    url = f"http://127.0.0.1:8124/order_plan?{params}"
    with urllib.request.urlopen(url, timeout=15) as r:
        return _json.loads(r.read().decode("utf-8"))

_ENSURE_WIDGET_JS = r"""()=>{
  const btns=[...document.querySelectorAll('button,[role=button]')].map(e=>(e.innerText||'').trim());
  return btns.includes('Купить') && btns.includes('Продать');
}"""

# найти бокс виджета заявки (предок кнопки «Купить», содержащий вкладки Рыночная/Лимитная)
_WIDGET_BOX_JS = r"""()=>{
  function box(e){const r=e.getBoundingClientRect();return{x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)};}
  const buy=[...document.querySelectorAll('button,[role=button]')].find(e=>(e.innerText||'').trim()==='Купить');
  if(!buy) return null;
  let w=buy;
  for(let i=0;i<12&&w.parentElement;i++){ w=w.parentElement; const t=w.innerText||''; if(/Рыночная/.test(t)&&/Лимитная/.test(t)&&w.getBoundingClientRect().width>=240) return box(w); }
  return null;
}"""

def _pick_result_point(pg, ticker):
    js = r"""(tk)=>{
      const up=tk.toUpperCase();
      let best=null;
      document.querySelectorAll('*').forEach(e=>{
        if(e.children.length>3) return;
        const t=(e.innerText||'').replace(/\s+/g,' ').trim();
        if(t.toUpperCase()===up){
          const b=e.getBoundingClientRect();
          if(b.width>0&&b.height>0&&b.y>120){ if(!best||b.y<best.y) best={x:Math.round(b.x+b.width/2),y:Math.round(b.y+b.height/2)}; }
        }
      });
      return best;
    }"""
    return pg.evaluate(js, ticker)

def _fill_widget(pg, ticker, lots, price, typ):
    # 1) виджет присутствует?
    if not pg.evaluate(_ENSURE_WIDGET_JS):
        try:
            pg.locator("button:has-text('Виджеты'), [role=button]:has-text('Виджеты')").first.click(timeout=6000)
            pg.wait_for_timeout(800)
            pg.get_by_text("Заявка", exact=True).first.click(timeout=6000)
            pg.wait_for_timeout(1400)
        except Exception as e:
            return f"нет виджета: {e}"
    # 2) открыть поиск инструмента
    box = pg.evaluate(_WIDGET_BOX_JS)
    opened = False
    if box:
        # клик по лупе в шапке виджета
        pg.mouse.click(box["x"] + 16, box["y"] + 14)
        pg.wait_for_timeout(500)
        opened = True
    if not opened:
        # пустой виджет: центральная лупа рядом с «Выберите инструмент»
        pos = pg.evaluate(r"""()=>{let leaf=null;for(const e of document.querySelectorAll('*')){if((e.textContent||'').includes('Выберите инструмент')){let d=false;for(const c of e.children)if((c.textContent||'').includes('Выберите инструмент')){d=true;break;}if(!d){leaf=e;break;}}}if(!leaf)return null;const b=leaf.getBoundingClientRect();return{cx:Math.round(b.x+b.width/2),cy:Math.round(b.y-48)};}""")
        if not pos:
            return "поиск инструмента не открыт"
        pg.mouse.click(pos["cx"], pos["cy"])
        pg.wait_for_timeout(500)
    # 3) ввести тикер и выбрать результат
    pg.keyboard.type(ticker, delay=60)
    pg.wait_for_timeout(1200)
    pt = _pick_result_point(pg, ticker)
    if not pt:
        pg.keyboard.press("Enter")
    else:
        pg.mouse.click(pt["x"], pt["y"])
    pg.wait_for_timeout(1400)
    # 4) вкладка
    tab = "Лимитная" if (typ == "limit" and price > 0) else "Рыночная"
    try:
        pg.get_by_text(tab, exact=True).first.click(timeout=4000)
        pg.wait_for_timeout(500)
    except Exception:
        pass
    # 5) заполнить цену/количество (нативный сеттер для React)
    filled = pg.evaluate(r"""(args)=>{
      const [price, lots, isLimit] = args;
      function setVal(input,val){const d=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');d.set.call(input,String(val));input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));}
      let inps=[...document.querySelectorAll('input')].filter(i=>{const b=i.getBoundingClientRect();return b.width>0 && /InlineInput-inp/i.test(i.className||'');});
      inps.sort((a,b)=>a.getBoundingClientRect().x-b.getBoundingClientRect().x);
      const out={n:inps.length};
      if(isLimit && inps.length>=2){ setVal(inps[0], price); out.price=inps[0].value; }
      const qi = inps.length? inps[inps.length-1] : null;
      if(qi){ setVal(qi, lots); out.qty=qi.value; }
      return out;
    }""", [price, lots, (typ == "limit" and price > 0)])
    return {"ok": True, "filled": filled}

@app.get("/fill_order")
def fill_order(q: str = "", side: str = "buy", qty: int = 1, price: float = 0.0, close: int = 0):
    if not _state["ok"]:
        return {"text": _state["err"] or "Терминал ещё не подключён, сэр."}
    try:
        plan = _plan_from_invest(q, side, qty, price, close)
    except Exception as e:
        return {"text": f"Не удалось рассчитать заявку, сэр. {type(e).__name__}"}
    if not plan.get("ok"):
        return {"text": plan.get("text", "Не смог подготовить заявку, сэр.")}
    ticker = plan["ticker"] or q
    res, err = _submit(lambda pg: _fill_widget(pg, ticker, plan["lots"], plan["price"], plan["type"]), timeout=40)
    if err or (isinstance(res, str)):
        return {"text": f"Открыл заявку, но заполнить не удалось ({err or res}), сэр. Проверьте виджет."}
    return {"text": plan["text"], "plan": plan}

# ------------------------------------------------------------------ СЦЕНАРИИ (авто-клик стакана)
def _scn_public(cfg):
    return {"scenarios": cfg.get("scenarios", []), "brain": cfg.get("brain", "ollama"),
            "has_claude": bool(cfg.get("claude_key")), "has_openai": bool(cfg.get("openai_key"))}

@app.get("/scn_list")
def scn_list():
    if not nox_scenarios:
        return {"scenarios": [], "brain": "ollama", "has_claude": False, "has_openai": False}
    return _scn_public(nox_scenarios.load())

@app.get("/scn_parse")
def scn_parse(text: str = ""):
    if not nox_scenarios:
        return {"ok": False, "text": "Модуль сценариев недоступен, сэр."}
    sc = nox_scenarios.parse_scenario(text)
    if not sc:
        return {"ok": False, "text": "Не понял сценарий, сэр. Повторите чётче."}
    return {"ok": True, "scenario": sc,
            "text": f"Сценарий для {sc.get('instrument','')}, событие {sc.get('event','')}, "
                    f"{len(sc.get('branches',[]))} веток. Задайте координаты стаканов."}

@app.get("/scn_save")
def scn_save(json_data: str = ""):
    if not nox_scenarios:
        return {"ok": False}
    try:
        sc = json.loads(json_data)
    except Exception:
        return {"ok": False, "text": "Плохой формат сценария, сэр."}
    cfg = nox_scenarios.load()
    sc.setdefault("id", "s" + str(int(time.time() * 1000)))
    sc.setdefault("enabled", True)
    sc.setdefault("name", sc.get("instrument", "сценарий"))
    scs = [x for x in cfg.get("scenarios", []) if x.get("id") != sc["id"]]
    scs.append(sc)
    cfg["scenarios"] = scs
    nox_scenarios.save(cfg)
    return {"ok": True, "scenario": sc}

@app.get("/scn_del")
def scn_del(id: str = ""):
    if not nox_scenarios:
        return {"ok": False}
    cfg = nox_scenarios.load()
    cfg["scenarios"] = [x for x in cfg.get("scenarios", []) if x.get("id") != id]
    nox_scenarios.save(cfg)
    return {"ok": True}

@app.get("/scn_toggle")
def scn_toggle(id: str = ""):
    if not nox_scenarios:
        return {"ok": False}
    cfg = nox_scenarios.load()
    for x in cfg.get("scenarios", []):
        if x.get("id") == id:
            x["enabled"] = not x.get("enabled")
    nox_scenarios.save(cfg)
    return {"ok": True}

@app.get("/scn_capture")
def scn_capture():
    if not nox_scenarios:
        return {"x": None, "y": None, "ts": 0}
    return nox_scenarios.last_captured()

@app.get("/scn_schedule")
def scn_schedule(id: str = "", dt: str = "", prewarm: int = 3):
    """Назначить сценарию время ожидаемого события (МСК). За prewarm минут до —
    прогрев Claude + анонс; срабатывание — по факту новости (США: BLS/BEA, РФ: лента)."""
    if not nox_scenarios:
        return {"ok": False, "text": "Сценарии недоступны."}
    return nox_scenarios.set_schedule(id, dt, prewarm)

@app.get("/scn_brain")
def scn_brain(brain: str = "", claude_key: str = "__keep__", openai_key: str = "__keep__"):
    if not nox_scenarios:
        return {"brain": "ollama"}
    cfg = nox_scenarios.load()
    if brain in ("ollama", "claude", "openai"):
        cfg["brain"] = brain
    if claude_key != "__keep__":
        cfg["claude_key"] = claude_key
    if openai_key != "__keep__":
        cfg["openai_key"] = openai_key
    nox_scenarios.save(cfg)
    return _scn_public(cfg)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8126, log_level="warning")
