"""Jarvis Invest reader — читает портфель, котировки и инструменты через
официальный T-Invest API (только чтение). Знает весь каталог инструментов
(акции, облигации, фонды, фьючерсы, валюты, индексы), распознаёт по тикеру и
названию, озвучивает цены словами (num2words).

Токен: app.db (invest_token) или env JARVIS_INVEST_TOKEN.
Запуск: pythonw invest_reader.py  ->  http://127.0.0.1:8124
"""
import os, re, json, ssl, time, datetime, threading, urllib.request, urllib.error, urllib.parse, difflib
from num2words import num2words
from fastapi import FastAPI
import uvicorn

APP_DB = os.path.join(os.environ.get("APPDATA") or os.path.expanduser(r"~\AppData\Roaming"), "com.priler.jarvis", "app.db")
CATALOG_FILE = r"C:\jarvis-voice\instruments.json"
BASE = "https://invest-public-api.tinkoff.ru/rest"
CATALOG_TTL = 24 * 3600  # обновлять каталог раз в сутки

try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

_UNVERIFIED = ssl.create_default_context()
_UNVERIFIED.check_hostname = False
_UNVERIFIED.verify_mode = ssl.CERT_NONE
_state = {"unverified": False}

# ------------------------------------------------------------------ token / http
def get_token() -> str:
    t = os.environ.get("JARVIS_INVEST_TOKEN", "").strip()
    if t:
        return t
    try:
        d = json.load(open(APP_DB, encoding="utf-8"))
        return (d.get("invest_token") or "").strip()
    except Exception:
        return ""

def _open(req):
    if _state["unverified"]:
        return urllib.request.urlopen(req, timeout=30, context=_UNVERIFIED)
    try:
        return urllib.request.urlopen(req, timeout=30)
    except urllib.error.URLError as e:
        r = getattr(e, "reason", e)
        if isinstance(r, ssl.SSLError) or isinstance(e, ssl.SSLError):
            _state["unverified"] = True
            return urllib.request.urlopen(req, timeout=30, context=_UNVERIFIED)
        raise

def call(method: str, body: dict, token: str):
    """Запрос к T-Invest API через nox_tinvest: ключ сервера сверяется с закреплённым ДО отправки токена.
    Раньше: сертификат Минцифры не проверялся -> молча без проверки сервера (токен с полным доступом!)."""
    import nox_tinvest
    last = None
    for attempt in range(3):
        try:
            return nox_tinvest.call(method, body)
        except nox_tinvest.PinError:
            raise                               # чужой ключ сервера — не повторять, токен не отправлялся
        except nox_tinvest.ApiError as e:
            # 401/403 (авторизация) и 404 — не ретраим; 429/5xx — временные, повторяем
            if e.status in (429, 500, 502, 503, 504) and attempt < 2:
                last = e; time.sleep(0.4 * (attempt + 1)); continue
            raise
        except Exception as e:
            last = e; time.sleep(0.3 * (attempt + 1))
    raise last

# ------------------------------------------------------------------ money / speech
def money(m) -> float:
    if not m:
        return 0.0
    return float(m.get("units", 0)) + float(m.get("nano", 0)) / 1e9

def _strip_zero_kop(s: str) -> str:
    return s.replace(", ноль копеек", "").replace(" ноль копеек", "").strip()

def rub_spoken(x: float) -> str:
    try:
        return _strip_zero_kop(num2words(round(x, 2), lang="ru", to="currency", currency="RUB"))
    except Exception:
        return f"{x:.2f}"

def num_spoken(x: float, unit_words=("пункт", "пункта", "пунктов")) -> str:
    n = round(x, 2)
    whole = int(n)
    frac = int(round((n - whole) * 100))
    try:
        words = num2words(whole, lang="ru")
    except Exception:
        words = str(whole)
    # склонение единицы
    if unit_words:
        w = whole % 100
        d = whole % 10
        if 11 <= w <= 14: u = unit_words[2]
        elif d == 1: u = unit_words[0]
        elif 2 <= d <= 4: u = unit_words[1]
        else: u = unit_words[2]
        words += " " + u
    if frac:
        # «98,9» -> «девяносто восемь целых девять десятых процента»: дробное число — единица в родительном падеже
        tenths = frac % 10 == 0
        f_val = frac // 10 if tenths else frac
        try:
            fw = num2words(f_val, lang="ru", gender="f") if hasattr(num2words, "__call__") else str(f_val)
        except Exception:
            fw = str(f_val)
        fw = fw.replace("один", "одна").replace("два", "две") if fw.endswith(("один", "два")) else fw
        dw = ("десятая" if f_val % 10 == 1 and f_val % 100 != 11 else "десятых") if tenths else              ("сотая" if f_val % 10 == 1 and f_val % 100 != 11 else "сотых")
        wh = num2words(whole, lang="ru")
        wh = (wh[:-4] + "одна") if wh.endswith("один") else (wh[:-3] + "две") if wh.endswith("два") else wh
        cel = "целая" if whole % 10 == 1 and whole % 100 != 11 else "целых"
        words = f"{wh} {cel} {fw} {dw}" + (" " + unit_words[1] if unit_words else "")
    return words

def plural(n: int, forms):  # forms=(1,2,5)
    w = n % 100; d = n % 10
    if 11 <= w <= 14: return forms[2]
    if d == 1: return forms[0]
    if 2 <= d <= 4: return forms[1]
    return forms[2]

def num_fem(n: int) -> str:
    """Число в женском роде: 1->одна, 2->две (для «позиция», «заявка», «акция»)."""
    s = num2words(int(n), lang="ru")
    if s.endswith("один"):
        s = s[:-4] + "одна"
    elif s.endswith("два"):
        s = s[:-3] + "две"
    return s

# ------------------------------------------------------------------ catalog
CATALOG_TYPES = [
    ("Shares", "share"), ("Indicatives", "index"), ("Etfs", "etf"),
    ("Futures", "future"), ("Currencies", "currency"), ("Bonds", "bond"),
]
TYPE_PRIORITY = {"share": 0, "index": 1, "etf": 2, "future": 3, "currency": 4, "bond": 5}

