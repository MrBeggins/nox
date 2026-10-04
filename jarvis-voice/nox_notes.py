# -*- coding: utf-8 -*-
"""
Заметки и напоминания для Nox.
  • «запомни ...»  -> заметка (хранится, читается по «мои заметки»).
  • «напомни ... через 10 минут / в 16:30 / завтра в 9» -> напоминание, озвучит голосом в срок.
Запуск: python nox_notes.py  ->  http://127.0.0.1:8133
"""
import threading, time, json, os, re, socket, base64, struct
import datetime as dt
from fastapi import FastAPI
import uvicorn

IPC_HOST, IPC_PORT = "127.0.0.1", 9712
STORE = r"C:\jarvis-voice\nox_notes.json"
app = FastAPI()

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Europe/Moscow"); dt.datetime.now(_TZ)
    def now(): return dt.datetime.now(_TZ).replace(tzinfo=None)
except Exception:
    def now(): return dt.datetime.now()

def load():
    try: d = json.load(open(STORE, encoding="utf-8"))
    except Exception: d = {}
    d.setdefault("notes", []); d.setdefault("reminders", [])
    return d
def save(d):
    try: json.dump(d, open(STORE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception: pass

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

# ------------------------------------------------------------------ разбор времени (рус)
_NUMW = {"один":1,"одну":1,"одна":1,"два":2,"две":2,"три":3,"четыре":4,"пять":5,"шесть":6,
         "семь":7,"восемь":8,"девять":9,"десять":10,"пятнадцать":15,"двадцать":20,"тридцать":30,"сорок":40,"полчаса":30}

def parse_when(text):
    """Вернуть (due_datetime | None, оставшийся_текст). Понимает «через N …», «в HH:MM», «завтра в …»."""
    t = text.strip(); low = t.lower()
    base = now(); due = None; used = ""
    # через N минут/часов/секунд / через час / через полчаса
    m = re.search(r"через\s+([а-яё]+|\d+)\s*(секунд\w*|сек|минут\w*|мин|час\w*|ч)\b", low)
    if m:
        qty = m.group(1); unit = m.group(2)
        n = int(qty) if qty.isdigit() else _NUMW.get(qty, 1)
        if unit.startswith(("секунд","сек")): due = base + dt.timedelta(seconds=n)
        elif unit.startswith(("минут","мин")): due = base + dt.timedelta(minutes=n)
        else: due = base + dt.timedelta(hours=n)
        used = m.group(0)
    elif "через полчаса" in low:
        due = base + dt.timedelta(minutes=30); used = "через полчаса"
    elif "через час" in low:
        due = base + dt.timedelta(hours=1); used = "через час"
    else:
        # [завтра] в HH[:MM]
        m = re.search(r"(завтра\s+)?в\s+(\d{1,2})(?:[:.](\d{2}))?\b", low)
        if m:
            hh = int(m.group(2)); mm = int(m.group(3) or 0)
            d = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if m.group(1): d += dt.timedelta(days=1)
            elif d <= base: d += dt.timedelta(days=1)   # время уже прошло -> завтра
            due = d; used = m.group(0)
    body = t
    if used:
        # вырезаем временную фразу из текста напоминания
        idx = low.find(used)
        body = (t[:idx] + t[idx+len(used):]).strip(" ,.")
    return due, body

# ------------------------------------------------------------------ напоминания: цикл
def _rem_loop():
    while True:
        try:
            cfg = load(); changed = False; n = now()
            for r in cfg["reminders"]:
                if r.get("fired"): continue
                due = dt.datetime.fromisoformat(r["due"])
                if n >= due:
                    r["fired"] = True; changed = True
                    _announce(f"Напоминание, сэр: {r['text']}.")
            if changed: save(cfg)
        except Exception:
            pass
        time.sleep(10)

# ------------------------------------------------------------------ API
@app.on_event("startup")
def _startup():
    threading.Thread(target=_rem_loop, daemon=True).start()

@app.get("/health")
def health():
    c = load(); return {"status": "ok", "notes": len(c["notes"]),
                        "reminders": len([r for r in c["reminders"] if not r.get("fired")])}

@app.get("/note_add")
def note_add(text: str = ""):
    t = text.strip()
    if not t: return {"ok": False, "text": "Что запомнить, сэр?"}
    cfg = load()
    cfg["notes"].insert(0, {"id": str(int(time.time()*1000)), "text": t,
                            "ts": now().strftime("%Y-%m-%d %H:%M")})
    save(cfg)
    return {"ok": True, "text": f"Запомнил, сэр: {t}."}

@app.get("/notes")
def notes(n: int = 10):
    cfg = load(); items = cfg["notes"][:max(1, n)]
    if not items: return {"text": "Заметок пока нет, сэр.", "items": []}
    return {"text": "Ваши заметки, сэр: " + "; ".join(x["text"] for x in items) + ".", "items": items}

@app.get("/note_del")
def note_del(id: str = ""):
    cfg = load(); cfg["notes"] = [x for x in cfg["notes"] if x["id"] != id]; save(cfg)
    return {"ok": True}

@app.get("/remind")
def remind(text: str = ""):
    due, body = parse_when(text)
    if not due:
        # времени нет -> сохраняем как заметку, чтобы не потерять
        return note_add(text.strip())
    if not body:
        body = "напоминание"
    cfg = load()
    cfg["reminders"].insert(0, {"id": str(int(time.time()*1000)), "text": body,
                                "due": due.isoformat(timespec="seconds"), "fired": False})
    save(cfg)
    when = due.strftime("%d.%m %H:%M") if (due.date() != now().date()) else ("в " + due.strftime("%H:%M"))
    return {"ok": True, "text": f"Напомню {when}, сэр: {body}."}

@app.get("/reminders")
def reminders():
    cfg = load(); act = [r for r in cfg["reminders"] if not r.get("fired")]
    act.sort(key=lambda r: r["due"])
    if not act: return {"text": "Активных напоминаний нет, сэр.", "items": []}
    def w(r):
        d = dt.datetime.fromisoformat(r["due"])
        return (d.strftime("%d.%m %H:%M") if d.date() != now().date() else d.strftime("%H:%M")) + " — " + r["text"]
    return {"text": "Напоминания, сэр: " + "; ".join(w(r) for r in act) + ".", "items": act}

@app.get("/remind_del")
def remind_del(id: str = ""):
    cfg = load(); cfg["reminders"] = [r for r in cfg["reminders"] if r["id"] != id]; save(cfg)
    return {"ok": True}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8133, log_level="warning")
