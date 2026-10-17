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
import threading, queue, time, json, re, os, socket, base64, struct, functools, collections
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

def _is_dup(text, smart=True):
    """Похожая новость уже озвучивалась сегодня (не меньше 2 ч)? Словами, а если не похоже — смысловой судья
    (Qwen 7B, тумблер «Фичи» → «Смысловой фильтр повторов»). smart=False — только словами (корпсобытия: у них
    своя память _corp_repeat, и там важна каждая доля секунды)."""
    try:
        if not nox_dedup:
            return False
        dup = bool(nox_dedup.is_dup(text, smart=smart and _feat("smart_dedup")))
        j = nox_dedup.last_judge
        if dup and j.get("verdict") and nox_scenarios:
            nox_scenarios._log(f"ПОВТОР [смысл, Qwen {j.get('ms')} мс]: не читаю — {text[:110]} ≈ {j.get('prev', '')[:110]}")
        return dup
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
    const seen = window.__noxSeen; let fresh = false;
    for (const inner of document.querySelectorAll('.news_row .news_title_inner')) {
      const k = keyOf(inner); if (k && !seen.has(k)) { seen.set(k, Date.now()); fresh = true; }
    }
    // новая строка — сразу будим монитор ридера (Runtime.addBinding), не ждём следующего опроса
    if (fresh) { try { window.noxNewsPing && window.noxNewsPing('tt'); } catch (e) {} }
    if (seen.size > 3000) { const ks = [...seen.keys()].slice(0, 1500); ks.forEach(k => seen.delete(k)); }
  };
  if (!window.__noxSeen) window.__noxSeen = new Map();
  const first = document.querySelector('.news_row');
  const box = first && first.parentElement;
  // версия наблюдателей: новый код ридера пересоздаёт их (иначе в странице жил старый stamp без сигнала —
  // лента TT не перерисовывалась и noxNewsPing не звался, 16.10)
  const OBS_VER = 2;
  if (box && (window.__noxObsTarget !== box || window.__noxObsVer !== OBS_VER)) {   // лента перерисована/вкладка сменилась
    if (window.__noxObs) window.__noxObs.disconnect();
    window.__noxObs = new MutationObserver(stamp);
    window.__noxObs.observe(box, {childList: true, subtree: true});
    window.__noxObsTarget = box;
    window.__noxObsVer = OBS_VER;
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
    // текст тела (для распознавания события/цены): раскрытия (ЦРКИ / e-disclosure) — до 4000 символов
    // (в решениях СД о дивидендах отсечка дальше 700-го символа), остальные — 700
    const longBody = /црки|disclosure/i.test(source);
    const body = ((r.querySelector('.news_preview_inner')||r.querySelector('.news_content')||{}).innerText||'').replace(/\s+/g,' ').trim().slice(0, longBody ? 4000 : 700);
    out.push({ text: text.slice(0,400), tickers, source, hl, hashtags, body, feed: 'TT',
               seen: window.__noxSeen.get(keyOf(inner)) || Date.now() });
  }
  // --- вторая лента: Stocksi Ultimate (.stocksiWidget). Новые сверху, у каждой id в классе stocksiNews-N;
  // дубли других источников Stocksi сам прячет под корень (.stocksiDupList) — их не берём
  const sbox = document.querySelector('.stocksiWidget .newsListContainer');
  if (!window.__noxSeenS) window.__noxSeenS = new Map();
  const sid = (p) => ((String(p.className).match(/stocksiNews-(\d+)/) || [])[1] || '');
  const sstamp = () => {
    const seen = window.__noxSeenS; let fresh = false;
    for (const p of document.querySelectorAll('.stocksiWidget .newsListContainer .stocksiNewsPreview')) {
      const k = sid(p); if (k && !seen.has(k)) { seen.set(k, Date.now()); fresh = true; }
    }
    if (fresh) { try { window.noxNewsPing && window.noxNewsPing('st'); } catch (e) {} }
    if (seen.size > 5000) { const ks = [...seen.keys()].slice(0, 2500); ks.forEach(k => seen.delete(k)); }
  };
  if (sbox && (window.__noxObsTargetS !== sbox || window.__noxObsVerS !== OBS_VER)) {
    if (window.__noxObsS) window.__noxObsS.disconnect();
    window.__noxObsS = new MutationObserver(sstamp);
    window.__noxObsS.observe(sbox, {childList: true, subtree: true});
    window.__noxObsTargetS = sbox;
    window.__noxObsVerS = OBS_VER;
  }
  if (sbox) {
    sstamp();
    // эмодзи в заголовках («💼Решения совета директоров») — вон: голосу и ключу повтора они не нужны
    const clean = (x) => (x || '').replace(/[\p{Extended_Pictographic}\uFE0F\u200D]/gu, '').replace(/\s+/g, ' ').trim();
    // Stocksi рисует лениво (content-visibility: auto): у невидимых строк innerText пустой — тогда textContent
    const tx = (el) => el ? (el.innerText || el.textContent || '') : '';
    let n = 0;
    for (const p of sbox.querySelectorAll('.stocksiNewsPreview')) {
      if (p.closest('.stocksiDupList')) continue;
      const k = sid(p);
      const title = clean(tx(p.querySelector('.stocksiNewsTitle')));
      const short = clean([tx(p.querySelector('.stocksiNewsShort')), tx(p.querySelector('.stocksiNewsFullText'))]
                          .filter(Boolean).join(' '));
      // заголовок-обрубок «Председатель Европейского совета Антониу Коста:» — суть в тексте под ним: склеить
      const cut = title && /:$/.test(title) && short;
      const text = cut ? (title + ' ' + short).slice(0, 400) : (title || short.slice(0, 400));
      if (!text) continue;
      const source = clean(tx(p.querySelector('.NewsSource')));
      const tickers = [...p.querySelectorAll('.header .ticker[data-link-widget]')]
        .map(t => (t.getAttribute('data-link-widget') || '').toUpperCase()).filter(Boolean);
      const hashtags = [...p.querySelectorAll('.header .hashtag')].map(h => h.textContent.trim()).filter(Boolean);
      const hl = [...p.querySelectorAll('.stocksiNewsTitle b')].map(e => e.textContent.trim()).filter(Boolean);
      const longBody = /црки|disclosure/i.test(source);
      out.push({ text: text.slice(0, 400), tickers, source, hl, hashtags,
                 body: (title ? short : '').slice(0, longBody ? 4000 : 700), feed: 'Stocksi', sid: k,
                 time: clean(tx(p.querySelector('.time-section'))),
                 seen: window.__noxSeenS.get(k) || Date.now() });
      if (++n >= 40) break;
    }
  }
  return out;   // в каждой ленте newest first (порядок между лентами — по seen, в ридере)
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


_AUTO_TABS = ("nsddata.ru",)     # служебные вкладки Нокса (опрос НРД) — их можно закрыть, служба откроет заново


def _unstick_tabs():
    """Зависшая страница (17.10: nsddata.ru перестала отвечать) не даёт Playwright подключиться к браузеру:
    connect_over_cdp ждёт её и падает по таймауту 15 с — ридер без терминала, новости не читаются.
    Проверяем служебные вкладки за 3 с и закрываем зависшую (вкладки пользователя не трогаем)."""
    try:
        import urllib.request as _ur
        from websockets.sync.client import connect as _wsc
        lst = json.loads(_ur.urlopen(CDP_URL + "/json/list", timeout=5).read().decode("utf-8", "replace"))
        for t in lst:
            if t.get("type") != "page" or not any(a in (t.get("url") or "") for a in _AUTO_TABS):
                continue
            try:
                with _wsc(t["webSocketDebuggerUrl"], open_timeout=3, max_size=None) as ws:
                    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": "1"}}))
                    ws.recv(timeout=3)
            except Exception:
                _ur.urlopen(f"{CDP_URL}/json/close/{t['id']}", timeout=5)
                if nox_scenarios:
                    nox_scenarios._log(f"ТЕРМИНАЛ: закрыл зависшую служебную вкладку {t.get('url', '')[:60]} — мешала подключению")
    except Exception:
        pass


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
            if "connect_over_cdp" in str(e) and "Timeout" in str(e):
                _unstick_tabs()
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
        threading.Thread(target=_read_worker, daemon=True).start()      # чтение ленты + умный отбор (проверка)
        threading.Thread(target=_focus_loop, daemon=True).start()       # режим тишины перед новостью по времени
        threading.Thread(target=_official_loop, daemon=True).start()    # решения ЦБ/ФРС прямо с cbr.ru / federalreserve.gov
        threading.Thread(target=_terminal_health_loop, daemon=True).start()
        threading.Thread(target=_overlay_loop, daemon=True).start()   # подписи веток наблюдателей
        threading.Thread(target=_cscalp_arm_loop, daemon=True).start()  # сверка стаканов CScalp у наблюдателей
        threading.Thread(target=_nsd_loop, daemon=True).start()       # НРД: неисполнение обязательств по облигациям
        threading.Thread(target=_ping_loop, daemon=True).start()      # мгновенный сигнал «новая строка в ленте»
        try:
            import nox_tinvest
            nox_tinvest.start_warm()      # соединение для заявок через API всегда тёплое (GetInfo раз в 20 с)
        except Exception:
            pass
        threading.Thread(target=_copy_loop, daemon=True).start()      # копирование сделок трейдера (ProfitKing)
        threading.Thread(target=_dialog_loop, daemon=True).start()    # история разговора (15 мин) из IPC Нокса
        if nox_dedup and _feat("smart_dedup"):                       # смысловой судья повторов — в видеопамять заранее
            threading.Thread(target=nox_dedup.warm_judge, daemon=True).start()
        if nox_scenarios:
            nox_scenarios.start_capture()    # слушаем F2 для захвата координат стаканов
            nox_scenarios.start_scheduler()  # планировщик ожидаемых событий (прогрев Ollama, окно гонки, анонс)

# Шлюз (страница Яндекса «установите сертификат Минцифры») ищем ТОЛЬКО когда интерфейса терминала нет:
# раньше слова проверялись по всему тексту страницы, и новость в ленте «Минцифры просит операторов…»
# (14.10, Интерфакс 13:11) считалась шлюзом — Нокс перезагружал терминал каждые 90 с, виджеты слетали.
_PAGE_STATE_JS = ("()=>{const t=document.body.innerText||'';"
                  "const ui=document.querySelectorAll('.pro-editable-tab-title').length>0"
                  "||!!document.querySelector('[class*=\"widgetsWrap\"]');"
                  "return {n:document.querySelectorAll('.news_row').length,blen:t.length,ui:ui,"
                  "gate:!ui&&/ертификат|инцифр|используйте .{0,4}ндекс/i.test(t)};}")

def _page_gated(st):
    if not st:
        return True
    if st.get("ui"):                     # терминал загружен (вкладки/виджеты есть) — не трогаем
        return False
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
                    try:
                        import nox_scenarios as _ns
                        _ns._log(f"ТЕРМИНАЛ: перезагружаю страницу — {'шлюз' if (st or {}).get('gate') else 'пустая'} "
                                 f"(текст {(st or {}).get('blen')}, новостей {(st or {}).get('n')})")
                    except Exception:
                        pass
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

# ------------------------------------------------------------------ разговор за 15 минут + живые данные для мозга
# Ридер слушает IPC Нокса (9712): speech_recognized — что сказал пользователь, assistant_reply — что ответил Нокс
# (туда же идут озвученные новости). История последних DIALOG_MIN минут уходит мозгу вместе с запросом —
# «а по Сберу?», «а почему он падает?» понятны. Новости обеих лент копятся в _news_recent — для ответов про инструмент.
DIALOG_MIN = 15
_dialog = collections.deque(maxlen=300)          # (ts, role, text): user | nox | news
_news_recent = collections.deque(maxlen=600)     # (ts, текст, тикеры)
_announced = collections.deque(maxlen=200)       # ключи текстов, которые ридер сам отправил на озвучку


def _dkey(t):
    return re.sub(r"[^а-яёa-z0-9]+", "", re.sub(r"(?<![а-я])сэр(?![а-я])", "", (t or "").lower()))[:40]


def _dialog_add(role, text):
    text = (text or "").strip()
    if not text or (role == "user" and len(text) < 3):
        return
    if role == "nox" and re.match(r"(Восстановил|Все системы|Добр(ое|ый|ой)\s|Готов к работе|Слушаю)", text):
        return                                   # служебные фразы сторожа и приветствия — не разговор
    if role == "nox":
        k = _dkey(text)
        last_user = next((ts for ts, r, _x in reversed(_dialog) if r == "user"), 0)
        answered = any(r == "nox" and ts >= last_user for ts, r, _x in _dialog)
        # ответ на вопрос — если пользователь говорил не дальше 40 с назад и ему ещё не ответили; иначе оповещение
        if k in _announced or time.time() - last_user > 40 or answered:
            role = "news"
    _dialog.append((time.time(), role, text[:400]))


