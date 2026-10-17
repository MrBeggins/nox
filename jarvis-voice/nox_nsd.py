# -*- coding: utf-8 -*-
"""
НРД (nsddata.ru, «Новости ЦКИ»): сообщения о неисполнении / ненадлежащем исполнении обязательств по облигациям.

Формат сообщения (пример: https://nsddata.ru/ru/news/view/1450163):
  заголовок страницы «REDM/BN ПАО "ГК "Самолет" (RU000A10CZA1)» — код КД, эмитент, ISIN;
  таблицы: «Реквизиты корпоративного действия» (Код типа КД REDM / Тип «Погашение облигаций» / Дата КД (план.)),
  «Информация о ценных бумагах» (ISIN, номинал, остаточный номинал), «Текущая выплата по КД» (сумма на 1 ц.б.,
  дата поступления денег в НРД, дата передачи депонентам) и главное —
  «Сведения о неисполнении/ненадлежащем исполнении эмитентом обязанности по выплате»:
    «Не исполнена эмитентом в срок, исполнена ненадлежащим образом» — деньги пришли с опозданием (техдефолт);
    «Не исполнена эмитентом в срок» — просрочка (техдефолт, денег в срок нет);
    в сообщениях «О корпоративном действии …» (ДИСК) — раздел «20.8 Информация о неисполнении обязательств
    эмитента по облигациям» (эмитент сам раскрыл неисполнение).
Коды КД: INTR — купон, REDM — погашение, PRED/DRAW — частичное погашение (амортизация), BPUT/MCAL — оферта /
досрочное погашение.

Сайт закрыт от программ (403, проверка браузера, «Access Blocked» при частых запросах), поэтому читаем его
вкладкой в браузере Нокса (CDP 9222) — своим подключением, в своём потоке, бережно: два поисковых запроса
(полнотекстовый поиск сайта по формулировкам неисполнения) раз в 90 с и тело только новых сообщений.
"""
import json
import os
import re
import threading
import time
import urllib.request

STATE = r"C:\jarvis-voice\nsd_state.json"
CDP_URL = "http://127.0.0.1:9222"
BASE = "https://nsddata.ru"
QUERIES = ["Не исполнена эмитентом", "неисполнении обязательств"]
POLL = 90            # с между проходами
MAX_NEW = 6          # тел сообщений за проход (остальные — в следующий)
PASS_LIMIT = 120     # проход дольше — вкладка зависла: сторож закрывает её, опрос открывает заново
RELOAD_EVERY = 3 * 3600   # вкладку — перезагружать раз в 3 ч (память страницы растёт)

# Состояние опроса — для /nsd_status и проверки здоровья Нокса
STATUS = {"started": 0.0, "last_ok": 0.0, "last_pass_ms": 0, "passes": 0, "events": 0, "tab_restarts": 0,
          "last_err": "", "blocked_until": 0.0}

KIND = {"INTR": "выплата купона", "REDM": "погашение облигаций", "PRED": "частичное погашение (амортизация)",
        "DRAW": "частичное погашение", "BPUT": "выкуп по оферте", "MCAL": "досрочное погашение",
        "CALL": "досрочное погашение"}

# ------------------------------------------------------------------ разбор
_MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7, "августа": 8,
           "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}


def _date(s):
    """«28 сентября 2026 г.» -> (2026, 9, 28) или None."""
    m = re.search(r"(\d{1,2})\s+([а-я]+)\s+(\d{4})", s or "")
    if not m or m.group(2) not in _MONTHS:
        return None
    return int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1))


def _fields(tables):
    """Таблицы страницы -> {подпись: значение}: строки «подпись ¦ значение» и пары «строка подписей / строка значений»."""
    out = {}
    for rows in tables:
        rows = [[c for c in r] for r in rows if any(c.strip() for c in r)]
        for r in rows:
            if len(r) == 2:
                out.setdefault(r[0], r[1])
        hdr = next((r for r in rows if len(r) >= 3), None)
        if hdr:
            vals = [r for r in rows if len(r) == len(hdr) and r is not hdr]
            if vals:
                out.update({h: v for h, v in zip(hdr, vals[0]) if h and h not in out})
        if len(rows) >= 2 and len(rows[0]) == 1 and len(rows[1]) == 1:   # заголовок таблицы + одно значение
            out.setdefault(rows[0][0], rows[1][0])
    return out


