# -*- coding: utf-8 -*-
"""
Дедупликация новостей для Nox: одна и та же новость часто приходит повторно — из разных источников,
в другом регистре (КАПС / обычный), короче или длиннее («Трамп: новый пресс-секретарь проведёт брифинг»
и «Трамп заявил, что новый пресс-секретарь Белого дома проведёт свой первый брифинг во вторник»).
is_dup(text) запоминает озвученные новости до конца дня (не меньше 2 часов) и считает дублями похожие:
  * сравнение без регистра, без подписи источника («— Reuters», «· ИНТЕРФАКС», «BRIEF»);
  * совпадение ключевых слов (грубый стем) по Жаккару ИЛИ по вложенности (короткая версия длинной);
  * разные числа (суммы, проценты) -> НЕ дубль: «дивиденды 35 руб» и «дивиденды 40 руб» — разные новости;
  * не похоже словами — смысловой судья (Qwen 7B в Ollama) про до 2 ближайших прочитанных («с Москвой и Пекином»
    = «с Россией и Китаем»).
"""
import time, re, threading, json, urllib.request, datetime as _dt

_TTL = 3 * 3600   # окно памяти, сек (3 часа) — повторы в ленте бывают и через час
_MIN_KEEP = 2 * 3600   # просьба пользователя 17.10: помнить прочитанное до конца дня, но не меньше 2 часов


def _keep_until(ts):
    """Новость помним до конца её дня (23:59:59), но не меньше 2 часов — вечерняя доживёт до ночи."""
    d = _dt.datetime.fromtimestamp(ts)
    eod = _dt.datetime(d.year, d.month, d.day, 23, 59, 59).timestamp()
    return max(eod, ts + _MIN_KEEP)

# ------------------------------------------------------------------ смысловой судья (Ollama, локально)
# Словами не ловятся пересказы: «с Россией и Китаем» / «с Москвой и Пекином», «не будет предметом переговоров» /
# «исключил обсуждение», «противоракеты» / «ПВО». Кандидатов (до 2) отбираем по общим словам, решает Qwen 7B.
# Замер 17.10 на 170 размеченных парах: ловит 35 из 56 повторов, ни одной разной новости не склеил, ~0.15 с на пару.
# Не ответил за JUDGE_TIMEOUT — считаем «не повтор» (лучше прочитать лишний раз, чем пропустить новость).
JUDGE_MODEL = "qwen2.5:7b"
JUDGE_TIMEOUT = 1.2
JUDGE_MAX = 2
_JUDGE_SYS = ("Ты фильтр повторов в ленте новостей для трейдера. Даны две новости, вторая пришла позже. "
              "Ответь ДА, если обе сообщают об одном и том же событии или заявлении одного и того же человека или "
              "организации — даже другими словами, короче или длиннее, из другого источника, с мелкими подробностями "
              "(Москва=Россия, Пекин=Китай, Вашингтон=США, Еврокомиссия=ЕС). "
              "Ответь НЕТ, если говорит другой человек, речь о другой компании или стране, другое решение, другое "
              "число в главном факте или это другое заявление на ту же тему. Только одно слово: ДА или НЕТ.")
judge_enabled = True
last_judge = {}      # последнее решение судьи — для лога ридера


def judge(a, b, timeout=JUDGE_TIMEOUT):
    """True/False — решение Qwen, None — недоступен/не успел."""
    body = {"model": JUDGE_MODEL, "stream": False, "keep_alive": -1, "options": {"temperature": 0, "num_predict": 3},
            "messages": [{"role": "system", "content": _JUDGE_SYS}, {"role": "user", "content": f"Первая: {a}\nВторая: {b}"}]}
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        r = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))
        return (r.get("message", {}).get("content") or "").strip().upper().startswith("ДА")
    except Exception:
        return None


def warm_judge():
    """Загрузить модель судьи в видеопамять заранее (холодный старт ~5 с, а судья ждёт только 1.2 с)."""
    judge("Тест.", "Тест.", timeout=90)