def _dialog_loop():
    """Клиент IPC Нокса: пишет реплики в _dialog. Переподключается сам (jarvis-app перезапускается)."""
    from websockets.sync.client import connect as _wsc
    while True:
        try:
            with _wsc(f"ws://{IPC_HOST}:{IPC_PORT}", open_timeout=5, max_size=None) as ws:
                while True:
                    m = json.loads(ws.recv())
                    ev = m.get("event")
                    if ev == "speech_recognized":
                        _dialog_add("user", m.get("text", ""))
                        _speech_log(m.get("text", ""))
                    elif ev == "assistant_reply":
                        _dialog_add("nox", m.get("text", ""))
        except Exception:
            time.sleep(5)


SPEECH_LOG = r"C:\jarvis-voice\speech_log.jsonl"     # что распознал Vosk — для словаря ошибок (nox_speech_fix.json)


def _speech_log(text):
    try:
        if os.path.exists(SPEECH_LOG) and os.path.getsize(SPEECH_LOG) > 5_000_000:
            os.replace(SPEECH_LOG, SPEECH_LOG + ".old")
        with open(SPEECH_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "text": text}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _dialog_text(current=""):
    """История за DIALOG_MIN минут для мозга (без текущей фразы), новости — не больше трёх последних."""
    cut = time.time() - DIALOG_MIN * 60
    rows = [(ts, r, x) for ts, r, x in _dialog if ts >= cut]
    cur = _dkey(current)
    if rows and rows[-1][1] == "user" and _dkey(rows[-1][2]) == cur:
        rows = rows[:-1]
    news_keep = {id(x) for x in [x for x in rows if x[1] == "news"][-3:]}
    out = []
    for row in rows:
        ts, r, x = row
        if r == "news" and id(row) not in news_keep:
            continue
        hm = time.strftime("%H:%M", time.localtime(ts))
        out.append({"user": f"[{hm}] Пользователь: {x}", "nox": f"[{hm}] Нокс: {x}",
                    "news": f"[{hm}] Нокс прочитал новость: {x[:200]}"}[r])
    s = "\n".join(out)
    return s[-2500:]


def _last_user_turn(current=""):
    cur = _dkey(current)
    for ts, r, x in reversed(_dialog):
        if r == "user" and _dkey(x) != cur and ts >= time.time() - DIALOG_MIN * 60:
            return x
    return ""


def _live_context(t):
    """Цена, изменение, позиция по инструментам из запроса (или из прошлой фразы — «а почему он падает?»)
    и новости по ним за 3 часа."""
    import urllib.request as _ur, urllib.parse as _up

    def ctx(q):
        try:
            return json.loads(_ur.urlopen("http://127.0.0.1:8124/context?" + _up.urlencode({"text": q}), timeout=5).read().decode("utf-8"))
        except Exception:
            return {"items": [], "text": ""}
    c = ctx(t)
    if not c.get("text"):
        prev = _last_user_turn(t)
        if prev:
            c = ctx(prev)
    if not c.get("text"):
        return ""
    lines = [c["text"]]
    cut = time.time() - 3 * 3600
    for it in c.get("items", []):
        tk = (it.get("ticker") or "").upper()
        stem = re.sub(r"[^а-яёa-z]", "", (it.get("name") or "").lower().split(" ")[0])[:5]
        hits = [(ts, x) for ts, x, tks in _news_recent if ts >= cut and
                (tk and tk in [str(z).upper() for z in tks] or (len(stem) >= 4 and stem in x.lower()))]
        for ts, x in hits[-3:]:
            lines.append(f"Новость {time.strftime('%H:%M', time.localtime(ts))} про {it.get('name')}: {x[:220]}")
    return "\n".join(lines)


def _ipc_announce(text):
    """Отправить проактивную озвучку в jarvis-app по WebSocket (9712)."""
    _announced.append(_dkey(text))
    if nox_scenarios:
        try:
            nox_scenarios._log(f"ГОЛОС: {str(text)[:220]}")
        except Exception:
            pass
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
    "CNRU": "Циан", "KAZT": "Куйбышевазот", "KAZTP": "Куйбышевазот преф", "SAGO": "Самараэнерго",
    "SAGOP": "Самараэнерго преф", "HEAD": "Хэдхантер", "FLOT": "Совкомфлот", "SGZH": "Сегежа", "AKRN": "Акрон",
}

NAMES_FILE = r"C:\jarvis-voice\ticker_names.txt"
_names_mt = [0.0]

def _names_load():
    """ticker_names.txt («ТИКЕР = название», # — комментарий) -> TICKER_NAME. Читаем заново, только если файл менялся."""
    try:
        mt = os.path.getmtime(NAMES_FILE)
    except OSError:
        return
    if mt == _names_mt[0]:
        return
    _names_mt[0] = mt
    try:
        for ln in open(NAMES_FILE, encoding="utf-8-sig"):
            ln = ln.split("#", 1)[0].strip()
            if "=" not in ln:
                continue
            tk, nm = (x.strip() for x in ln.split("=", 1))
            if tk and nm:
                TICKER_NAME[tk.upper()] = nm
            elif tk:
                TICKER_NAME.pop(tk.upper(), None)
    except Exception:
        pass

_names_load()

def _tk_name(tk):
    _names_load()
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
    try:   # решение о дивидендах: «Циан: совет директоров рекомендовал дивиденды 33 руб на акцию …, отсечка 30 ноября»
        import nox_divdisc
        d = nox_divdisc.parse(title, body, item.get("tickers"), TICKER_NAME)
        if d:
            return nox_divdisc.speak(d)
    except Exception:
        pass
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

_EV_PRICE_RX = re.compile(r"(?:дивиденд|оферт|выкуп|допэмисс|buyback|байбек)[^.;!?]{0,60}?(\d+(?:[.,]\d+)?)\s*(?:руб|₽|р\.)",
                          re.I)

def _concise(item):
    """Корпсобытие В ЗАГОЛОВКЕ с суммой при нём, без оговорок -> кратко «Имя: дивиденды 35 руб».
    Иначе None — читаем заголовок целиком. Раньше событие искалось и в теле новости, а цена бралась первая
    попавшаяся: «Альфа повысила цель по Совкомфлоту до 120 руб» (в теле: «дивиденды 9 руб») звучало
    «Совкомфлот 120 руб дивиденды»; «рекомендовал НЕ выплачивать дивиденды» — «Куйбышевазот дивиденды»."""
    tks = item.get("tickers") or []
    head = item.get("text", "") or ""
    ev = _detect_event(head)
    if not (tks and ev):
        return None
    try:
        import nox_scenarios as _ns
        if _ns.HEDGE_RX.search(head.lower().replace("ё", "е")):
            return None
    except Exception:
        return None
    m = _EV_PRICE_RX.search(head)
    return f"{_tk_name(tks[0])}: {ev} {m.group(1)} руб" if m else None

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
    for tk in tickers:                                 # тикеры -> произносимые имена («$KFBA» -> «Инград»)
        s = re.sub(r"(?<![A-Za-zА-Яа-яЁё])\$?" + re.escape(tk) + r"(?![A-Za-zА-Яа-яЁё])",
                   _tk_name(tk), s, flags=re.I)
    # «$KFBA», которого нет в шапке новости, — тоже по списку названий
    s = re.sub(r"\$([A-Z][A-Z0-9]{1,6})\b", lambda m: TICKER_NAME.get(m.group(1), m.group(1)), s)
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

_read_q = queue.Queue()

# Умный отбор = ПРОВЕРКА поверх белого списка (15.10, просьба пользователя): мозг ничего не добавляет «по своему
# желанию», только отсеивает новости, которые не двигают котировки. Читается исходный заголовок, не пересказ.
# Критерии слов белого списка (зачем слово добавлено) — news_filter.json "why": {"санкции": "…"} — идут в вопрос.
_CHECK_SYS = (
    "Ты — фильтр биржевой ленты для трейдера (акции и фьючерсы Мосбиржи, облигации, валюта, нефть, золото, индексы). "
    "Новость УЖЕ совпала с белым списком пользователя; ты ничего не добавляешь, только отсеиваешь лишнее.\n"
    "ДА — если новость может сдвинуть котировки: решения властей и регуляторов, санкции, переговоры и военные события "
    "с влиянием на рынок и экономику, корпоративные события (дивиденды, отчётность, сделки, допэмиссия, выкуп, дефолт), "
    "макроданные, ставки, сырьё, налоги и пошлины.\n"
    "НЕТ — если это спорт, культура, погода, происшествия без последствий для экономики, бытовые и социальные темы, "
    "реклама, обзоры и мнения без новых фактов, служебные объявления биржи (графики торгов, риск-параметры, лимиты), "
    "или слово из белого списка встретилось в другом смысле.\n"
    "Если для совпавших слов даны критерии пользователя — решай строго по ним.\n"
    "Политика, дипломатия и переговоры государств (визиты, встречи, перемирие, заявления лидеров и переговорщиков), "
    "мобилизация и военные действия — это ДА. Если сомневаешься — ДА: пропустить важную новость хуже, "
    "чем прочитать лишнюю.\n"
    "Ответь одной строкой: «ДА» или «НЕТ: причина (до 6 слов)»."
)
_CHECK_SHOTS = [
    ("Совпало: санкции.\nНовость: США ввели санкции против крупнейших нефтяных компаний России", "ДА"),
    ("Совпало: переговоры.\nНовость: «Спартак» ведёт переговоры с новым главным тренером", "НЕТ: спорт"),
    ("Совпало: помощь.\nНовость: Волонтёры оказали помощь бездомным животным в Москве", "НЕТ: не про рынок"),
    ("Совпало: поддержка.\nНовость: Минфин подготовил меры поддержки угольной отрасли", "ДА"),
    ("Совпало: территории.\nНовость: На территории Московского региона ожидается сильный снегопад", "НЕТ: погода"),
    ("Совпало: источник мосбирж.\nНовость: Мосбиржа опубликовала график торгов в праздничные дни",
     "НЕТ: служебное объявление биржи"),
    ("Совпало: уиткофф.\nНовость: Уиткофф прибыл в Москву для переговоров с Путиным", "ДА"),
    ("Совпало: прибыль.\nНовость: Чистая прибыль Сбербанка по МСФО за 9 месяцев выросла на 12%", "ДА"),
    ("Совпало: дмитриев, переговоры.\nНовость: Дмитриев встретится с представителями США на следующей неделе", "ДА"),
    ("Совпало: прибыль.\nНовость: Блогер рассказал, как получить прибыль от продажи старых вещей", "НЕТ: бытовая тема"),
]


def _match_why(item, cfg):
    """Что из белого списка совпало: [«тикер SBER», «санкции», «источник мосбирж»]."""
    out = []
    tks = [t.upper() for t in (item.get("tickers") or [])]
    out += [f"тикер {t}" for t in (cfg.get("tickers") or []) if t in tks]
    low = _IDIOM.sub(" ", (item.get("text", "") + " " + item.get("body", "")).lower().replace("ё", "е"))
    out += [p for p in (cfg.get("phrases") or []) if _phrase_hit(p.replace("ё", "е"), low)]
    src = (item.get("source", "") or "").lower()
    out += [f"источник {x}" for x in (cfg.get("sources") or []) if x in src]
    return out


def _smart_check(item, cfg):
    """(читать?, причина). Совпало несколько слов — спрашиваем мозг ПО КАЖДОМУ со своим критерием и читаем, если
    подошло хоть одно (16.10: «…с помощью 22-го пакета санкций» и «Уиткофф … для переговоров» модель 3b отсеивала
    по критерию ДРУГОГО слова — «помощь», «переговоры»)."""
    hits = _match_why(item, cfg)
    no = []
    for h in (hits or [""])[:4]:
        ok, why = _smart_ask(item, cfg, [h] if h else [])
        if ok:
            return True, why
        no.append(why)
    return False, no[0]


