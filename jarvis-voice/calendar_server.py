"""Jarvis Economic Calendar reader — investing.com в реальном времени.

Держит РЕАЛЬНЫЙ (видимый, но за экраном) браузер Chromium на странице
экономического календаря investing.com (Cloudflare проходит только реальный
браузер). Отдаёт факт/прогноз/пред. по названию индикатора и умеет ждать выхода
цифры (poll) и озвучивать её через ~1с после публикации.

Запуск: python calendar_server.py  ->  http://127.0.0.1:8125
ВНИМАНИЕ: браузер стартует только в интерактивной сессии пользователя.
Резервный источник — FMP API (если задан ключ fmp_key в app.db).
"""
import threading, queue, time, json, re, os, urllib.request, urllib.parse, ssl
from fastapi import FastAPI
import uvicorn

CAL_URL = "https://www.investing.com/economic-calendar/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
APP_DB = r"C:\Users\Дамир\AppData\Roaming\com.priler.jarvis\app.db"

# JS: извлечь все события календаря
EXTRACT_JS = r"""
() => {
  const rows = [...document.querySelectorAll('tr[class*="datatable-v2_row"]')];
  const out = [];
  for (const r of rows) {
    const tds = [...r.querySelectorAll('td')];
    if (tds.length < 6) continue;
    const vals = [...r.querySelectorAll('td[class*="align-end"]')].map(td => td.textContent.trim());
    if (vals.length < 3) continue;
    const nameCell = r.querySelector('td[class*="w-full"]');
    let name = nameCell ? nameCell.textContent.trim() : '';
    name = name.replace(/\s*Act:.*/i, '').replace(/\s*Cons:.*/i, '').trim();
    if (!name) continue;
    let time = '', cur = '';
    for (const td of tds) {
      const t = td.textContent.trim();
      if (!time && /^\d{1,2}:\d{2}$/.test(t)) time = t;
      if (!cur && /^[A-Z]{2,3}$/.test(t)) cur = t;
    }
    out.push({ id: r.id || '', time, cur, name, actual: vals[0] || '', forecast: vals[1] || '', previous: vals[2] || '' });
  }
  return out;
}
"""

# ------------------------------------------------------------------ Playwright worker
_cmd_q = queue.Queue()
_ready = threading.Event()
_state = {"ok": False, "err": "", "browser": ""}

LAUNCH_ARGS = [
    "--window-position=-32000,-32000", "--window-size=1280,900",
    "--disable-blink-features=AutomationControlled", "--disable-infobars",
    "--no-first-run", "--no-default-browser-check",
]
# Порядок браузеров: системный Edge -> системный Chrome -> встроенный chromium.
# Системные (channel) надёжнее: встроенный chromium иногда «Executable doesn't exist».
CHANNELS = ["msedge", "chrome", None]
# Постоянный профиль: один раз проходит Cloudflare challenge в реальной сессии,
# хранит cookie cf_clearance -> дальше страница открывается сразу.
PROFILE_DIR = r"C:\jarvis-voice\browser_profile"
REFRESH_SEC = 600  # период фонового обновления страницы, когда нет запросов

def _is_closed_err(e):
    s = str(e).lower()
    return "has been closed" in s or "target page" in s or "browser has been closed" in s or "closed" in s and "context" in s

def _launch_persistent(p):
    """Открыть постоянный контекст в системном браузере; вернуть (context, page)."""
    last = None
    for ch in CHANNELS:
        try:
            kw = dict(user_data_dir=PROFILE_DIR, headless=False, args=LAUNCH_ARGS,
                      user_agent=UA, viewport={"width": 1280, "height": 900},
                      locale="en-US", ignore_https_errors=True)
            if ch:
                kw["channel"] = ch
            ctx = p.chromium.launch_persistent_context(**kw)
            _state["browser"] = ch or "chromium"
            return ctx
        except Exception as e:
            last = e
            continue
    raise last or RuntimeError("no browser available")