def status_of(text):
    """Статус выплаты по тексту сообщения: late | overdue | default | disclosed | ok."""
    t = (text or "").lower().replace("ё", "е")
    if "исполнена ненадлежащим образом" in t:
        return "late"
    if "не исполнена эмитентом в срок" in t:
        return "overdue"
    if re.search(r"не исполнена эмитентом|неисполнени\w* обязательств", t) and "20.8" not in t:
        return "default"
    if "20.8" in t and "неисполнени" in t:
        return "disclosed"
    return "ok"


STATUS_RU = {"late": "выплата с опозданием — не исполнено в срок, исполнено ненадлежащим образом (технический дефолт)",
             "overdue": "не исполнено в срок (технический дефолт)",
             "default": "обязательство не исполнено (дефолт)",
             "disclosed": "эмитент раскрыл неисполнение обязательств по облигациям",
             "ok": "исполнено"}


def parse(page):
    """page = {id, title, text, tables} (из VIEW_JS) -> событие НРД (dict) или None."""
    title = (page.get("title") or "").strip()
    text = page.get("text") or ""
    f = _fields(page.get("tables") or [])
    m = re.match(r"([A-Z]{4})(?:/\w+)?\s+(.*?)\s*\((RU[0-9A-Z]{10})\)\s*$", title)
    code = (m.group(1) if m else (re.search(r"\(([A-Z]{4})\)", text) or [None, ""])[1]) or ""
    issuer = (m.group(2) if m else "").strip()
    isin = m.group(3) if m else (re.search(r"RU[0-9A-Z]{10}", text) or [""])[0]
    if not issuer:
        mi = re.search(r"эмитента\s+(.+?)\s+ИНН", text)
        issuer = mi.group(1).strip() if mi else ""
    st = status_of(" ".join([f.get("Сведения о неисполнении/ненадлежащем исполнении эмитентом обязанности по выплате", ""),
                             text]))
    amount = f.get("Размер денежных средств, подлежащих выплате на 1 ц.б.") or f.get("Размер купонного дохода в RUB") or ""
    try:
        amount = float(str(amount).replace(",", ".").split()[0]) if amount else None
    except Exception:
        amount = None
    plan = _date(f.get("Дата КД (план.)", ""))
    got = _date(f.get("Дата поступления в НРД денежных средств", ""))
    series = f.get("Серия выпуска облигаций", "")
    if not series:
        ms = re.search(r"сери[ия]\s+([0-9A-Za-zА-Яа-я\-]+)", f.get("Наименование ценной бумаги", "") + " " + text)
        series = ms.group(1) if ms else ""
    return {"id": page.get("id"), "url": f"{BASE}/ru/news/view/{page.get('id')}", "code": code,
            "kind": KIND.get(code, f"корпоративное действие {code}".strip()), "issuer": issuer, "isin": isin,
            "series": series, "status": st, "status_ru": STATUS_RU[st], "amount": amount, "plan": plan, "paid": got,
            "late_days": ((time.mktime(got + (0, 0, 0, 0, 0, -1)) - time.mktime(plan + (0, 0, 0, 0, 0, -1))) // 86400
                          if plan and got else None)}


def _issuer_short(name):
    """«ПАО "ГК "Самолет"» -> «Самолет», «Банк ВТБ (ПАО)» -> «ВТБ» (для голоса): последний кусок в кавычках."""
    n = re.sub(r"\((?:ПАО|АО|ООО|ЗАО|НАО)\)", "", (name or "")).strip()
    parts = [x.strip() for x in n.split('"') if x.strip()]
    n = parts[-1] if len(parts) > 1 else re.sub(r"^(ПАО|АО|ООО|ЗАО|НАО|МКПАО)\s+", "", n)
    return re.sub(r"^(ГК|Группа компаний|Группа|Банк)\s+", "", n).strip() or name


def _days(n):
    n = int(n)
    w = "день" if n % 10 == 1 and n % 100 != 11 else "дня" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "дней"
    return f"{n} {w}"


def summary(ev):
    """Фраза для голоса и ленты: «НРД: Самолет, серия БО-П20 — погашение облигаций: выплачено с опозданием
    на 1 день, технический дефолт.»"""
    who = _issuer_short(ev["issuer"]) + (f", серия {ev['series']}" if ev.get("series") else "")
    st = {"late": "выплачено с опозданием" + (f" на {_days(ev['late_days'])}" if ev.get("late_days") else "")
                  + ", технический дефолт",
          "overdue": "не исполнено в срок, технический дефолт",
          "default": "не исполнено, дефолт",
          "disclosed": "эмитент сообщил о неисполнении обязательств",
          "ok": "исполнено"}[ev["status"]]
    return f"НРД: {who} — {ev['kind']}: {st}."