def _smart_ask(item, cfg, hits):
    """Один вопрос мозгу. Мозг — модель Ollama со страницы «Мозг»; сбой или таймаут — читать (не терять новость)."""
    import urllib.request
    why = cfg.get("why") or {}
    crit = "; ".join(f"«{h}» — {why[h]}" for h in hits if why.get(h))
    try:
        import nox_feedback                     # «такие не читай» / «такие читай всегда» — примеры пользователя
        crit += nox_feedback.prompt_examples(cfg, hits)
    except Exception:
        pass
    user = (f"Совпало: {', '.join(hits) or 'белый список'}." + (f"\nКритерии пользователя: {crit}" if crit else "")
            + f"\nНовость: {(item.get('text') or '').strip()[:400]}")
    msgs = [{"role": "system", "content": _CHECK_SYS}]
    for u, a in _CHECK_SHOTS:
        msgs += [{"role": "user", "content": u}, {"role": "assistant", "content": a}]
    msgs.append({"role": "user", "content": user})
    body = json.dumps({"model": (nox_scenarios._ollama_model() if nox_scenarios else "qwen2.5:7b"), "messages": msgs,
                       "stream": False, "temperature": 0, "max_tokens": 20, "keep_alive": "30m"}).encode("utf-8")
    try:
        req = urllib.request.Request(_OLLAMA_CHAT, data=body, headers={"Content-Type": "application/json"})
        resp = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8"))["choices"][0]["message"]["content"]
    except Exception as e:
        return True, f"мозг не ответил ({type(e).__name__}) — читаю"
    resp = (resp or "").strip().strip("«»\"' ")
    if re.match(r"(?i)нет\b", resp):
        return False, (resp.split(":", 1)[1].strip() if ":" in resp else "не про рынок")
    return True, ", ".join(hits)


# --- одно корпсобытие из многих источников (16.10, дивиденды ВИ.ру ~15 раз: ЦРКИ в двух лентах, Смартлаб,
# TradingSharks, канал компании, «ПАО «ВИ.ру» дивиденды»… — каждый своими словами, похожесть текста не склеивала)
_corp_said = {}            # (эмитент, тип) -> {"t": время, "amounts": {суммы на акцию}}
_CORP_TTL = 6 * 3600
_AMT_RX = re.compile(r"(\d[\d\s\u00a0]*(?:[.,]\d+)?)\s*(млрд|миллиард\w*|млн|миллион\w*|тыс\w*)?", re.I)

def _amount_rub(s):
    """«7 руб» -> 7, «42 коп» -> 0.42, «3 500 000 000 рублей» / «3,5 млрд» -> 3.5e9, «7/акция» -> 7."""
    m = _AMT_RX.search(s or "")
    if not m:
        return None
    try:
        v = float(re.sub(r"[\s\u00a0]", "", m.group(1)).replace(",", "."))
    except Exception:
        return None
    mul = (m.group(2) or "").lower()
    v *= 1e9 if mul.startswith(("млрд", "миллиард")) else 1e6 if mul.startswith(("млн", "миллион")) else \
        1e3 if mul.startswith("тыс") else 1
    if re.match(r"\s*коп", (s or "")[m.end():]):
        v /= 100
    return v

def _corp_issuer(it, name=""):
    tk = ((it.get("tickers") or [""])[0] or "").upper()
    if tk:
        return tk
    return re.sub(r"[^0-9a-zа-я]+", "", re.sub(r"\b(?:пао|мкпао|ао|оао)\b", "", (name or "").lower().replace("ё", "е")))

def _corp_norm(n):
    return re.sub(r"[^0-9a-zа-я]+", "", re.sub(r"\b(?:пао|мкпао|ао|оао)\b", "", (n or "").lower().replace("ё", "е")))

def _corp_keys(it, name=""):
    """Все имена эмитента: тикер, его название из списка, имя из новости («HNFG», «henderson», «хендерсон»…) —
    16.10 событие легло под тикером, а следующая новость искала его по имени и читалась снова."""
    keys = set()
    tk = ((it.get("tickers") or [""])[0] or "").upper()
    if tk:
        keys |= {tk, _corp_norm(TICKER_NAME.get(tk, ""))}
    keys.add(_corp_norm(name))
    if name:   # имя без тикера — найти тикер по названию из списка
        nn = _corp_norm(name)
        keys |= {t for t, nm in TICKER_NAME.items() if nn and _corp_norm(nm) == nn}
    keys.discard("")
    return keys

def _corp_repeat(it, typ, amount=None, name=""):
    """True — это корпсобытие эмитента уже озвучено за 6 ч: сумма не названа, та же сумма на акцию или (для
    дивидендов) названа ОБЩАЯ сумма (≥100 тыс ₽ — не на акцию). Иначе запоминает и False (озвучить)."""
    keys = _corp_keys(it, name)
    if not keys or not typ:
        return False
    now = time.time()
    total = typ == "дивиденд" and amount is not None and amount >= 1e5
    recs = [r for k in keys for r in [_corp_said.get((k, typ))] if r and now - r["t"] < _CORP_TTL]
    # одна общая запись на эмитента: все его имена (и из прошлых новостей) указывают на неё
    rec = {"t": now, "amounts": set().union(*(r["amounts"] for r in recs)) if recs else set()}
    ids = {id(r) for r in recs}
    group = keys | {k for (k, t), r in _corp_said.items() if t == typ and id(r) in ids}
    repeat = bool(recs) and (amount is None or total or any(abs(amount - a) < 1e-6 for a in rec["amounts"]))
    if not repeat and amount is not None and not total:
        rec["amounts"].add(round(amount, 6))
    for k in group:
        _corp_said[(k, typ)] = rec
    return repeat

def _rub_total(v):
    """3.5e9 -> «3,5 млрд рублей»."""
    for k, w in ((1e9, "млрд"), (1e6, "млн"), (1e3, "тыс")):
        if v >= k:
            return f"{v / k:.2f}".rstrip("0").rstrip(".").replace(".", ",") + f" {w} рублей"
    return f"{v:.0f} рублей"

def _feed_tag(it):
    """«TT, увидел +0.03 с, +0.6 с» — лента, когда монитор увидел новость и сколько прошло к решению
    (от появления на странице)."""
    seen, det = it.get("seen"), it.get("_detect")
    if not seen:
        return it.get("feed") or "TT"
    return ((it.get("feed") or "TT") + (f", увидел +{det - seen / 1000:.2f} с" if det else "")
            + f", +{time.time() - seen / 1000:.1f} с")

def _log_repeat(it, why):
    if nox_scenarios:
        nox_scenarios._log(f"ПОВТОР [{it.get('feed') or 'TT'}]: не читаю — {why} | {it.get('text', '')[:110]}")

def _read_worker():
    """Чтение обычных новостей по очереди (порядок ленты сохраняется): белый список и повтор уже проверены;
    умный отбор (если включён) может отсеять. Каждое решение — в nox_scenarios.log («УМНЫЙ ОТБОР: …»)."""
    while True:
        it, cfg = _read_q.get()
        try:
            if cfg.get("smart"):
                ok, why = _smart_check(it, cfg)
                if nox_scenarios:
                    nox_scenarios._log(f"УМНЫЙ ОТБОР: {'читаю (' + why + ')' if ok else 'НЕ читаю — ' + why}"
                                       f" [{_feed_tag(it)}] | {it.get('text', '')[:110]}")
                if not ok:
                    continue
            if _mon_enabled() and not _focus.get("active"):
                _read_last.append({"ts": time.time(), "text": it.get("text", ""), "hits": _match_why(it, cfg)})
                _ipc_announce(_shorten(it)); time.sleep(0.4)
        except Exception:
            pass


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

# однокоренные слова другой части речи: «блокировка» — и «заблокировал», «территории» — и «территориальные»
# (критерии пользователя 15.10: блокировка активов, территориальные уступки)
_DERIV = {"блокировка": ("блокир",), "блокировки": ("блокир",), "территории": ("территориальн",),
          "территория": ("территориальн",)}

@functools.lru_cache(maxsize=2000)
def _phrase_parts(phrase):
    m = _morph_get()
    out = []
    for w in re.findall(r"[а-яa-z0-9]+", phrase.lower().replace("ё", "е")):
        known = bool(m) and len(w) > 3 and m.word_is_known(w)
        # незнакомое словарю слово (≥6 букв) — по угаданной начальной форме: «допэмиссия» ловит «допэмиссию»
        guess = bool(m) and not known and len(w) >= 6
        lems = _lemmas(w) if (known or guess) else None
        if lems and "Surn" in m.parse(w)[0].tag:
            # фамилия — только как фамилия: «дмитриев» иначе ещё и «(нет) Дмитриев» от имени «Дмитрий», и «пресс-
            # секретарь Дмитрий Песков» читался по слову «дмитриев» (16.10: пять новостей Кремля, хоккей)
            lems = frozenset(x.normal_form.replace("ё", "е") for x in m.parse(w)[:3] if "Surn" in x.tag)
        out.append((w, lems))
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
        if w in _DERIV and any(r in t for t in toks for r in _DERIV[w]):
            continue
        if lems:
            if lem_set is None:
                lem_set = set().union(*(_lemmas(t) for t in toks)) if toks else set()
            if lems & lem_set:
                continue
        return False
    return True

# «с помощью санкций», «при помощи дронов» — предлог, а не «помощь» из белого списка
_IDIOM = re.compile(r"(?<![а-я])(?:с|при)\s+(?:(?:его|ее|их|чьей|этой|своей|нашей)\s+)?помощ(?:ью|и)(?![а-я])")

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
    low = _IDIOM.sub(" ", (item.get("text", "") + " " + item.get("body", "")).lower().replace("ё", "е"))
    if cfg["phrases"] and any(_phrase_hit(p.replace("ё", "е"), low) for p in cfg["phrases"]):
        return True
    src = (item.get("source", "") or "").lower()
    # ЦРКИ НЕ приравниваем к e-disclosure: читать все раскрытия подряд — шум («одобрение сделок»).
    # Важные раскрытия (дивиденды, выкуп, допэмиссия…) читает _disclosure — независимо от фильтра.
    if cfg["sources"] and any(s in src for s in cfg["sources"]):
        return True
    return False

# Факт макроданных из календаря Trading Tools: «US CORE CPI MOM ACTUAL 0.3% (FORECAST 0.2%, PREVIOUS 0.3%)».
# 14.10 строка пришла через 8,7 с после релиза, а API BLS почти 2 минуты отвечал 503 (перегрузка на релизе) —
# отдаём наблюдателям как данные BLS с ГОТОВЫМ числом (кто первый — API или лента — тот и сработал).
# Только м/м базового ИПЦ: «BLS базовый ИПЦ» иначе совпало бы и с годовой строкой (2,6%).
_TT_ACTUAL = [(re.compile(r"^\s*US\s+CORE\s+CPI\s+M[O/]M\s+ACTUAL\s+(-?\d+(?:[.,]\d+)?)\s*%", re.I),
               "базовый ИПЦ США м/м")]

def _tt_actual_items(items):
    out = []
    for it in items:
        for rx, name in _TT_ACTUAL:
            m = rx.search(it.get("text", "") or "")
            if m:
                v = float(m.group(1).replace(",", "."))
                fd = it.get("feed") or "TT"
                out.append({**it, "text": f"BLS: {name} (лента {fd}): {v:+.1f}%", "body": "", "value": v, "source": fd})
                if nox_scenarios:
                    nox_scenarios._log(f"ЛЕНТА {fd} -> наблюдатели: {name} = {v} («{it.get('text', '')[:70]}»)")
    return out

def _div_items(items):
    """Решение СД/собрания о дивидендах в раскрытии ЦРКИ -> новость с ГОТОВЫМ числом (руб на акцию) для наблюдателей:
    заголовок у таких раскрытий пустой («Решения совета директоров»), сумма и отсечка — в длинном теле."""
    out = []
    try:
        import nox_divdisc
    except Exception:
        return out
    for it in items:
        try:
            d = nox_divdisc.parse(it.get("text", ""), it.get("body", ""), it.get("tickers"), TICKER_NAME)
        except Exception:
            d = None
        if not d or d.get("amount") is None:
            continue
        tk = d.get("ticker") or ""
        out.append({**it, "text": f"{d['who']} {tk}: {d['verb']} дивиденды {d['amount']:g} руб на акцию",
                    "body": "", "value": float(d["amount"]), "source": "ЦРКИ"})
        if nox_scenarios:
            nox_scenarios._log(f"ДИВИДЕНДЫ (раскрытие) -> наблюдатели: {d['who']} {tk} = {d['amount']:g} руб, "
                               f"отсечка {d.get('record') or '?'}")
    return out

def _news_key(text):
    return nox_dedup.key(text) if nox_dedup else (text or "")[:80]

_DISC_SRC = re.compile(r"црки|disclosure", re.I)