def _worker():
    from playwright.sync_api import sync_playwright
    os.makedirs(PROFILE_DIR, exist_ok=True)
    attempt = 0
    while True:  # авто-повтор: если браузер упал/не поднялся — пробуем снова
        attempt += 1
        try:
            with sync_playwright() as p:
                ctx = _launch_persistent(p)
                pg = ctx.pages[0] if ctx.pages else ctx.new_page()
                pg.goto(CAL_URL, wait_until="domcontentloaded", timeout=60000)
                # дождаться реальных строк (или прохождения Cloudflare) до ~30с
                deadline = time.time() + 30
                while time.time() < deadline:
                    try:
                        n = pg.evaluate("()=>document.querySelectorAll('tr[class*=\"datatable-v2_row\"]').length")
                    except Exception:
                        n = 0
                    if n:
                        break
                    pg.wait_for_timeout(1000)
                _state["ok"] = True
                _state["err"] = ""
                _ready.set()
                last_refresh = time.time()
                while True:
                    try:
                        fn, resp = _cmd_q.get(timeout=30)
                    except queue.Empty:
                        # периодическое обновление страницы (свежесть за многочасовое ожидание)
                        if time.time() - last_refresh > REFRESH_SEC:
                            last_refresh = time.time()
                            try:
                                pg.goto(CAL_URL, wait_until="domcontentloaded", timeout=60000)
                                pg.wait_for_timeout(3000)
                            except Exception as e:
                                if _is_closed_err(e):
                                    raise
                        continue
                    try:
                        resp["result"] = fn(pg)
                    except Exception as e:
                        resp["error"] = str(e)
                        # страница/браузер закрылись -> выйти и пересоздать браузер
                        if _is_closed_err(e):
                            resp["done"].set()
                            raise
                    resp["done"].set()
                    last_refresh = time.time()  # команда сама «освежила» страницу
        except Exception as e:
            _state["ok"] = False
            _state["err"] = f"try {attempt}: {e}"
            _ready.set()
            time.sleep(8)  # подождать и повторить (перезапуск браузера)

def _submit(fn, timeout=40):
    resp = {"done": threading.Event()}
    _cmd_q.put((fn, resp))
    if not resp["done"].wait(timeout):
        return None, "timeout"
    return resp.get("result"), resp.get("error")

_worker_started = False

def start_worker():
    """Запустить браузер-воркер один раз (вызывается при старте сервера,
    НЕ при импорте — иначе конфликт за профиль браузера)."""
    global _worker_started
    if not _worker_started:
        _worker_started = True
        threading.Thread(target=_worker, daemon=True).start()

