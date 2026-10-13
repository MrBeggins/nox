# -*- coding: utf-8 -*-
"""Решения по ставке ПРЯМО с официальных сайтов, без ленты новостей (2026-10-13).

ФРС: заявление FOMC публикуется в 14:00 по Нью-Йорку по заранее известному адресу
     https://www.federalreserve.gov/newsevents/pressreleases/monetaryYYYYMMDDa.htm
     — берём фразу «The Committee decided to … the target range for the federal funds rate …».
ЦБ:  пресс-релиз в 13:30 МСК: https://www.cbr.ru/press/pr/?file=DDMMYYYY_133000key.htm
     — решение прямо в заголовке: «Банк России принял решение снизить ключевую ставку на 50 б.п., до …».
Минфин — адрес заранее неизвестен, остаётся на ленте.

Ридер (terminal_reader._official_loop) зовёт poll_once() для включённых наблюдателей «Новость по времени»
с банком cbr/fed в их окне; найденный текст отдаётся наблюдателям как первая новость.
Соединение держится открытым (keep-alive) — без нового TLS на каждый запрос; к адресу добавляется
метка против кэша CDN (иначе закэшированная 404 «до публикации» может держаться секунды).
"""
import html
import http.client
import re
import ssl
import threading
import time
import urllib.parse

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
_CTX = ssl.create_default_context()
_conns = {}
_lock = threading.Lock()


def url_for(bank, date):
    """date — 'ГГГГ-ММ-ДД' (день решения)."""
    y, m, d = date.split("-")
    if bank == "fed":
        return f"https://www.federalreserve.gov/newsevents/pressreleases/monetary{y}{m}{d}a.htm"
    if bank == "cbr":
        return f"https://www.cbr.ru/press/pr/?file={d}{m}{y}_133000key.htm"
    return ""


def _get(url, timeout=4.0):
    """GET через постоянное соединение. -> (status, text)."""
    u = urllib.parse.urlsplit(url)
    sep = "&" if u.query else "?"
    path = (u.path or "/") + (("?" + u.query) if u.query else "") + f"{sep}nc={int(time.time() * 1000)}"
    for attempt in (0, 1):
        with _lock:
            c = _conns.get(u.netloc)
            if c is None:
                c = http.client.HTTPSConnection(u.netloc, timeout=timeout, context=_CTX)
                _conns[u.netloc] = c
        try:
            c.request("GET", path, headers={"User-Agent": UA, "Accept": "text/html", "Cache-Control": "no-cache",
                                            "Pragma": "no-cache", "Connection": "keep-alive"})
            r = c.getresponse()
            body = r.read()
            return r.status, body.decode("utf-8", "ignore")
        except Exception:
            with _lock:
                try:
                    c.close()
                except Exception:
                    pass
                _conns.pop(u.netloc, None)
            if attempt:
                raise
    return 0, ""


def _text(raw):
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()


def extract(bank, raw):
    """Текст решения со страницы или None (страницы ещё нет / решения в ней нет)."""
    if bank == "fed":
        t = _text(raw)
        m = re.search(r"\b(?:The|the) Committee (?:decided|voted) to [^.]*federal funds rate[^.]*\.", t) or \
            re.search(r"[^.>:]*\b(?:decided|voted) to [^.]*federal funds rate[^.]*\.", t)
        return m.group(0).strip() if m else None
    if bank == "cbr":
        m = re.search(r"Банк России принял решение[^<|]{5,200}?годовых", html.unescape(raw))
        if m:
            return re.sub(r"\s+", " ", m.group(0)).strip()
        t = _text(raw)
        m = re.search(r"Совет директоров Банка России[^.]{0,160}?принял решение[^.]{0,200}?годовых", t)
        return m.group(0) if m else None
    return None