def _candidates(words, nums, k=JUDGE_MAX):
    """До k прочитанных ранее новостей с общими словами (≥2 слова и ≥25% короткой) — для судьи."""
    out = []
    for (t, s, sn, se, ss, x, _u) in _seen:
        if nums and sn and not (nums & sn):
            continue                         # в обеих числа, и они разные — это разные новости, судью не спрашиваем
        inter = len(words & s)
        if inter >= 2 and inter / (min(len(words), len(s)) or 1) >= 0.25:
            out.append((inter / min(len(words), len(s)), t, x))
    out.sort(key=lambda r: (-r[0], -r[1]))
    return [x for _sc, _t, x in out[:k]]
_THR = 0.5        # Жаккар: доля общих ключевых слов от всех
_OVL = 0.75       # вложенность: доля слов КОРОТКОЙ версии, найденных в длинной
_STEM = 5         # длина стема (русская морфология: «пресс-секретарь/секретаря» -> «секре»)
_lock = threading.Lock()
_seen = []        # [(ts, слова, числа, имена собственные, (направление, отрицание), text)]

_STOP = set((
    "и в во не на с со по за из у о от до как что это так же бы для при об а но или если то над под про между "
    "рбк интерфакс прайм тасс ведомости коммерсант рейтер reuters bloomberg блумберг смарт смартлаб смартлаба "
    "сегодня вчера завтра года году руб рублей акции акций компания компании рынок рынка новости новость "
    "сообщает сообщил сообщила заявил заявила заявили пишет источник источники отчеты отчёты брифинг brief "
    "срочно молния breaking update обновление свой своей свои который которая которые также ранее "
    "веб сайт против отношение связь связать связанный"
).split())

# Слова — по словарной форме (pymorphy3): «Ираном» и «Ирана» -> «иран» (грубый стем в 5 букв их различал:
# «ирано» / «ирана» — четыре заголовка о санкциях США против Ирана звучали подряд, 15.10). Участники события
# (страны, организации, фамилии, латиница, крупные эмитенты) — отдельно: у одной новости они те же.
try:
    import pymorphy3
    _M = pymorphy3.MorphAnalyzer()
except Exception:
    _M = None
_LEM = {}
_ENT_TAGS = ("Geox", "Orgn", "Surn", "Name", "Abbr")
_SHORT = {"ес", "цб", "рф", "оаэ", "сша", "мвф", "фрс", "мид", "оон", "нато", "втб", "мтс"}
_COMPANIES = set((
    "сбербанк сбер газпром лукойл норникель роснефть новатэк сургутнефтегаз татнефть полюс пик магнит яндекс озон "
    "мосбиржа аэрофлот северсталь нлмк ммк алроса фосагро русал система россети русгидро лента самолет позитив "
    "газпромнефть транснефть ростелеком мечел селигдар юнипро башнефть совкомфлот циан хэдхантер ozon x5 тинькофф "
    "т-банк совкомбанк мтс-банк акрон куйбышевазот сегежа сибур росатом"
).split())
# видовые пары глаголов новостей: «вводят» и «ввели» — одно событие
_ASPECT = {"ввести": "вводить", "повысить": "повышать", "понизить": "понижать", "снизить": "снижать",
           "сохранить": "сохранять", "объявить": "объявлять", "заявить": "заявлять", "купить": "покупать",
           "продать": "продавать", "запретить": "запрещать", "отменить": "отменять", "выплатить": "выплачивать",
           "утвердить": "утверждать", "одобрить": "одобрять", "расширить": "расширять", "продлить": "продлевать",
           "снять": "снимать", "смягчить": "смягчать", "ужесточить": "ужесточать", "начать": "начинать"}