# популярные разговорные имена -> тикер (у индексов/валют неочевидные тикеры)
ALIASES = {
    # индексы
    "мосбиржа": "IMOEX", "индекс мосбиржи": "IMOEX", "ммвб": "IMOEX", "имоэкс": "IMOEX", "индекс": "IMOEX",
    "ртс": "RTSI", "индекс ртс": "RTSI",
    # валюты (спот)
    "доллар": "USD000UTSTOM", "бакс": "USD000UTSTOM", "usd": "USD000UTSTOM",
    "евро": "EUR_RUB__TOM", "юань": "CNYRUB_TOM", "золото спот": "GLDRUB_TOM", "серебро": "SLVRUB_TOM",
    # акции — сленг/сокращения
    "сбер": "SBER", "сбербанк": "SBER", "сберпреф": "SBERP", "сбер преф": "SBERP",
    "газ": "GAZP", "газпром": "GAZP",
    "лук": "LKOH", "лукойл": "LKOH",
    "роснефть": "ROSN", "рося": "ROSN",
    "новатэк": "NVTK", "новатек": "NVTK", "бспб": "BSPB", "мкб": "CBOM", "дом рф": "DOMRF", "т-банк": "T", "тбанк": "T",
    "норка": "GMKN", "норникель": "GMKN", "норильский никель": "GMKN", "гмк": "GMKN", "гмкн": "GMKN",
    "полюс": "PLZL", "полюс золото": "PLZL",
    "северсталь": "CHMF", "севка": "CHMF",
    "нлмк": "NLMK", "ммк": "MAGN", "магнитка": "MAGN",
    "магнит": "MGNT",
    "втб": "VTBR",
    "тинькофф": "T", "тбанк": "T", "т банк": "T", "т-банк": "T", "тинёк": "T",
    "яндекс": "YDEX", "янд": "YDEX",
    "озон": "OZON", "ozon": "OZON",
    "вк": "VKCO", "вконтакте": "VKCO",
    "мтс": "MTSS",
    "ростелеком": "RTKM", "ртк": "RTKM",
    "алроса": "ALRS", "алмаз": "ALRS",
    "фосагро": "PHOR",
    "татнефть": "TATN", "тата": "TATN", "татнефтьпреф": "TATNP",
    "сургут": "SNGS", "сургутнефтегаз": "SNGS", "сургутпреф": "SNGSP", "сНг преф": "SNGSP",
    "аэрофлот": "AFLT", "афлт": "AFLT",
    "русал": "RUAL",
    "эн плюс": "ENPG", "en+": "ENPG",
    "пик": "PIKK", "самолёт": "SMLT", "самолет": "SMLT", "лср": "LSRG",
    "интер рао": "IRAO", "интеррао": "IRAO",
    "русгидро": "HYDR", "гидра": "HYDR",
    "россети": "FEES", "фск": "FEES",
    "юнипро": "UPRO",
    "позитив": "POSI", "позитив текнолоджис": "POSI",
    "астра": "ASTR",
    "хэдхантер": "HEAD", "хедхантер": "HEAD", "хх": "HEAD",
    "вуш": "WUSH", "whoosh": "WUSH", "самокаты": "WUSH",
    "распадская": "RASP", "мечел": "MTLR", "мечелпреф": "MTLRP",
    "транснефть": "TRNFP", "транса": "TRNFP",
    "фикспрайс": "FIXP", "х5": "X5", "икс пять": "X5", "пятёрочка": "X5",
    "башнефть": "BANE", "камаз": "KMAZ", "белуга": "BELU", "новабев": "BELU",
    "русагро": "RAGR", "сегежа": "SGZH", "черкизово": "GCHE",
    "мосбиржа акция": "MOEX", "биржа": "MOEX",
    "полиметалл": "POLY", "п805": "POLY",
    "детский мир": "DSKY", "globaltrans": "GLTR",
    "займер": "ZAYM", "цифра": "CNRU", "диасофт": "DIAS", "элемент": "ELMT",
    "совкомбанк": "SVCB", "совком": "SVCB", "мкб": "CBOM",
    "мвидео": "MVID", "м видео": "MVID", "ленэнерго": "LSNG",
}

# фьючерсы: разговорная база -> код в названии контракта (MIX-9.26 ...)
FUTURE_BASE = {
    "микс": "MIX", "мх": "MIX", "фьючерс на индекс мосбиржи": "MIX", "фьючерс мосбиржи": "MIX",
    "ри": "RTS", "эрай": "RTS", "фьючерс ртс": "RTS", "фьючерс на ртс": "RTS",
    "си": "Si", "доллар фьючерс": "Si", "фьючерс доллар": "Si",
    "бр": "BR", "брент": "BR", "нефть": "BR", "фьючерс нефть": "BR",
    "сбер фьючерс": "SBRF", "сберфьюч": "SBRF", "фьючерс сбер": "SBRF",
    "газ фьючерс": "GAZR", "фьючерс газпром": "GAZR",
    "юань фьючерс": "CNY", "золото фьючерс": "GOLD", "золото": "GOLD",
    "серебро фьючерс": "SILV", "евро фьючерс": "Eu",
}

def resolve_future(base):
    now = datetime.date.today()
    cands = []
    for it in _INDEX["items"]:
        if it.get("type") != "future":
            continue
        m = re.match(r"^" + re.escape(base) + r"-(\d{1,2})\.(\d{2})\b", it.get("name", ""))
        if m:
            mon, yr = int(m.group(1)), 2000 + int(m.group(2))
            try:
                exp = datetime.date(yr, mon, 20)
            except ValueError:
                continue
            cands.append((exp, it))
    if not cands:
        return None
    active = [c for c in cands if c[0] >= now]
    return (min(active, key=lambda c: c[0]) if active else max(cands, key=lambda c: c[0]))[1]

_INDEX = {"items": [], "by_ticker": {}, "by_figi": {}, "names": [], "ts": 0}
_lock = threading.Lock()

def _norm(s: str) -> str:
    return re.sub(r"[^0-9a-zа-яё ]+", " ", (s or "").lower()).strip()

def build_index(items):
    by_ticker, by_figi, names = {}, {}, []
    for it in items:
        tk = (it.get("ticker") or "").upper()
        if tk:
            by_ticker.setdefault(tk, []).append(it)
        if it.get("figi"):
            by_figi[it["figi"]] = it
        names.append((_norm(it.get("name", "")), it))
    _INDEX.update(items=items, by_ticker=by_ticker, by_figi=by_figi, names=names)

