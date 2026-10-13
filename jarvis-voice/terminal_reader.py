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
import threading, queue, time, json, re, os, socket, base64, struct, functools
import urllib.parse as _uparse
from fastapi import FastAPI
import uvicorn
try:
    import nox_scenarios
except Exception:
    nox_scenarios = None
try:
    import nox_calendar
except Exception:
    nox_calendar = None
try:
    import nox_dedup
except Exception:
    nox_dedup = None
try:
    import nox_watchers
except Exception:
    nox_watchers = None
try:
    import nox_features  # выключатели функций («Фичи» в Настройках)
except Exception:
    nox_features = None

def _feat(key):
    try:
        return nox_features.enabled(key) if nox_features else True
    except Exception:
        return True

try:
    import nox_cscalp   # стаканы CScalp по тикеру + горячие клавиши (проп без API)
except Exception:
    nox_cscalp = None
try:
    import nox_widgets
except Exception:
    nox_widgets = None

def _is_dup(text):
    """Похожая новость уже озвучивалась в последние 15 мин? (анти-повтор из разных источников)"""
    try:
        return bool(nox_dedup and nox_dedup.is_dup(text))
    except Exception:
        return False

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
  // момент ПОЯВЛЕНИЯ каждой новости на странице: следим за контейнером ленты (Date.now(), мс)
  const keyOf = (inner) => (inner.innerText || '').replace(/\s+/g,' ').trim().slice(0,120);
  const stamp = () => {
    const seen = window.__noxSeen;
    for (const inner of document.querySelectorAll('.news_row .news_title_inner')) {
      const k = keyOf(inner); if (k && !seen.has(k)) seen.set(k, Date.now());
    }
    if (seen.size > 3000) { const ks = [...seen.keys()].slice(0, 1500); ks.forEach(k => seen.delete(k)); }
  };
  if (!window.__noxSeen) window.__noxSeen = new Map();
  const first = document.querySelector('.news_row');
  const box = first && first.parentElement;
  if (box && window.__noxObsTarget !== box) {            // лента перерисована/вкладка сменилась — переподписаться
    if (window.__noxObs) window.__noxObs.disconnect();
    window.__noxObs = new MutationObserver(stamp);
    window.__noxObs.observe(box, {childList: true, subtree: true});
    window.__noxObsTarget = box;
  }
  stamp();
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
    const body = ((r.querySelector('.news_preview_inner')||r.querySelector('.news_content')||{}).innerText||'').replace(/\s+/g,' ').trim().slice(0,700);
    out.push({ text: text.slice(0,400), tickers, source, hl, hashtags, body,
               seen: window.__noxSeen.get(keyOf(inner)) || Date.now() });
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

TERMINAL_URL_DEFAULT = "https://www.tbank.ru/terminal/"


def _terminal_url():
    """Адрес терминала: из Настроек (tterminal_url), иначе по умолчанию."""
    try:
        u = (nox_scenarios._appdb().get("tterminal_url") or "").strip() if nox_scenarios else ""
    except Exception:
        u = ""
    return u if "tbank.ru" in u else TERMINAL_URL_DEFAULT


def _is_terminal(url):
    """Именно терминал, а не любая страница tbank.ru (status.tbank.ru при сбое — НЕ терминал)."""
    u = url or ""
    return "tbank.ru" in u and "/terminal" in u and "status.tbank" not in u


def _all_pages(browser):
    out = []
    for ctx in browser.contexts:
        for pg in ctx.pages:
            out.append(pg)
    return out


_keep_tid = {}


def _target_id(pg):
    """CDP-идентификатор вкладки (чтобы не закрыть свою)."""
    try:
        s = pg.context.new_cdp_session(pg)
        tid = s.send("Target.getTargetInfo")["targetInfo"]["targetId"]
        s.detach()
        return tid
    except Exception:
        return None


def _close_dupes(browser, keep):
    """Одна вкладка терминала на всё: лишние (восстановленные браузером, открытые вручную) закрываем.
    Playwright не видит вкладки, открытые не им (руками, другим клиентом), поэтому полный список берём
    у самого браузера (/json/list) и закрываем через /json/close — всё, кроме своей вкладки."""
    for pg in _all_pages(browser):
        if pg is not keep and _is_terminal(pg.url):
            try:
                pg.close()
            except Exception:
                pass
    try:
        import urllib.request as _u
        tid = _keep_tid.get(id(keep)) or _target_id(keep)
        if not tid:
            return                                   # своя вкладка не опознана — ничего не трогаем
        _keep_tid.clear(); _keep_tid[id(keep)] = tid
        for t in json.loads(_u.urlopen(CDP_URL + "/json/list", timeout=5).read().decode("utf-8")):
            if t.get("type") == "page" and _is_terminal(t.get("url")) and t.get("id") != tid:
                _u.urlopen(CDP_URL + "/json/close/" + t["id"], timeout=5).read()
    except Exception:
        pass


_last_open = [0.0]


def _find_terminal_page(browser):
    """Вкладка терминала: найти (лишние закрыть) или открыть ОДНУ — в пустой вкладке или на месте
    страницы статуса Т-Банка, новую создаём только если переиспользовать нечего."""
    pages = _all_pages(browser)
    term = [pg for pg in pages if _is_terminal(pg.url)]
    if term:
        _close_dupes(browser, term[0])
        return term[0]
    # идёт вход в аккаунт — не мешаем, работаем с этой страницей (health-loop попросит войти)
    login = next((pg for pg in pages if "id.tbank" in (pg.url or "") or "/auth" in (pg.url or "")), None)
    if login:
        return login
    if time.time() - _last_open[0] < 60:      # не долбить сайт при сбое Т-Банка: не чаще раза в минуту
        return next((pg for pg in pages if "tbank.ru" in (pg.url or "")), None)
    _last_open[0] = time.time()
    reuse = next((pg for pg in pages if "status.tbank" in (pg.url or "") or (pg.url or "") in ("", "about:blank")
                  or (pg.url or "").startswith(("chrome://newtab", "browser://", "about:"))), None)
    try:
        pg = reuse or (browser.contexts[0].new_page() if browser.contexts else None)
        if pg is None:
            return None
        pg.goto(_terminal_url(), timeout=30000)
        return pg
    except Exception:
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
                last_check = 0.0
                while True:
                    try:
                        fn, resp = _cmd_q.get(timeout=20)
                    except queue.Empty:
                        fn, resp = None, None
                    # раз в 20 с (по таймеру — очередь почти никогда не пустеет: лента, подписи веток):
                    # жива ли вкладка, не ушла ли на статус Т-Банка; лишние вкладки терминала — закрыть
                    if time.time() - last_check > 20:
                        last_check = time.time()
                        if not _is_terminal(pg.url):
                            np = _find_terminal_page(browser)
                            if np: pg = np
                        else:
                            _close_dupes(browser, pg)
                    if fn is None:
                        continue
                    try:
                        # если вкладку сменили/закрыли — найти заново
                        if not _is_terminal(pg.url):
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
        threading.Thread(target=_event_worker, daemon=True).start()     # корпсобытия — мозг в фоне
        threading.Thread(target=_focus_loop, daemon=True).start()       # режим тишины перед новостью по времени
        threading.Thread(target=_official_loop, daemon=True).start()    # решения ЦБ/ФРС прямо с cbr.ru / federalreserve.gov
        threading.Thread(target=_terminal_health_loop, daemon=True).start()
        threading.Thread(target=_overlay_loop, daemon=True).start()   # подписи веток наблюдателей
        threading.Thread(target=_cscalp_arm_loop, daemon=True).start()  # сверка стаканов CScalp у наблюдателей
        if nox_scenarios:
            nox_scenarios.start_capture()    # слушаем F2 для захвата координат стаканов
            nox_scenarios.start_scheduler()  # планировщик ожидаемых событий (прогрев Ollama, окно гонки, анонс)

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
# «Не читать» по умолчанию (2026-10-12, жалоба: биржевые уведомления НКЦ, законы, лимиты)
_DEFAULT_EXCLUDE = ["риск-параметры", "ставки рыночного риска", "лимит", "ценовой коридор"]

def _load_filter():
    try:
        d = json.load(open(FILTER_FILE, encoding="utf-8"))
    except Exception:
        d = {}
    d.setdefault("tickers", [])
    d.setdefault("phrases", [])
    d.setdefault("smart", False)   # умный отбор новостей через Ollama
    if "exclude" not in d:         # «Не читать»: сильнее источника/фраз/корпсобытий (наблюдатели — не трогает)
        d["exclude"] = list(_DEFAULT_EXCLUDE)
        _save_filter(d)
    if "sources" not in d:
        d["sources"] = list(_DEFAULT_SOURCES)
    d["smart"] = bool(d.get("smart"))
    d["tickers"] = [str(x).upper().strip() for x in d["tickers"] if str(x).strip()]
    d["phrases"] = [str(x).lower().strip() for x in d["phrases"] if str(x).strip()]
    d["exclude"] = [str(x).lower().strip() for x in d.get("exclude") or [] if str(x).strip()]
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

# ---- раскрытия (ЦРКИ / e-disclosure): «Проведение заседания совета директоров и его повестка дня» ----
# Заголовок пустой по смыслу, суть — в повестке. Важные пункты превращаем в фразу:
# «Циан: совет директоров 15 октября рассмотрит дивиденды за 9 месяцев 2026 года».
_DISC_ORG = [(r"совет\w* директоров|наблюдательн\w* совет", "совет директоров"),
             (r"собрани\w* акционеров|собрани\w* участников", "собрание акционеров")]
_DISC_TOPICS = [
    (r"дивиденд", "дивиденды"),
    (r"выкуп|байбек|приобретени\w* (?:собственных |размещенных |обыкновенных )?акций", "выкуп акций"),
    (r"допэмисс|дополнительн\w* (?:выпуск|эмисс|акци)|размещени\w* дополнительн|увеличени\w* уставного капитала",
     "допэмиссию"),
    (r"сплит|дроблени\w* акций|консолидаци\w* акций", "сплит акций"),
    (r"реорганизаци|присоединени|слияни", "реорганизацию"),
    (r"делистинг|исключени\w* (?:акций )?из (?:котировальн|списк)", "делистинг"),
    (r"\bipo\b|\bspo\b|публичн\w* (?:предложени|размещени)", "размещение акций"),
    (r"оферт", "оферту"),
]
_MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
               "октября", "ноября", "декабря"]