def _item_key(it):
    """Ключ «уже видели в ленте»: текст без регистра/знаков/подписи источника. У раскрытий ЦРКИ/e-disclosure
    заголовок ОДИН на всех («Решения совета директоров (наблюдательного совета)») — с ключом только по заголовку
    второе и третье раскрытие за день (Куйбышевазот, Самараэнерго, Циан…) молча отбрасывались как «уже было».
    Для них в ключ — первый тикер и начало тела."""
    k = _news_key(it.get("text", ""))
    if _DISC_SRC.search(it.get("source") or ""):
        # тело в ключ НЕ берём: оно подгружается/раскрывается позже — та же новость казалась новой (16.10, ВИ.ру)
        k += "|" + ((it.get("tickers") or [""])[0] or "").upper()
    return k

_STOCKSI_OLD = re.compile(r"вчера|\d{1,2}\.\d{1,2}|январ|феврал|март|апрел|ма[яй]|июн|июл|август|сентябр|октябр|ноябр|декабр", re.I)

def _stale(it):
    """Старая новость второй ленты («Вчера, 18:40», «Сегодня, 10:05» при 10:30): Stocksi держит сотни новостей;
    если строка перерисовалась/поменялась — это не свежая новость (наблюдатели и голос её не получают)."""
    ts = it.get("time") or ""
    if not ts:
        return False
    if _STOCKSI_OLD.search(ts):
        return True
    m = re.search(r"(\d{1,2}):(\d{2})", ts)
    if not m:
        return False                        # «только что» и т.п. — свежая
    now = time.localtime()
    return (now.tm_hour * 60 + now.tm_min) - (int(m.group(1)) * 60 + int(m.group(2))) > 15

# --- гонка лент (Trading Tools vs Stocksi): кто первым показал одну и ту же новость и на сколько секунд.
# Время — момент появления строки на странице (MutationObserver, часы страницы одни на обе ленты).
RACE_FILE = r"C:\jarvis-voice\feed_race.jsonl"
_race = {}                 # key -> {feed, seen, text, tickers, disc, t, also:set}
_race_recent = []          # [(t, key, sig)] — для сравнения по смыслу (та же новость другими словами)
_race_only = {}            # feed -> сколько новостей было ТОЛЬКО в этой ленте (копится до сброса в файл)
_race_last_purge = [0.0]

def _race_write(rec):
    try:
        with open(RACE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass

def _race_pair(e, feed2, seen2, text2):
    lag = (seen2 - e["seen"]) / 1000.0
    first, second = (e["feed"], feed2) if lag >= 0 else (feed2, e["feed"])
    _race_write({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "first": first, "second": second,
                 "lag_s": round(abs(lag), 2), "text": e["text"][:110], "text2": (text2 or "")[:110]})

def _race_same(e, sig, it):
    if e["disc"] or _DISC_SRC.search(it.get("source") or ""):
        # раскрытия: заголовок общий у всех компаний — одна и та же, только если тот же эмитент
        tk = ((it.get("tickers") or [""])[0] or "").upper()
        return bool(tk) and e["tk"] == tk and nox_dedup.similar(e["sig"], sig)
    return nox_dedup.similar(e["sig"], sig)

def _race_note(key, it, is_new):
    """is_new: ключ впервые в ленте (новая формулировка). Иначе — та же строка уже была: в другой ленте?"""
    try:
        feed = it.get("feed") or "TT"
        now = time.time()
        seen = it.get("seen") or now * 1000
        e = _race.get(key)
        if e is not None:
            if feed not in e["also"]:
                e["also"].add(feed)
                _race_pair(e, feed, seen, it.get("text", ""))
                return True
            return False
        if not is_new or not nox_dedup:
            return False
        sig = nox_dedup.sig(it.get("text", ""))
        e = {"feed": feed, "seen": seen, "text": it.get("text", "")[:120], "t": now, "also": {feed}, "sig": sig,
             "tk": ((it.get("tickers") or [""])[0] or "").upper(), "disc": bool(_DISC_SRC.search(it.get("source") or ""))}
        _race[key] = e
        for (_t0, k0, _s0) in reversed(_race_recent):
            e0 = _race.get(k0)
            if e0 and e0["feed"] != feed and feed not in e0["also"] and _race_same(e0, sig, it):
                e0["also"].add(feed); e["also"].add(e0["feed"])
                _race_pair(e0, feed, seen, it.get("text", ""))
                break
        _race_recent.append((now, key, sig))
        if now - _race_last_purge[0] > 60:
            _race_last_purge[0] = now
            for k in [k for k, x in _race.items() if now - x["t"] > 1200]:     # 20 мин — дальше не ждём
                x = _race.pop(k)
                if len(x["also"]) == 1:
                    f = next(iter(x["also"]))
                    _race_only[f] = _race_only.get(f, 0) + 1
            _race_recent[:] = [r for r in _race_recent if now - r[0] <= 1200 and r[1] in _race]
            if _race_only:
                _race_write({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "only": dict(_race_only)})
                _race_only.clear()
    except Exception:
        pass

_seen_news = set()
_handled = {}              # key -> время: новость взята в работу (чтение/событие/раскрытие) — повтор из другой ленты не нужен
_primed = set()            # ленты, чья текущая страница уже запомнена (первый проход — без реакции)
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

_DIV_PS = [
    re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:руб\w*|₽|р\.)?\s*/\s*акци", re.I),                      # «7/акция»
    re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:руб\w*|₽|р\.|коп\w*)\s*(?:\d{1,2}\s*коп\w*\s*)?на\s+(?:одну\s+|1\s+|каждую\s+)?"
               r"(?:обыкновенн\w+\s+|привилегированн\w+\s+)?акци", re.I),                          # «7 рублей на 1 акцию»
    re.compile(r"дивиденд\w*[^.;!?]{0,60}?(\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)\s*"
               r"(?:млрд|млн|тыс)?\.?\s*(?:руб\w*|₽|р\.|коп\w*)", re.I),                            # «дивиденды 7 руб»
]

def _fast_div(it):
    """Дивиденды ПРАВИЛАМИ, без мозга: сумма в заголовке и нет оговорок («может», «прогноз», «не выплачивать»)
    -> событие сразу. Иначе None (разбирает мозг, как раньше)."""
    try:
        import nox_scenarios as _ns
    except Exception:
        return None
    text = (it.get("text") or "").strip()
    low = text.lower().replace("ё", "е")
    if "дивиденд" not in low or _ns.HEDGE_RX.search(low):
        return None
    m = next((m for rx in _DIV_PS for m in [rx.search(text)] if m), None)
    if not m:
        return None
    amt = _amount_rub(text[m.start(1):m.end()])
    if not amt:
        return None
    tks = it.get("tickers") or []
    who = _tk_name(tks[0]) if tks else ""
    if not who:
        sm = re.match(r"^\s*(?:\$?[A-Z]{3,6}\s+)?([^:]{2,40}):", text)
        who = sm.group(1).strip() if sm else ""
    if not who:
        return None
    if amt >= 1e5:
        val = _rub_total(amt)
    elif amt < 1:
        val = f"{round(amt * 100)} коп"
    else:
        val = ("%.2f" % amt).rstrip("0").rstrip(".").replace(".", ",") + " руб"
    return {"type": "дивиденд", "ticker": who, "value": val, "kw_type": "дивиденд", "fast": True}

def HEDGE_OK(ev, text):
    """Событие без оговорок (тот же критерий, что у шаблона озвучки)."""
    try:
        import nox_scenarios as _ns
        return not _ns.HEDGE_RX.search((text or "").lower().replace("ё", "е"))
    except Exception:
        return True

def _event_worker():
    """Корпсобытия по очереди (порядок новостей сохраняется): мозг извлекает тип/тикер/размер.
    Озвучиваем, только если мозг подтвердил ТОТ ЖЕ тип, что нашли слова; иначе новость идёт
    обычным фильтром (как все остальные)."""
    while True:
        it, cfg = _event_q.get()
        try:
            import nox_scenarios as _ns
            text = it.get("text", "")
            ev = _fast_div(it) if _ns.event_type(text) == "дивиденд" else None
            if ev:
                _ns._log(f"СОБЫТИЕ дивиденд (правила, без мозга): {ev['ticker']} {ev['value']} | {text[:60]}")
            else:
                ev = _ns.extract_event(text, it.get("body", ""))
            if _focus.get("active"):
                continue                    # разбор шёл до тишины — уже не озвучиваем
            if _ns.event_confirmed(ev):
                amt = _amount_rub(ev.get("value")) if ev.get("type") != "дефолт" else None
                if not ev.get("report") and _corp_repeat(it, ev.get("type"), amt, ev.get("ticker")):
                    _log_repeat(it, f"{ev.get('type')} {_corp_issuer(it, ev.get('ticker'))} уже озвучен(а)")
                    continue
                if _mon_enabled() and not _is_dup(text, smart=False):
                    # шаблон «Дивиденды Новатэк: 35 руб.» — только чёткое событие с суммой; новость с оговоркой
                    # («решение пока не принято», «может выплатить») или без суммы — заголовком целиком
                    if ev.get("type") == "дивиденд" and amt and amt >= 1e5 and not HEDGE_OK(ev, text):
                        _read_q.put((it, cfg))
                    elif ev.get("type") == "дивиденд" and amt and amt >= 1e5:
                        # «ВИ.ру: дивиденды 3 500 000 000 рублей» — это вся выплата, не на акцию
                        _ipc_announce(f"{ev.get('ticker') or ''}: на дивиденды направят {_rub_total(amt)}.".lstrip(": "))
                        time.sleep(0.4)
                    elif _ns.event_speakable(ev, text):
                        _ipc_announce(_ns.speak_event(ev)); time.sleep(0.4)
                    else:
                        _read_q.put((it, cfg))   # оговорки/прогнозы — через умный отбор (критерий «только факт»)
            elif _mon_enabled() and _match(it, cfg) and not _is_dup(text):
                _read_q.put((it, cfg))
        except Exception:
            pass

def _monitor_loop():
    _ready.wait(90)
    time.sleep(3)
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
            _names_load()
            new_items = []
            # ключ без регистра/знаков/подписи источника: «ТРАМП: …» и «Трамп: …» — одна новость (и в обеих лентах)
            # лента, которой ещё не было на странице (старт ридера / виджет Stocksi только что добавили): её текущие
            # новости — старые; запоминаем без реакции, иначе вся лента разом «свежая» (чтение пачкой, наблюдатели)
            fresh = {it.get("feed") or "TT" for it in items} - _primed
            for it in items:                    # newest first (в каждой ленте)
                key = _item_key(it)
                if (it.get("feed") or "TT") in fresh or _stale(it):
                    if key not in _seen_news and not _stale(it):   # уже висящие в ленте — в память для ответов, без озвучки
                        _news_recent.append((time.time(), it.get("text", "")[:300], tuple(it.get("tickers") or ())))
                    _seen_news.add(key)
                    continue
                is_new = key not in _seen_news
                twin = _race_note(key, it, is_new)
                it["_key"] = key
                if is_new:
                    _news_recent.append((time.time(), it.get("text", "")[:300], tuple(it.get("tickers") or ())))
                if not is_new:
                    if twin and key not in _handled:
                        # та же строка из другой ленты, а первую версию в работу не взяли: у этой может быть тело
                        # («…вести переговоры о продаже…» у TT, у Stocksi — пусто) — разобрать заново
                        it["_repeat"] = True
                        new_items.append(it)
                    elif twin and _mon_enabled() and _match(it, cfg):
                        _log_repeat(it, "та же строка")
                    continue
                _seen_news.add(key)
                new_items.append(it)
            _primed.update(fresh)
            if len(_handled) > 5000:
                for k in [k for k, t in _handled.items() if time.time() - t > 3600]:
                    _handled.pop(k, None)
            # две ленты — по моменту появления на странице, новые первыми (наблюдатели разбирают с конца)
            new_items.sort(key=lambda x: -(x.get("seen") or 0))
            if len(_seen_news) > 5000:
                # НЕ очищать целиком: иначе вся текущая лента (до 40 новостей) разом станет «свежей» —
                # повторное чтение пачкой и ложные срабатывания наблюдателей. Оставляем только текущую ленту.
                _seen_news.clear()
                _seen_news.update(_item_key(it) for it in items)
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
                        # факт макроданных из ленты TT — первым, с готовым числом
                        # готовые числа (лента TT, раскрытия о дивидендах) — В КОНЕЦ: on_news идёт с конца, и
                        # наблюдатель «один раз» должен получить число раньше исходной новости без числа
                        nox_watchers.on_news(new_items + _tt_actual_items(new_items) + _div_items(new_items), "trading")
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
                    _handled[it.get("_key")] = time.time()
                    try:
                        import nox_divdisc
                        dd = nox_divdisc.parse(it.get("text", ""), it.get("body", ""), it.get("tickers"), TICKER_NAME)
                    except Exception:
                        dd = None
                    if dd:
                        d_amt, d_who = dd.get("amount"), dd.get("who")
                    elif re.search(r"дивиденд", disc, re.I):
                        # раскрытие о дивидендах без разобранного решения («…8 руб./ао — раскрытие», «собрание
                        # рассмотрит дивиденды») — сумма из самой фразы, если есть
                        m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:руб|коп)", disc)
                        d_amt, d_who = (_amount_rub(disc[m.start():]) if m else None), disc.split(":", 1)[0]
                    else:
                        d_amt = d_who = None
                    if (dd or d_who) and _corp_repeat(it, "дивиденд", d_amt, d_who):
                        _log_repeat(it, f"дивиденды {_corp_issuer(it, d_who)} уже озвучены")
                        continue
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
                    _handled[it.get("_key")] = time.time()
                    # разбор мозгом (облако 4–9 с) — в фоне: опрос ленты не ждёт (раньше стоял на каждой
                    # «событийной» новости — в окне решения по ставке это задержало бы реакцию)
                    _event_q.put((it, cfg))
                    continue
                # прочие новости: ТОЛЬКО белый список (тикеры/фразы/источники) и не повтор; умный отбор
                # (если включён) лишь отсеивает нерыночное — в фоне, опрос ленты его не ждёт
                if _mon_enabled() and _match(it, cfg):
                    _handled[it.get("_key")] = time.time()
                    if not _is_dup(it.get("text", "")):
                        _read_q.put((it, cfg))
                    else:
                        _log_repeat(it, "похожая уже была")
        except Exception:
            pass
        # было 2 с: на новость реагируем быстрее; в окне решения по ставке (−5 с…+3 мин) — 0.1 с
        try:
            fast = bool(nox_watchers and nox_watchers.fast_window())
        except Exception:
            fast = False
        # ждём сигнала «новая строка» из страницы (_ping_loop) — или опрос по таймеру, если сигнала нет
        _news_wake.wait(0.1 if fast else 0.5)
        _news_wake.clear()