# синонимы: «Соединённые Штаты» = «США», «министерство финансов» = «Минфин», OFAC — минфин США
_SYN = [(r"соединенн\w*\s+штат\w*(?:\s+америк\w*)?", "сша"), (r"министерств\w*\s+финанс\w*", "минфин"),
        (r"ofac|офак", "минфин сша"), (r"евросоюз\w*|европейск\w*\s+союз\w*", "ес"),
        (r"белый\s+дом\w*|белого\s+дома", "белыйдом"), (r"банк\w*\s+росси\w*", "цб"),
        (r"федеральн\w*\s+резервн\w*\s+систем\w*", "фрс")]


def _lem(w):
    """Слово -> (словарная форма, участник события?)."""
    r = _LEM.get(w)
    if r is None:
        if _M is None:
            r = (w[:_STEM], False)
        else:
            pr = _M.parse(w)[0]
            nf = _ASPECT.get(pr.normal_form.replace("ё", "е"), pr.normal_form.replace("ё", "е"))
            ent = (any(t in pr.tag for t in _ENT_TAGS) or "LATN" in pr.tag or nf in _COMPANIES
                   or w in _SHORT or not pr.is_known)
            r = (nf, ent)
        if len(_LEM) > 20000:
            _LEM.clear()
        _LEM[w] = r
    return r

# подпись источника в конце заголовка: «— Reuters», « - СМИ», «· ИНТЕРФАКС», « - EURACTIV»
# тире — только с пробелами вокруг: дефис внутри слова («пресс-секретарь проведёт брифинг») — не подпись
_SRC_TAIL = re.compile(r"(?:\s+[-–—|]\s+|\s*·\s*)[A-Za-zА-ЯЁа-яё .\-]{2,40}$")
_HEAD_TAG = re.compile(r"^\s*(?:brief|срочно|молния|отчеты|отчёты|breaking|update)\s*[:\-–—.]\s*", re.I)


def _clean(text):
    t = (text or "").replace("ё", "е").replace("Ё", "Е")
    t = _HEAD_TAG.sub("", t)
    t = _SRC_TAIL.sub("", t)
    return t.lower()


# направление события: «купит» и «продаст», «повысил» и «понизил» — РАЗНЫЕ новости, даже при общих словах
_DIRS = {
    "up": ("купит", "купил", "покуп", "повыс", "повыш", "вырос", "вырас", "рост", "увелич", "подорож", "одобр",
           "ввел", "введ", "запуст", "начн", "начал", "возобнов"),
    "down": ("продаст", "продал", "продаж", "пониз", "сниз", "упал", "паден", "сократ", "сокращ", "подешев",
             "отклон", "отмен", "прекрат", "останов", "приостан", "запрет", "снял", "сняли", "снимает", "снят",
             "смягч"),
    "hold": ("сохран", "оставил", "оставит", "не изменил"),
}


def _sense(clean_low):
    dirs = frozenset(k for k, stems in _DIRS.items() if any(st in clean_low for st in stems))
    neg = bool(re.search(r"(?<![а-я])(не|нет|без|отказ\w*)(?![а-я])", clean_low))
    return dirs, neg


def _tokens(text):
    raw = _SRC_TAIL.sub("", _HEAD_TAG.sub("", (text or "").replace("ё", "е").replace("Ё", "Е")))
    low = raw.lower()
    for rx, to in _SYN:
        low = re.sub(rx, to, low)
    words, nums, names = set(), set(), set()
    for w in re.findall(r"[0-9a-zа-я]+(?:-[a-zа-я]+)?", low):
        if w.isdigit():
            if len(w) >= 2:
                nums.add(w)              # число как есть (разные суммы различаются)
            continue
        if (len(w) < 4 and w not in _SHORT) or w in _STOP:
            continue
        nf, ent = _lem(w)
        if nf in _STOP:
            continue
        words.add(nf)
        if ent:
            names.add(nf)
    # обычный регистр: слово с заглавной не в начале — тоже участник («… сообщил Набиуллиной»)
    for w in re.findall(r"(?<=[\s«\"(])[A-ZА-ЯЕ][a-zа-яе]{2,}", raw):
        nf = _lem(w.lower())[0]
        if nf in words:
            names.add(nf)
    return words, nums, names, _sense(re.sub(r"[^0-9a-zа-я ]+", " ", _clean(text)))


