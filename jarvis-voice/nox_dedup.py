# -*- coding: utf-8 -*-
"""
Дедупликация новостей для Nox: одна и та же новость часто приходит повторно — из разных источников,
в другом регистре (КАПС / обычный), короче или длиннее («Трамп: новый пресс-секретарь проведёт брифинг»
и «Трамп заявил, что новый пресс-секретарь Белого дома проведёт свой первый брифинг во вторник»).
is_dup(text) запоминает озвученные новости на 3 часа и считает дублями похожие:
  * сравнение без регистра, без подписи источника («— Reuters», «· ИНТЕРФАКС», «BRIEF»);
  * совпадение ключевых слов (грубый стем) по Жаккару ИЛИ по вложенности (короткая версия длинной);
  * разные числа (суммы, проценты) -> НЕ дубль: «дивиденды 35 руб» и «дивиденды 40 руб» — разные новости.
"""
import time, re, threading

_TTL = 3 * 3600   # окно памяти, сек (3 часа) — повторы в ленте бывают и через час
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
    "срочно молния breaking update обновление свой своей свои который которая которые также ранее"
).split())

# подпись источника в конце заголовка: «— Reuters», « - СМИ», «· ИНТЕРФАКС», « - EURACTIV»
_SRC_TAIL = re.compile(r"\s*[-–—·|]\s*[A-Za-zА-ЯЁа-яё .]{2,30}$")
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
             "отклон", "отмен", "прекрат", "останов", "приостан", "запрет"),
    "hold": ("сохран", "оставил", "оставит", "не изменил"),
}


def _sense(clean_low):
    dirs = frozenset(k for k, stems in _DIRS.items() if any(st in clean_low for st in stems))
    neg = bool(re.search(r"(?<![а-я])(не|нет|без|отказ\w*)(?![а-я])", clean_low))
    return dirs, neg


def _tokens(text):
    raw = _SRC_TAIL.sub("", _HEAD_TAG.sub("", (text or "").replace("ё", "е").replace("Ё", "Е")))
    # имена собственные (компании, люди, страны): слова с заглавной буквы
    names = {w.lower()[:_STEM] for w in re.findall(r"[A-ZА-ЯЕ][A-Za-zА-Яа-яЕе-]{3,}", raw)
             if w.lower() not in _STOP}
    low = re.sub(r"[^0-9a-zа-я ]+", " ", _clean(text))
    words, nums = set(), set()
    for w in low.split():
        if w.isdigit():
            if len(w) >= 2:
                nums.add(w)              # число как есть (разные суммы различаются)
        elif len(w) >= 4 and w not in _STOP:
            words.add(w[:_STEM])         # грубый стем (морфология)
    return words, nums, names, _sense(low)


def _similar(a, an, ae, asn, b, bn, be, bsn):
    if asn[0] and bsn[0] and asn[0] != bsn[0]:
        return False                     # разное направление: «купит» / «продаст», «повысил» / «понизил»
    if asn[1] != bsn[1]:
        return False                     # «выплатит» и «не выплатит» — разные новости
    if an and bn and not (an & bn):
        return False                     # в обеих есть числа, и они разные -> разные новости
    if ae and be and not (ae & be):
        return False                     # разные компании/люди: «Сбербанк …» и «Газпром …» — разные новости
    inter = len(a & b)
    if not inter:
        return False
    if inter / (len(a | b) or 1) >= _THR:
        return True
    small = min(len(a), len(b))
    return small >= 3 and inter / small >= _OVL


def is_dup(text, ttl=_TTL, thr=None):
    """True — похожая новость уже была в окне (не озвучивать). Первую запоминает и возвращает False."""
    now = time.time()
    words, nums, names, sense = _tokens(text)
    if len(words) < 2:
        return False                     # слишком короткая — не судим
    with _lock:
        global _seen
        _seen = [x for x in _seen if now - x[0] < ttl]
        for (_t, s, sn, se, ss, _x) in _seen:
            if _similar(words, nums, names, sense, s, sn, se, ss):
                return True
        _seen.append((now, frozenset(words), frozenset(nums), frozenset(names), sense, (text or "")[:80]))
    return False


def key(text):
    """Точный ключ новости для «уже видели в ленте»: без регистра, знаков и подписи источника."""
    return re.sub(r"[^0-9a-zа-я]+", "", _clean(text))[:90]


def reset():
    with _lock:
        _seen.clear()
