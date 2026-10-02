# -*- coding: utf-8 -*-
"""
Дедупликация новостей для Nox: одна и та же новость часто приходит из разных источников.
is_dup(text) запоминает озвученные новости и в течение окна (15 мин) считает похожие дублями
по совпадению ключевых слов (стемминг + коэффициент Жаккара) — чтобы не читать 5 раз.
Числа учитываются (разные суммы/значения -> НЕ дубль).
"""
import time, re, threading

_TTL = 900        # окно памяти, сек (15 минут)
_THR = 0.5        # порог схожести (доля общих ключевых слов)
_STEM = 4         # длина стема (русская морфология: «квартал/квартале» -> «квар»)
_lock = threading.Lock()
_seen = []        # [(ts, frozenset(tokens), text)]

_STOP = set((
    "и в во не на с со по за из у о от до как что это так же бы для при об а но или если то над под про между "
    "рбк интерфакс прайм тасс ведомости коммерсант рейтер bloomberg сегодня вчера завтра года году руб рублей "
    "акции акций компания компании рынок рынка новости новость сообщает сообщил пишет источник"
).split())

def _tokens(text):
    low = re.sub(r"[^0-9a-zа-яё ]+", " ", (text or "").lower())
    toks = set()
    for w in low.split():
        if w.isdigit():
            if len(w) >= 2:
                toks.add(w)              # число как есть (разные суммы различаются)
        elif len(w) >= 4 and w not in _STOP:
            toks.add(w[:_STEM])          # грубый стем (морфология)
    return toks

def is_dup(text, ttl=_TTL, thr=_THR):
    """True — похожая новость уже была в окне (не озвучивать). Первую запоминает и возвращает False."""
    now = time.time()
    toks = _tokens(text)
    if len(toks) < 3:
        return False                     # слишком короткая — не судим
    with _lock:
        global _seen
        _seen = [(t, s, x) for (t, s, x) in _seen if now - t < ttl]
        for (t, s, x) in _seen:
            inter = len(toks & s); union = len(toks | s) or 1
            if inter / union >= thr:
                return True
        _seen.append((now, frozenset(toks), (text or "")[:80]))
    return False

def reset():
    with _lock:
        _seen.clear()
