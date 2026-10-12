# -*- coding: utf-8 -*-
"""
Telegram-ридер для Nox: следит за ВЫБРАННЫМИ чатами/каналами и по фильтру слов
озвучивает важные сообщения + логирует + подаёт в движок сценариев (авто-клик) и
разбор событий (дивиденд/оферта/…). Клиент на аккаунт пользователя (Telethon/MTProto).

Доступ: нужен api_id + api_hash (my.telegram.org) и РАЗОВЫЙ вход `python tg_login.py`
(телефон + код из Telegram). Сессия хранится локально в tg_session.session; креды и
сообщения никуда не отправляются. Только ЧТЕНИЕ сообщений, ничего не постит и не пишет.
Запуск: python tg_reader.py  ->  http://127.0.0.1:8131
"""
import threading, asyncio, json, os, socket, base64, struct, time
from fastapi import FastAPI
import uvicorn

CONFIG   = r"C:\jarvis-voice\tg_config.json"
SESSION  = r"C:\jarvis-voice\tg_session"     # Telethon добавит .session
IPC_HOST, IPC_PORT = "127.0.0.1", 9712

app = FastAPI()
_state = {"ok": False, "authorized": False, "err": "", "me": ""}
_loop = None
_client = None

# ------------------------------------------------------------------ конфиг
_DEF = {"api_id": 0, "api_hash": "", "chats": [], "keywords": [], "match": "any", "mon": True,
        "public": []}   # public — имена публичных каналов, читаемых БЕЗ входа (t.me/s/<имя>)
def load():
    try:
        d = json.load(open(CONFIG, encoding="utf-8"))
    except Exception:
        d = {}
    for k, v in _DEF.items():
        d.setdefault(k, v if not isinstance(v, list) else list(v) if isinstance(v, list) else v)
    d["chats"] = [int(x) for x in d.get("chats", []) if str(x).lstrip("-").isdigit()]
    d["keywords"] = [str(x) for x in d.get("keywords", []) if str(x).strip()]
    d["public"] = [str(x).lower() for x in d.get("public", []) if str(x).strip()]
    return d
