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
_DEF = {"api_id": 0, "api_hash": "", "chats": [], "keywords": [], "match": "any", "mon": True}
def load():
    try:
        d = json.load(open(CONFIG, encoding="utf-8"))
    except Exception:
        d = {}
    for k, v in _DEF.items():
        d.setdefault(k, v if not isinstance(v, list) else list(v) if isinstance(v, list) else v)
    d["chats"] = [int(x) for x in d.get("chats", []) if str(x).lstrip("-").isdigit()]
    d["keywords"] = [str(x) for x in d.get("keywords", []) if str(x).strip()]
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
    _announce(f"Телеграм, {chat_title}: {short}")
    # подать в движок сценариев и разбор событий (как новость)
    try:
        import nox_scenarios
        item = {"text": text, "body": "", "tickers": [], "source": "telegram"}
        nox_scenarios.on_news([item])
        nox_scenarios.extract_event(text)
    except Exception:
        pass

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
            _state.update(ok=True, authorized=False, err="Нужен вход: запусти tg_login.py")
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
        else:
            _loop.run_forever()   # ждём — вход появится, перезапуск подхватит
    except Exception as e:
        _state.update(ok=False, err=str(e)[:160])

def _run(coro, timeout=25):
    if not _loop:
        raise RuntimeError("клиент не запущен")
    return asyncio.run_coroutine_threadsafe(coro, _loop).result(timeout=timeout)

# ------------------------------------------------------------------ HTTP API
@app.on_event("startup")
def _startup():
    threading.Thread(target=_client_thread, daemon=True).start()

@app.get("/health")
def health():
    return {"status": "ok" if _state["ok"] else "starting",
            "authorized": _state["authorized"], "err": _state["err"], "me": _state["me"]}

@app.get("/dialogs")
def dialogs(limit: int = 200):
    """Список чатов/каналов аккаунта — чтобы выбрать, за какими следить."""
    if not _state.get("authorized"):
        return {"ok": False, "text": "Сначала вход: tg_login.py", "items": []}
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
    return {"ok": True, "has_creds": bool(cfg["api_id"] and cfg["api_hash"])}

@app.get("/mon")
def mon(on: int = -1):
    cfg = load()
    if on in (0, 1):
        cfg["mon"] = bool(on); save(cfg)
    return {"on": cfg.get("mon", True),
            "text": "Слежу за Телеграмом, сэр." if cfg.get("mon", True) else "Слежка за Телеграмом выключена, сэр."}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8131, log_level="warning")