# ------------------------------------------------------------------ matching
# Страна: русский корень -> коды валют/страны (cur) и подстроки названия (name).
COUNTRY = {
    "сша": {"cur": ["usd", "us"], "name": ["united states"]},
    "америк": {"cur": ["usd", "us"], "name": ["united states"]},
    "фрс": {"cur": ["usd", "us"], "name": ["united states", "fed"]},
    "фед": {"cur": ["usd", "us"], "name": ["united states", "fed"]},
    "британи": {"cur": ["gbp"], "name": ["united kingdom"]},
    "англи": {"cur": ["gbp"], "name": ["united kingdom"]},
    "германи": {"cur": ["eur"], "name": ["germany"]},
    "еврозон": {"cur": ["eur"], "name": ["euro"]},
    "европ": {"cur": ["eur"], "name": ["euro"]},
    "япони": {"cur": ["jpy"], "name": ["japan"]},
    "кита": {"cur": ["cny"], "name": ["china"]},
    "росси": {"cur": ["rub"], "name": ["russia"]},
    "канад": {"cur": ["cad"], "name": ["canada"]},
    "австрали": {"cur": ["aud"], "name": ["australia"]},
    "швейцари": {"cur": ["chf"], "name": ["switzerland"]},
    "франци": {"cur": ["eur"], "name": ["france"]},
    "италь": {"cur": ["eur"], "name": ["italy"]},
    "испани": {"cur": ["eur"], "name": ["spain"]},
    "новозеланд": {"cur": ["nzd"], "name": ["new zealand"]},
    "мексик": {"cur": ["mxn"], "name": ["mexico"]},
}
# Индикатор: правила (стемы запроса -> группа подстрок названия события).
# Правило срабатывает, если ВСЕ его стемы есть в нормализованном запросе (в любой
# морфологической форме, т.к. стемы короткие). Порядок = от специфичного к общему;
# первое сработавшее правило с непустым результатом выигрывает. GROUP = все подстроки
# должны присутствовать в названии события; для правила пробуется по группам.
INDICATOR_RULES = [
    # --- энергоносители (EIA) ---
    (["импорт", "нефт"], [["crude oil imports"]]),
    (["добыч", "нефт"], [["crude oil production"]]),
    (["кушинг"], [["cushing"]]),
    (["дистиллят"], [["distillate"]]),
    (["производств", "бензин"], [["gasoline production"]]),
    (["запас", "бензин"], [["gasoline inventories"]]),
    (["бензин"], [["gasoline inventories"], ["gasoline"]]),
    (["запас", "газ"], [["natural gas"]]),
    (["запас", "нефт"], [["crude oil inventories"], ["crude oil"]]),
    (["сырой нефт"], [["crude oil inventories"]]),
    (["нефт"], [["crude oil inventories"], ["crude oil"]]),
    # --- ставки ---
    (["ставк"], [["interest rate decision"]]),
    (["fomc"], [["interest rate decision"]]),
    (["фомс"], [["interest rate decision"]]),
    # --- макро ---
    (["валов"], [["gdp"]]),
    (["внутренний продукт"], [["gdp"]]),
    (["ввп"], [["gdp"]]),
    (["gdp"], [["gdp"]]),
    # ИЦП/PPI — «индекс цен производителей» (ставим ДО общего «промышленн»)
    (["цен производ"], [["ppi"]]),
    (["производ цен"], [["ppi"]]),
    (["производител"], [["ppi"]]),
    (["ппц"], [["ppi"]]),
    (["ицп"], [["ppi"]]),
    (["ppi"], [["ppi"]]),
    (["ппи"], [["ppi"]]),
    # ИПЦ/CPI
    (["потребительск"], [["cpi"]]),
    (["ипц"], [["cpi"]]),
    (["cpi"], [["cpi"]]),
    (["инфляц"], [["cpi"]]),
    (["безработиц"], [["unemployment rate"], ["unemployment"]]),
    (["занятост"], [["employment change"], ["employment"]]),
    (["нонфарм"], [["nonfarm payrolls"], ["nonfarm"]]),
    (["нон фарм"], [["nonfarm payrolls"], ["nonfarm"]]),
    (["payrolls"], [["nonfarm payrolls"], ["payrolls"]]),
    (["pmi"], [["pmi"]]),
    (["деловой активност"], [["pmi"]]),
    (["розничн"], [["retail sales"]]),
    (["промышленн"], [["industrial production"]]),
    (["торгов", "баланс"], [["trade balance"]]),
    (["настроени"], [["sentiment"]]),
    (["доверия"], [["confidence"]]),
]
PERIOD = {"год к году": "yoy", "годовая": "yoy", "г к г": "yoy",
          "месяц к месяцу": "mom", "м к м": "mom", "квартал": "qoq"}

def _norm(s): return re.sub(r"[^0-9a-zа-яё ]+", " ", (s or "").lower())

def _country_ok(e, spec):
    if not spec:
        return True
    cur = (e.get("cur", "") + " " + e.get("id", "")).lower()
    nm = e.get("name", "").lower()
    if any(c == cur.strip() or c in cur.split() or c in cur for c in spec["cur"]):
        return True
    if any(n in nm for n in spec["name"]):
        return True
    return False

def _period_ok(e, period):
    nm = e.get("name", "").lower().replace(" ", "")
    if period == "yoy":
        return "yoy" in nm or "y/y" in nm or "год" in nm
    if period == "mom":
        return "mom" in nm or "m/m" in nm
    if period == "qoq":
        return "qoq" in nm or "q/q" in nm or "квартал" in nm
    return True

def match_events(events, q):
    qn = _norm(q)
    # страна (по вхождению корня)
    spec = None
    for k, v in COUNTRY.items():
        if k in qn:
            spec = v
            break
    # период
    period = ""
    for k, v in PERIOD.items():
        if k in qn:
            period = v
            break
    # индикатор: первое правило, все стемы которого есть в запросе
    groups = None
    for stems, grps in INDICATOR_RULES:
        if all(s in qn for s in stems):
            groups = grps
            break
    if groups is None:
        # без индикатора — только по стране (иначе пусто, чтобы не вернуть всё)
        if spec:
            return sorted([e for e in events if _country_ok(e, spec)],
                          key=lambda e: len(e.get("name", "")))
        return []
    for group in groups:
        res = []
        for e in events:
            nm = e.get("name", "").lower()
            if all(sub in nm for sub in group) and _country_ok(e, spec) and _period_ok(e, period):
                res.append(e)
        if res:
            # предпочесть точное короткое имя (без лишних слов вроде "cushing"/"api weekly")
            res.sort(key=lambda e: len(e.get("name", "")))
            return res
    return []