def feed_item(ev):
    """Новость для наблюдателей: в тексте — «НРД», эмитент, ISIN и исходная формулировка статуса."""
    raw = {"late": "не исполнена эмитентом в срок, исполнена ненадлежащим образом",
           "overdue": "не исполнена эмитентом в срок", "default": "не исполнена эмитентом",
           "disclosed": "неисполнение обязательств эмитента по облигациям", "ok": "исполнена"}[ev["status"]]
    now = time.time()
    return {"text": f"НРД: {ev['issuer']} ({ev['isin']}) — {ev['kind']}: {raw}", "body": summary(ev),
            "tickers": [ev["isin"]] if ev.get("isin") else [], "source": "НРД", "seen": now * 1000, "_detect": now,
            "nsd": ev}


# ------------------------------------------------------------------ опрос сайта через браузер Нокса
LIST_JS = r"""async (q) => {
  const ac = new AbortController(); setTimeout(() => ac.abort(), 25000);   // запрос не висит дольше 25 с
  const r = await fetch('/ru/news?text=' + encodeURIComponent(q), {credentials: 'include', signal: ac.signal});
  const h = await r.text();
  if (r.status !== 200 || /Access Blocked/.test(h)) return {blocked: true, status: r.status};
  const d = new DOMParser().parseFromString(h, 'text/html');
  return {items: [...d.querySelectorAll('a[href*="/news/view/"]')].map(a => ({id: a.getAttribute('href').split('/').pop(),
          title: (a.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 300)}))};
}"""

VIEW_JS = r"""async (id) => {
  const ac = new AbortController(); setTimeout(() => ac.abort(), 25000);
  const r = await fetch('/ru/news/view/' + id, {credentials: 'include', signal: ac.signal});
  const h = await r.text();
  if (r.status !== 200 || /Access Blocked/.test(h)) return {blocked: true, status: r.status};
  const d = new DOMParser().parseFromString(h, 'text/html');
  d.querySelectorAll('script, style, noscript').forEach(e => e.remove());
  const tables = [...d.getElementsByTagName('table')].map(t => [...t.querySelectorAll('tr')]
    .map(r => [...r.children].map(c => (c.textContent || '').replace(/\s+/g, ' ').trim())));
  return {id, title: ((d.querySelector('title') || {}).textContent || '').replace(/\s+/g, ' ').trim(),
          text: (d.body.textContent || '').replace(/[ \t\r]+/g, ' ').replace(/\n\s*\n+/g, '\n').slice(0, 20000), tables};
}"""


def _load():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {"seen": [], "events": []}


def _save(st):
    st["seen"] = st["seen"][-3000:]
    st["events"] = st["events"][-300:]
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def _tab():
    """Вкладка nsddata.ru (id, ws) по CDP или None."""
    try:
        tabs = json.load(urllib.request.urlopen(CDP_URL + "/json/list", timeout=5))
    except Exception:
        return None
    return next((t for t in tabs if t.get("type") == "page" and "nsddata.ru" in (t.get("url") or "")), None)


def tab_alive(timeout=3):
    """True — вкладка отвечает на Runtime.evaluate за timeout с; False — зависла; None — вкладки нет."""
    t = _tab()
    if not t:
        return None
    try:
        from websockets.sync.client import connect
        with connect(t["webSocketDebuggerUrl"], open_timeout=timeout, max_size=None) as ws:
            ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": "1"}}))
            ws.recv(timeout=timeout)
        return True
    except Exception:
        return False


def close_tab(log=print, why="зависла"):
    t = _tab()
    if not t:
        return False
    try:
        urllib.request.urlopen(f"{CDP_URL}/json/close/{t['id']}", timeout=5).read()
        STATUS["tab_restarts"] += 1
        log(f"НРД: вкладка {why} — закрыл, открою заново")
        return True
    except Exception:
        return False