def _request(netloc, method, path, headers):
    """Запрос по постоянному соединению. -> (status, bytes). HEAD не читает тело."""
    for attempt in (0, 1):
        with _lock:
            c = _conns.get(netloc)
            if c is None:
                c = http.client.HTTPSConnection(netloc, timeout=8, context=_CTX)
                _conns[netloc] = c
        try:
            c.request(method, path, headers={"User-Agent": UA, "Cache-Control": "no-cache", "Pragma": "no-cache",
                                             "Connection": "keep-alive", **headers})
            r = c.getresponse()
            return r.status, r.read()
        except Exception:
            with _lock:
                try:
                    c.close()
                except Exception:
                    pass
                _conns.pop(netloc, None)
            if attempt:
                raise
    return 0, b""


def warm(bank, date):
    """Заранее открыть соединение (TLS до сайта ФРС из РФ — 0.4–13 с: Cloudflare замедлен)."""
    u = urllib.parse.urlsplit(url_for(bank, date))
    try:
        _request(u.netloc, "HEAD", u.path or "/", {})
    except Exception:
        pass


def poll_fed(date):
    """ФРС: HEAD «опубликовано?» (страница 404 до публикации весит 82 КБ и игнорирует Range — её не качаем),
    при 200 — первые 2 КБ (в заявлениях 2026 г. фраза с решением стоит в первых ~450 байтах), иначе больше.
    -> (текст | None, мс, статус)."""
    u = urllib.parse.urlsplit(url_for("fed", date))
    nc = f"?nc={time.time_ns()}"
    t0 = time.perf_counter()
    try:
        st, _ = _request(u.netloc, "HEAD", u.path + nc, {})
        if st != 200:
            return None, round((time.perf_counter() - t0) * 1000), st
        # полная фраза — на 70–72 КБ полной страницы (14 заявлений 2025–2026: 70 075…71 668 байт); в ответах
        # на Range страница на ~4% короче (77 678 вместо 81 213 — другие переводы строк) -> там ~67–69 КБ
        for rng in ("bytes=63000-74999", "bytes=50000-84999", None):
            st2, body = _request(u.netloc, "GET", u.path + nc, {"Range": rng} if rng else {})
            txt = extract("fed", body.decode("utf-8", "ignore")) if st2 in (200, 206) else None
            if txt:
                return txt, round((time.perf_counter() - t0) * 1000), st2
            if rng == "bytes=63000-74999":
                # запас: в мета-описании (первые ~450 байт) — начало фразы, хотя бы направление решения
                st3, head = _request(u.netloc, "GET", u.path + nc, {"Range": "bytes=0-1999"})
                part = extract_meta(head.decode("utf-8", "ignore")) if st3 in (200, 206) else None
                if part:
                    return part, round((time.perf_counter() - t0) * 1000), st3
        return None, round((time.perf_counter() - t0) * 1000), 200
    except Exception:
        return None, round((time.perf_counter() - t0) * 1000), 0


def poll(bank, date):
    """Одна попытка для банка: ФРС — HEAD+кусок, ЦБ — страница целиком (~0.2 с)."""
    return poll_fed(date) if bank == "fed" else poll_once(bank, date)


def extract_meta(raw):
    """Мета-описание страницы FOMC: «…The Committee decided to raise the target range for the» (обрезано) —
    направление без размера. -> «The Committee decided to raise the target range for the federal funds rate»."""
    m = re.search(r"(?:The|the) Committee (?:decided|voted) to (raise|lower|maintain|reduce|increase|keep)\b",
                  html.unescape(raw))
    return f"The Committee decided to {m.group(1)} the target range for the federal funds rate" if m else None


def poll_once(bank, date):
    """Одна попытка: -> (текст решения | None, мс запроса, http-статус)."""
    url = url_for(bank, date)
    if not url:
        return None, 0, 0
    t0 = time.perf_counter()
    try:
        st, raw = _get(url)
    except Exception:
        return None, round((time.perf_counter() - t0) * 1000), 0
    ms = round((time.perf_counter() - t0) * 1000)
    return (extract(bank, raw) if st == 200 else None), ms, st