def _disclosure(item):
    """Раскрытие о заседании совета/собрания с ВАЖНОЙ повесткой -> фраза для озвучки, иначе None."""
    title, body = item.get("text", "") or "", item.get("body", "") or ""
    full = re.sub(r"\s+", " ", title + " " + body)
    low = full.lower().replace("ё", "е")
    agenda = "повестк" in low
    decided = bool(re.search(r"принят\w* (?:им |ими )?решени|решени\w*,? принят|итоги голосования|"
                             r"рекомендовал|утвердил", low))
    if not (agenda or decided):
        return None
    org = next((name for rx, name in _DISC_ORG if re.search(rx, low)), None)
    if not org:
        return None
    topics = []
    for rx, name in _DISC_TOPICS:
        if re.search(rx, low) and name not in topics:
            topics.append(name)
    if not topics:
        return None
    tks = item.get("tickers") or []
    who = TICKER_NAME.get(tks[0].upper()) if tks else None
    if not who:
        m = re.search(r"«([^»]{2,40})»", full)
        who = m.group(1) if m else (tks[0] if tks else "")
    when = ""
    m = re.search(r"дата проведения\W*(\d{1,2})\.(\d{2})\.(\d{4})", low)
    if m and 1 <= int(m.group(2)) <= 12:
        when = f" {int(m.group(1))} {_MONTHS_GEN[int(m.group(2)) - 1]}"
    period = ""
    if "дивиденды" in topics:
        m = re.search(r"(?:по результатам|по итогам|за)\s+((?:\d+|перв\w+|втор\w+|трет\w+|четверт\w+)\s+"
                      r"(?:месяц\w*|квартал\w*|полугоди\w*)\s+\d{4}\s+года|\d{4}\s+год\w*)", low)
        if m:
            per = m.group(1)
            # «по результатам первого полугодия» -> «за первое полугодие»; «по итогам 2025 года» -> «за 2025 год»
            for a, b in (("первого полугодия", "первое полугодие"), ("второго полугодия", "второе полугодие"),
                         ("первого квартала", "первый квартал"), ("второго квартала", "второй квартал"),
                         ("третьего квартала", "третий квартал"), ("четвертого квартала", "четвертый квартал")):
                per = per.replace(a, b)
            per = re.sub(r"^(\d{4}) года$", r"\1 год", per)
            period = " за " + per
    amount = ""
    if decided and "дивиденды" in topics:
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:руб|₽)", full, re.I)
        if m:
            amount = f" {m.group(1)} руб. на акцию"
    what = ", ".join(topics)
    if agenda and not decided:
        return f"{who}: {org}{when} рассмотрит {what}{period}".strip(" :")
    return f"{who}: решение, {org}{when}: {what}{amount}{period}".strip(" :")

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
# любая латинская подпись агентства в конце: «- AL HADATH», «- AL ARABIYA», «- SKY NEWS», «- WSJ»
_SRC_TAIL_LAT_RE = re.compile(r"\s+[—\-–·|]\s+[A-Z][A-Za-z.&']*(?:\s+[A-Z][A-Za-z.&']*){0,3}\s*$")
_MAX_SAY = 320   # символов: длиннее — только по границе предложения/фразы, без обрыва посреди

def _clean_sentence(s, tickers):
    s = _SRC_TAIL_LAT_RE.sub("", _SRC_TAIL_RE.sub("", s.strip()))
    s = _ATTR_RE.sub("", s).strip(" .,;—-–")          # «…, сообщает X»
    for tk in tickers:                                 # тикеры -> произносимые имена
        s = re.sub(r"(?<![A-Za-zА-Яа-яЁё])" + re.escape(tk) + r"(?![A-Za-zА-Яа-яЁё])",
                   _tk_name(tk), s, flags=re.I)
    return s