def download_catalog(token):
    items = []
    for svc, typ in CATALOG_TYPES:
        try:
            r = call(f"InstrumentsService/{svc}", {"instrumentStatus": "INSTRUMENT_STATUS_BASE"}, token)
            for i in r.get("instruments", []):
                items.append({
                    "ticker": i.get("ticker", ""), "figi": i.get("figi", ""),
                    "uid": i.get("uid", ""), "name": i.get("name", ""),
                    "type": typ, "class": i.get("classCode", ""),
                    "currency": i.get("currency", "rub"),
                    "lot": int(i.get("lot", 1) or 1),
                })
        except Exception as e:
            print(f"[catalog] {svc} error: {e}")
    if items:
        try:
            json.dump({"ts": time.time(), "items": items},
                      open(CATALOG_FILE, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception:
            pass
    return items

def ensure_catalog(force=False):
    with _lock:
        if _INDEX["items"] and not force and (time.time() - _INDEX["ts"] < CATALOG_TTL):
            return True
        # попробовать кэш с диска
        if not force and os.path.exists(CATALOG_FILE):
            try:
                c = json.load(open(CATALOG_FILE, encoding="utf-8"))
                if c.get("items") and (time.time() - c.get("ts", 0) < CATALOG_TTL):
                    build_index(c["items"]); _INDEX["ts"] = c["ts"]; return True
            except Exception:
                pass
        token = get_token()
        if not token:
            # если есть хоть какой-то кэш — используем его
            if os.path.exists(CATALOG_FILE):
                try:
                    c = json.load(open(CATALOG_FILE, encoding="utf-8"))
                    if c.get("items"):
                        build_index(c["items"]); _INDEX["ts"] = c.get("ts", 0); return True
                except Exception:
                    pass
            return False
        items = download_catalog(token)
        if items:
            build_index(items); _INDEX["ts"] = time.time(); return True
        return False

def _best_by_priority(cands):
    # предпочесть основную биржу (TQBR) и низкий приоритет типа
    return sorted(cands, key=lambda it: (TYPE_PRIORITY.get(it["type"], 9),
                                         0 if it.get("class") in ("TQBR", "") else 1))[0]

_CASE_ENDS = ("ами", "ями", "ом", "ем", "ой", "ей", "ою", "а", "у", "е", "ы", "и", "я", "ю")


def _variants(qn):
    """«газпрома» -> «газпром», «сберу» -> «сбер», «пику» -> «пик»: голос даёт падежи, каталог — именительный."""
    out = [qn]
    if " " not in qn:
        for suf in _CASE_ENDS:
            if qn.endswith(suf) and len(qn) - len(suf) >= 3:
                out.append(qn[: -len(suf)])
    return out


NAMES_FILE = r"C:\jarvis-voice\ticker_names.txt"      # как Нокс называет тикеры вслух (проверено пользователем)
_NAMES = {"mtime": None, "map": {}}


def _spoken_names():
    """«татка» -> TATN, «бэ эс пэ бэ» -> BSPB: так Нокс читает тикеры — так их и говорят. Только для resolve
    (заявки/котировки), не для поиска инструментов в свободной речи: «система», «аптека» там — обычные слова."""
    try:
        mt = os.path.getmtime(NAMES_FILE)
    except OSError:
        return {}
    if _NAMES["mtime"] != mt:
        m = {}
        for line in open(NAMES_FILE, encoding="utf-8"):
            line = line.split("#", 1)[0]
            if "=" not in line:
                continue
            tk, nm = line.split("=", 1)
            nm = _norm(nm)
            if tk.strip() and nm:
                m.setdefault(nm, tk.strip().upper())
                m.setdefault(nm.replace(" ", ""), tk.strip().upper())
        _NAMES.update(mtime=mt, map=m)
    return _NAMES["map"]


def resolve(query: str):
    if not ensure_catalog():
        return None
    q = query.strip()
    qn = _norm(q)
    # «облигации пик» — явно облигация; иначе голосом почти всегда имеют в виду акцию
    bond_wanted = bool(re.search(r"облиг|бонд", qn))
    qn = re.sub(r"\s*\b(облигац\w*|бонд\w*)\b\s*", " ", qn).strip()
    qu = q.upper().replace(" ", "")
    for v in _variants(qn):
        # фьючерсы по разговорной базе (микс, ри, си, бр, сбер фьючерс...)
        if v in FUTURE_BASE:
            fut = resolve_future(FUTURE_BASE[v])
            if fut:
                return fut
        # алиасы акций/индексов/валют -> тикер
        if v in ALIASES and not bond_wanted:
            qu = ALIASES[v].upper()
            break
        spn = _spoken_names()
        sp = next((spn[x] for x in (v, v.replace(" ", ""), v + "а", v + "я") if x in spn), None)   # «татку» -> «татка»
        if sp and not bond_wanted:
            qu = sp
            break
    # 1) точный тикер
    if qu in _INDEX["by_ticker"]:
        return _best_by_priority(_INDEX["by_ticker"][qu])
    if not qn:
        return None
    # 2) по названию: скоринг. Тип весит заметно: «пик» раньше находил облигацию «ПИК-Корпорация 001Р-05»
    #    (короче название — выше балл), и голосовая заявка «купи пять пик» ушла бы в облигации.
    best, best_score, best_raw = None, -1.0, 0.0
    for v in _variants(qn):
        stem = v[: max(3, len(v) - 2)]
        for nm, it in _INDEX["names"]:
            if not nm:
                continue
            if v == nm:
                score = 5.0
            elif v in nm:
                score = 3.0 + len(v) / len(nm)
            else:
                score = difflib.SequenceMatcher(None, v, nm).ratio()
                if nm.startswith(stem):
                    score += 0.35
            if v != qn:
                score -= 0.05                                  # отрезанное окончание — чуть менее уверенно
            raw = score
            pr = TYPE_PRIORITY.get(it["type"], 9)
            if bond_wanted:
                score -= 0 if it["type"] == "bond" else 0.3 * pr + 1.0
            else:
                score -= 0.3 * pr                              # облигация (5) −1.5, фьючерс (3) −0.9
            if score > best_score:
                best_score, best, best_raw = score, it, raw
    return best if best_raw >= 0.6 else None              # порог — по самому совпадению; тип только выбирает

# ------------------------------------------------------------------ живые данные для ответов мозга
# «что с моим Газпромом?», «почему падает нефть?» — найти инструменты во фразе СТРОГО (синонимы, фьючерсы,
# однословные названия акций, латинские тикеры) и дать мозгу цену, изменение за день и позицию.
_GENERIC = {"банк", "группа", "гк", "пао", "ао", "компания", "корпорация", "холдинг", "акции", "акция", "ао", "ап",
            "мкпао", "сзо", "фонд", "индекс", "биржа"}
_NAME1 = {"built": 0, "map": {}}
try:
    import pymorphy3 as _pm
    _MORPH = _pm.MorphAnalyzer()
except Exception:
    _MORPH = None


def _name1_map():
    """«магнит» -> акция Магнит, «аэрофлот» -> Аэрофлот: первое значимое слово названия акции TQBR."""
    if _NAME1["built"] == _INDEX.get("ts") and _NAME1["map"]:
        return _NAME1["map"]
    m = {}
    for it in _INDEX["items"]:
        if it.get("type") != "share" or it.get("class") != "TQBR":
            continue
        toks = [x for x in _norm(it.get("name", "")).split() if x not in _GENERIC]
        if toks and len(toks[0]) >= 4:
            m.setdefault(toks[0], it)
    _NAME1.update(built=_INDEX.get("ts"), map=m)
    return m


def _forms(w):
    out = set(_variants(w))
    if _MORPH and re.fullmatch(r"[а-яё]+", w):
        try:
            out.add(_MORPH.parse(w)[0].normal_form.replace("ё", "е"))
        except Exception:
            pass
    return out


_MARKET_WORDS = {"рынок", "рынка", "рынке", "рынку", "рынком", "мосбиржа", "индекс мосбиржи"}


def _portfolio_brief(token):
    acc = first_account(token)
    p = call("OperationsService/GetPortfolio", {"accountId": acc, "currency": "RUB"}, token)
    total, y = money(p.get("totalAmountPortfolio")), money(p.get("expectedYield"))
    pos = [x for x in p.get("positions", []) if x.get("instrumentType") != "currency"]
    pos.sort(key=lambda x: abs(money(x.get("quantity")) * money(x.get("currentPrice"))), reverse=True)
    parts = [f"Портфель: {_fmt_num(total)} ₽, результат {'+' if y >= 0 else '−'}{_fmt_num(abs(y))} ₽, позиций {len(pos)}"]
    for x in pos[:5]:
        it = _INDEX["by_figi"].get(x.get("figi")) or {}
        nm = it.get("name") or x.get("ticker") or x.get("figi")
        q, py = money(x.get("quantity")), money(x.get("expectedYield"))
        parts.append(f"{nm}: {'лонг' if q > 0 else 'шорт'} {abs(q):g} шт, результат {'+' if py >= 0 else '−'}{_fmt_num(abs(py))} ₽")
    return "; ".join(parts) + "."


def detect_instruments(text, limit=2):
    if not ensure_catalog():
        return []
    toks = re.findall(r"[a-zа-яё0-9]+", (text or "").lower().replace("ё", "е"))
    names = _name1_map()
    found, seen = [], set()

    def add(it):
        if it and it.get("figi") not in seen and len(found) < limit:
            seen.add(it.get("figi")); found.append(it)

    if _MARKET_WORDS & set(toks) or "индекс мосбиржи" in " ".join(toks):
        lst = _INDEX["by_ticker"].get("IMOEX")
        if lst:
            add(_best_by_priority(lst))
    for n in (2, 1):
        for i in range(len(toks) - n + 1):
            ph = " ".join(toks[i:i + n])
            for v in (_forms(ph) if n == 1 else {ph}):
                if len(v) < 3:
                    continue                      # «си», «ри», «бр» в свободной речи — это не фьючерсы («Си Цзиньпин»)
                if v in FUTURE_BASE:
                    add(resolve_future(FUTURE_BASE[v])); break
                if v in ALIASES:
                    lst = _INDEX["by_ticker"].get(ALIASES[v].upper())
                    if lst:
                        add(_best_by_priority(lst)); break
                if n == 1 and v in names:
                    add(names[v]); break
                if n == 1 and re.fullmatch(r"[a-z]{4,5}", v) and v.upper() in _INDEX["by_ticker"]:
                    add(_best_by_priority(_INDEX["by_ticker"][v.upper()])); break
    return found


_PF = {"t": 0.0, "pos": []}


def _positions(token):
    """Позиции счёта (кэш 10 с)."""
    if time.time() - _PF["t"] < 10:
        return _PF["pos"]
    acc = first_account(token)
    p = call("OperationsService/GetPortfolio", {"accountId": acc, "currency": "RUB"}, token)
    _PF.update(t=time.time(), pos=p.get("positions", []))
    return _PF["pos"]


def _fmt_num(x):
    return (f"{x:,.2f}" if abs(x) < 1000 else f"{x:,.1f}").replace(",", " ").replace(".", ",").rstrip("0").rstrip(",")


def context(text: str = ""):
    """Живые данные по инструментам из фразы: цена, изменение к закрытию, позиция пользователя (только чтение)."""
    token = get_token()
    if not token or not text.strip():
        return {"items": [], "text": ""}
    items, lines = [], []
    for inst in detect_instruments(text):
        figi = inst.get("figi") or inst.get("uid")
        row = {"ticker": inst.get("ticker"), "name": inst.get("name"), "type": inst.get("type")}
        try:
            last = last_price(figi, token)
            cp = call("MarketDataService/GetClosePrices", {"instruments": [{"instrumentId": figi}]}, token)
            close = money((cp.get("closePrices") or [{}])[0].get("price"))
            row.update(last=last, close=close)
            unit = " п." if inst.get("type") in ("future", "index") else (" %" if inst.get("type") == "bond" else " ₽")
            s = f"{inst.get('name')} ({inst.get('ticker')}): {_fmt_num(last)}{unit}"
            if close > 0 and last > 0:
                ch = (last / close - 1) * 100
                row["change_pct"] = round(ch, 2)
                s += f", {'+' if ch >= 0 else '−'}{abs(ch):.2f}% к закрытию {_fmt_num(close)}".replace(".", ",")
            pos = next((x for x in _positions(token) if x.get("figi") == figi), None)
            if pos:
                units = money(pos.get("quantity"))
                lot = int(inst.get("lot", 1) or 1)
                avg = money(pos.get("averagePositionPrice"))
                y = money(pos.get("expectedYield"))
                row.update(position=units, avg=avg, pnl=y)
                side = "лонг" if units > 0 else "шорт"
                s += (f". Позиция пользователя: {side} {abs(units):g} шт ({abs(units) / lot:g} лот.), средняя {_fmt_num(avg)}, "
                      f"результат {'+' if y >= 0 else '−'}{_fmt_num(abs(y))} ₽")
            else:
                s += ". Позиции нет"
            lines.append(s + ".")
        except Exception as e:
            lines.append(f"{inst.get('name')} ({inst.get('ticker')}): данные недоступны ({type(e).__name__}).")
        items.append(row)
    if re.search(r"портфел|мои позиц|мой сч[её]т|на сч[её]те|по сч[её]ту|мои акци", text.lower()):
        try:
            lines.append(_portfolio_brief(token))
        except Exception:
            pass
    return {"items": items, "text": "\n".join(lines)}


def nsd_exposure(isin: str = "", issuer: str = ""):
    """Сообщение НРД о неисполнении: что у пользователя по эмитенту (только чтение) — этот выпуск, другие облигации
    эмитента, его акции (цена, изменение, позиция)."""
    token = get_token()
    if not token:
        return {"ok": False, "text": ""}
    ensure_catalog()
    isin = (isin or "").strip().upper()
    short = _norm(issuer or "")
    pos = _positions(token)
    out = {"ok": True, "bond": None, "issuer_bonds": [], "share": None}
    parts = []
    for x in pos:
        it = _INDEX["by_figi"].get(x.get("figi")) or {}
        q = money(x.get("quantity"))
        if not q:
            continue
        if isin and (it.get("ticker") or "").upper() == isin:
            out["bond"] = {"name": it.get("name"), "qty": q}
            parts.append(f"облигации этого выпуска {q:g} шт")
        elif short and it.get("type") == "bond" and short in _norm(it.get("name", "")):
            out["issuer_bonds"].append({"name": it.get("name"), "ticker": it.get("ticker"), "qty": q})
            parts.append(f"другие облигации эмитента ({it.get('name')}) {q:g} шт")
    sh = None
    for key in ([short, short.split()[0]] if short else []):       # «пик корпорация» -> ещё и «пик»
        if len(key) < 3:
            continue
        sh = next((it for nm, it in _INDEX["names"] if it.get("type") == "share" and it.get("class") == "TQBR"
                   and (nm == key or nm.startswith(key + " ") or key in nm.split())), None)
        if not sh:
            sh = _share_outside_catalog(key, token)
        if sh:
            break
    if sh:
        figi = sh.get("figi")
        try:
            last = last_price(figi, token)
            cp = call("MarketDataService/GetClosePrices", {"instruments": [{"instrumentId": figi}]}, token)
            close = money((cp.get("closePrices") or [{}])[0].get("price"))
            row = {"last": last, "change_pct": round((last / close - 1) * 100, 2) if close > 0 and last > 0 else None}
        except Exception:
            row = None
        held = next((money(x.get("quantity")) for x in pos if x.get("figi") == figi), 0)
        out["share"] = {"ticker": sh.get("ticker"), "name": sh.get("name"), "qty": held,
                        "last": (row or {}).get("last"), "change_pct": (row or {}).get("change_pct")}
        s = f"{sh.get('name')} ({sh.get('ticker')})"
        if row and row.get("last"):
            ch = row.get("change_pct")
            s += f" {_fmt_num(row['last'])} ₽" + (f", {'+' if ch >= 0 else '−'}{abs(ch):.1f}%".replace(".", ",") if ch is not None else "")
        if held:
            parts.append(f"акции {s}, {abs(held):g} шт ({'лонг' if held > 0 else 'шорт'})")
        out["share_text"] = f"Акции эмитента: {s}."
    out["text"] = ("У вас: " + "; ".join(parts) + ".") if parts else "Позиций по эмитенту у вас нет."
    if out.get("share_text") and not (out.get("share") or {}).get("qty"):   # держит акции — цена уже в «У вас»
        out["text"] += " " + out["share_text"]
    return out


def _share_outside_catalog(q, token):
    """Акция на TQBR, которой нет в каталоге (торговля через API выключена, а в терминале — есть): только чтение."""
    qn = re.sub(r"\s*\b(облигац\w*|бонд\w*)\b\s*", " ", _norm(q)).strip()
    for v in _variants(qn):
        try:
            r = call("InstrumentsService/FindInstrument", {"query": v, "instrumentKind": "INSTRUMENT_TYPE_SHARE"}, token)
        except Exception:
            continue
        cands = [x for x in r.get("instruments", []) if x.get("classCode") == "TQBR"
                 and (x.get("instrumentType") or "").lower() == "share" and v in _norm(x.get("name", ""))]
        if not cands:
            continue
        x = cands[0]
        try:
            full = call("InstrumentsService/GetInstrumentBy",
                        {"idType": "INSTRUMENT_ID_TYPE_FIGI", "id": x.get("figi")}, token).get("instrument", {})
            lot = int(full.get("lot", 1) or 1)
        except Exception:
            lot = 1
        return {"ticker": x.get("ticker", ""), "figi": x.get("figi", ""), "uid": x.get("uid", ""), "name": x.get("name", ""),
                "type": "share", "class": "TQBR", "currency": "rub", "lot": lot, "api_off": True}
    return None


def resolve_pref(q, token=None):
    """resolve() для голоса: нашлась облигация, а слово «облигации» не звучало — акция того же названия, если есть
    (у PIKK выключена торговля через API, в каталоге её нет, и «котировка пик» давала облигацию ПИК-Корпорации)."""
    inst = resolve(q)
    if inst and inst.get("type") == "bond" and not re.search(r"облиг|бонд", (q or "").lower()):
        sh = _share_outside_catalog(q, token or get_token())
        if sh:
            return sh
    return inst


_NAME_CACHE = {}


def inst_name_any(figi, token):
    """Имя по FIGI, даже если инструмента нет в каталоге (раньше голос читал «BBG004S68BH6»)."""
    nm = inst_name(figi)
    if nm and nm != figi:
        return nm
    if figi not in _NAME_CACHE:
        try:
            r = call("InstrumentsService/GetInstrumentBy", {"idType": "INSTRUMENT_ID_TYPE_FIGI", "id": figi}, token)
            _NAME_CACHE[figi] = (r.get("instrument") or {}).get("name") or figi
        except Exception:
            _NAME_CACHE[figi] = figi
    return _NAME_CACHE[figi]


def last_price(figi, token):
    lp = call("MarketDataService/GetLastPrices", {"instrumentId": [figi]}, token)
    pr = lp.get("lastPrices", [])
    return money(pr[0].get("price")) if pr else 0.0

def price_phrase(inst, price):
    name = inst.get("name") or inst.get("ticker")
    t = inst.get("type")
    if t in ("index", "future"):
        return f"{name}: {num_spoken(round(price))}."
    if t == "bond":
        return f"{name}: {num_spoken(price, unit_words=('процент','процента','процентов'))} от номинала."
    return f"{name}: {rub_spoken(price)}."

# ------------------------------------------------------------------ accounts
def first_account(token):
    acc = call("UsersService/GetAccounts", {}, token)
    accounts = [a for a in acc.get("accounts", []) if a.get("status") != "ACCOUNT_STATUS_CLOSED"] or acc.get("accounts", [])
    if not accounts:
        raise RuntimeError("нет счетов")
    return accounts[0]["id"]

def inst_name(figi):
    it = _INDEX["by_figi"].get(figi)
    return it.get("name") if it else figi

# ------------------------------------------------------------------ HTTP API
app = FastAPI(title="Jarvis Invest Reader")
app.get("/context")(context)      # живые данные для ответов мозга (определена выше)
app.get("/nsd_exposure")(nsd_exposure)   # НРД: что у пользователя по эмитенту (определена выше)

@app.get("/health")
def health():
    return {"status": "ok", "token_set": bool(get_token()), "catalog": len(_INDEX["items"])}

@app.get("/accounts")
def accounts():
    """Только чтение: счета и уровень доступа токена (может ли он торговать). Номер счёта — последние 4 цифры."""
    token = get_token()
    if not token:
        return {"ok": False, "text": "Токен Invest API не задан."}
    try:
        t0 = time.time()
        r = call("UsersService/GetAccounts", {}, token)
        ms = round((time.time() - t0) * 1000)
    except Exception as e:
        return {"ok": False, "text": f"{type(e).__name__}: {str(e)[:150]}"}
    acc = [{"id": "…" + str(a.get("id", ""))[-4:], "name": a.get("name"), "type": a.get("type"),
            "status": a.get("status"), "access": a.get("accessLevel")} for a in r.get("accounts", [])]
    import nox_tinvest
    return {"ok": True, "accounts": acc, "ms": ms, "pinned": (nox_tinvest.load_pin().get("spki_sha256") or "")[:12],
            "can_trade": any(a["access"] == "ACCOUNT_ACCESS_LEVEL_FULL_ACCESS" and a["status"] == "ACCOUNT_STATUS_OPEN"
                             for a in acc)}

@app.get("/reload")
def reload():
    ok = ensure_catalog(force=True)
    return {"ok": ok, "catalog": len(_INDEX["items"])}

@app.get("/portfolio")
def portfolio():
    token = get_token()
    if not token:
        return {"text": "Токен Invest API не задан, сэр. Укажите его на вкладке Т-Терминал."}
    try:
        ensure_catalog()
        acc = first_account(token)
        p = call("OperationsService/GetPortfolio", {"accountId": acc, "currency": "RUB"}, token)
        total = money(p.get("totalAmountPortfolio"))
        y = money(p.get("expectedYield"))
        positions = [pos for pos in p.get("positions", []) if pos.get("instrumentType") != "currency"]
        def pv(pos): return money(pos.get("quantity")) * money(pos.get("currentPrice"))
        positions.sort(key=pv, reverse=True)
        n = len(positions)
        parts = [f"Портфель: {rub_spoken(total)}."]
        if abs(y) >= 1:
            parts.append(("Прибыль " if y >= 0 else "Убыток ") + rub_spoken(abs(y)) + ".")
        parts.append(f"{num_fem(n)} {plural(n, ('позиция','позиции','позиций'))}.".capitalize())
        top = positions[:3]
        if top:
            names = [f"{inst_name_any(pos.get('figi', ''), token)} на {rub_spoken(pv(pos))}" for pos in top]
            parts.append("Крупнейшие: " + "; ".join(names) + ".")
        return {"text": " ".join(parts)}
    except urllib.error.HTTPError as e:
        return {"text": "Токен отклонён, сэр." if e.code in (401, 403) else f"Ошибка биржи, код {e.code}, сэр."}
    except Exception as e:
        return {"text": f"Не удалось получить портфель, сэр. {type(e).__name__}"}

def resolve_strict(query: str):
    """Строгое распознавание (без фаззи) — для решения, инструмент ли это вообще."""
    if not ensure_catalog():
        return None
    qn = _norm(query)
    qu = query.upper().replace(" ", "")
    if qn in FUTURE_BASE:
        return resolve_future(FUTURE_BASE[qn])
    if qn in ALIASES:
        tk = ALIASES[qn].upper()
        if tk in _INDEX["by_ticker"]:
            return _best_by_priority(_INDEX["by_ticker"][tk])
    if qu in _INDEX["by_ticker"]:
        return _best_by_priority(_INDEX["by_ticker"][qu])
    for nm, it in _INDEX["names"]:
        if nm == qn and qn:
            return it
    return None

@app.get("/resolve")
def resolve_ep(q: str = ""):
    return {"found": bool(resolve_strict(q))}

def resolve_future_for(inst, month=0):
    """Фьючерс на бумагу inst (акция) с месяцем экспирации month (0 — ближайший): «LKOH-12.26 Лукойл» -> LKZ6.
    Код в названии контракта = тикер акции (LKOH) или имя бумаги в хвосте названия."""
    tk = (inst.get("ticker") or "").upper()
    nm = _norm(inst.get("name", ""))
    now = datetime.date.today()
    cands = []
    for it in _INDEX["items"]:
        if it.get("type") != "future":
            continue
        m = re.match(r"^([A-Za-z]+)-(\d{1,2})\.(\d{2})\b(.*)$", it.get("name", ""))
        if not m:
            continue
        base, mon, yy, rest = m.group(1).upper(), int(m.group(2)), int(m.group(3)), _norm(m.group(4))
        if base != tk and not (nm and nm in rest):
            continue
        if month and mon != int(month):
            continue
        try:
            cands.append((datetime.date(2000 + yy, mon, 20), it))
        except ValueError:
            continue
    if not cands:
        return None
    active = [c for c in cands if c[0] >= now]
    return (min(active, key=lambda c: c[0]) if active else max(cands, key=lambda c: c[0]))[1]

@app.get("/ticker")
def ticker_ep(q: str = "", kind: str = "", month: int = 0):
    """Название/склонение -> тикер (для наблюдателей: «газпрома» -> GAZP).
    kind=future (+month) — фьючерс на эту бумагу: q=лукойл, month=12 -> LKZ6."""
    if not ensure_catalog() or not q.strip():
        return {"ticker": "", "name": ""}
    inst = resolve(q)
    if inst and kind == "future" and inst.get("type") != "future":
        inst = resolve_future_for(inst, month)
    if not inst:
        return {"ticker": "", "name": ""}
    return {"ticker": (inst.get("ticker") or "").upper(), "name": inst.get("name", ""),
            "lot": inst.get("lot", 1), "type": inst.get("type", "")}

@app.get("/quote")
def quote(q: str = ""):
    token = get_token()
    if not token:
        return {"text": "Токен Invest API не задан, сэр."}
    if not q.strip():
        return {"text": "Уточните инструмент, сэр."}
    try:
        inst = resolve_pref(q, token)
        if not inst:
            return {"text": f"Не нашёл инструмент «{q}», сэр."}
        figi = inst.get("figi") or inst.get("uid")
        price = last_price(figi, token)
        if price <= 0:
            # запасной путь: поиск по API (вдруг другой инструмент торгуется)
            try:
                f = call("InstrumentsService/FindInstrument", {"query": q}, token)
                for cand in f.get("instruments", []):
                    fg = cand.get("figi") or cand.get("uid")
                    pr = last_price(fg, token)
                    if pr > 0:
                        nm = cand.get("name", inst.get("name"))
                        return {"text": f"{nm}: {rub_spoken(pr)}."}
            except Exception:
                pass
            return {"text": f"Цена по «{inst.get('name')}» сейчас недоступна, сэр."}
        return {"text": price_phrase(inst, price)}
    except urllib.error.HTTPError as e:
        return {"text": "Токен отклонён, сэр." if e.code in (401, 403) else f"Ошибка биржи, код {e.code}, сэр."}
    except Exception as e:
        return {"text": f"Не удалось получить котировку, сэр. {type(e).__name__}"}

TRADING_STATUS = {
    "SECURITY_TRADING_STATUS_NORMAL_TRADING": "идут нормальные торги",
    "SECURITY_TRADING_STATUS_NOT_AVAILABLE_FOR_TRADING": "торги недоступны",
    "SECURITY_TRADING_STATUS_OPENING_PERIOD": "период открытия",
    "SECURITY_TRADING_STATUS_OPENING_AUCTION_PERIOD": "идёт аукцион открытия",
    "SECURITY_TRADING_STATUS_CLOSING_AUCTION_PERIOD": "идёт аукцион закрытия",
    "SECURITY_TRADING_STATUS_CLOSING_PERIOD": "период закрытия",
    "SECURITY_TRADING_STATUS_BREAK_IN_TRADING": "перерыв в торгах",
    "SECURITY_TRADING_STATUS_TRADING_AT_CLOSING_AUCTION_PRICE": "торги по цене аукциона закрытия",
    "SECURITY_TRADING_STATUS_DARK_POOL_AUCTION": "аукцион крупных пакетов",
    "SECURITY_TRADING_STATUS_DISCRETE_AUCTION": "идёт дискретный аукцион",
    "SECURITY_TRADING_STATUS_SESSION_ASSIGNED": "сессия назначена",
    "SECURITY_TRADING_STATUS_SESSION_CLOSE": "сессия закрыта",
    "SECURITY_TRADING_STATUS_SESSION_OPEN": "сессия открыта",
    # торги выходного дня и вечер у брокера (дилерский режим Т-Банка)
    "SECURITY_TRADING_STATUS_DEALER_NORMAL_TRADING": "идут торги у брокера (дилерский режим, выходной день или вечер)",
    "SECURITY_TRADING_STATUS_DEALER_BREAK_IN_TRADING": "перерыв в дилерских торгах",
    "SECURITY_TRADING_STATUS_DEALER_NOT_AVAILABLE_FOR_TRADING": "дилерские торги недоступны",
}

@app.get("/orderbook")
def orderbook(q: str = ""):
    token = get_token()
    if not token:
        return {"text": "Токен Invest API не задан, сэр."}
    inst = resolve_pref(q, token)
    if not inst:
        return {"text": f"Не нашёл инструмент «{q}», сэр."}
    try:
        figi = inst.get("figi") or inst.get("uid")
        ob = call("MarketDataService/GetOrderBook", {"instrumentId": figi, "depth": 10}, token)
        bids = ob.get("bids", [])
        asks = ob.get("asks", [])
        if not bids and not asks:
            return {"text": f"Стакан по «{inst['name']}» пуст или торги закрыты, сэр."}
        name = inst["name"]
        parts = [f"Стакан {name}."]
        if bids:
            bp = money(bids[0].get("price")); bq = int(bids[0].get("quantity", 0))
            parts.append(f"Лучший спрос {rub_spoken(bp)}, {num2words(bq, lang='ru')} {plural(bq, ('лот','лота','лотов'))}.")
        if asks:
            ap = money(asks[0].get("price")); aq = int(asks[0].get("quantity", 0))
            parts.append(f"Лучшее предложение {rub_spoken(ap)}, {num2words(aq, lang='ru')} {plural(aq, ('лот','лота','лотов'))}.")
        if bids and asks:
            spread = money(asks[0].get("price")) - money(bids[0].get("price"))
            parts.append(f"Спред {rub_spoken(spread)}.")
        return {"text": " ".join(parts)}
    except Exception as e:
        return {"text": f"Не удалось получить стакан, сэр. {type(e).__name__}"}

@app.get("/status")
def status(q: str = ""):
    token = get_token()
    if not token:
        return {"text": "Токен Invest API не задан, сэр."}
    inst = resolve_pref(q, token)
    if not inst:
        return {"text": f"Не нашёл инструмент «{q}», сэр."}
    try:
        figi = inst.get("figi") or inst.get("uid")
        st = call("MarketDataService/GetTradingStatus", {"instrumentId": figi}, token)
        code = st.get("tradingStatus", "")
        human = TRADING_STATUS.get(code, "статус неизвестен")
        return {"text": f"{inst['name']}: {human}, сэр."}
    except Exception as e:
        return {"text": f"Не удалось получить статус торгов, сэр. {type(e).__name__}"}

@app.get("/order")
def prepare_order(q: str = "", side: str = "buy", qty: int = 1, price: float = 0.0, tp: float = 0.0):
    """Готовит (НЕ выставляет) лимитную заявку. Пользователь выставляет сам."""
    token = get_token()
    if not token:
        return {"text": "Токен Invest API не задан, сэр."}
    inst = resolve(q)
    if not inst:
        return {"text": f"Не нашёл инструмент «{q}», сэр."}
    lot = int(inst.get("lot", 1) or 1)
    shares = qty * lot
    side_word = "покупку" if side == "buy" else "продажу"
    if price <= 0:
        try:
            price = last_price(inst.get("figi") or inst.get("uid"), token)
        except Exception:
            price = 0.0
    total = shares * price
    parts = [f"Готова заявка на {side_word}: {num2words(qty, lang='ru')} {plural(qty, ('лот','лота','лотов'))}"]
    if lot > 1:
        parts.append(f"({num2words(shares, lang='ru')} шт.)")
    parts.append(f"{inst['name']} по цене {rub_spoken(price)}.")
    if total > 0:
        parts.append(f"Сумма {rub_spoken(total)}.")
    if tp > 0:
        pct = (tp - price) / price * 100 if price else 0
        parts.append(f"Тейк-профит {rub_spoken(tp)} ({'плюс' if pct>=0 else 'минус'} {num2words(round(abs(pct)), lang='ru')} процентов).")
    parts.append("Выставьте её сами в терминале, сэр — я сделки не совершаю.")
    return {"text": " ".join(parts)}

NEWS_KEYWORDS = {
    "dividends": ["дивиденд"],
    "offer": ["оферт", "выкуп", "buyback", "тендер"],
    "emission": ["допэмисс", "доп. эмисс", "доп эмисс", "эмисси", "spo", "размещени", "делистинг", "сплит"],
}

def http_get_json(url):
    req = urllib.request.Request(url, headers={"accept": "application/json", "User-Agent": "jarvis"})
    with _open(req) as r:
        return json.loads(r.read().decode("utf-8"))

def moex_news(limit=40):
    url = f"https://iss.moex.com/iss/sitenews.json?iss.meta=off&lang=ru&limit={limit}"
    d = http_get_json(url)
    sn = d.get("sitenews", {})
    cols = sn.get("columns", [])
    rows = sn.get("data", [])
    ti = cols.index("title") if "title" in cols else (1 if cols else 0)
    return [row[ti] for row in rows if len(row) > ti and row[ti]]

def upcoming_dividends(token, q=""):
    today = datetime.date.today()
    frm = today.isoformat() + "T00:00:00.000Z"
    to = (today + datetime.timedelta(days=140)).isoformat() + "T00:00:00.000Z"
    targets = []
    if q.strip():
        inst = resolve(q)
        if inst:
            targets = [(inst.get("figi"), inst.get("name"))]
    else:
        try:
            acc = first_account(token)
            p = call("OperationsService/GetPortfolio", {"accountId": acc, "currency": "RUB"}, token)
            for pos in p.get("positions", []):
                if pos.get("instrumentType") == "share":
                    fg = pos.get("figi")
                    targets.append((fg, inst_name(fg)))
        except Exception:
            pass
    out = []
    for fg, nm in targets[:8]:
        try:
            dv = call("InstrumentsService/GetDividends", {"figi": fg, "from": frm, "to": to}, token)
            for d in dv.get("dividends", []):
                amt = money(d.get("dividendNet"))
                rec = (d.get("recordDate") or "")[:10]
                if amt > 0:
                    out.append(f"{nm}: {rub_spoken(amt)} на акцию, отсечка {rec}")
        except Exception:
            pass
    return out

@app.get("/news")
def news(type: str = "", q: str = ""):
    kind = type
    # Дивиденды — официальный источник (GetDividends)
    if kind == "dividends":
        token = get_token()
        if token:
            divs = upcoming_dividends(token, q)
            if divs:
                return {"text": "Ближайшие дивиденды, сэр. " + "; ".join(divs[:5]) + "."}
    # Общие корп-новости из MOEX
    try:
        items = moex_news(50)
    except Exception:
        return {"text": "Не удалось получить новости, сэр."}
    label = ""
    if kind in NEWS_KEYWORDS:
        kws = NEWS_KEYWORDS[kind]
        items = [t for t in items if any(w in t.lower() for w in kws)]
        label = {"dividends": "по дивидендам", "offer": "по офертам и выкупам",
                 "emission": "по допэмиссиям и размещениям"}.get(kind, "")
    if not items:
        return {"text": f"Свежих новостей {label} не нашёл, сэр." if label else "Свежих новостей не нашёл, сэр."}
    top = " … ".join(items[:4])
    prefix = f"Новости {label}. " if label else "Последние новости биржи. "
    return {"text": prefix + top}

@app.get("/close")
def prepare_close(q: str = ""):
    """Готовит заявку на ЗАКРЫТИЕ позиции (НЕ выставляет). Читает объём из портфеля."""
    token = get_token()
    if not token:
        return {"text": "Токен Invest API не задан, сэр."}
    inst = resolve(q)
    if not inst:
        return {"text": f"Не нашёл инструмент «{q}», сэр."}
    try:
        acc = first_account(token)
        p = call("OperationsService/GetPortfolio", {"accountId": acc, "currency": "RUB"}, token)
        figi = inst.get("figi")
        pos = next((x for x in p.get("positions", []) if x.get("figi") == figi), None)
        if not pos:
            return {"text": f"Позиции по «{inst['name']}» нет, сэр."}
        units = money(pos.get("quantity"))
        if abs(units) < 1e-9:
            return {"text": f"Позиция по «{inst['name']}» пуста, сэр."}
        side = "продажу" if units > 0 else "покупку"
        lot = int(inst.get("lot", 1) or 1)
        lots = int(abs(units) / lot) if lot else int(abs(units))
        price = last_price(figi, token)
        total = abs(units) * price
        parts = [f"Для закрытия позиции {inst['name']} нужна заявка на {side}"]
        parts.append(f"{num2words(lots, lang='ru')} {plural(lots, ('лот','лота','лотов'))}")
        if lot > 1:
            parts.append(f"({num2words(int(abs(units)), lang='ru')} шт.)")
        if price > 0:
            parts.append(f"по цене около {rub_spoken(price)}, сумма примерно {rub_spoken(total)}.")
        else:
            parts.append(".")
        parts.append("Выставьте её сами в терминале, сэр — сделки я не совершаю.")
        return {"text": " ".join(parts)}
    except Exception as e:
        return {"text": f"Не удалось подготовить закрытие, сэр. {type(e).__name__}"}

@app.get("/order_plan")
def order_plan(q: str = "", side: str = "buy", qty: int = 1, price: float = 0.0, close: int = 0):
    """Структурированный план заявки для заполнения виджета (тикер, лоты, цена, тип)."""
    token = get_token()
    if not token:
        return {"ok": False, "text": "Токен Invest API не задан, сэр."}
    inst = resolve(q)
    if not inst:
        return {"ok": False, "text": f"Не нашёл инструмент «{q}», сэр."}
    figi = inst.get("figi") or inst.get("uid")
    lot = int(inst.get("lot", 1) or 1)
    name = inst["name"]
    ticker = (inst.get("ticker") or "").upper()
    # голосом почти всегда имеют в виду акцию: «купи пять пик». Акции ПИК нет в каталоге (у неё выключена
    # торговля через API — apiTradeAvailableFlag=false), и раньше заявка готовилась на облигацию
    # «ПИК-Корпорация 001Р-05». Ищем акцию в полном списке T-Invest (виджет терминала её торгует);
    # облигацию — только если сказано «облигации».
    if inst.get("type") == "bond" and not re.search(r"облиг|бонд", q.lower()):
        sh = _share_outside_catalog(q, token)
        if sh:
            inst = sh
            figi, lot, name, ticker = sh["figi"], sh["lot"], sh["name"], sh["ticker"]
        elif not close:
            return {"ok": False, "text": f"По «{q}» нашёл только облигацию «{name}», акции нет, сэр. "
                                         f"Если нужна облигация — скажите «облигации {q}»."}
    if close:
        try:
            acc = first_account(token)
            p = call("OperationsService/GetPortfolio", {"accountId": acc, "currency": "RUB"}, token)
            pos = next((x for x in p.get("positions", []) if x.get("figi") == figi), None)
        except Exception as e:
            return {"ok": False, "text": f"Не удалось прочитать позицию, сэр. {type(e).__name__}"}
        if not pos:
            return {"ok": False, "text": f"Позиции по «{name}» нет, сэр."}
        units = money(pos.get("quantity"))
        if abs(units) < 1e-9:
            return {"ok": False, "text": f"Позиция по «{name}» пуста, сэр."}
        lots = int(abs(units) / lot) if lot else int(abs(units))
        side = "sell" if units > 0 else "buy"
    else:
        lots = max(1, int(qty or 1))
    px = float(price or 0.0)
    typ = "limit" if px > 0 else "market"
    # последняя цена — для оценки суммы рыночной заявки (лимит голосового исполнения в рублях)
    try:
        last = last_price(figi, token)
    except Exception:
        last = 0.0
    side_word = "покупку" if side == "buy" else "продажу"
    parts = [f"Заявка в терминале: {name}, {num2words(lots, lang='ru')} {plural(lots, ('лот','лота','лотов'))}"]
    if lot > 1:
        parts.append(f"({num2words(lots*lot, lang='ru')} шт.)")
    parts.append(f"по {rub_spoken(px)}" if px > 0 else "по рынку")
    parts.append("на закрытие." if close else f"на {side_word}.")
    parts.append("Проверьте и нажмите Купить или Продать, сэр — сделку я не совершаю.")
    return {"ok": True, "ticker": ticker, "name": name, "figi": figi,
            "lots": lots, "lot": lot, "price": px, "type": typ, "side": side, "last": last,
            "text": " ".join(parts)}

if __name__ == "__main__":
    try:
        ensure_catalog()
    except Exception as e:
        print("[startup] catalog:", e)
    uvicorn.run(app, host="127.0.0.1", port=8124, log_level="warning")