# ------------------------------------------------------------------ FMP fallback
def _fmp_key():
    try:
        return json.load(open(APP_DB, encoding="utf-8")).get("fmp_key", "").strip()
    except Exception:
        return ""

_FMP_CTX = ssl.create_default_context()
_FMP_CTX.check_hostname = False
_FMP_CTX.verify_mode = ssl.CERT_NONE

def fmp_event(q):
    """Резерв: экономический календарь через FMP (нужен ключ fmp_key в app.db)."""
    key = _fmp_key()
    if not key:
        return None
    import datetime
    today = datetime.date.today().isoformat()
    url = f"https://financialmodelingprep.com/api/v3/economic_calendar?from={today}&to={today}&apikey={key}"
    try:
        with urllib.request.urlopen(url, timeout=15, context=_FMP_CTX) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    # привести к формату событий и сопоставить
    evs = [{"id": (e.get("country", "")), "cur": e.get("country", ""), "name": e.get("event", ""),
            "actual": ("" if e.get("actual") is None else str(e.get("actual"))),
            "forecast": ("" if e.get("estimate") is None else str(e.get("estimate"))),
            "previous": ("" if e.get("previous") is None else str(e.get("previous")))}
           for e in data]
    m = match_events(evs, q)
    return m[0] if m else None

# ------------------------------------------------------------------ HTTP
app = FastAPI(title="Jarvis Economic Calendar")

@app.on_event("startup")
def _on_startup():
    start_worker()

def _events(pg):
    return pg.evaluate(EXTRACT_JS)

@app.get("/health")
def health():
    return {"status": "ok" if _state["ok"] else "starting", "err": _state["err"], "browser": _state["browser"]}