def _similar(a, an, ae, asn, b, bn, be, bsn):
    if asn[0] and bsn[0] and asn[0] != bsn[0]:
        return False                     # разное направление: «купит» / «продаст», «ввели» / «сняли»
    if asn[1] != bsn[1]:
        return False                     # «выплатит» и «не выплатит» — разные новости
    if an and bn and not (an & bn):
        return False                     # в обеих есть числа, и они разные -> разные новости
    if ae and be and len(ae & be) / min(len(ae), len(be)) < 0.67:
        return False                     # другие участники: «ЕС ввёл…» и «США ввели…», «Сбербанк» и «ВТБ»
    inter = len(a & b)
    if not inter:
        return False
    if inter / (len(a | b) or 1) >= _THR:
        return True
    small = min(len(a), len(b))
    if small >= 3 and inter / small >= _OVL:
        return True
    # та же новость из другого источника, подробнее: те же участники и большая часть смысла короткой версии
    return bool(ae and be) and inter >= 3 and inter / small >= 0.6


def is_dup(text, ttl=None, thr=None, smart=None):
    """True — похожая новость уже была в окне (не озвучивать). Первую запоминает и возвращает False.
    Окно: до конца дня, но не меньше 2 часов (ttl — явное окно в секундах, для тестов).
    smart (по умолчанию judge_enabled): если словами не похоже — спросить смыслового судью про до 2 кандидатов."""
    now = time.time()
    until = (now + ttl) if ttl else _keep_until(now)
    words, nums, names, sense = _tokens(text)
    last_judge.clear()
    if len(words) < 2:
        # слишком короткая для сравнения по смыслу — повтором считаем только тот же текст («ПАО «ВИ.ру» дивиденды»
        # 16.10 читался снова и снова)
        k = key(text)
        if not k:
            return False
        with _lock:
            for kk in [kk for kk, u in _short.items() if u <= now]:
                _short.pop(kk, None)
            if k in _short:
                return True
            _short[k] = until
        return False
    global _seen
    with _lock:
        _seen = [x for x in _seen if x[6] > now]
        for (_t, s, sn, se, ss, _x, _u) in _seen:
            if _similar(words, nums, names, sense, s, sn, se, ss):
                return True
        cands = _candidates(frozenset(words), frozenset(nums)) if (judge_enabled if smart is None else smart) else []
    # судья — вне блокировки (сеть до 1.2 с на кандидата)
    for prev in cands:
        t0 = time.time()
        v = judge(prev, text)
        last_judge.update(prev=prev, verdict=v, ms=int((time.time() - t0) * 1000))
        if v is None:
            break                            # судья недоступен — не ждём второго кандидата
        if v:
            return True
    with _lock:
        _seen.append((now, frozenset(words), frozenset(nums), frozenset(names), sense, (text or "")[:300], until))
    return False


def sig(text):
    """Подпись новости для similar(): (слова, числа, участники, смысл)."""
    w, n, e, s = _tokens(text)
    return frozenset(w), frozenset(n), frozenset(e), s


def similar(sa, sb):
    """Одна и та же новость (другими словами / из другого источника)? sa, sb — sig()."""
    if len(sa[0]) < 2 or len(sb[0]) < 2:
        return False
    return _similar(*sa, *sb)


def key(text):
    """Точный ключ новости для «уже видели в ленте»: без регистра, знаков и подписи источника."""
    return re.sub(r"[^0-9a-zа-я]+", "", _clean(text))[:90]


_short = {}                # точный ключ короткой строки -> время


def reset():
    with _lock:
        _seen.clear()
        _short.clear()