def _shorten(item):
    """Новость для озвучки: корпсобытие -> 'Имя [цена] событие'; иначе — заголовок ЦЕЛИКОМ
    (без подписи агентства и «… сообщает X», тикеры -> имена).
    Раньше: только первое предложение и обрезка на 120 символах посреди фразы — длинные заголовки
    (МИД Великобритании, 150–230 симв.) обрывались на «…вызвали посла Ирана, чтобы…». Теперь читаем
    целиком; несколько предложений — пока влезает в _MAX_SAY; одно очень длинное — по последней
    запятой/двоеточию. Предложение, обрезанное самой лентой (без знака в конце), не читаем."""
    c = _concise(item)
    if c:
        return c
    raw = re.sub(r"\s+", " ", item.get("text") or "").strip()
    sents = [s for s in re.split(r"(?<=[.!?…])\s", raw) if s.strip()]
    if len(sents) > 1 and not re.search(r"[.!?…»\")]$", sents[-1]):
        sents = sents[:-1]                             # хвост, обрезанный лентой («…сможет вернут»)
    tickers = item.get("tickers") or []
    t = _clean_sentence(sents[0], tickers) if sents else ""
    for s in sents[1:]:
        s = _clean_sentence(s, tickers)
        if not s or len(t) + 2 + len(s) > _MAX_SAY:
            break
        t += ". " + s
    if len(t) > _MAX_SAY:                              # одно очень длинное предложение
        cut = t[:_MAX_SAY]
        m = max(cut.rfind(","), cut.rfind(":"), cut.rfind(";"), cut.rfind(" - "), cut.rfind(" — "))
        t = (cut[:m] if m > _MAX_SAY // 2 else cut.rsplit(" ", 1)[0]).rstrip(" .,;—-–")
    return t or raw

# ------------------------------------------------------------------ умный отбор (Ollama, локально)
_OLLAMA_CHAT = "http://127.0.0.1:11434/v1/chat/completions"
_SMART_SYS = (
    "Перескажи суть биржевой новости ОДНОЙ короткой фразой грамотным русским языком для озвучки "
    "(правильные падежи и окончания, названия компаний по-русски, без латиницы) "
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
    """Умный отбор: важная новость -> краткая суть; иначе None. Ollama (модель со страницы «Мозг»), chat + few-shot."""
    import urllib.request
    text = (text or "").strip()
    if len(text) < 8:
        return None
    msgs = [{"role": "system", "content": _SMART_SYS}]
    for u, a in _SMART_SHOTS:
        msgs += [{"role": "user", "content": u}, {"role": "assistant", "content": a}]
    msgs.append({"role": "user", "content": text})
    body = json.dumps({
        "model": (nox_scenarios._ollama_model() if nox_scenarios else "qwen2.5:3b"), "messages": msgs, "stream": False,
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

_VOW = "аеёиоуыэюяьъй"

_morph = {"m": None}

def _morph_get():
    if _morph["m"] is None:
        try:
            import pymorphy3
            _morph["m"] = pymorphy3.MorphAnalyzer()
        except Exception:
            _morph["m"] = False
    return _morph["m"]

@functools.lru_cache(maxsize=50000)
def _lemmas(w):
    """Все начальные формы слова («санкций» -> {санкция}); без словаря — само слово."""
    m = _morph_get()
    if not m:
        return frozenset([w])
    return frozenset(p.normal_form.replace("ё", "е") for p in m.parse(w)[:3])

@functools.lru_cache(maxsize=2000)
def _phrase_parts(phrase):
    m = _morph_get()
    out = []
    for w in re.findall(r"[а-яa-z0-9]+", phrase.lower().replace("ё", "е")):
        known = bool(m) and len(w) > 3 and m.word_is_known(w)
        out.append((w, _lemmas(w) if known else None))
    return tuple(out)

def _phrase_hit(phrase, low):
    """Фраза фильтра совпадает с любой ФОРМОЙ тех же слов: «дивиденды» -> «дивиденда/дивидендов»,
    «санкции» -> «санкций», «военное положение» -> «военного положения». Сравнение по начальной форме
    (pymorphy3), а не по обрезанной основе: основа «поддерж» ловила «ПОДДЕРЖиваемое Ираном»,
    «приб» — «прибытие». Плюс начало слова целиком («выкуп» -> «выкупит»). Имена вне словаря
    («уиткоф») — по началу слова («уиткоффа»). Короткие (≤3: «фас») — только целым словом («фасад» — мимо)."""
    toks = re.findall(r"[а-яa-z0-9]+", low)
    tok_set = None
    lem_set = None
    for w, lems in _phrase_parts(phrase):
        if len(w) <= 3:
            if tok_set is None:
                tok_set = set(toks)
            if w not in tok_set:
                return False
            continue
        if any(t.startswith(w) for t in toks):
            continue
        if lems:
            if lem_set is None:
                lem_set = set().union(*(_lemmas(t) for t in toks)) if toks else set()
            if lems & lem_set:
                continue
        return False
    return True

def _excluded(item, cfg):
    """Заголовок попал в «Не читать» (любая форма слова: «лимит» -> «лимитов», «закон» -> «законопроект»)."""
    ex = cfg.get("exclude") or []
    if not ex:
        return False
    low = (item.get("text", "") or "").lower().replace("ё", "е")
    return any(_phrase_hit(p.replace("ё", "е"), low) for p in ex)

def _match(item, cfg):
    """Реагировать только на заданное: тикер ИЛИ фраза (любая форма слова) ИЛИ источник из конфига."""
    tks = [t.upper() for t in (item.get("tickers") or [])]
    if cfg["tickers"] and any(t in cfg["tickers"] for t in tks):
        return True
    low = (item.get("text", "") + " " + item.get("body", "")).lower().replace("ё", "е")
    if cfg["phrases"] and any(_phrase_hit(p.replace("ё", "е"), low) for p in cfg["phrases"]):
        return True
    src = (item.get("source", "") or "").lower()
    # ЦРКИ НЕ приравниваем к e-disclosure: читать все раскрытия подряд — шум («одобрение сделок»).
    # Важные раскрытия (дивиденды, выкуп, допэмиссия…) читает _disclosure — независимо от фильтра.
    if cfg["sources"] and any(s in src for s in cfg["sources"]):
        return True
    return False

_seen_news = set()
_event_q = queue.Queue()
_focus = {"active": None}     # режим тишины: {"wid","reason","until","at"} или None

def _official_loop():
    """Решения ЦБ и ФРС ПРЯМО с официальных сайтов (nox_official) — параллельно с лентой, кто первый.
    Окно наблюдателя «Новость по времени» (cbr/fed): за 90 с — заранее открыть соединение (до сайта ФРС
    из РФ TLS идёт 0.4–13 с: Cloudflare замедлен); T−20 с … T+2 мин — опрос ~3 раза/с; дальше до T+15 мин —
    раз в 5 с. Найденное решение отдаётся наблюдателям как первая новость (on_news под замком — без дублей)."""
    try:
        import nox_official
    except Exception:
        return
    done, mark = set(), {}
    while True:
        fast = False
        try:
            if nox_watchers and _feat("watchers"):
                now = nox_watchers._now()
                today = now.strftime("%Y-%m-%d")
                secs = now.hour * 3600 + now.minute * 60 + now.second
                polled = set()
                for w in nox_watchers.load()["watchers"]:
                    bank = w.get("bank")
                    if not (w.get("enabled") and w.get("kind") == "rate" and bank in ("cbr", "fed") and w.get("from")):
                        continue
                    if (w.get("date") and w["date"] != today) or (w.get("once") and w.get("fired_day") == today):
                        continue
                    key = (bank, today)
                    if key in done or key in polled:
                        continue
                    h, m, sec = (int(x) for x in w["from"].split(":"))
                    d = secs - (h * 3600 + m * 60 + sec)
                    if -90 <= d < -20:
                        if time.time() - mark.get(("warm",) + key, 0) > 25:
                            mark[("warm",) + key] = time.time()
                            nox_official.warm(bank, today)
                        continue
                    if not (-20 <= d <= 900):
                        continue
                    if d > 120 and time.time() - mark.get(("slow",) + key, 0) < 5:
                        continue
                    mark[("slow",) + key] = time.time()
                    polled.add(key)
                    fast = fast or d <= 120
                    txt, ms, st = nox_official.poll(bank, today)
                    if txt:
                        done.add(key)
                        src = "cbr.ru" if bank == "cbr" else "federalreserve.gov"
                        if nox_scenarios:
                            nox_scenarios._log(f"ОФИЦИАЛЬНО ({src}, запрос {ms} мс): {txt[:140]}")
                        item = {"text": txt, "body": "", "tickers": [], "source": src,
                                "seen": time.time() * 1000, "_detect": time.time()}
                        nox_watchers.on_news([item], "trading")
        except Exception:
            pass
        time.sleep(0.3 if fast else 1.0)

def _focus_loop():
    """Режим тишины перед новостью по времени: за N минут до выхода (наблюдатель «Решение по времени») новости,
    корпсобытия и календарь НЕ читаются — ждём только нужное сообщение. Отчёт наблюдателя о сделке звучит.
    Другим службам (монитор ПК, Магеллан, watchdog) — через nox_focus.json."""
    prev = None
    focus_file = r"C:\jarvis-voice\nox_focus.json"
    while True:
        try:
            st = nox_watchers.focus_state() if (nox_watchers and _feat("watchers")) else None
            _focus["active"] = st
            if st:
                if not prev or prev.get("wid") != st["wid"] or abs(prev.get("until", 0) - st["until"]) > 5:
                    with open(focus_file, "w", encoding="utf-8") as f:
                        json.dump({"until": st["until"], "reason": st["reason"]}, f, ensure_ascii=False)
                if not prev or prev.get("wid") != st["wid"]:
                    _ipc_announce(f"Режим тишины: жду {st['reason']} в {st['at']}. Новости не читаю.")
            else:
                if os.path.exists(focus_file):
                    os.remove(focus_file)
                if prev:
                    w = nox_watchers.get(prev["wid"]) if nox_watchers else None
                    today = nox_watchers._now().strftime("%Y-%m-%d") if nox_watchers else ""
                    done = (not w) or (not w.get("enabled")) or w.get("fired_day") == today
                    if not done:            # время вышло, а решения нет
                        _ipc_announce(f"Режим тишины снят: {prev['reason']} пока не вышло. Снова читаю новости.")
            prev = st
        except Exception:
            pass
        time.sleep(1)

def _event_worker():
    """Корпсобытия по очереди (порядок новостей сохраняется): мозг извлекает тип/тикер/размер.
    Озвучиваем, только если мозг подтвердил ТОТ ЖЕ тип, что нашли слова; иначе новость идёт
    обычным фильтром (как все остальные)."""
    while True:
        it, cfg = _event_q.get()
        try:
            import nox_scenarios as _ns
            text = it.get("text", "")
            ev = _ns.extract_event(text)
            if _focus.get("active"):
                continue                    # разбор шёл до тишины — уже не озвучиваем
            if _ns.event_confirmed(ev):
                if _mon_enabled() and not _is_dup(text):
                    _ipc_announce(_ns.speak_event(ev)); time.sleep(0.4)
            elif _mon_enabled() and _match(it, cfg) and not _is_dup(text):
                _ipc_announce(_shorten(it)); time.sleep(0.4)
        except Exception:
            pass

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
            # ключ без регистра/знаков/подписи источника: «ТРАМП: …» и «Трамп: …» — одна новость
            _key = (lambda x: nox_dedup.key(x)) if nox_dedup else (lambda x: x[:80])
            for it in items:                    # newest first
                key = _key(it["text"])
                if key in _seen_news:
                    continue
                _seen_news.add(key)
                if not first:
                    new_items.append(it)
            first = False
            if len(_seen_news) > 5000:
                # НЕ очищать целиком: иначе вся текущая лента (до 40 новостей) разом станет «свежей» —
                # повторное чтение пачкой и ложные срабатывания наблюдателей. Оставляем только текущую ленту.
                _seen_news.clear()
                _seen_news.update(_key(it["text"]) for it in items)
            _t_detect = time.time()
            for it in new_items:
                it["_detect"] = _t_detect
            # Сценарии (авто-клик стакана) работают ВСЕГДА, независимо от монитора озвучки.
            if new_items:
                if _feat("scenarios"):
                    try:
                        import nox_scenarios
                        nox_scenarios.on_news(new_items)
                    except Exception:
                        pass
                if _feat("watchers"):
                    try:
                        import nox_watchers
                        nox_watchers.on_news(new_items, "trading")   # «мультимозг»: наблюдатели на 1 новость
                    except Exception:
                        pass
            # Всегда-он разбор ключевых событий (дивиденд/оферта/допэмиссия/дефолт/выкуп):
            # тикер+размер ловим и ЛОГИРУЕМ всегда; озвучиваем при включённом мониторе.
            for it in reversed(new_items):      # в хронологическом порядке
                low = it.get("text", "").lower()
                if _focus.get("active"):
                    continue                     # режим тишины: ждём только нужную новость, остальное не читаем
                if _excluded(it, cfg):
                    continue                     # «Не читать»: риск-параметры НКЦ, лимиты, законы…
                disc = None
                corp = _feat("corp_events")      # «Фичи»: выкл — новость идёт обычным фильтром
                try:
                    disc = _disclosure(it) if corp else None
                except Exception:
                    disc = None
                if disc:
                    # заседание совета/собрания с важной повесткой — читаем всегда (как корпсобытия)
                    if _mon_enabled() and not _is_dup(disc):
                        _ipc_announce(disc); time.sleep(0.4)
                    continue
                ev_typ = None
                try:
                    import nox_scenarios as _ns
                    ev_typ = _ns.event_type(it.get("text", "")) if corp else None
                except Exception:
                    ev_typ = None
                if ev_typ:
                    # разбор мозгом (облако 4–9 с) — в фоне: опрос ленты не ждёт (раньше стоял на каждой
                    # «событийной» новости — в окне решения по ставке это задержало бы реакцию)
                    _event_q.put((it, cfg))
                    continue
                if _mon_enabled():               # прочие новости — прежняя логика
                    if smart:
                        say = _ollama_judge(it.get("text", ""))
                        if say and not _is_dup(it.get("text", "")):
                            _ipc_announce(say); time.sleep(0.4)
                    elif _match(it, cfg) and not _is_dup(it.get("text", "")):
                        _ipc_announce(_shorten(it)); time.sleep(0.4)
        except Exception:
            pass
        # было 2 с: на новость реагируем быстрее; в окне решения по ставке (−5 с…+3 мин) — 0.1 с
        try:
            fast = bool(nox_watchers and nox_watchers.fast_window())
        except Exception:
            fast = False
        time.sleep(0.1 if fast else 0.5)

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

def _warm_brain():
    """Прогреть соединение с API-мозгом, чтобы первый роут не был холодным (25с)."""
    try:
        import nox_scenarios as ns
        model = ns._appdb().get("openai_model") or None
        if model:
            ns._llm_model("Ответь одним словом: ок", "прогрев", model, max_tokens=5)
    except Exception:
        pass

@app.on_event("startup")
def _startup():
    start_worker()
    _start_cal_loop()
    _start_cal_arm_loop()
    threading.Thread(target=_warm_brain, daemon=True).start()

@app.get("/health")
def health():
    return {"status": "ok" if _state["ok"] else "starting", "err": _state["err"], "url": _state["url"]}

_ROW_DUMP_JS = r"""
(q) => {
  q = (q || '').toLowerCase();
  const out = [];
  for (const r of document.querySelectorAll('.news_row')) {
    const all = (r.innerText || '').replace(/\s+/g, ' ').trim();
    if (q && !all.toLowerCase().includes(q)) continue;
    const cls = [...new Set([...r.querySelectorAll('[class]')].map(e => String(e.className).split(' ')[0]))].slice(0, 40);
    out.push({ all: all.slice(0, 1500), cls });
    if (out.length >= 5) break;
  }
  return out;
}
"""

@app.get("/mon_items")
def mon_items(q: str = ""):
    """Отладка (только чтение): что монитор берёт из ленты — заголовок/тело/источник/тикеры — и полный текст строк."""
    if not _state["ok"]:
        return {"ok": False, "text": _state.get("err") or "Терминал не подключён."}
    items, err = _submit(lambda pg: pg.evaluate(MON_JS), timeout=25)
    rows, err2 = _submit(lambda pg: pg.evaluate(_ROW_DUMP_JS, q), timeout=25)
    ql = q.lower()
    hit = [it for it in (items or []) if not ql or ql in (it.get("text", "") + " " + it.get("body", "") + " "
                                                         + " ".join(it.get("tickers") or [])).lower()]
    return {"ok": not (err or err2), "items": hit[:5], "rows": rows or [], "err": err or err2}

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

# ------------------------------------------------------------------ интернет через браузер (CDP)
def _web_search(query, n_chars=3500):
    """Поиск в сети через тот же Яндекс-браузер: открыть новую вкладку, прочитать выдачу, закрыть."""
    def fn(pg):
        np = pg.context.new_page()
        try:
            np.goto("https://yandex.ru/search/?text=" + _uparse.quote(query),
                    timeout=20000, wait_until="domcontentloaded")
            np.wait_for_timeout(1800)
            txt = np.inner_text("body")
            return re.sub(r"\n{3,}", "\n\n", txt)[:n_chars]
        finally:
            try: np.close()
            except Exception: pass
    if not _state["ok"]:
        return ""
    res, err = _submit(fn, timeout=35)
    return (res or "") if not err else ""

@app.get("/web")
def web(q: str = ""):
    """Сырой текст веб-выдачи (для отладки)."""
    if not q.strip():
        return {"text": ""}
    return {"text": _web_search(q)}

@app.get("/web_answer")
def web_answer(q: str = ""):
    """Поиск в интернете + ответ API-мозгом (погода, курс, факты, «посмотри в сети»)."""
    if not q.strip():
        return {"text": "Что найти, сэр?"}
    results = _web_search(q)
    if not results.strip():
        return {"text": "Не смог открыть поиск, сэр. Проверьте окно браузера."}
    try:
        import nox_scenarios as ns
        model = ns._appdb().get("openai_model") or None
        sysp = ("Ты голосовой ассистент. Ответь на вопрос пользователя КРАТКО по-русски (1-2 предложения), "
                "опираясь на результаты веб-поиска ниже. Если есть конкретная цифра, курс или дата — назови её. "
                "Не упоминай сам поиск и источники." + ns.SPEECH_RULES)
        user = f"Вопрос: {q}\n\nРезультаты поиска:\n{results[:3500]}"
        ans = (ns._llm_model(sysp, user, model, max_tokens=160) if model
               else ns._llm(sysp, user, max_tokens=160)) or ""
        return {"text": ans.strip() or "Не нашёл точного ответа, сэр."}
    except Exception:
        return {"text": "Поиск прочитал, но мозг недоступен, сэр."}

# ------------------------------------------------------------------ РОУТЕР: запрос -> мозг -> действие
_ROUTER_SYS = (
    "Ты — маршрутизатор голосового ассистента Nox (биржевой терминал + помощник). "
    "На запрос пользователя верни СТРОГИЙ JSON одного действия, без пояснений и без markdown:\n"
    '{"action":"answer","text":"<короткий ответ по-русски, 1-3 предложения>"} — обычный вопрос или разговор, '
    "на который можно ответить общими знаниями (без свежих данных).\n"
    '{"action":"web","query":"<поисковый запрос>"} — нужны СВЕЖИЕ данные из интернета: погода, курс валют, '
    "цены, что происходит сейчас/сегодня, факты о текущих событиях, кто/что это.\n"
    '{"action":"watch","phrase":"<исходная фраза пользователя>"} — просит следить/наблюдать за КОНКРЕТНОЙ новостью или темой '
    '(«следи за дивидендами Сбера»). «Следи за новостями / читай новости» БЕЗ темы — это local (включить чтение ленты).\n'
    '{"action":"explain_news"} — просит объяснить или про что последняя/эта новость.\n'
    '{"action":"local"} — это КОМАНДА программе: портфель, котировки, заявка/ордер, открой программу или сайт, '
    "заметка, напоминание, нагрузка компьютера, настройки. Их исполнит сама программа.\n"
    "Если сомневаешься между answer и web — выбирай web. Отвечай ТОЛЬКО JSON."
    " Текст в поле text — грамотным русским языком: правильные падежи и окончания, числа согласованы "
    "со словами, иностранные названия по-русски, без латиницы и эмодзи."
)
_LOCAL = "__LOCAL__"

def _brain_explain_news():
    news = last_news().get("text", "")
    if not news.strip():
        return "Пока не вижу новостей в ленте, сэр."
    try:
        import nox_scenarios as ns
        model = ns._appdb().get("openai_model") or None
        sysp = ("Объясни простыми словами по-русски эту биржевую новость и почему она важна, 2-3 коротких предложения."
                + ns.SPEECH_RULES)
        ans = (ns._llm_model(sysp, news, model, max_tokens=160) if model else ns._llm(sysp, news, max_tokens=160)) or ""
        return ans.strip() or news
    except Exception:
        return news

# «следи за новостями / читай новости» БЕЗ темы = включить чтение ленты (а не наблюдатель «новости»);
# «следи за новостями про Сбер» — тема есть -> это уже наблюдатель (идёт в мозг как раньше).
_NEWS_FILLER = {"нокс", "следи", "следить", "последи", "за", "новостями", "новости", "новостей", "новость", "лентой", "ленту",
                "ленты", "читай", "читать", "включи", "включить", "озвучивай", "озвучивать", "вслух", "пожалуйста",
                "все", "всеми", "всё", "давай", "начни", "мне", "весь", "день", "сегодня", "теперь", "снова", "опять",
                "и", "а", "можешь", "можно", "отслеживай", "новостную", "новостной", "биржевые", "биржевыми"}
_NEWS_OFF = {"не", "выключи", "выключить", "отключи", "хватит", "прекрати", "перестань", "стоп", "останови", "замолчи"}


def news_command(t):
    """Команда чтения ленты без темы: True — включить, False — выключить, None — это не она."""
    words = re.findall(r"[а-яёa-z]+", (t or "").lower())
    if not words or not any(w.startswith(("новост", "лент")) for w in words):
        return None
    if any(w in _NEWS_OFF for w in words) and all(w in _NEWS_FILLER or w in _NEWS_OFF for w in words):
        return False
    if all(w in _NEWS_FILLER for w in words):
        return True
    return None


# Явные команды программе — сразу «__LOCAL__», без запроса к мозгу (экономит 2–5 с на каждой).
# Только однозначные слова-команды; вопросы («курс доллара», «что такое дивиденды») идут в мозг как раньше.
_LOCAL_KW = ("портфел", "стакан", "заявк", "выставь", "выстави", "открой", "запусти", "заметк", "запомни",
             "напомни", "напоминани", "нагрузк", "магеллан", "котировк", "почём", "почем", "закрой позиц",
             "микрофон", "сценари", "статус торг", "аукцион", "на счету", "баланс")
_WATCH_RE = re.compile(r"^(?:нокс[,\s]+)?(?:наблюдай|понаблюдай|последи\s+за|отслеживай|создай\s+наблюдател|"
                       r"добавь\s+наблюдател|следи\s+весь\s+день)")


def fast_route(t):
    """Быстрый путь без мозга: «наблюдай …» -> наблюдатель, явная команда -> __LOCAL__, иначе None."""
    low = (t or "").lower().strip()
    if _WATCH_RE.search(low):
        try:
            import nox_watchers
            return nox_watchers.add_from_voice(t).get("text", "")
        except Exception:
            return None
    if any(k in low for k in _LOCAL_KW):
        return _LOCAL
    return None


@app.get("/route")
def route(text: str = ""):
    """Запрос -> мозг решает действие -> исполняем (answer/web/watch/explain) или '__LOCAL__' для команды программе."""
    t = (text or "").strip()
    if not t:
        return {"text": _LOCAL}
    nc = news_command(t)                      # быстрый путь, без мозга
    if nc is not None:
        _set_mon(nc)
        return {"text": "Читаю новости, сэр." if nc else "Чтение новостей выключено, сэр."}
    fr = fast_route(t)
    if fr is not None:
        return {"text": fr}
    try:
        import nox_scenarios as ns
        model = ns._appdb().get("openai_model") or None
        raw = (ns._llm_model(_ROUTER_SYS, t, model, max_tokens=220) if model
               else ns._llm(_ROUTER_SYS, t, max_tokens=220))
        j = ns._json_from(raw) or {}
    except Exception:
        j = {}
    act = (j.get("action") or "local").lower()
    if act == "answer":
        return {"text": (j.get("text") or "").strip() or _LOCAL}
    if act == "web":
        return {"text": web_answer(j.get("query") or t).get("text", "")}
    if act == "explain_news":
        return {"text": _brain_explain_news()}
    if act == "watch":
        if news_command(t) is not None:       # подстраховка: «следи за новостями» без темы — не наблюдатель
            _set_mon(news_command(t))
            return {"text": "Читаю новости, сэр."}
        try:
            import nox_watchers
            # исходная фраза целиком: пересказ роутера может потерять ветки и заявки
            return {"text": nox_watchers.add_from_voice(t).get("text", "")}
        except Exception:
            return {"text": _LOCAL}
    return {"text": _LOCAL}   # команда программе — исполнит app.rs своим путём

@app.get("/last_news")
def last_news():
    """Самая свежая новость из ленты — для «объясни/про что последняя новость»."""
    if not _state["ok"]:
        return {"text": ""}
    items, err = _submit(lambda pg: pg.evaluate(NEWS_JS))
    if err or not items:
        return {"text": ""}
    return {"text": items[0], "items": items[:5]}

_PROBE_JS = r"""
() => {
  const res = {groups:0, items:0, classes:{}, timeHits:[], usHits:[], econ:[]};
  // счётчики по текущему виджету
  res.groups = document.querySelectorAll('.mind-events-date-group').length;
  res.items  = document.querySelectorAll('.mind-event-item-new').length;
  // ищем элементы с временем HH:MM и/или упоминанием США/макро
  const all = document.querySelectorAll('div,li,tr,span');
  let n=0;
  for (const el of all) {
    const t=(el.innerText||'').replace(/\s+/g,' ').trim();
    if(!t || t.length>160) continue;
    if(/\b\d{1,2}:\d{2}\b/.test(t) && (res.timeHits.length<12)) res.timeHits.push(t.slice(0,120));
    if(/(сша|u\.s|нонфарм|nonfarm|ввп|gdp|инфляц|cpi|ставка|фрс|fomc|запас|нефт|газ)/i.test(t) && res.usHits.length<15) res.usHits.push(t.slice(0,120));
    if(n++>4000) break;
  }
  // классы контейнеров, похожих на календарь/экономику
  for (const el of document.querySelectorAll('[class*=calend],[class*=event],[class*=econom],[class*=macro]')) {
    const c = (el.className||'').toString().slice(0,80);
    if(c) res.classes[c]=(res.classes[c]||0)+1;
    if(Object.keys(res.classes).length>40) break;
  }
  return res;
}
"""
# ------------------------------------------------------------------ авто-календарь (утренний импорт)
def _cal_fetch_raw():
    if not _state["ok"]:
        return None, _state["err"] or "терминал не подключён"
    return _submit(lambda pg: pg.evaluate(CAL_JS))

def _cal_import_and_digest(announce=True):
    if not nox_calendar:
        return {"ok": False, "text": "Календарь-модуль недоступен."}
    raw, err = _cal_fetch_raw()
    if err or raw is None:
        return {"ok": False, "text": f"Не смог прочитать календарь: {err or ''}".strip()}
    evs = nox_calendar.ingest(raw)
    today = nox_calendar.today_events()
    digest = nox_calendar.digest_text(today)
    try:
        if nox_scenarios:
            nox_scenarios._log(f"КАЛЕНДАРЬ импортирован: всего ключевых {len(evs)}, сегодня {len(today)}. {digest}")
    except Exception:
        pass
    if announce:
        _ipc_announce(digest)
    return {"ok": True, "imported": len(evs), "today": len(today), "text": digest, "events": evs}

_cal_loop_started = False
def _start_cal_loop():
    global _cal_loop_started
    if _cal_loop_started:
        return
    _cal_loop_started = True
    def loop():
        import time as _t
        _t.sleep(30)                       # дать терминалу подключиться
        try: _cal_import_and_digest(announce=False)   # первый импорт тихо
        except Exception: pass
        last_day = ""
        while True:
            try:
                now = nox_calendar.now_msk() if nox_calendar else None
                if now:
                    # утренний импорт+дайджест раз в день в ~08:00 МСК
                    if now.hour >= 8 and now.strftime("%Y-%m-%d") != last_day:
                        last_day = now.strftime("%Y-%m-%d")
                        _cal_import_and_digest(announce=_feat("calendar") and not _focus.get("active"))
            except Exception:
                pass
            _t_sleep = 300
            import time as _t; _t.sleep(_t_sleep)
    threading.Thread(target=loop, daemon=True).start()

_cal_arm_started = False
def _start_cal_arm_loop():
    """За ~3 мин до КАЖДОГО timed-события календаря (FOMC, PMI, инфляция и т.п.) —
    активировать гонку источников + прогрев + анонс. Сам факт ловится лентой/сценариями."""
    global _cal_arm_started
    if _cal_arm_started or not nox_calendar:
        return
    _cal_arm_started = True
    def loop():
        import time as _t
        fired = set()
        while True:
            try:
                now = nox_calendar.now_msk()
                for e in nox_calendar.today_events():
                    tm = e.get("time")
                    if not tm:
                        continue
                    try:
                        hh, mm = tm.split(":")
                        evt = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
                    except Exception:
                        continue
                    mins = (evt - now).total_seconds() / 60.0
                    key = e.get("date", "") + tm + e.get("text", "")[:25]
                    if 0 < mins <= 3 and key not in fired:
                        fired.add(key)
                        if nox_scenarios:
                            try: nox_scenarios.arm_window(360)
                            except Exception: pass
                        if _feat("calendar") and not _focus.get("active"):
                            _ipc_announce(f"Через {max(1, int(round(mins)))} минут — "
                                          f"{e.get('text', 'событие')[:90]}, сэр. Готовлюсь.")
            except Exception:
                pass
            _t.sleep(40)
    threading.Thread(target=loop, daemon=True).start()

@app.get("/cal_import")
def cal_import():
    """Прочитать календарь сейчас, импортировать ключевые события, вернуть дайджест (без озвучки)."""
    return _cal_import_and_digest(announce=False)

@app.get("/cal_today")
def cal_today():
    if not nox_calendar:
        return {"events": []}
    ev = nox_calendar.today_events()
    return {"events": ev, "text": nox_calendar.digest_text(ev)}

@app.get("/cal_digest")
def cal_digest():
    """Озвучить дайджест календаря на сегодня."""
    if not nox_calendar:
        return {"ok": False}
    ev = nox_calendar.today_events()
    d = nox_calendar.digest_text(ev)
    _ipc_announce(d)
    return {"ok": True, "text": d}

@app.get("/dom_probe")
def dom_probe():
    """ВРЕМЕННЫЙ диагностический: найти, где в DOM висят US/макро-события и есть ли время."""
    if not _state["ok"]:
        return {"text": _state["err"] or "терминал не подключён"}
    res, err = _submit(lambda pg: pg.evaluate(_PROBE_JS))
    return {"err": err, "res": res}

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
    elif kind.startswith("exclude"):
        v = val.lower()
        if v not in cfg["exclude"]:
            cfg["exclude"].append(v)
        say = f"Не буду читать новости про «{v}»."
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
    elif kind.startswith("exclude"):
        cfg["exclude"] = []
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
    elif kind.startswith("exclude"):
        cfg["exclude"] = [x for x in cfg["exclude"] if x != val.lower()]
        say = f"Снова читаю новости про «{val}»."
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
    if cfg.get("exclude"):
        parts.append("не читать: " + ", ".join(cfg["exclude"]))
    return {"text": ("Фильтр — " + "; ".join(parts)) if parts else "Фильтр пуст, сэр.", "filter": cfg}

@app.get("/terminal_show")
def terminal_show():
    """Показать терминал в окне Нокса (вкладка на передний план). Новых вкладок не открывает:
    если терминала нет — ридер сам откроет ровно одну."""
    if not _state["ok"]:
        return {"ok": False, "text": _state.get("err") or "Терминал ещё подключается, сэр."}
    res, err = _submit(lambda pg: (pg.bring_to_front(), pg.url)[1], timeout=15)
    if err:
        return {"ok": False, "text": f"Не смог показать терминал: {err}"}
    return {"ok": True, "text": "Терминал открыт в окне Нокса, сэр.", "url": res}

@app.get("/mon")
def mon(on: int = -1):
    if on in (0, 1):
        _set_mon(bool(on))
    return {"text": ("Чтение новостей включено, сэр." if _mon_enabled() else "Чтение новостей выключено, сэр."),
            "on": _mon_enabled()}

# ------------------------------------------------------------------ ЗАПОЛНЕНИЕ ВИДЖЕТА «ЗАЯВКА»
# /fill_order?exec=0 — Nox ГОТОВИТ заявку (тикер/цена/объём), кнопку жмёт пользователь.
# /fill_order?exec=1 — заполняет и САМ жмёт Купить/Продать (голос «выставь заявку …»,
# решение пользователя 2026-10-05: без подтверждения). Кнопка жмётся, только если
# в виджете точно наш тикер и нужное количество — иначе отказ без нажатия.

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

_ORDER_RESULT_RE = (r"заявк|исполнен|выставлен|отклон|ошибк|недостаточно|не удалось|"
                    r"запрещ|недоступ|лимит|маржин")

def _press_order_button(pg, ticker, lots, side):
    """Нажать Купить/Продать в виджете «Заявка». Перед нажатием — сверка тикера и количества."""
    btn_text = "Купить" if side == "buy" else "Продать"
    info = pg.evaluate(r"""(args)=>{
      const [ticker, lots, btnText] = args;
      const all=[...document.querySelectorAll('button,[role=button]')];
      const buy=all.find(e=>(e.innerText||'').trim()==='Купить');
      if(!buy) return {err:'виджет заявки не найден'};
      let w=buy, box=null;
      for(let i=0;i<12&&w.parentElement;i++){ w=w.parentElement; const t=w.innerText||'';
        if(/Рыночная/.test(t)&&/Лимитная/.test(t)&&w.getBoundingClientRect().width>=240){box=w;break;} }
      if(!box) return {err:'виджет заявки не найден'};
      const text=(box.innerText||'').toUpperCase();
      if(!new RegExp('(^|[^A-ZА-Я0-9])'+ticker.toUpperCase()+'([^A-ZА-Я0-9]|$)').test(text))
        return {err:'в виджете не тот инструмент (ожидался '+ticker+')'};
      let inps=[...box.querySelectorAll('input')].filter(i=>i.getBoundingClientRect().width>0 && /InlineInput-inp/i.test(i.className||''));
      inps.sort((a,b)=>a.getBoundingClientRect().x-b.getBoundingClientRect().x);
      const q=inps.length? inps[inps.length-1].value.replace(/\s/g,'') : '';
      if(String(parseInt(q,10))!==String(lots)) return {err:'количество в виджете '+(q||'пусто')+', а нужно '+lots};
      const b=[...box.querySelectorAll('button,[role=button]')].find(e=>(e.innerText||'').trim().startsWith(btnText));
      if(!b) return {err:'нет кнопки '+btnText};
      if(b.disabled||b.getAttribute('aria-disabled')==='true') return {err:'кнопка '+btnText+' неактивна'};
      const r=b.getBoundingClientRect();
      return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};
    }""", [ticker, lots, btn_text])
    if not info or info.get("err"):
        return {"ok": False, "err": (info or {}).get("err", "виджет не найден")}
    pg.mouse.click(info["x"], info["y"])
    pg.wait_for_timeout(900)
    # если терминал спросил подтверждение — подтверждаем (пользователь выбрал режим без подтверждения)
    confirm = pg.evaluate(r"""(btnText)=>{
      const dlg=[...document.querySelectorAll('[role=dialog],[role=alertdialog],[class*=Modal],[class*=modal]')]
        .filter(d=>d.getBoundingClientRect().width>0);
      for(const d of dlg){
        const b=[...d.querySelectorAll('button,[role=button]')].find(e=>/^(Подтвердить|Отправить|Выставить|Да)/.test((e.innerText||'').trim())
              || (e.innerText||'').trim().startsWith(btnText));
        if(b){const r=b.getBoundingClientRect();return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),t:(b.innerText||'').trim()};}
      }
      return null;
    }""", btn_text)
    if confirm:
        pg.mouse.click(confirm["x"], confirm["y"])
        pg.wait_for_timeout(900)
    # ответ терминала (уведомление/тост), если есть
    note = pg.evaluate(r"""(re)=>{
      const rx=new RegExp(re,'i');
      const els=[...document.querySelectorAll('[role=alert],[role=status],[class*=otification],[class*=oast],[class*=nackbar]')];
      for(const e of els){const t=(e.innerText||'').replace(/\s+/g,' ').trim(); if(t && t.length<300 && rx.test(t)) return t;}
      return '';
    }""", _ORDER_RESULT_RE)
    return {"ok": True, "confirmed": bool(confirm), "note": note}

def _order_words(plan):
    lots = plan["lots"]
    what = "Покупка" if plan.get("side") == "buy" else "Продажа"
    px = f"по {plan['price']:g}" if plan.get("type") == "limit" else "по рынку"
    return f"{what}: {plan.get('name') or plan.get('ticker')}, {lots} {_plural(lots)} {px}"

def _plural(n):
    n = abs(int(n)) % 100
    if 11 <= n <= 14: return "лотов"
    return {1: "лот", 2: "лота", 3: "лота", 4: "лота"}.get(n % 10, "лотов")

_APP_DB = r"C:\Users\Дамир\AppData\Roaming\com.priler.jarvis\app.db"
ORDER_JOURNAL = r"C:\jarvis-voice\order_journal.jsonl"

_exec_last = {"val": None, "ts": 0.0}


def _exec_settings():
    """Тумблер и лимиты исполнения — из app.db (Настройки → Т-Терминал), читаем каждый раз.
    app.db перезаписывают голосовой движок и окно Нокса: попав на середину записи, JSON не читается.
    Раньше это считалось «исполнение выключено» -> пропуск сделки. Теперь: 3 попытки, затем последнее
    удачно прочитанное значение (не старше 10 мин); ничего нет — безопасно «выключено»."""
    for _ in range(3):
        try:
            db = json.loads(open(_APP_DB, "rb").read().decode("utf-8-sig"))
            val = (bool(db.get("tterminal_exec_enabled", False)),
                   int(db.get("tterminal_exec_max_lots", 10) or 0),
                   int(db.get("tterminal_exec_max_rub", 50000) or 0))
            _exec_last.update(val=val, ts=time.time())
            return val
        except Exception as e:
            _exec_last["err"] = f"{type(e).__name__}: {e}"
            time.sleep(0.03)
    if _exec_last["val"] and time.time() - _exec_last["ts"] < 600:
        return _exec_last["val"]
    return (False, 10, 50000)


@app.get("/settings_sync")
def settings_sync():
    """Разовый перенос настроек из копии AppData пакетного Claude Desktop в НАСТОЯЩИЙ app.db.
    Процессы, запущенные из Claude Desktop (MSIX), пишут AppData в свою копию (LocalCache) — настройки,
    сделанные в таком окне Нокса, настоящий Нокс не видел. Ридер работает в обычном окружении и видит оба файла.
    Тумблер исполнения сделок и лимиты НЕ переносятся (их включает пользователь сам); mute/DND — тоже."""
    import glob, shutil
    pkgs = glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Packages", "Claude_*",
                                  "LocalCache", "Roaming", "com.priler.jarvis"))
    if not pkgs:
        return {"ok": False, "text": "Копии настроек Claude Desktop не найдено."}
    src_dir = pkgs[0]; dst_dir = os.path.dirname(_APP_DB)
    out = {"ok": True, "copied": [], "kept": []}
    try:
        src = json.loads(open(os.path.join(src_dir, "app.db"), "rb").read().decode("utf-8-sig"))
        dst = json.loads(open(_APP_DB, "rb").read().decode("utf-8-sig"))
        shutil.copy2(_APP_DB, _APP_DB + ".before_sync")
        keep = {"tterminal_exec_enabled", "tterminal_exec_max_lots", "tterminal_exec_max_rub", "mic_muted", "dnd"}
        for k, v in src.items():
            if k in keep:
                out["kept"].append(k); continue
            if dst.get(k) != v:
                dst[k] = v; out["copied"].append(k)
        open(_APP_DB, "w", encoding="utf-8").write(json.dumps(dst, ensure_ascii=False, indent=2))
    except Exception as e:
        return {"ok": False, "text": f"Не удалось перенести настройки: {e}"}
    # календарь: объединить записи (по id), ничего не теряя
    try:
        a_src = json.load(open(os.path.join(src_dir, "nox_agenda.json"), encoding="utf-8"))
        a_path = os.path.join(dst_dir, "nox_agenda.json")
        a_dst = json.load(open(a_path, encoding="utf-8")) if os.path.exists(a_path) else []
        have = {x.get("id") for x in a_dst}
        add = [x for x in a_src if x.get("id") not in have]
        if add:
            shutil.copy2(a_path, a_path + ".before_sync") if os.path.exists(a_path) else None
            json.dump(add + a_dst, open(a_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        out["agenda_added"] = len(add)
    except Exception as e:
        out["agenda_error"] = str(e)
    # голосовой движок перечитывает настройки сразу
    try:
        s = socket.create_connection((IPC_HOST, IPC_PORT), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall((f"GET / HTTP/1.1\r\nHost: {IPC_HOST}:{IPC_PORT}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        s.recv(4096)
        data = json.dumps({"action": "reload_settings"}).encode("utf-8")
        mask = os.urandom(4)
        s.sendall(bytes([0x81, 0x80 | len(data)]) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
        time.sleep(0.2); s.close()
        out["reloaded"] = True
    except Exception as e:
        out["reloaded"] = f"нет: {e}"
    return out


@app.get("/exec_state")
def exec_state():
    """Как ридер сейчас видит тумблер исполнения и лимиты (для проверки перед боевым запуском)."""
    on, lots, rub = _exec_settings()
    dbg = {}
    try:
        raw = open(_APP_DB, "rb").read()
        d = json.loads(raw.decode("utf-8-sig"))
        dbg = {"path": _APP_DB, "bytes": len(raw), "has_key": "tterminal_exec_enabled" in d,
               "raw_val": d.get("tterminal_exec_enabled"), "keys": sorted(d.keys()),
               "brain": d.get("brain_backend"), "model": d.get("openai_model"), "url": d.get("openai_url"),
               "ollama_model": d.get("ollama_model"), "has_key": bool((d.get("api_keys") or {}).get("openai")),
               "limits": [d.get("tterminal_exec_max_lots"), d.get("tterminal_exec_max_rub")],
               "presets": len(json.loads(d.get("ai_models") or "[]"))}
    except Exception as e:
        dbg = {"path": _APP_DB, "error": str(e)}
    return {"enabled": on, "max_lots": lots, "max_rub": rub, "last_error": _exec_last.get("err", ""), "debug": dbg}

def _journal(**rec):
    """Журнал заявок: что сказали, что распознали, что ушло в терминал. Одна строка JSON на заявку."""
    rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), **rec}
    try:
        with open(ORDER_JOURNAL, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass

def _limit_problem(plan, max_lots, max_rub):
    """None — заявка в лимитах; иначе текст причины отказа."""
    lots = int(plan.get("lots") or 0)
    if max_lots and lots > max_lots:
        return f"{lots} {_plural(lots)} больше лимита {max_lots}"
    if max_rub:
        px = float(plan.get("price") or 0) or float(plan.get("last") or 0)
        if px <= 0:
            return "не смог оценить сумму заявки (нет цены)"
        total = lots * int(plan.get("lot") or 1) * px
        if total > max_rub:
            return f"сумма около {total:,.0f} ₽ больше лимита {max_rub:,} ₽".replace(",", " ")
    return None

@app.get("/fill_order")
def fill_order(q: str = "", side: str = "buy", qty: int = 1, price: float = 0.0, close: int = 0,
               exec: int = 0, said: str = ""):
    if not _state["ok"]:
        return {"text": _state["err"] or "Терминал ещё не подключён, сэр."}
    try:
        plan = _plan_from_invest(q, side, qty, price, close)
    except Exception as e:
        return {"text": f"Не удалось рассчитать заявку, сэр. {type(e).__name__}"}
    if not plan.get("ok"):
        if exec:
            _journal(said=said, q=q, exec=True, sent=False, reason=plan.get("text", "план не построен"))
        return {"text": plan.get("text", "Не смог подготовить заявку, сэр.")}
    ticker = plan["ticker"] or q
    # на вкладке наблюдателя виджеты — заранее заряженные заявки веток: голосом их не перезаписываем
    if nox_widgets:
        act, _e = _submit(lambda pg: nox_widgets.active_tab(pg), timeout=10)
        if act and act in _watcher_tabs():
            return {"text": f"Открыта вкладка наблюдателя «{act}» — её заявки голосом не трогаю, сэр. "
                            "Переключитесь на другую вкладку терминала."}
    brief = {k: plan.get(k) for k in ("ticker", "side", "lots", "lot", "price", "type", "last")}
    brief["close"] = bool(close)

    # решаем ДО заполнения: можно ли жать кнопку (тумблер + лимиты)
    refuse = None
    if exec:
        enabled, max_lots, max_rub = _exec_settings()
        if not enabled:
            refuse = "исполнение голосом выключено в Настройках"
        else:
            refuse = _limit_problem(plan, max_lots, max_rub)

    res, err = _submit(lambda pg: _fill_widget(pg, ticker, plan["lots"], plan["price"], plan["type"]), timeout=40)
    if err or (isinstance(res, str)):
        if exec:
            _journal(said=said, plan=brief, exec=True, sent=False, reason=f"не заполнил: {err or res}")
        return {"text": f"Открыл заявку, но заполнить не удалось ({err or res}), сэр. Проверьте виджет."}
    if not exec:
        return {"text": plan["text"], "plan": plan}
    if refuse:
        _journal(said=said, plan=brief, exec=True, sent=False, reason=refuse)
        return {"text": f"Заявку заполнил, но НЕ отправил: {refuse}, сэр. Нажмите сами, если всё верно.",
                "plan": plan, "sent": False}

    # ── исполнение: жмём Купить/Продать ──
    pres, perr = _submit(lambda pg: _press_order_button(pg, ticker, plan["lots"], plan.get("side", side)), timeout=20)
    if perr or not pres:
        _journal(said=said, plan=brief, exec=True, sent=None, reason=f"сбой нажатия: {perr or 'нет ответа'}")
        return {"text": f"Заявку заполнил, но нажать кнопку не смог ({perr or 'нет ответа'}), сэр. "
                        "Проверьте виджет — возможно, нажимать надо вручную.", "plan": plan, "sent": False}
    if not pres.get("ok"):
        _journal(said=said, plan=brief, exec=True, sent=False, reason=pres.get("err"))
        return {"text": f"Заявку НЕ отправил: {pres.get('err')}, сэр. Проверьте виджет.", "plan": plan, "sent": False}
    _journal(said=said, plan=brief, exec=True, sent=True, confirmed=pres.get("confirmed"), note=pres.get("note", ""))
    tail = f" Терминал: {pres['note']}." if pres.get("note") else ""
    return {"text": f"Котлета заряжена. {_order_words(plan)} — заявка отправлена.{tail}",
            "plan": plan, "sent": True, "note": pres.get("note", "")}

# ------------------------------------------------------------------ СЦЕНАРИИ (авто-клик стакана)
def _scn_public(cfg):
    # мозг сценариев = общий выбор со страницы «Мозг» (app.db): облако по ключу или Ollama
    return {"scenarios": cfg.get("scenarios", []), "brain": nox_scenarios.brain_choice(),
            "has_openai": bool(nox_scenarios._cloud()[0])}

@app.get("/scn_list")
def scn_list():
    if not nox_scenarios:
        return {"scenarios": [], "brain": "ollama", "has_openai": False}
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

import subprocess as _subp, sys as _sys
def _spawn_marker(points):
    """Показать красные «плюсы» в точках (оверлей nox_marker.py), клико-прозрачно, ~3.5с."""
    pts = [str(int(v)) for xy in points for v in xy]
    if not pts:
        return False
    try:
        _subp.Popen([_sys.executable, os.path.join(os.path.dirname(__file__), "nox_marker.py"), *pts],
                    creationflags=0x08000000, close_fds=True)
        return True
    except Exception:
        return False

@app.get("/scn_show")
def scn_show(x: int = 0, y: int = 0):
    """Показать точку клика красным «плюсом» на экране (в т.ч. на другом мониторе)."""
    return {"ok": _spawn_marker([(x, y)])}

@app.get("/scn_show_all")
def scn_show_all(id: str = ""):
    """Показать ВСЕ точки сценария сразу (кроме веток «без действий»)."""
    if not nox_scenarios:
        return {"ok": False}
    cfg = nox_scenarios.load()
    sc = next((s for s in cfg.get("scenarios", []) if s.get("id") == id), None)
    if not sc:
        return {"ok": False}
    pts = [(b["x"], b["y"]) for b in sc.get("branches", [])
           if not b.get("noop") and b.get("x") is not None]
    return {"ok": _spawn_marker(pts), "count": len(pts)}

@app.get("/scn_inject")
def scn_inject(text: str = "", tickers: str = ""):
    """ПРОКЛАДКА для теста: подать виртуальную новость в движок как будто из ленты ТТ,
    замерить время полной обработки (парсинг -> выбор ветки -> клик). Пишет в nox_scenarios.log."""
    if not nox_scenarios:
        return {"ok": False, "text": "Сценарии недоступны."}
    item = {"text": text, "body": "", "tickers": [t.strip() for t in tickers.split(",") if t.strip()]}
    t0 = time.time()
    nox_scenarios.on_news([item])
    ms = (time.time() - t0) * 1000.0
    nox_scenarios._log(f"INJECT «{text[:60]}» -> полная обработка {ms:.0f}мс")
    return {"ok": True, "ms": round(ms), "text": f"Обработано за {ms:.0f} мс."}

@app.get("/scn_test")
def scn_test(id: str = "", value: str = ""):
    """Тест сценария: подставить значение, выбрать ветку и кликнуть (проверка без ожидания новости)."""
    if not nox_scenarios:
        return {"ok": False, "text": "Сценарии недоступны."}
    return nox_scenarios.test_fire(id, value)

@app.get("/scn_schedule")
def scn_schedule(id: str = "", dt: str = "", prewarm: int = 3):
    """Назначить сценарию время ожидаемого события (МСК). За prewarm минут до —
    прогрев Ollama + анонс; срабатывание — по факту новости (США: BLS/BEA, РФ: лента)."""
    if not nox_scenarios:
        return {"ok": False, "text": "Сценарии недоступны."}
    return nox_scenarios.set_schedule(id, dt, prewarm)

@app.get("/scn_brain")
def scn_brain(brain: str = "", claude_key: str = "__keep__", openai_key: str = "__keep__"):
    """Совместимость со старым GUI: мозг сценариев теперь берётся из общего выбора (app.db), тут ничего не храним."""
    if not nox_scenarios:
        return {"brain": "ollama"}
    return _scn_public(nox_scenarios.load())

@app.get("/brain_models")
def brain_models():
    """Для страницы «Мозг»: установленные модели Ollama и каталог облака (OpenAI-совместимый /models)."""
    import urllib.request as _u
    out = {"ollama": [], "cloud": [], "ollama_ok": False, "cloud_ok": False}
    try:
        tags = json.loads(_u.urlopen("http://127.0.0.1:11434/api/tags", timeout=4).read().decode("utf-8"))
        out["ollama"] = sorted(m.get("name", "") for m in tags.get("models", []) if m.get("name"))
        out["ollama_ok"] = True
    except Exception:
        pass
    if nox_scenarios:
        key, url, _m = nox_scenarios._cloud()
        if key:
            try:
                req = _u.Request(url.rstrip("/") + "/models", headers={"Authorization": "Bearer " + key})
                data = json.loads(_u.urlopen(req, timeout=10).read().decode("utf-8"))
                out["cloud"] = sorted(m.get("id", "") for m in data.get("data", []) if m.get("id"))
                out["cloud_ok"] = True
            except Exception:
                pass
    return out

# ------------------------------------------------------------------ наблюдатели («мультимозг»)
@app.get("/watch_list")
def watch_list():
    if not nox_watchers:
        return {"watchers": []}
    return nox_watchers.list_public()

@app.get("/watch_add")
def watch_add(name: str = "", keywords: str = "", source: str = "all",
              brain: str = "", scenario_id: str = "", until: str = "", once: int = 1):
    if not nox_watchers:
        return {"ok": False, "text": "Наблюдатели недоступны."}
    kws = [k.strip() for k in re.split(r"[,; ]+", keywords) if k.strip()]
    if not kws:
        return {"ok": False, "text": "Укажите ключевые слова, сэр."}
    w = nox_watchers.add(name or " ".join(kws), kws, source=source, brain=brain,
                         scenario_id=scenario_id, until=until, once=bool(once))
    return {"ok": True, "id": w["id"], "text": f"Наблюдатель «{w['name']}» создан."}

@app.get("/watch_voice")
def watch_voice(text: str = ""):
    if not nox_watchers:
        return {"ok": False, "text": "Наблюдатели недоступны."}
    return nox_watchers.add_from_voice(text)

@app.get("/watch_parse")
def watch_parse(text: str = ""):
    """Разобрать строку для формы (без сохранения): название, что ловить, до скольки, ветки с заявками."""
    if not nox_watchers or not text.strip():
        return {"ok": False, "text": "Опишите, за чем следить."}
    p = nox_watchers.parse_full(text)
    return {"ok": bool(p.get("keywords")), **p}

# ---- наблюдатели с ветками: вкладка терминала + виджеты «Заявка» (nox_widgets) ----
SPEED_LOG = r"C:\jarvis-voice\speed_log.jsonl"


def _speed_record(w, news_text, meta, res, t_decide):
    """Замер «новость -> нажатие» по шагам (мс): появилась на странице -> ридер увидел -> решение -> клик."""
    try:
        seen = (meta or {}).get("seen")
        det = (meta or {}).get("_detect")
        clicks = [r.get("t_click") for r in res or [] if r.get("t_click")]
        if not clicks:
            return ""
        t_click = min(clicks)
        rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "watcher": w.get("name"), "news": (news_text or "")[:160],
               "detect_ms": round((det - seen / 1000) * 1000) if (seen and det) else None,
               "decide_ms": round((t_decide - det) * 1000) if det else None,
               "click_ms": round((t_click - t_decide) * 1000),
               "total_ms": round((t_click - seen / 1000) * 1000) if seen else None}
        with open(SPEED_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return f" Реакция {rec['total_ms'] / 1000:.1f} с от появления новости." if rec["total_ms"] is not None else ""
    except Exception:
        return ""


def _cscalp_exec_text(box, cs_orders, dry, enabled):
    """Отчёт по заявкам ветки в CScalp."""
    act = lambda side, t: f"{'купить' if side == 'buy' else 'продать'} {t}"
    planned = ", ".join(act(o.get("side"), o["ticker"]) for o in cs_orders)
    m = box.get("res")
    if box.get("err") or not m:
        return f"CScalp: {box.get('err') or 'нет ответа'} — ничего не нажато"
    if dry or not enabled:
        if not m.get("ok"):
            return f"CScalp: {m.get('error')}"
        return (f"CScalp, нажал бы: {planned}" if dry
                else f"CScalp: исполнение выключено в Настройках — нажмите сами: {planned}")
    done = [r for r in m.get("results") or [] if r.get("ok")]
    did = lambda r: f"{'купил' if r.get('direction') == 'Buy' else 'продал'} {r['ticker']}"
    txt = "CScalp: " + (", ".join(did(r) for r in done) or "ничего не исполнено")
    if done:
        txt += f" (приняты за {max(r.get('hotkey_ms') or 0 for r in done)} мс)"
    if not m.get("ok"):
        txt += ". ОШИБКА: " + (m.get("error") or "не всё исполнено")
    return txt


def _watch_exec(w, branch, news_text, dry=False, meta=None):
    """EXECUTOR для nox_watchers: нажать кнопки ветки на вкладке наблюдателя.
    Тумблер «исполнение» и лимиты — те же, что у голосовых заявок (Настройки → Т-Терминал).
    Заявки venue=cscalp идут в CScalp горячими клавишами (nox_cscalp) — параллельно с Т-Терминалом,
    под тем же тумблером исполнения; объём — тот, что стоит в стакане CScalp."""
    t_decide = time.time()
    all_orders = branch.get("orders") or []
    cs_orders = [o for o in all_orders if o.get("venue") == "cscalp"]
    orders = [o for o in all_orders if o.get("venue") != "cscalp"]
    enabled, max_lots, max_rub = _exec_settings()
    really = enabled and not dry
    cs_box, cs_thread = {}, None
    if cs_orders:
        if not _feat("cscalp"):
            cs_box["err"] = "CScalp выключен во вкладке «Фичи»"
        elif not nox_cscalp:
            cs_box["err"] = "модуль CScalp не загружен"
        else:
            pairs = [(o["ticker"], "buy_market" if o.get("side") == "buy" else "sell_market") for o in cs_orders]
            def _cs():
                r, e = nox_cscalp.submit(lambda: nox_cscalp.execute_orders(pairs, go=really), timeout=25)
                cs_box.update(res=r, err=e)
            cs_thread = threading.Thread(target=_cs, daemon=True)
            cs_thread.start()
    if orders:
        res, err = _submit(lambda pg: nox_widgets.press_orders(pg, w.get("tab"), orders, max_lots, max_rub,
                                                               dry=not really), timeout=30)
    else:
        res, err = [], None
    cs_txt, cs_res = "", []
    if cs_thread:
        cs_thread.join(30)
        cs_txt = _cscalp_exec_text(cs_box, cs_orders, dry, enabled)
        cs_res = [r for r in ((cs_box.get("res") or {}).get("results") or []) if r.get("sent")]
        if not dry:
            m = cs_box.get("res") or {}
            for o in cs_orders:
                r = next((x for x in m.get("results") or [] if x.get("ticker") == nox_cscalp.norm(o["ticker"])), {})
                _journal(said=f"наблюдатель {w.get('name')}: {news_text[:120]}", watcher=w.get("id"),
                         plan={"ticker": o["ticker"], "side": o.get("side"), "venue": "cscalp"},
                         exec=True, sent=bool(r.get("ok") and really),
                         reason=(r.get("error") or m.get("error") or cs_box.get("err") or "")
                         if really else "исполнение выключено",
                         note=" / ".join(x["text"] for x in r.get("orders") or [] if "Amount=" in x["text"]))
    if not orders:
        txt = cs_txt or "ничего"
        if really and not dry:
            sp = _speed_record(w, news_text, meta, cs_res, t_decide).rstrip(".")
            if sp:
                txt += "." + sp
        return {"text": txt + ".", "results": [], "cscalp": cs_box.get("res")}
    if err or res is None:
        txt = f"терминал не ответил ({err or 'нет ответа'}), ничего не нажато"
        if not dry:
            _journal(said=f"наблюдатель {w.get('name')}: {news_text[:120]}", exec=True, sent=False, reason=txt)
        return {"text": txt + (f". {cs_txt}" if cs_txt else "")}
    sent = [r for r in res if r.get("ok")]
    failed = [r for r in res if not r.get("ok")]
    act = lambda r: f"{'купить' if r['order'].get('side') == 'buy' else 'продать'} {r['order'].get('ticker')}"
    if dry:
        txt = "Нажал бы: " + (", ".join(f"{act(r)} ({r.get('widget')})" for r in sent) or "ничего")
    elif not enabled:
        txt = ("Исполнение выключено в Настройках — нажмите сами: "
               + ", ".join(f"{act(r)} ({r.get('widget')})" for r in sent))
    else:
        txt = "Отправлено: " + (", ".join(act(r) for r in sent) or "ничего")
    if failed:
        txt += ". Не ушло: " + "; ".join(f"{act(r)} — {r.get('err')}" for r in failed)
    notes = next((r.get("notes") for r in res if r.get("notes")), None)
    if notes and really:
        txt += ". Терминал: " + " / ".join(notes[:2])
    if cs_txt:
        txt += ". " + cs_txt
    if really and not dry:
        sp = _speed_record(w, news_text, meta, list(res) + cs_res, t_decide).rstrip(".")
        if sp:
            txt += "." + sp
    if not dry:
        for r in res:
            _journal(said=f"наблюдатель {w.get('name')}: {news_text[:120]}", watcher=w.get("id"),
                     plan={"ticker": r["order"].get("ticker"), "n": r["order"].get("n"), "side": r["order"].get("side"),
                           "widget": r.get("widget")},
                     exec=True, sent=bool(r.get("ok") and really), reason=r.get("err") or ("" if enabled else "исполнение выключено"),
                     note=" / ".join(r.get("notes") or []))
    return {"text": txt + ".", "results": res, "cscalp": cs_box.get("res")}

def _watch_layout(w):
    res, err = _submit(lambda pg: nox_widgets.layout(pg, w), timeout=180)
    return res or {"ok": False, "text": f"Раскладка не удалась: {err}"}

if nox_watchers and nox_widgets:
    nox_watchers.EXECUTOR = _watch_exec
    nox_watchers.LAYOUT = _watch_layout

def _watcher_tabs():
    """вкладка -> список наблюдателей с ветками на ней (на одной вкладке их может быть несколько)."""
    out = {}
    try:
        for w in nox_watchers.load()["watchers"]:
            if w.get("tab") and w.get("branches"):
                out.setdefault(w["tab"], []).append(w)
    except Exception:
        pass
    return out

def _overlay_loop():
    """Подписи веток на вкладке наблюдателя: перерисовка раз в 3с (виджеты двигают, страницу обновляют)."""
    _ready.wait(120)
    while True:
        try:
            if _state["ok"] and nox_widgets and nox_watchers:
                tabs = _watcher_tabs()
                def _draw(pg):
                    w = tabs.get(nox_widgets.active_tab(pg))
                    if w:
                        return nox_widgets.draw_overlay(pg, w)
                    nox_widgets.clear_overlay(pg); return 0
                _submit(_draw, timeout=10)
        except Exception:
            pass
        time.sleep(3)

def _cscalp_arm_loop():
    """Стаканы CScalp из включённых наблюдателей — держать сверенными (nox_cscalp сверяет их в простое)."""
    while True:
        try:
            if nox_cscalp and nox_watchers and not _feat("cscalp"):
                nox_cscalp.arm(set())        # «Фичи»: CScalp выключен — не сверять стаканы
            elif nox_cscalp and nox_watchers:
                nox_cscalp.arm({o["ticker"] for w in nox_watchers.load()["watchers"] if w.get("enabled", True)
                                for b in w.get("branches") or [] for o in b.get("orders") or []
                                if o.get("venue") == "cscalp"})
        except Exception:
            pass
        time.sleep(5)

@app.get("/cscalp_layout")
def cscalp_layout():
    """Стаканы CScalp для редактора веток: видимые, скрытые вкладки, состояние взведённых."""
    if not nox_cscalp:
        return {"ok": False, "error": "модуль CScalp не загружен", "doms": [], "hidden": []}
    r, e = nox_cscalp.submit(nox_cscalp.visible, timeout=30)
    return r or {"ok": False, "error": e or "CScalp не ответил", "doms": [], "hidden": []}

@app.get("/watch_save")
def watch_save(json_data: str = ""):
    if not nox_watchers:
        return {"ok": False, "text": "Наблюдатели недоступны."}
    try:
        data = json.loads(json_data)
    except Exception:
        return {"ok": False, "text": "Некорректные данные наблюдателя."}
    return nox_watchers.upsert(data)

@app.get("/watch_scan")
def watch_scan(tab: str = ""):
    """Виджеты «Заявка» на вкладке (открывает её): для привязки веток к уже заполненным заявкам."""
    if not (_state["ok"] and nox_widgets):
        return {"ok": False, "text": _state.get("err") or "Терминал не подключён.", "widgets": []}
    def _scan(pg):
        if tab and not nox_widgets.ensure_tab(pg, tab):
            return {"ok": False, "text": f"Вкладки «{tab}» нет в терминале.", "widgets": []}
        ws = nox_widgets.widgets(pg)
        return {"ok": True, "tab": nox_widgets.active_tab(pg),
                "widgets": [{**{k: x[k] for k in ("ticker", "n", "type", "price", "qty", "cost")},
                             "label": nox_widgets.describe(x)} for x in ws]}
    res, err = _submit(_scan, timeout=20)
    return res or {"ok": False, "text": f"Терминал не ответил: {err}", "widgets": []}

@app.get("/watch_layout")
def watch_layout(id: str = ""):
    w = nox_watchers.get(id) if nox_watchers else None
    if not w:
        return {"ok": False, "text": "Наблюдатель не найден."}
    if not _state["ok"]:
        return {"ok": False, "text": _state.get("err") or "Терминал не подключён."}
    return _watch_layout(w)

@app.get("/watch_show")
def watch_show(id: str = ""):
    """Открыть вкладку наблюдателя и подсветить, какие кнопки нажмёт каждая ветка."""
    w = nox_watchers.get(id) if nox_watchers else None
    if not w or not w.get("tab"):
        return {"ok": False, "text": "У наблюдателя нет вкладки терминала."}
    def _show(pg):
        if not nox_widgets.ensure_tab(pg, w["tab"]):
            return {"ok": False, "text": f"Вкладки «{w['tab']}» нет — нажмите «Разложить в терминале»."}
        same_tab = _watcher_tabs().get(w["tab"]) or [w]      # все наблюдатели этой вкладки
        n = nox_widgets.draw_overlay(pg, same_tab)
        need = sum(len(nox_widgets.overlay_marks(x)) for x in same_tab)
        return {"ok": n == need, "text": f"Подсвечено кнопок: {n} из {need}."
                + ("" if n == need else " Каких-то виджетов нет на вкладке — проверьте тикеры и №.")}
    res, err = _submit(_show, timeout=20)
    return res or {"ok": False, "text": f"Терминал не ответил: {err}"}

@app.get("/watch_dry")
def watch_dry(id: str = "", value: str = ""):
    """Проверка без нажатий: какая ветка сработает при значении и что именно нажмётся."""
    if not nox_watchers:
        return {"ok": False, "text": "Наблюдатели недоступны."}
    return nox_watchers.dry_run(id, value)

@app.get("/watch_del")
def watch_del(id: str = ""):
    if not nox_watchers:
        return {"ok": False}
    return {"ok": nox_watchers.remove(id)}

@app.get("/watch_toggle")
def watch_toggle(id: str = ""):
    if not nox_watchers:
        return {"ok": False}
    return {"ok": True, "enabled": nox_watchers.toggle(id)}

# ------------------------------------------------------------------ выбор голоса (движок + диктор)
_ENG_FILE = r"C:\jarvis-voice\tts_engine.txt"
_SPK_FILE = r"C:\jarvis-voice\tts_silero_speaker.txt"
_SILERO_SPK = ["aidar", "eugene", "baya", "kseniya", "xenia"]

def _cur_engine():
    try: return open(_ENG_FILE, encoding="utf-8").read().strip().lower()
    except Exception: return "silero"

_XTTS_FLAG = r"C:\jarvis-voice\xtts_ready.flag"   # создаётся после успешного селф-теста XTTS
@app.get("/voice_list")
def voice_list():
    opts = [{"engine": "silero", "speaker": s, "label": f"Silero — {s}"} for s in _SILERO_SPK]
    if os.path.exists(_XTTS_FLAG):   # XTTS показываем только когда он реально работает
        opts.append({"engine": "xtts", "speaker": "", "label": "XTTS — голос Джарвиса"})
    return {"options": opts}

@app.get("/voice_get")
def voice_get():
    spk = ""
    try: spk = open(_SPK_FILE, encoding="utf-8").read().strip()
    except Exception: pass
    return {"engine": _cur_engine() or "silero", "speaker": spk or "aidar"}

def _apply_voice(engine, speaker):
    """Применить движок/диктор. Возвращает (engine, speaker, switched_engine: bool)."""
    import urllib.request as _u, subprocess as _sp
    engine = (engine or "silero").lower().strip()
    prev = _cur_engine()
    try: open(_ENG_FILE, "w", encoding="utf-8").write(engine)
    except Exception: pass
    if engine == "silero" and speaker in _SILERO_SPK:
        try: open(_SPK_FILE, "w", encoding="utf-8").write(speaker)
        except Exception: pass
    # смена только диктора на уже работающем Silero — вживую, без перезапуска
    if engine == "silero" and prev == "silero" and speaker in _SILERO_SPK:
        try:
            _u.urlopen(f"http://127.0.0.1:8123/set_speaker?name={speaker}", timeout=5).read()
            return engine, speaker, False
        except Exception:
            pass
    # смена движка — убить голос на 8123, watchdog поднимет нужный интерпретатор/сервер
    try:
        out = _sp.run(['netstat', '-ano', '-p', 'TCP'], capture_output=True, text=True).stdout
        for ln in out.splitlines():
            if ':8123' in ln and 'LISTENING' in ln:
                _sp.run(['taskkill', '/PID', ln.split()[-1], '/F'], capture_output=True)
    except Exception:
        pass
    return engine, speaker, True

@app.get("/voice_set")
def voice_set(engine: str = "silero", speaker: str = ""):
    eng, spk, switched = _apply_voice(engine, speaker)
    lbl = eng + ((" " + spk) if spk else "")
    txt = (f"Переключаю голос на {lbl}, пара секунд, сэр." if switched
           else f"Голос переключён на {spk}, сэр.")
    return {"ok": True, "engine": eng, "speaker": spk, "text": txt}

_VOICE_SAMPLE = ("Приветствую, сэр. Это тестовая фраза выбранного голоса. "
                 "СБЕР вырос на 2,3 процента, Минфин купит валюты на 120 миллиардов рублей.")
@app.get("/voice_test")
def voice_test(engine: str = "silero", speaker: str = "", text: str = ""):
    """Применить голос и проиграть пример (после готовности движка)."""
    eng, spk, switched = _apply_voice(engine, speaker)
    sample = (text.strip() or _VOICE_SAMPLE)
    def _speak():
        import urllib.request as _u, json as _j
        # дождаться готовности голоса на 8123: порт открывается сразу, а модель
        # (особенно XTTS) грузится ещё ~30с — ждём /health status=ok (до ~2 мин)
        if switched:
            time.sleep(2.0)   # старый сервер успевает умереть, иначе ответит он
        for _ in range(120 if switched else 3):
            try:
                st = _j.loads(_u.urlopen("http://127.0.0.1:8123/health", timeout=2).read()).get("status", "ok")
                if st == "ok":
                    break
            except Exception:
                pass
            time.sleep(1)
        _ipc_announce(sample)
    threading.Thread(target=_speak, daemon=True).start()
    lbl = eng + ((" " + spk) if spk else "")
    return {"ok": True, "engine": eng, "speaker": spk,
            "text": (f"Переключаю на {lbl} и проигрываю пример…" if switched else f"Проигрываю пример голоса {spk}…")}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8126, log_level="warning")