@app.get("/reload")
def reload():
    def job(pg):
        pg.goto(CAL_URL, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(6000)
        return len(pg.evaluate(EXTRACT_JS))
    n, err = _submit(job)
    return {"events": n, "error": err}

try:
    from num2words import num2words as _n2w
except Exception:
    _n2w = None

UNIT_WORDS = {"m": "миллиона", "b": "миллиарда", "k": "тысячи", "t": "триллиона"}
# формы единиц: (1, 2-4, 5+) — для согласования у целых чисел
UNIT_FORMS = {
    "m": ("миллион", "миллиона", "миллионов"),
    "b": ("миллиард", "миллиарда", "миллиардов"),
    "k": ("тысяча", "тысячи", "тысяч"),
    "t": ("триллион", "триллиона", "триллионов"),
}

def _plural_form(n_int, forms):
    n = abs(int(n_int)) % 100
    if 11 <= n <= 14:
        return forms[2]
    d = n % 10
    if d == 1:
        return forms[0]
    if 2 <= d <= 4:
        return forms[1]
    return forms[2]

def _num_to_words(numstr):
    """'-1.600'->'минус одна целая шесть десятых'; '4.00'->'четыре'."""
    if _n2w is None:
        return numstr
    s = (numstr or "").replace(",", ".").replace("−", "-").strip()
    neg = s.startswith("-")
    s = s.lstrip("+-").strip()
    if "." in s:
        s = s.rstrip("0").rstrip(".")   # 1.600->1.6 ; 4.00->4
    if not s or s == ".":
        return ""
    try:
        val = float(s) if "." in s else int(s)
        words = _n2w(val, lang="ru")
    except Exception:
        return numstr
    return ("минус " + words) if neg else words

UNIT_RU = {"m": " млн", "b": " млрд", "k": " тыс", "t": " трлн"}

def fmt_digits(raw):
    """Значение -> компактно ЦИФРАМИ для показа/озвучки: '-0.64M'->'-0.64 млн',
    '4.00%'->'4.00%', '227K'->'227 тыс'. TTS сам прочитает число коротко."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    m = re.match(r"^\s*([+\-−]?[\d.,]+)\s*([%a-zA-Zа-яА-Я]*)", raw)
    if not m:
        return raw
    num = m.group(1).replace("−", "-").replace(",", ".")
    if "." in num:  # убрать хвостовые нули: 0.640 -> 0.64, 1.00 -> 1
        num = num.rstrip("0").rstrip(".")
    if "%" in raw:
        return num + "%"
    u = (m.group(2).lower()[:1]) if m.group(2) else ""
    return num + UNIT_RU.get(u, "")

def fmt_value(raw):
    """Значение календаря -> чистые слова для озвучки ('4.00%'->'четыре процента')."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    m = re.match(r"^\s*([+\-−]?[\d.,]+)\s*([%a-zA-Zа-яА-Я]*)", raw)
    if not m:
        return raw
    num, unit = m.group(1), m.group(2).lower()
    words = _num_to_words(num)
    if not words:
        return raw
    if "%" in raw:
        return words + " процента"
    u = unit[:1] if unit else ""
    if not u or u not in UNIT_FORMS:
        return words
    # целое -> согласование по числу; дробное -> родительный ед. (миллиона/тысячи)
    ns = num.replace(",", ".").replace("−", "-").lstrip("+-")
    is_frac = "." in ns and ns.rstrip("0").rstrip(".") not in ("", ns.split(".")[0])
    if is_frac:
        uw = UNIT_WORDS[u]
    else:
        int_part = ns.split(".")[0] or "0"
        uw = _plural_form(int_part, UNIT_FORMS[u])
    return (words + " " + uw).strip()

@app.get("/event")
def event(q: str = "", field: str = "actual"):
    e = None
    if _state["ok"]:
        evs, err = _submit(lambda pg: match_events(_events(pg), q))
        if not err and evs:
            e = evs[0]
    if e is None:
        # резерв — FMP (если задан ключ)
        e = fmp_event(q)
    if e is None:
        if not _state["ok"]:
            return {"text": "Календарь ещё запускается, сэр. Попробуйте через минуту."}
        return {"text": f"Не нашёл событие «{q}» в календаре, сэр."}
    val = e.get(field) or e.get("actual") or ""
    if not val:
        fc = fmt_digits(e.get("forecast") or "")
        return {"text": (f"Ещё не вышло, прогноз {fc}" if fc else "Данные ещё не вышли, сэр."), "raw": e}
    return {"text": fmt_digits(val) or val, "raw": e}

@app.get("/watch")
def watch(q: str = "", timeout: float = 280.0):
    """Ждать выхода факта: живой опрос (250мс ловит стриминг мгновенно) + периодический
    перезапуск страницы как страховка -> вернуть, как появится (~<1с после обновления)."""
    if not _state["ok"]:
        return {"text": "Календарь ещё запускается, сэр."}
    deadline = time.time() + timeout
    state = {"reload": False}  # первый цикл — без перезапуска (страница уже свежая)
    def job(pg):
        if state["reload"]:
            try:
                pg.goto(CAL_URL, wait_until="domcontentloaded", timeout=60000)
                pg.wait_for_timeout(1200)
            except Exception:
                pass
        # быстрый опрос (250мс) до ~18с за цикл — минимальная задержка детекта
        end = min(time.time() + 18, deadline)
        while time.time() < end:
            evs = match_events(_events(pg), q)
            if evs and (evs[0].get("actual") or "").strip():
                return {"done": True, "e": evs[0]}
            pg.wait_for_timeout(250)
        return {"done": False, "e": (match_events(_events(pg), q) or [None])[0]}
    while time.time() < deadline:
        res, err = _submit(job, timeout=80)
        state["reload"] = True  # последующие циклы перезапускают страницу (страховка)
        if err:
            return {"text": f"Ошибка ожидания, сэр. {err}"}
        if res and res.get("done"):
            e = res["e"]
            return {"text": fmt_digits(e.get("actual")) or e.get("actual"), "raw": e}
        if res and res.get("e") is None:
            return {"text": f"Не нашёл событие «{q}», сэр."}
    e = (res or {}).get("e") or {}
    return {"text": f"Данные по «{q}» так и не вышли за отведённое время, сэр."}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8125, log_level="warning")