_news_wake = threading.Event()

def _ping_loop():
    """Свой CDP-канал к вкладке терминала: binding noxNewsPing — MutationObserver лент (MON_JS) зовёт его на
    каждую новую строку, и монитор просыпается сразу (было: до 0,5 с ожидания следующего опроса)."""
    import asyncio
    import urllib.request
    async def session():
        import websockets
        tabs = json.load(urllib.request.urlopen(CDP_URL + "/json", timeout=5))
        t = next((x for x in tabs if x.get("type") == "page" and TERMINAL_MATCH in (x.get("url") or "")), None)
        if not t:
            raise RuntimeError("нет вкладки терминала")
        async with websockets.connect(t["webSocketDebuggerUrl"], max_size=50_000_000, open_timeout=10,
                                      ping_interval=20) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Runtime.addBinding", "params": {"name": "noxNewsPing"}}))
            async for raw in ws:
                m = json.loads(raw)
                if m.get("method") == "Runtime.bindingCalled" and m["params"].get("name") == "noxNewsPing":
                    _news_wake.set()
    _ready.wait(120)
    while True:
        try:
            asyncio.run(session())
        except Exception:
            pass
        time.sleep(3)

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

@app.get("/memory_get")
def memory_get():
    import nox_memory
    return {"ok": True, **nox_memory.load(), "prompt": nox_memory.block()}


@app.get("/memory_save")
def memory_save(json_data: str = ""):
    import nox_memory
    try:
        d = nox_memory.save(json.loads(json_data or "{}"))
    except Exception as e:
        return {"ok": False, "text": f"Не сохранил память: {type(e).__name__}"}
    return {"ok": True, **d, "prompt": nox_memory.block()}


@app.get("/vision_shot")
def vision_shot(q: str = ""):
    """Проверка зрения БЕЗ отправки в облако: какой экран снимется и сколько весит снимок."""
    try:
        import nox_vision
        t0 = time.time()
        jpeg, where = nox_vision.grab(q)
        return {"ok": True, "where": where, "kb": len(jpeg) // 1024, "ms": int((time.time() - t0) * 1000),
                "monitors": nox_vision.monitors(), "match": bool(nox_vision.VISION_RE.search((q or "").lower())),
                "enabled": _feat("vision")}
    except Exception as e:
        return {"ok": False, "error": repr(e)}


@app.get("/feed_race")
def feed_race(day: str = ""):
    """Гонка лент: какая (Trading Tools / Stocksi) первой показала одну и ту же новость и на сколько секунд."""
    day = day or time.strftime("%Y-%m-%d")
    pairs, only = [], {}
    try:
        with open(RACE_FILE, encoding="utf-8") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if not str(r.get("ts", "")).startswith(day):
                    continue
                if "only" in r:
                    for k, v in r["only"].items():
                        only[k] = only.get(k, 0) + v
                else:
                    pairs.append(r)
    except FileNotFoundError:
        pass
    for x in _race.values():                       # ещё не сброшенные в файл (последние 20 мин)
        if len(x["also"]) == 1 and time.strftime("%Y-%m-%d", time.localtime(x["t"])) == day:
            f1 = next(iter(x["also"]))
            only[f1] = only.get(f1, 0) + 1
    for k, v in _race_only.items():
        only[k] = only.get(k, 0) + v
    first, lead = {}, {}
    for r in pairs:
        first[r["first"]] = first.get(r["first"], 0) + 1
        lead.setdefault(r["first"], []).append(r["lag_s"])
    med = {k: sorted(v)[len(v) // 2] for k, v in lead.items()}
    parts = [f"{k} первой {first[k]} раз (медиана на {med[k]:.1f} с раньше)" for k in sorted(first, key=lambda k: -first[k])]
    text = (f"Общих новостей {len(pairs)}: " + ", ".join(parts) if pairs else "Общих новостей в двух лентах пока нет")
    if only:
        text += ". Только в одной ленте: " + ", ".join(f"{k} {v}" for k, v in sorted(only.items()))
    return {"day": day, "pairs": len(pairs), "first": first, "median_lead_s": med, "only": only,
            "last": pairs[-15:], "text": text + "."}

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
def web_answer(q: str = "", extra: str = ""):
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
                "Не упоминай сам поиск и источники." + ns.SPEECH_RULES + _memory_block())
        user = f"Вопрос: {q}\n\nРезультаты поиска:\n{results[:3500]}"
        if extra:
            user += f"\n\nДАННЫЕ ТЕРМИНАЛА (точнее поиска — цифры по инструментам бери отсюда):\n{extra}"
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
    "Если сомневаешься между answer и web — выбирай web. Отвечай ТОЛЬКО JSON.\n"
    "Если к запросу приложены ДАННЫЕ ТЕРМИНАЛА (цена, изменение, позиция, свежие новости) и их хватает для ответа — "
    "выбирай answer и бери цифры ТОЛЬКО оттуда; объясняй, что это значит для позиции и риска. "
    "Если приложена ИСТОРИЯ РАЗГОВОРА, а запрос — её продолжение («а по Сберу?», «а почему?», «а что с ним?») — "
    "пойми его по истории."
    " Текст в поле text — грамотным русским языком: правильные падежи и окончания, числа согласованы "
    "со словами, иностранные названия по-русски, без латиницы и эмодзи."
)
_LOCAL = "__LOCAL__"


def _memory_block():
    """Память Нокса (правила общения + факты о пользователе) для системной подсказки; вкладка «Память»."""
    try:
        import nox_memory
        return nox_memory.block()
    except Exception:
        return ""

def _brain_explain_news():
    news = last_news().get("text", "")
    if not news.strip():
        return "Пока не вижу новостей в ленте, сэр."
    try:
        import nox_scenarios as ns
        model = ns._appdb().get("openai_model") or None
        sysp = ("Объясни простыми словами по-русски эту биржевую новость и почему она важна, 2-3 коротких предложения."
                + ns.SPEECH_RULES + _memory_block())
        ans = (ns._llm_model(sysp, news, model, max_tokens=160) if model else ns._llm(sysp, news, max_tokens=160)) or ""
        return ans.strip() or news
    except Exception:
        return news

# «следи за новостями / читай новости» БЕЗ темы = включить чтение ленты (а не наблюдатель «новости»);
# «следи за новостями про Сбер» — тема есть -> это уже наблюдатель (идёт в мозг как раньше).
_NEWS_FILLER = {"нокс", "следи", "следить", "последи", "за", "новостями", "новости", "новостей", "новость", "лентой", "ленту",
                "ленты", "читай", "читать", "включи", "включить", "озвучивай", "озвучивать", "вслух", "пожалуйста",
                "все", "всеми", "всё", "давай", "начни", "мне", "весь", "день", "сегодня", "теперь", "снова", "опять",
                "и", "а", "можешь", "можно", "отслеживай", "новостную", "новостной", "биржевые", "биржевыми",
                "больше", "вообще", "пока", "сейчас"}
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
# Торговые команды — сразу программе (раньше «купи пять пик…» шло в облачный мозг: 2–5 с и риск ответа словами
# «покупаю, сэр» без действия). Только в начале фразы: «стоит ли купить сбер?» остаётся вопросом мозгу.
_TRADE_RE = re.compile(r"^(?:нокс[,\s]+)?(?:подготовь|подготовить|купи|продай|закрой|закрыть|выстав\w*|поставь\s+заявк|"
                       r"отправь\s+заявк|исполни|соверши|сделай\s+сделк|открой\s+сделк|шортани|зашорть)(?![а-я])")
# Правка наблюдателей/сценариев голосом: «выключи наблюдатель санкции», «поменяй объём в сценарии газпром на 10 лотов»
_WEDIT_RE = re.compile(r"(?:включи|выключи|отключи|удали|измени|поменяй|смени|поставь|переименуй|переведи)\w*\s.*"
                       r"(?:наблюдател|сценари)")


def route_kind(t):
    """Куда уйдёт фраза — БЕЗ исполнения (для fast_route и голосовых проверок voice_tests):
    news_on / news_off — чтение ленты; vision; memory; watch_edit; watch; trade / local — программе (Rust);
    brain — облачный маршрутизатор."""
    low = (t or "").lower().strip()
    nc = news_command(low)
    if nc is not None:
        return "news_on" if nc else "news_off"
    try:
        import nox_vision
        if nox_vision.VISION_RE.search(low):
            return "vision"
    except Exception:
        pass
    try:
        import nox_memory
        if nox_memory.VOICE_RE.search(low):
            return "memory"
    except Exception:
        pass
    try:
        import nox_feedback
        if nox_feedback.kind(low):
            return "news_feedback"
    except Exception:
        pass
    try:
        import nox_bond_rules
        if nox_bond_rules.VOICE_RE.search(low):
            return "bond_rule"
    except Exception:
        pass
    if _WEDIT_RE.search(low):
        return "watch_edit"
    if _WATCH_RE.search(low):
        return "watch"
    if _TRADE_RE.search(low):
        return "trade"
    if any(k in low for k in _LOCAL_KW):
        return "local"
    return "brain"


def fast_route(t):
    """Быстрый путь без мозга: «наблюдай …» -> наблюдатель, явная команда -> __LOCAL__, иначе None."""
    kind = route_kind(t)
    if kind == "vision":                       # «что на экране?» — снимок в облако, только по запросу
        if not _feat("vision"):
            return "Зрение выключено во вкладке «Фичи», сэр."
        import nox_vision
        r = nox_vision.look(t)
        (nox_scenarios._log if nox_scenarios else print)(f"ЗРЕНИЕ ({r.get('where', '?')}, {r.get('kb', '?')} КБ, {r.get('ms', '?')} мс): {r.get('text', '')[:160]}"
            + (f" | ошибка: {r['error']}" if r.get("error") else ""))
        return r.get("text") or None
    if kind == "bond_rule":                    # «при дефолте самолета продай акции», «какие правила по облигациям»
        try:
            import nox_bond_rules
            return nox_bond_rules.voice(t).get("text", "")
        except Exception as e:
            return f"Не смог разобрать правило по облигациям, сэр: {type(e).__name__}."
    if kind == "news_feedback":                # «такие не читай» / «такие читай всегда» — про последнюю прочитанную
        return news_feedback(t)
    if kind == "memory":                       # «запомни правило: …», «что ты обо мне знаешь», «забудь, что …»
        try:
            import nox_memory
            return nox_memory.voice(t).get("text", "")
        except Exception:
            return None
    if kind == "watch_edit":
        try:
            import nox_watchers
            return nox_watchers.edit_from_voice(t).get("text", "")
        except Exception as e:
            return f"Не смог изменить наблюдателя, сэр: {type(e).__name__}."
    if kind == "watch":
        try:
            import nox_watchers
            return nox_watchers.add_from_voice(t).get("text", "")
        except Exception:
            return None
    if kind in ("trade", "local"):
        return _LOCAL
    return None