def save(d):
    try:
        json.dump(d, open(CONFIG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass

def _mon_enabled():
    return bool(load().get("mon", True))

# ------------------------------------------------------------------ лог (общий с сценариями)
def _log(msg):
    try:
        import nox_scenarios
        nox_scenarios._log("TG: " + str(msg)); return
    except Exception:
        pass
    try:
        open(r"C:\jarvis-voice\tg_reader.log", "a", encoding="utf-8").write(
            time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg) + "\n")
    except Exception:
        pass

# ------------------------------------------------------------------ IPC-озвучка
def _announce(text):
    try:
        s = socket.create_connection((IPC_HOST, IPC_PORT), timeout=4)
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall((f"GET / HTTP/1.1\r\nHost: {IPC_HOST}:{IPC_PORT}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        s.recv(4096)
        data = json.dumps({"action": "announce", "text": text}, ensure_ascii=False).encode("utf-8")
        mask = os.urandom(4); hdr = bytearray([0x81]); n = len(data)
        if n < 126: hdr.append(0x80 | n)
        else: hdr.append(0x80 | 126); hdr += struct.pack(">H", n)
        hdr += mask
        s.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
        time.sleep(0.15); s.close()
    except Exception:
        pass

# ------------------------------------------------------------------ фильтр
def _match(text, kws, mode):
    if not kws:
        return True   # нет слов -> пропускаем всё из выбранных чатов
    low = (text or "").lower()
    hits = sum(1 for k in kws if k.lower() in low)
    return hits == len(kws) if mode == "all" else hits > 0

def _shorten(text, n=220):
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[:n] + "…"

import re as _re0
_EMOJI_RE = _re0.compile("[\U0001F000-\U0001FAFF☀-➿️‍⃣⬀-⯿]")
def _speakable(text):
    """Для озвучки: без эмодзи/флагов и #хэштегов (MarketTwits: «⚠️🇺🇸 #дкп #сша …»)."""
    t = _EMOJI_RE.sub(" ", text or "")
    t = _re0.sub(r"(?<!\w)#\w+", " ", t)
    return " ".join(t.split())

# ------------------------------------------------------------------ обработка сообщения
def _handle_message(chat_title, text):
    cfg = load()
    if not _mon_enabled():
        return
    if not _match(text, cfg.get("keywords", []), cfg.get("match", "any")):
        return
    # дедуп: ту же новость (из ТГ или из ленты терминала) не повторяем 15 мин
    try:
        import nox_dedup
        if nox_dedup.is_dup(text):
            return
    except Exception:
        pass
    short = _shorten(text)
    _log(f"[{chat_title}] {short}")
    _announce(f"Телеграм, {chat_title}: {_shorten(_speakable(text))}")
    # подать в движок сценариев и разбор событий (как новость)
    try:
        import nox_scenarios
        item = {"text": text, "body": "", "tickers": [], "source": "telegram"}
        nox_scenarios.on_news([item])
        nox_scenarios.extract_event(text)
    except Exception:
        pass

# ------------------------------------------------------------------ публичные каналы БЕЗ входа (t.me/s/<имя>)
# Веб-превью публичного канала отдаётся без авторизации: 20 последних постов, а
# ?after=<id> — только новее id (пусто ≈ 20 КБ). Не нужны api_id/api_hash и вход.
import re as _re, html as _html
_TME = "https://t.me/s/"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"}
_pub = {}            # имя -> {"title","last_id","ok","err","seen"}
_pub_stop = {}       # имя -> threading.Event
_POST_RE = _re.compile(r'data-post="([^"/]+)/(\d+)"')
_TEXT_RE = _re.compile(r'class="tgme_widget_message_text js-message_text[^"]*"[^>]*>')
_TIME_RE = _re.compile(r'<time datetime="([^"]+)"')
_TITLE_RE = _re.compile(r'<meta property="og:title" content="([^"]*)"')

def _pub_name(s):
    """@name / t.me/name / https://t.me/s/name/123 -> name (нижний регистр)."""
    s = (s or "").strip()
    m = _re.search(r"(?:t\.me/(?:s/)?|^@?)([A-Za-z][A-Za-z0-9_]{3,31})(?:[/?].*)?$", s)
    return m.group(1).lower() if m else ""

def _div_inner(page, start):
    """Содержимое div от позиции после открывающего тега до парного </div>."""
    depth, i = 1, start
    while depth and i < len(page):
        o = page.find("<div", i); c = page.find("</div>", i)
        if c < 0:
            return page[start:]
        if 0 <= o < c:
            depth += 1; i = o + 4
        else:
            depth -= 1; i = c + 6
    return page[start:i - 6]

def _clean(h):
    h = _re.sub(r"<br\s*/?>", "\n", h)
    return _html.unescape(_re.sub(r"<[^>]+>", "", h)).strip()

def _parse_posts(page):
    """-> [(id, text, iso_time)] по возрастанию id (посты без текста пропускаем)."""
    out = []
    marks = [(m.start(), int(m.group(2))) for m in _POST_RE.finditer(page)]
    for k, (pos, pid) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(page)
        block = page[pos:end]
        t = _TEXT_RE.search(block)
        text = _clean(_div_inner(block, t.end())) if t else ""
        tm = _TIME_RE.search(block)
        if text:
            out.append((pid, text, tm.group(1) if tm else ""))
    return sorted(set(out))

def _pub_fetch(sess, name, after=None):
    url = _TME + name + (f"?after={after}" if after else "")
    r = sess.get(url, headers=_UA, timeout=15, allow_redirects=False)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    return r.text

def _pub_probe(name):
    """Проверка канала при добавлении -> (ok, title, err)."""
    import requests
    try:
        page = _pub_fetch(requests.Session(), name)
    except Exception as e:
        if "302" in str(e):   # t.me уводит с /s/ — нет публичной ленты
            return False, "", "не найден или закрытый (без публичной ленты) — такой читается только через вход в аккаунт"
        return False, "", f"не открылся: {e}"
    title = _html.unescape((_TITLE_RE.search(page) or [None, name])[1])
    if not _POST_RE.search(page):
        return False, title, "нет публичной ленты (канал закрыт, это не канал, или превью отключено)"
    return True, title, ""

def _age_min(iso):
    try:
        from datetime import datetime, timezone
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 60
    except Exception:
        return 0

def _pub_loop(name, stop):
    import requests
    sess = requests.Session()
    st = _pub.setdefault(name, {"title": name, "last_id": 0, "ok": False, "err": "", "seen": 0})
    delay = 2.0
    while not stop.is_set():
        try:
            page = _pub_fetch(sess, name, st["last_id"] or None)
            m = _TITLE_RE.search(page)
            if m: st["title"] = _html.unescape(m.group(1))
            posts = _parse_posts(page)
            if not st["last_id"]:
                # первый заход — запомнить, где лента сейчас; старое не озвучиваем
                ids = [int(x.group(2)) for x in _POST_RE.finditer(page)]
                st["last_id"] = max(ids) if ids else 0
            else:
                new = [p for p in posts if p[0] > st["last_id"]]
                ids = [int(x.group(2)) for x in _POST_RE.finditer(page)]
                if ids: st["last_id"] = max(st["last_id"], max(ids))
                fresh = [p for p in new if _age_min(p[2]) <= 10]   # после обрыва связи — без «археологии»
                for pid, text, _ in fresh[-3:]:                    # пачкой — максимум 3 последних
                    st["seen"] += 1
                    _handle_message(st["title"], text)
            st.update(ok=True, err="")
            delay = 2.0
        except Exception as e:
            st.update(ok=False, err=str(e)[:120])
            delay = min(60.0, delay * 2)          # 429/обрыв — отступаем
        stop.wait(delay + (hash((name, time.time())) % 500) / 1000.0)

def _pub_sync():
    """Запустить/остановить потоки опроса под список public из конфига (каждые 3 с)."""
    while True:
        try:
            want = set(load().get("public", []))
            for n in want - set(_pub_stop):
                ev = threading.Event(); _pub_stop[n] = ev
                threading.Thread(target=_pub_loop, args=(n, ev), daemon=True).start()
            for n in set(_pub_stop) - want:
                _pub_stop.pop(n).set(); _pub.pop(n, None)
        except Exception:
            pass
        time.sleep(3)

# ------------------------------------------------------------------ Telethon-клиент (отдельный поток)
def _client_thread():
    global _loop, _client
    cfg = load()
    if not cfg.get("api_id") or not cfg.get("api_hash"):
        _state.update(ok=True, authorized=False, err="Нет api_id/api_hash (my.telegram.org)")
        return
    try:
        from telethon import TelegramClient, events
    except Exception as e:
        _state.update(ok=False, err=f"telethon не установлен: {e}")
        return
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _client = TelegramClient(SESSION, int(cfg["api_id"]), cfg["api_hash"], loop=_loop)

    async def _start():
        await _client.connect()
        if not await _client.is_user_authorized():
            _state.update(ok=True, authorized=False, err="Нужен вход: кнопка «Войти в Телеграм»")
            await _client.disconnect()   # отпустить сессию: окно входа пишет в тот же файл
            return
        me = await _client.get_me()
        _state.update(ok=True, authorized=True, err="",
                      me=(getattr(me, "username", None) or getattr(me, "first_name", "") or "аккаунт"))

        @_client.on(events.NewMessage)
        async def _on_msg(event):
            try:
                c = load()
                cid = event.chat_id
                if c.get("chats") and cid not in c["chats"]:
                    return
                title = ""
                try:
                    ch = await event.get_chat()
                    title = getattr(ch, "title", None) or getattr(ch, "username", None) \
                        or getattr(ch, "first_name", None) or str(cid)
                except Exception:
                    title = str(cid)
                _handle_message(title, event.raw_text or "")
            except Exception:
                pass

    try:
        _loop.run_until_complete(_start())
        if _state["authorized"]:
            _loop.run_until_complete(_client.run_until_disconnected())
    except Exception as e:
        _state.update(ok=False, authorized=False, err=str(e)[:160])
    finally:
        # без входа (или после отключения) — закрыть файл сессии; после входа
        # /reconnect (его зовёт tg_login.py, «Сохранить» и «Обновить список чатов») поднимет заново
        try: _client.session.close()
        except Exception: pass
        _state["authorized"] = False

_thread = None
_restart_lock = threading.Lock()

def _restart_client(wait=8.0):
    """Перезапустить Telethon-клиент (после ввода ключей / входа). Ждёт до `wait` с авторизации."""
    global _thread
    with _restart_lock:
        if _thread and _thread.is_alive():
            if _state.get("authorized"):
                return True              # уже подключены и слушаем
            try: asyncio.run_coroutine_threadsafe(_client.disconnect(), _loop).result(timeout=5)
            except Exception: pass
            _thread.join(timeout=5)
        _state.update(ok=False, authorized=False, err="подключаюсь…")
        _thread = threading.Thread(target=_client_thread, daemon=True)
        _thread.start()
    t0 = time.time()
    while time.time() - t0 < wait and _thread.is_alive() and not _state.get("authorized"):
        time.sleep(0.3)
    return bool(_state.get("authorized"))

def _run(coro, timeout=25):
    if not _loop:
        raise RuntimeError("клиент не запущен")
    return asyncio.run_coroutine_threadsafe(coro, _loop).result(timeout=timeout)

# ------------------------------------------------------------------ HTTP API
@app.on_event("startup")
def _startup():
    _restart_client(wait=0)
    threading.Thread(target=_pub_sync, daemon=True).start()   # публичные каналы без входа

@app.get("/reconnect")
def reconnect():
    """Подхватить свежий вход/ключи без перезапуска Nox (зовёт tg_login.py после входа)."""
    ok = _restart_client()
    return {"ok": True, "authorized": ok, "err": _state["err"], "me": _state["me"]}

@app.get("/health")
def health():
    return {"status": "ok" if _state["ok"] else "starting",
            "authorized": _state["authorized"], "err": _state["err"], "me": _state["me"],
            "public": len(load().get("public", []))}

@app.get("/dialogs")
def dialogs(limit: int = 200):
    """Список чатов/каналов аккаунта — чтобы выбрать, за какими следить."""
    if not _state.get("authorized") and not _restart_client():   # вдруг вход только что сделан
        return {"ok": False, "text": _state.get("err") or "Сначала вход: «Войти в Телеграм»", "items": []}
    try:
        from telethon import TelegramClient  # noqa
        async def _get():
            out = []
            async for d in _client.iter_dialogs(limit=limit):
                out.append({"id": d.id, "name": d.name or str(d.id),
                            "kind": ("канал" if d.is_channel else "группа" if d.is_group else "личка")})
            return out
        items = _run(_get())
        sel = set(load().get("chats", []))
        for it in items:
            it["selected"] = it["id"] in sel
        return {"ok": True, "items": items}
    except Exception as e:
        return {"ok": False, "text": str(e)[:160], "items": []}

@app.get("/chats_set")
def chats_set(ids: str = ""):
    """Задать выбранные чаты (id через запятую)."""
    cfg = load()
    cfg["chats"] = [int(x) for x in ids.split(",") if x.strip().lstrip("-").isdigit()]
    save(cfg)
    return {"ok": True, "chats": cfg["chats"]}

@app.get("/chats_get")
def chats_get():
    return {"chats": load().get("chats", [])}

@app.get("/filter_get")
def filter_get():
    c = load()
    return {"keywords": c.get("keywords", []), "match": c.get("match", "any")}

@app.get("/filter_add")
def filter_add(q: str = ""):
    cfg = load(); v = q.strip()
    if v and v not in cfg["keywords"]:
        cfg["keywords"].append(v); save(cfg)
    return {"keywords": cfg["keywords"]}

@app.get("/filter_remove")
def filter_remove(q: str = ""):
    cfg = load(); cfg["keywords"] = [k for k in cfg["keywords"] if k != q.strip()]; save(cfg)
    return {"keywords": cfg["keywords"]}

@app.get("/filter_mode")
def filter_mode(mode: str = "any"):
    cfg = load(); cfg["match"] = "all" if mode == "all" else "any"; save(cfg)
    return {"match": cfg["match"]}

@app.get("/creds_set")
def creds_set(api_id: int = 0, api_hash: str = ""):
    """Сохранить api_id/api_hash (с my.telegram.org). После — запустить tg_login.py."""
    cfg = load()
    if api_id: cfg["api_id"] = int(api_id)
    if api_hash.strip(): cfg["api_hash"] = api_hash.strip()
    save(cfg)
    threading.Thread(target=_restart_client, daemon=True).start()   # новые ключи — переподключиться
    return {"ok": True, "has_creds": bool(cfg["api_id"] and cfg["api_hash"])}

@app.get("/public_get")
def public_get():
    """Публичные каналы без входа + их состояние (название, последний пост, ошибка)."""
    out = []
    for n in load().get("public", []):
        st = _pub.get(n, {})
        out.append({"name": n, "title": st.get("title", n), "ok": st.get("ok", False),
                    "err": st.get("err", ""), "last_id": st.get("last_id", 0), "seen": st.get("seen", 0)})
    return {"channels": out}

@app.get("/public_add")
def public_add(q: str = ""):
    """Добавить канал: @имя, имя или ссылка t.me/… Проверяет, что у канала есть публичная лента."""
    n = _pub_name(q)
    if not n:
        return {"ok": False, "text": "Не понял имя канала. Пример: @markettwits или t.me/markettwits"}
    ok, title, err = _pub_probe(n)
    if not ok:
        return {"ok": False, "text": f"{title or n}: {err}"}
    cfg = load()
    if n not in cfg["public"]:
        cfg["public"].append(n); save(cfg)
    _pub.setdefault(n, {"title": title, "last_id": 0, "ok": False, "err": "", "seen": 0})["title"] = title
    return {"ok": True, "text": f"Добавлен: {title}", **public_get()}

@app.get("/public_remove")
def public_remove(q: str = ""):
    n = _pub_name(q) or q.strip().lower()
    cfg = load(); cfg["public"] = [x for x in cfg["public"] if x != n]; save(cfg)
    return {"ok": True, **public_get()}

@app.get("/mon")
def mon(on: int = -1):
    cfg = load()
    if on in (0, 1):
        cfg["mon"] = bool(on); save(cfg)
    return {"on": cfg.get("mon", True),
            "text": "Слежу за Телеграмом, сэр." if cfg.get("mon", True) else "Слежка за Телеграмом выключена, сэр."}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8131, log_level="warning")