def run(on_event, enabled=lambda: True, quiet=lambda: False, log=print):
    """Цикл опроса (в своём потоке, своё подключение к браузеру). on_event(ev) — новое сообщение о неисполнении."""
    from playwright.sync_api import sync_playwright
    st = _load()
    seen = set(st["seen"])
    first = not seen                 # самый первый запуск — только запомнить текущее, не объявлять старое
    backoff = 0
    opened = 0                       # сколько раз подряд открывали вкладку nsddata.ru
    STATUS["started"] = time.time()
    pass_t = [0.0]                   # начало текущего прохода (0 — не идёт)

    def _guard():
        """Сторож: проход завис (evaluate не вернулся) — закрыть вкладку; ожидание в playwright оборвётся ошибкой."""
        while True:
            time.sleep(10)
            if pass_t[0] and time.time() - pass_t[0] > PASS_LIMIT:
                pass_t[0] = 0.0
                close_tab(log, f"не ответила за {PASS_LIMIT} с")
    threading.Thread(target=_guard, daemon=True).start()
    while True:
        try:
            if tab_alive() is False:          # зависшая вкладка мешает и опросу, и подключению ридера терминала
                close_tab(log)
                time.sleep(3)
            with sync_playwright() as p:
                br = p.chromium.connect_over_cdp(CDP_URL, timeout=15000)
                ctx = br.contexts[0]
                pg = next((x for c in br.contexts for x in c.pages if "nsddata.ru" in (x.url or "")), None)
                if pg is None:
                    # вкладку — через /json/new браузера: ctx.new_page() в этом Яндексе падает («Cannot read
                    # properties of undefined (reading '_page')», 16.10), а вкладку, открытую ПОСЛЕ подключения,
                    # playwright не видит — открыть и переподключиться
                    opened += 1
                    if opened > 3:
                        opened = 0
                        raise RuntimeError("не открылась вкладка nsddata.ru")
                    import urllib.request
                    urllib.request.urlopen(urllib.request.Request(CDP_URL + "/json/new?" + BASE + "/ru/news",
                                                                  method="PUT"), timeout=15).read()
                    time.sleep(5)                        # проверка браузера сайта ставит куку
                    try:                                 # терминал — на передний план (фоновую вкладку браузер тормозит)
                        tabs = json.load(urllib.request.urlopen(CDP_URL + "/json", timeout=5))
                        tid = next((t["id"] for t in tabs if t.get("type") == "page"
                                    and "tbank.ru/terminal" in (t.get("url") or "")), None)
                        if tid:
                            urllib.request.urlopen(CDP_URL + "/json/activate/" + tid, timeout=5).read()
                    except Exception:
                        pass
                    continue
                opened = 0
                loaded = time.time()
                while True:
                    if backoff:
                        STATUS["blocked_until"] = time.time() + backoff
                        time.sleep(backoff); backoff = 0
                    if not enabled() or quiet():
                        time.sleep(POLL); continue
                    if time.time() - loaded > RELOAD_EVERY:
                        pg.goto(BASE + "/ru/news", timeout=30000); time.sleep(5); loaded = time.time()
                    if tab_alive() is False:
                        close_tab(log); raise RuntimeError("вкладка nsddata.ru зависла")
                    pass_t[0] = time.time()
                    new = []
                    for q in QUERIES:
                        r = pg.evaluate(LIST_JS, q)
                        if r.get("blocked"):
                            log(f"НРД: сайт ограничил запросы ({r.get('status')}) — пауза 10 мин")
                            backoff = 600; break
                        for it in r.get("items") or []:
                            if it["id"] not in seen and it["id"] not in [x["id"] for x in new]:
                                new.append(it)
                        time.sleep(2)
                    if backoff:
                        pass_t[0] = 0.0
                        continue
                    if first:
                        seen.update(x["id"] for x in new); first = False
                        st["seen"] = sorted(seen); _save(st)
                        log(f"НРД: запомнил {len(new)} прошлых сообщений, слежу за новыми")
                        STATUS.update(last_ok=time.time(), passes=STATUS["passes"] + 1); pass_t[0] = 0.0
                        time.sleep(POLL); continue
                    for it in sorted(new, key=lambda x: int(x["id"]) if x["id"].isdigit() else 0)[:MAX_NEW]:
                        page = pg.evaluate(VIEW_JS, it["id"])
                        if page.get("blocked"):
                            backoff = 600; break
                        seen.add(it["id"])
                        ev = parse(page)
                        if ev and ev["status"] != "ok":
                            st["events"].append({**ev, "at": time.strftime("%Y-%m-%d %H:%M:%S")})
                            on_event(ev)
                        time.sleep(3)
                    st["seen"] = sorted(seen); _save(st)
                    STATUS.update(last_ok=time.time(), last_pass_ms=int((time.time() - pass_t[0]) * 1000),
                                  passes=STATUS["passes"] + 1, events=len(st["events"]), last_err="")
                    pass_t[0] = 0.0
                    time.sleep(POLL)
        except Exception as e:
            pass_t[0] = 0.0
            STATUS["last_err"] = f"{type(e).__name__}: {str(e)[:120]}"
            log(f"НРД: ошибка опроса — {type(e).__name__}: {str(e)[:120]}")
            time.sleep(15 if "зависла" in str(e) or "closed" in str(e).lower() else 60)