_LAST_CTX = {}
_read_last = collections.deque(maxlen=30)       # прочитанные обычные новости: {"ts", "text", "hits"}

_ANSWER_SYS = (
    "Ты — Нокс, голосовой помощник трейдера на Мосбирже. Ответь на ЗАПРОС пользователя по-русски, коротко "
    "(2–4 предложения), по ДАННЫМ ТЕРМИНАЛА: цены, изменения, позиции и новости бери ТОЛЬКО оттуда, ответь про ВСЕ "
    "инструменты из запроса. Скажи, что это значит для позиции и риска. Если для ответа данных не хватает — честно "
    "скажи, чего не хватает, ничего не выдумывай. Учитывай ИСТОРИЮ РАЗГОВОРА, если запрос — её продолжение."
)


def _route_log(act, t, t0, t1):
    if nox_scenarios:
        nox_scenarios._log(f"МОЗГ-РОУТЕР: {act} | данные {int((t1 - t0) * 1000)} мс, мозг "
                           f"{int((time.time() - t1) * 1000)} мс | {t[:90]}")


def news_feedback(t):
    import nox_feedback
    k = nox_feedback.kind(t)
    last = next((x for x in reversed(_read_last) if time.time() - x["ts"] <= nox_feedback.WINDOW), None)
    if not last:
        return "Не помню, о какой новости речь, сэр: за последние десять минут я ничего не читал."
    cfg = _load_filter()
    keys = nox_feedback.apply(cfg, last["text"], last["hits"], k)
    _save_filter(cfg)
    if nox_scenarios:
        nox_scenarios._log(f"РЕАКЦИЯ: {'не читать' if k == 'no' else 'читать'} такие ({', '.join(keys)}) | {last['text'][:110]}")
    txt = nox_feedback.reply(k, keys, last["text"])
    if not cfg.get("smart"):
        txt += " Чтобы это работало, включите умный отбор на вкладке «Новости»."
    return txt


def _brain_request(t):
    """Запрос для мозга: история разговора за 15 минут + живые данные терминала + сам запрос."""
    hist = _dialog_text(t)
    try:
        ctx = _live_context(t)
    except Exception:
        ctx = ""
    _LAST_CTX.update(ctx=ctx, hist=hist, t=t, ts=time.time())
    parts = []
    if hist:
        parts.append(f"ИСТОРИЯ РАЗГОВОРА (последние {DIALOG_MIN} минут):\n{hist}")
    if ctx:
        parts.append(f"ДАННЫЕ ТЕРМИНАЛА (сейчас {time.strftime('%H:%M')} МСК):\n{ctx}")
    parts.append(f"ЗАПРОС: {t}")
    return "\n\n".join(parts)


@app.get("/brain_context")
def brain_context(text: str = ""):
    """Что получит мозг на этот запрос (проверка): история + данные терминала. Мозг НЕ вызывается."""
    return {"request": _brain_request(text), "dialog_rows": len(_dialog), "news_rows": len(_news_recent)}


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
    t0 = time.time()
    try:
        import nox_scenarios as ns
        model = ns._appdb().get("openai_model") or None
        user = _brain_request(t)                  # + история разговора за 15 минут и живые данные терминала
        t1 = time.time()
        if _LAST_CTX.get("ctx"):
            # Вопрос про инструмент, а данные терминала уже есть — маршрутизатор не нужен: ОДИН запрос «ответь по
            # данным» (раньше мозг часто выбирал поиск в интернете: браузер + второй запрос = до 17 с и потеря части
            # вопроса — «что с нефтью и долларом» отвечал только про нефть).
            sysa = (_ANSWER_SYS + ns.SPEECH_RULES + _memory_block())
            ans = (ns._llm_model(sysa, user, model, max_tokens=300) if model else ns._llm(sysa, user, max_tokens=300)) or ""
            _route_log("данные", t, t0, t1)
            if ans.strip():
                return {"text": ans.strip()}
        sysr = _ROUTER_SYS + _memory_block()       # правила общения и факты о пользователе — в текст ответа
        raw = (ns._llm_model(sysr, user, model, max_tokens=260) if model
               else ns._llm(sysr, user, max_tokens=260))
        j = ns._json_from(raw) or {}
    except Exception:
        j, t1 = {}, time.time()
    act = (j.get("action") or "local").lower()
    _route_log(act, t, t0, t1)
    if act == "answer":
        return {"text": (j.get("text") or "").strip() or _LOCAL}
    if act == "web":
        return {"text": web_answer(j.get("query") or t, extra=_LAST_CTX.get("ctx", "")).get("text", "")}
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

@app.get("/filter_why")
def filter_why(phrase: str = "", why: str = ""):
    """Критерий для слова белого списка (зачем добавлено) — умный отбор решает по нему. Пусто — убрать."""
    cfg = _load_filter()
    w = cfg.setdefault("why", {})
    if not phrase.strip():
        return {"ok": True, "why": w}
    if why.strip():
        w[phrase.strip()] = why.strip()
    else:
        w.pop(phrase.strip(), None)
    _save_filter(cfg)
    return {"ok": True, "why": w}


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

_APP_DB = os.path.join(os.environ.get("APPDATA") or os.path.expanduser(r"~\AppData\Roaming"), "com.priler.jarvis", "app.db")
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

# ------------------------------------------------------------------ стрелки «Купить» / «Продать» на оверлее
@app.get("/arrows_get")
def arrows_get():
    import nox_arrows
    cfg = nox_arrows.load()
    labels = {s: [nox_arrows.label(o) for o in cfg[s]["orders"]] for s in nox_arrows.SIDES}
    cs = None
    tab = (cfg.get("cscalp_tab") or "").strip()
    if tab and nox_cscalp:
        # вкладка CScalp: её текущий состав, открыта ли она сейчас, счета CScalp (только показать)
        try:
            hw, st, why = nox_cscalp.tab_window(tab, float("inf"))
            acc = nox_cscalp.accounts()
        except Exception as e:
            hw, st, why, acc = None, [], f"{type(e).__name__}: {e}", {}
        cs = {"tab": tab, "stacks": st, "open": bool(hw), "why": why, "accounts": acc}
        for s in nox_arrows.SIDES:
            act = "купить" if s == "buy" else "продать"
            labels[s] = [l for o, l in zip(cfg[s]["orders"], labels[s]) if o.get("venue") != "cscalp"] + \
                        [f"{act} {t} (CScalp «{tab}»)" for t in st]
    return {"ok": True, **cfg, "labels": labels, "cscalp": cs}

@app.get("/arrows_save")
def arrows_save(json_data: str = ""):
    import nox_arrows
    try:
        cfg = nox_arrows.save(json.loads(json_data or "{}"))
    except Exception as e:
        return {"ok": False, "text": f"Не сохранил стрелки: {e}"}
    return {"ok": True, "text": "Стрелки сохранены.", **cfg}

def _arrow_run(side, dry):
    import nox_arrows
    if side not in nox_arrows.SIDES:
        return {"ok": False, "text": "Нет такой стрелки."}
    cfg = nox_arrows.load()
    if not dry and not cfg[side]["enabled"]:
        return {"ok": False, "text": f"Стрелка «{nox_arrows.RU[side]}» выключена."}
    cs_tab = (cfg.get("cscalp_tab") or "").strip()
    if cs_tab and nox_cscalp:
        # вкладка CScalp: ВСЕ стаканы, какие на ней сейчас (состав — из файла раскладки CScalp, в момент клика);
        # список CScalp-заявок стрелки не используется. Жмёт execute_tab — только в окне, где вкладка открыта
        st, why = nox_cscalp.tab_stacks(cs_tab)
        if st is None:
            return {"ok": False, "text": f"CScalp: {why}."}
        seen, ph = {}, []
        for t in st:
            seen[t] = seen.get(t, 0) + 1
            ph.append({"ticker": t, "side": side, "venue": "cscalp", "n": seen[t]})
        cfg = dict(cfg)
        cfg[side] = {**cfg[side], "orders": [o for o in cfg[side]["orders"] if o.get("venue") != "cscalp"] + ph}
    if not cfg[side]["orders"]:
        return {"ok": False, "text": f"У стрелки «{nox_arrows.RU[side]}» нет заявок."}
    if not _state["ok"] and any(o.get("venue") != "cscalp" for o in cfg[side]["orders"]):
        return {"ok": False, "text": _state.get("err") or "Терминал не подключён."}
    w = nox_arrows.pseudo(cfg, (side,))
    now = time.time()
    res = _watch_exec(w, w["branches"][0], f"ручная стрелка «{nox_arrows.RU[side]}»", dry=dry,
                      meta={"seen": now * 1000, "_detect": now}, cs_tab=cs_tab or None) or {}
    if nox_scenarios:
        nox_scenarios._log(f"СТРЕЛКА «{nox_arrows.RU[side]}»{' (проверка)' if dry else ''}: {res.get('text', '')}")
    return {"ok": True, "text": res.get("text", "")}

@app.get("/arrow_fire")
def arrow_fire(side: str = ""):
    """Нажата стрелка на оверлее: исполнить ВСЕ её заявки сразу (Т-Терминал и CScalp параллельно)."""
    return _arrow_run(side, dry=False)

@app.get("/cscalp_probe")
def cscalp_probe(side: str = "buy"):
    """Проба стрелки в CScalp БЕЗ нажатий: весь путь до клавиш (окно группы, планы, вывод окна вперёд, проверка
    стаканов под точками) с замером шагов и ожидания в очереди рабочего потока."""
    import nox_arrows
    cfg = nox_arrows.load()
    act = "buy_market" if side == "buy" else "sell_market"
    tab = (cfg.get("cscalp_tab") or "").strip()
    pairs = [(o["ticker"], act, o.get("n")) for o in cfg[side]["orders"] if o.get("venue") == "cscalp"]
    if not pairs and not tab:
        return {"ok": False, "text": "у стрелки нет стаканов CScalp"}
    t_put = time.time()
    box = {}
    def job():
        box["queue_ms"] = round((time.time() - t_put) * 1000)
        if tab:
            return nox_cscalp.execute_tab(tab, act, go=True, probe=True)
        return nox_cscalp.execute_orders(pairs, go=True, probe=True)
    r, e = nox_cscalp.submit(job, timeout=60)
    return {"ok": not e, "error": e, "queue_ms": box.get("queue_ms"), "total_ms": round((time.time() - t_put) * 1000),
            "steps": (r or {}).get("steps") or nox_cscalp._last_trace.get("steps"), "result_error": (r or {}).get("error"),
            "targets": [(p.get("ticker"), p.get("hwnd"), p.get("point")) for p in (r or {}).get("results") or []]}

@app.get("/cscalp_trace")
def cscalp_trace():
    """Где сейчас рабочий поток CScalp: шаги последнего execute_orders и стек потока (для зависаний)."""
    import sys, traceback
    th = next((t for t in threading.enumerate() if t.name == "cscalp"), None)
    stack = traceback.format_stack(sys._current_frames().get(th.ident)) if th and th.ident in sys._current_frames() else []
    return {"trace": nox_cscalp._last_trace if nox_cscalp else None, "queue": nox_cscalp._tasks.qsize() if nox_cscalp else None,
            "stack": [s.strip()[:300] for s in stack[-12:]]}

@app.get("/arrow_dry")
def arrow_dry(side: str = ""):
    """Проверка стрелки без нажатий: какие кнопки и стаканы она нажмёт."""
    return _arrow_run(side, dry=True)

@app.get("/arrow_layout")
def arrow_layout():
    """Разложить виджеты Т-Терминала для обеих стрелок на их вкладке (CScalp — стаканы открываете сами)."""
    import nox_arrows
    cfg = nox_arrows.load()
    if not any(o.get("venue") != "cscalp" for s in nox_arrows.SIDES for o in cfg[s]["orders"]):
        return {"ok": True, "text": "У стрелок нет заявок Т-Терминала — раскладывать нечего."}
    if not _state["ok"]:
        return {"ok": False, "text": _state.get("err") or "Терминал не подключён."}
    return _watch_layout(nox_arrows.pseudo(cfg))

@app.get("/watch_inject")
def watch_inject(text: str = "", value: str = "", source: str = ""):
    """Данные из официального API (BLS) — наблюдателям как новость с ГОТОВЫМ числом: ветка выбирается
    сразу, без мозга. Текст начинается с «BLS:» — ключевое слово наблюдателя, лента его не содержит."""
    if not nox_watchers:
        return {"ok": False, "text": "Наблюдатели недоступны."}
    try:
        v = float(str(value).replace(",", "."))
    except Exception:
        v = None
    now = time.time()
    item = {"text": text, "body": "", "tickers": [], "source": source or "API", "seen": now * 1000, "_detect": now,
            "value": v}
    nox_watchers.on_news([item])
    if nox_scenarios:
        nox_scenarios._log(f"API -> наблюдатели: «{text}» значение={v} ({(time.time() - now) * 1000:.0f} мс)")
    return {"ok": True, "value": v}

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

@app.get("/brain_model_set")
def brain_model_set(model: str = ""):
    """Сменить облачную модель мозга (как клик на странице «Мозг»): openai_model в app.db + reload_settings в Нокс.
    Ридер запущен сторожем и видит НАСТОЯЩИЙ app.db (процессы из сессии Claude видят его копию)."""
    model = (model or "").strip()
    cat = brain_models().get("cloud") or []
    if not model or (cat and model not in cat):
        return {"ok": False, "text": f"Модели «{model}» нет в каталоге облака."}
    import nox_scenarios as ns
    try:
        raw = open(ns._APPDB, "rb").read().decode("utf-8-sig")
        d = json.loads(raw)
        old = d.get("openai_model", "")
        d["openai_model"] = model
        tmp = ns._APPDB + ".tmp"
        open(tmp, "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False, indent=2))   # UTF-8 без BOM
        os.replace(tmp, ns._APPDB)
    except Exception as e:
        return {"ok": False, "text": f"Не записал настройки: {type(e).__name__}"}
    try:   # Нокс перечитывает настройки с диска (голосовой мозг brain.rs)
        s = socket.create_connection((IPC_HOST, IPC_PORT), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall((f"GET / HTTP/1.1\r\nHost: {IPC_HOST}:{IPC_PORT}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        s.recv(4096)
        data = json.dumps({"action": "reload_settings"}).encode("utf-8")
        mask = os.urandom(4)
        s.sendall(bytes([0x81, 0x80 | len(data)]) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
        time.sleep(0.2); s.close()
    except Exception:
        pass
    if nox_scenarios:
        nox_scenarios._log(f"МОЗГ: облачная модель {old or '—'} -> {model}")
    return {"ok": True, "old": old, "model": model, "text": f"Мозг переключён на {model}."}


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


def _watch_api_on():
    """Тумблер «Заявки наблюдателей через API» во «Фичах»: только при явном true (тут реальные заявки)."""
    try:
        return bool(nox_features) and nox_features._load().get("watch_api") is True
    except Exception:
        return False

def _watch_exec_api(w, orders, max_lots, max_rub, really):
    """Заявки ветки через T-Invest API: тип (рыночная/лимитная), цена и объём — из виджета «Заявка» на вкладке
    наблюдателя (их меняют в терминале), счёт — выбранный в терминале. Кнопку не нажимаем.
    -> (results в формате press_orders, err)."""
    import nox_tinvest
    plan, err = _submit(lambda pg: nox_widgets.api_plan(pg, w.get("tab"), orders, max_lots, max_rub), timeout=20)
    if err or not plan:
        return None, err or "нет ответа"
    acct = plan.get("account") or ""
    res = []
    for p in plan["plans"]:
        o = p["order"]
        if p.get("bind"):
            o["wid"] = p["bind"]              # виджет найден по тикеру+объёму — дальше узнаём его по id
        item = {"order": o, "ok": bool(p.get("ok")), "widget": p.get("widget"), "api": True}
        if p.get("err"):
            item["err"] = p["err"]
        if not p.get("ok"):
            res.append(item)
            continue
        inst = nox_tinvest.find_instrument(p["ticker"])
        if not inst:
            item.update(ok=False, err=f"нет инструмента {p['ticker']} в каталоге T-Invest")
        elif not acct:
            item.update(ok=False, err="не знаю счёт, выбранный в терминале")
        elif not really:
            item["dry"] = True
        else:
            t0 = time.time()
            try:
                ans = nox_tinvest.post_order(acct, inst, p["side"], p["qty"], p["otype"], p.get("price"))
                st = (ans.get("executionReportStatus") or "").replace("EXECUTION_REPORT_STATUS_", "")
                px = nox_tinvest.q(ans.get("executedOrderPrice"))
                item["t_click"] = t0
                item["api_ms"] = round((time.time() - t0) * 1000)
                item["notes"] = [f"API {st}: {ans.get('lotsExecuted', 0)}/{p['qty']} лот"
                                 + (f" по {px:g}" if px else "") + f", {item['api_ms']} мс"]
                if st in ("REJECTED", "CANCELLED"):
                    item.update(ok=False, err=f"API отклонил: {ans.get('message') or st}")
            except Exception as e:
                item.update(ok=False, err=f"API: {str(e)[:160]}")
        res.append(item)
    return res, None

@app.get("/watch_api_dry")
def watch_api_dry(tab: str = "нокс", ticker: str = "SBER", side: str = "buy", lots: int = 0, wid: str = ""):
    """Проверка без заявки: что Нокс прочитал бы из виджета «Заявка» и что отправил бы через API (PostOrder НЕ зовём)."""
    import nox_tinvest
    o = {"ticker": ticker.upper(), "side": side, "n": 1, "lots": lots or None, "wid": wid or None}
    enabled, max_lots, max_rub = _exec_settings()
    t0 = time.time()
    plan, err = _submit(lambda pg: nox_widgets.api_plan(pg, tab, [o], max_lots, max_rub), timeout=20)
    if err or not plan:
        return {"ok": False, "text": f"терминал: {err}"}
    p = plan["plans"][0]
    inst = nox_tinvest.find_instrument(p.get("ticker") or ticker) or {}
    acct = plan.get("account") or ""
    return {"ok": bool(p.get("ok")), "read_ms": round((time.time() - t0) * 1000), "account": "…" + acct[-4:] if acct else "",
            "widget": p.get("widget"), "wid": p.get("wid"), "type": p.get("otype"), "price": p.get("price"),
            "qty": p.get("qty"), "cost": p.get("cost"), "instrument": {k: inst.get(k) for k in ("ticker", "name", "class", "lot")},
            "why": p.get("err") or "", "api_on": _watch_api_on(), "warm_rtt_ms": nox_tinvest._warm.get("rtt")}

def _watch_exec(w, branch, news_text, dry=False, meta=None, cs_tab=None):
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
            # третий элемент — № стакана с этим тикером (два MIX на вкладке — №1 и №2); группа — из одного окна CScalp
            pairs = [(o["ticker"], "buy_market" if o.get("side") == "buy" else "sell_market", o.get("n"))
                     for o in cs_orders]
            deadline = time.time() + 20      # позже — не жать (опоздавшая рыночная заявка хуже, чем никакой)
            if cs_tab:     # стрелка привязана к вкладке CScalp: её текущий состав и только её окно
                act = "buy_market" if cs_orders[0].get("side") == "buy" else "sell_market"
                job = lambda: nox_cscalp.execute_tab(cs_tab, act, go=really, deadline=deadline)
            else:
                job = lambda: nox_cscalp.execute_orders(pairs, go=really, deadline=deadline)
            def _cs():
                r, e = nox_cscalp.submit(job, timeout=25)
                cs_box.update(res=r, err=e)
            cs_thread = threading.Thread(target=_cs, daemon=True)
            cs_thread.start()
    if orders and _watch_api_on():
        res, err = _watch_exec_api(w, orders, max_lots, max_rub, really)   # пульт — виджет, исполнение — API
    elif orders:
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
    peers = _watcher_tabs().get(w.get("tab") or w.get("name") or "")   # ряды всех наблюдателей вкладки
    res, err = _submit(lambda pg: nox_widgets.layout(pg, w, peers), timeout=180)
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

_persist = {}   # вкладка -> {"chk": когда проверяли, "bad": подряд несохранённых, "said": когда предупредили}

def _persist_watch(tab, lost):
    """Заявки вкладки наблюдателя не сохранены терминалом (его сбой после создания вкладки: до перезагрузки
    страницы он ничего не сохраняет) — при F5 они пропадут, и наблюдателю будет нечего нажимать. Предупредить."""
    st = _persist.setdefault(tab, {"bad": 0, "said": 0})
    st["bad"] = st["bad"] + 1 if lost else 0
    if st["bad"] >= 2 and time.time() - st["said"] > 1800 and not _focus.get("active"):
        st["said"] = time.time()
        _ipc_announce(f"Сэр, терминал не сохранил заявки на вкладке «{tab}» — при перезагрузке страницы они "
                      f"пропадут. Нажмите «Разложить в терминале» у наблюдателя — я перезагружу терминал и разложу заново.")
        nox_scenarios_log(f"СОХРАНЕНИЕ: вкладка «{tab}» — терминал не сохранил {lost}")

def nox_scenarios_log(msg):
    try:
        import nox_scenarios as _ns
        _ns._log(msg)
    except Exception:
        pass

_guard = {"chk": 0.0}   # вкладка -> {"fix": когда восстанавливали, "said": когда предупреждали}

_foreign = {"said": 0.0, "chk": 0.0}

def _foreign_terminals():
    """Окна «Терминал | Т-Инвестиции» НЕ из профиля браузера Нокса (yandex_jarvis): другой экземпляр терминала
    шлёт на сервер Т-Банка свою раскладку вкладок и затирает виджеты наблюдателей (16.10: окно «2: Терминал»
    в обычном профиле Яндекса записало «нокс» с одной лентой — заявки CNRU пропали)."""
    import ctypes
    try:
        import psutil
    except Exception:
        return []
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _cb(h, _l):
        if user32.IsWindowVisible(h):
            n = user32.GetWindowTextLengthW(h)
            if n:
                b = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(h, b, n + 1)
                if "Т-Инвестиции" in b.value and "Терминал" in b.value:
                    pid = ctypes.c_ulong()
                    user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
                    found.append((pid.value, b.value))
        return True

    user32.EnumWindows(_cb, 0)
    out = []
    for pid, title in found:
        try:
            cmd = " ".join(psutil.Process(pid).cmdline())
        except Exception:
            cmd = ""
        if "yandex_jarvis" not in cmd:
            out.append(title)
    return out

def _foreign_watch():
    """Раз в минуту: чужой терминал открыт — только запись в журнал (не чаще раза в 30 мин), БЕЗ голоса:
    пользователь 17.10 — «это напоминание мне не надо» (пропавшие заявки и так восстанавливает сторож)."""
    if time.time() - _foreign["chk"] < 60:
        return
    _foreign["chk"] = time.time()
    try:
        titles = _foreign_terminals()
    except Exception:
        return
    if titles and time.time() - _foreign["said"] > 1800:
        _foreign["said"] = time.time()
        nox_scenarios_log(f"ЧУЖОЙ ТЕРМИНАЛ: открыт вне браузера Нокса — {titles} (может перезаписать раскладку вкладок)")

def _orders_guard(active, saved, tabs):
    """Заявки включённых наблюдателей пропали из раскладки терминала (сброс при перезагрузке страницы).
    Пропали ВСЕ заявки наблюдателя и его вкладка открыта — разложить заново и сказать; вкладка не открыта
    или пропала часть (например, вы поменяли объём руками) — предупредить. Раз в 10/30 мин на вкладку."""
    if _focus.get("active"):
        return                                   # режим тишины перед новостью по времени — ничего не трогаем
    for tab, ws in tabs.items():
        en = [w for w in ws if w.get("enabled", True) and nox_widgets.needed_orders(w)]
        if not en or tab not in saved:
            continue
        st = _guard.setdefault(tab, {"fix": 0, "said": 0})
        if saved[tab] is None:                   # вкладки нет вовсе (удалили) — разложить может только «Разложить»
            if time.time() - st["said"] > 1800:
                st["said"] = time.time()
                _ipc_announce(f"Сэр, вкладки «{tab}» в терминале нет — наблюдателю «{en[0].get('name')}» нечего "
                              f"нажимать. Нажмите «Разложить в терминале».")
            continue
        due = time.time() - st["fix"] > 600      # одно окно на вкладку: восстанавливаем всех её наблюдателей сразу
        for w in en:
            miss = nox_widgets.missing_orders(w, saved[tab])
            if not miss:
                continue
            what = ", ".join(f"{t} ×{q}" for t, q in miss)
            whole = len(miss) == len(nox_widgets.needed_orders(w))
            if whole and tab == active and due:
                st["fix"] = time.time()
                nox_scenarios_log(f"СТОРОЖ ЗАЯВОК: вкладка «{tab}» — пропали {what}, раскладываю заново")
                r = _watch_layout(w)
                _ipc_announce(f"Сэр, терминал сбросил заявки на вкладке «{tab}» — разложил заново. {r.get('text', '')}")
            elif time.time() - st["said"] > 1800:
                st["said"] = time.time()
                nox_scenarios_log(f"СТОРОЖ ЗАЯВОК: вкладка «{tab}», наблюдатель «{w.get('name')}» — нет {what}")
                tail = ("эти ветки ничего не нажмут. Нажмите «Разложить в терминале» или поправьте объём." if not whole
                        else "не получилось разложить заново — нажмите «Разложить в терминале»." if tab == active
                        else "откройте вкладку, я разложу их заново.")
                _ipc_announce(f"Сэр, на вкладке «{tab}» нет заявок наблюдателя «{w.get('name')}»: {what} — {tail}")

def _persist_wids(bind):
    """[(id наблюдателя, № ветки, № заявки, id виджета)] -> записать в nox_watchers.json. Под замком срабатывания:
    иначе запись «наблюдатель сработал/выключен» могла бы затереться старой копией."""
    try:
        with nox_watchers._on_news_lock:
            d = nox_watchers.load()
            changed = False
            for wid_, bi, oi, xid in bind:
                w = next((x for x in d["watchers"] if x.get("id") == wid_), None)
                try:
                    o = w["branches"][bi]["orders"][oi]
                except Exception:
                    continue
                if o.get("wid") != xid:
                    o["wid"] = xid
                    changed = True
            if changed:
                nox_watchers.save(d)
    except Exception:
        pass

def _overlay_loop():
    """Подписи веток на вкладке наблюдателя: перерисовка раз в 3с (виджеты двигают, страницу обновляют).
    Раз в минуту — сверка заявок вкладки с сохранённой раскладкой терминала (_persist_watch) и сторож
    пропавших заявок наблюдателей (_orders_guard)."""
    _ready.wait(120)
    while True:
        try:
            if _state["ok"] and nox_widgets and nox_watchers:
                tabs = _watcher_tabs()
                chk = time.time() - _guard["chk"] > 60
                if chk:
                    _guard["chk"] = time.time()
                def _draw(pg):
                    tab = nox_widgets.active_tab(pg)
                    w = tabs.get(tab)
                    out = {"tab": tab}
                    if w:
                        nox_widgets.draw_overlay(pg, w)
                        # запомнить id виджетов заявок (объём/тип/цену потом можно менять — наблюдатель узнает свой)
                        out["bind"] = nox_widgets.bind_plan(pg, w)
                        if chk:
                            out["unsaved"] = nox_widgets.unsaved(pg)
                    else:
                        nox_widgets.clear_overlay(pg)
                    if chk and tabs:
                        out["saved"] = nox_widgets.saved_tabs(pg, list(tabs))
                    return out
                res, _err = _submit(_draw, timeout=10)
                if res and res.get("bind"):
                    _persist_wids(res["bind"])
                if res and res.get("unsaved") is not None:
                    _persist_watch(res["tab"], res["unsaved"])
                if res and res.get("saved"):
                    _orders_guard(res["tab"], res["saved"], tabs)
                _foreign_watch()
        except Exception:
            pass
        time.sleep(3)

def _nsd_event(ev):
    """Новое сообщение НРД о неисполнении/ненадлежащем исполнении по облигации: наблюдателям и голосом."""
    import nox_nsd
    item = nox_nsd.feed_item(ev)
    say = nox_nsd.summary(ev)
    short = nox_nsd._issuer_short(ev.get("issuer", ""))
    exp = {}
    try:                                         # что у пользователя по эмитенту (только чтение)
        import urllib.request as _ur, urllib.parse as _up
        exp = json.loads(_ur.urlopen("http://127.0.0.1:8124/nsd_exposure?" + _up.urlencode(
            {"isin": ev.get("isin", ""), "issuer": short}), timeout=15).read().decode("utf-8"))
    except Exception:
        exp = {}
    held = bool(exp.get("bond") or exp.get("issuer_bonds") or (exp.get("share") or {}).get("qty"))
    if exp.get("text"):
        say = f"{say} {exp['text']}"
    rules = []
    try:
        import nox_bond_rules
        rules = [r for r in nox_bond_rules.load()["rules"] if nox_bond_rules.matches(r, ev, short)]
    except Exception:
        rules = []
    if nox_scenarios:
        nox_scenarios._log(f"НРД: {say} | {ev.get('isin')} | {ev.get('url')} | правил: {len(rules)}")
    if rules:
        threading.Thread(target=_bond_rules_run, args=(rules, ev, exp, say), daemon=True).start()
    if _feat("watchers") and nox_watchers:
        try:
            nox_watchers.on_news([item], "trading")     # сценарии по облигациям: «НРД» + эмитент/ISIN + статус
        except Exception:
            pass
    try:
        # свои бумаги или правило «предупредить» — озвучиваем, даже если чтение ленты выключено
        important = held or any(r.get("action") == "alert" for r in rules)
        if not _focus.get("active") and not _is_dup(item["text"], smart=False) and (
                important or (_mon_enabled() and not _excluded(item, _load_filter()))):
            _ipc_announce(("Внимание! " if important else "") + say)
    except Exception:
        pass


def _bond_rules_run(rules, ev, exp, say):
    """Сценарии по облигациям: заявки — тем же путём, что голосовые (fill_order: тумблер исполнения и лимиты)."""
    import nox_bond_rules
    for r in rules:
        if r.get("action") == "alert":
            continue
        orders, why = nox_bond_rules.plan(r, ev, exp)
        if not orders:
            _ipc_announce(f"Правило по облигациям: {why}")
            continue
        for o in orders:
            try:
                res = fill_order(q=o["q"], side=o.get("side", "sell"), qty=int(o.get("qty") or 1), price=0.0,
                                 close=int(o.get("close") or 0), exec=1 if r.get("exec") else 0,
                                 said=f"правило по облигациям {r.get('issuer')}: {say[:120]}")
                txt = res.get("text", "")
            except Exception as e:
                txt = f"не смог: {type(e).__name__}"
            if nox_scenarios:
                nox_scenarios._log(f"НРД-ПРАВИЛО: {nox_bond_rules.describe(r)} | {o} | {txt[:160]}")
            _ipc_announce(f"Правило по облигациям: {why} {txt}")


@app.get("/nsd_status")
def nsd_status():
    """Здоровье опроса НРД: последний успешный проход, ошибки, перезапуски вкладки."""
    try:
        import nox_nsd
        st = dict(nox_nsd.STATUS)
    except Exception as e:
        return {"ok": False, "text": repr(e)}
    now = time.time()
    st["ago_s"] = int(now - st["last_ok"]) if st.get("last_ok") else None
    st["alive"] = nox_nsd.tab_alive()
    st["ok"] = bool(st.get("last_ok")) and now - st["last_ok"] < 15 * 60
    return st

# ------------------------------------------------------------------ копирование сделок трейдера (nox_copytrade)
def _copy_after(sig, rec):
    """После решения по сигналу: журнал заявок и голос (в своём потоке — канал копирования не ждёт)."""
    name = _tk_name(sig.get("ticker") or "")
    verb = sig.get("verb", "")
    if rec.get("side"):
        _journal(said=f"копирование {sig.get('trader')}: {sig.get('title', '')[:120]}",
                 plan={"ticker": sig.get("ticker"), "side": rec["side"], "lots": rec.get("qty") or 1, "venue": "api-copy"},
                 exec=True, sent=bool(rec.get("sent")), reason=rec.get("why") or "",
                 note=f"{rec.get('status', '')} цена {rec.get('price')} его {rec.get('his_price')} "
                      f"за {rec.get('total_ms')} мс".strip())
    if rec.get("sent"):
        import nox_copytrade
        done = nox_copytrade.say_action(rec.get("action") or ("buy" if rec["side"] == "buy" else "sell"), rec.get("qty") or 1)
        say = f"{sig.get('trader')} {verb} {name}. {done[:1].upper() + done[1:]}."
    elif rec.get("dry"):
        say = f"{sig.get('trader')} {verb} {name}. Заявку не отправил: {rec.get('why')}."
    else:
        say = f"{sig.get('trader')} {verb} {name}. {rec.get('why') or 'Пропуск'}."
    if not _focus.get("active"):
        threading.Thread(target=_ipc_announce, args=(say,), daemon=True).start()

def _copy_loop():
    _ready.wait(120)
    try:
        import nox_copytrade
    except Exception:
        return
    log = (lambda m: nox_scenarios._log(m)) if nox_scenarios else print
    nox_copytrade.run({"log": log, "exec_settings": _exec_settings, "after": _copy_after,
                       "feat": _copy_on})

def _copy_on():
    """Тумблер «Повтор сделок ProfitKing» во «Фичах»: включено ТОЛЬКО при явном true (нет ключа/ошибка — выключено,
    в отличие от остальных фич: тут реальные заявки)."""
    try:
        return bool(nox_features) and nox_features._load().get("copytrade") is True
    except Exception:
        return False

@app.get("/copy_get")
def copy_get():
    """Копирование сделок: настройки, счёт, позиции от копирования, последние сигналы, состояние канала."""
    import nox_copytrade as ct
    return {**ct.status(), "exec": _exec_settings()}

@app.get("/copy_save")
def copy_save(enabled: int = -1, trader: str = "", buy: int = -1, close: int = -1, fresh_min: int = 0,
              account_suffix: str = "", lots: int = 0, short: int = -1):
    import nox_copytrade as ct
    d = {}
    if lots > 0: d["lots"] = lots
    if short >= 0: d["short"] = bool(short)
    if enabled >= 0: d["enabled"] = bool(enabled)
    if buy >= 0: d["buy"] = bool(buy)
    if close >= 0: d["close"] = bool(close)
    if trader.strip(): d["trader"] = trader.strip()
    if fresh_min > 0: d["fresh_min"] = fresh_min
    if account_suffix.strip(): d["account_suffix"] = account_suffix.strip()[-4:]
    return {"ok": True, "cfg": ct.save_cfg(d)}

@app.get("/copy_pos")
def copy_pos(ticker: str, lots: int = 0):
    """Поправить позицию от копирования вручную (0 — забыть: «закрыл» её больше не продаст)."""
    import nox_copytrade as ct
    st = ct.load_state()
    tk = ticker.upper().strip()
    if lots > 0:
        st["positions"][tk] = 1
    else:
        st["positions"].pop(tk, None)
    ct.save_state(st)
    return {"ok": True, "positions": st["positions"]}

@app.get("/copy_dry")
def copy_dry(ticker: str = "GAZP", verb: str = "купил"):
    """Проверка без сделки: счёт, инструмент, цена и лимиты — всё, что перед заявкой. Заявка НЕ отправляется."""
    import nox_copytrade as ct
    try:
        return ct.call(ct.dry_plan(ticker, verb, _exec_settings), timeout=20)
    except Exception as e:
        return {"ok": False, "text": f"{type(e).__name__}: {e}"}

def _nsd_loop():
    """НРД (nsddata.ru) — бережный опрос своим подключением к браузеру Нокса (nox_nsd.run)."""
    _ready.wait(120)
    try:
        import nox_nsd
    except Exception:
        return
    log = (lambda m: nox_scenarios._log(m)) if nox_scenarios else print
    nox_nsd.run(_nsd_event, enabled=lambda: _feat("nsd"), quiet=lambda: bool(_focus.get("active")), log=log)

def _cscalp_arm_loop():
    """Стаканы CScalp из включённых наблюдателей — держать сверенными (nox_cscalp сверяет их в простое)."""
    while True:
        try:
            if nox_cscalp and nox_watchers and not _feat("cscalp"):
                nox_cscalp.arm(set(), watch=False)   # «Фичи»: CScalp выключен — не сверять стаканы и окна
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
_F5_CKPT = r"C:\jarvis-voice\models\ru\F5TTS_v1_Base_v2\model_last_inference.safetensors"
@app.get("/voice_list")
def voice_list():
    opts = [{"engine": "silero", "speaker": s, "label": f"Silero — {s}"} for s in _SILERO_SPK]
    if os.path.exists(_XTTS_FLAG):   # XTTS показываем только когда он реально работает
        opts.append({"engine": "xtts", "speaker": "", "label": "XTTS — голос Джарвиса"})
    if os.path.exists(_F5_CKPT):     # F5 (видеокарта NVIDIA) — когда модель на месте
        opts.append({"engine": "f5", "speaker": "", "label": "F5 — голос Джарвиса"})
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
