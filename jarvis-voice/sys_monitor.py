# -*- coding: utf-8 -*-
"""
Системный помощник Nox:
  • монитор производительности (CPU/RAM/GPU) + голосовые уведомления о высоких нагрузках;
  • открытие программ, сайтов и веб-поиск по команде.
Запуск: python sys_monitor.py  ->  http://127.0.0.1:8132
"""
import threading, time, json, os, socket, base64, struct, subprocess, webbrowser, shutil
import urllib.parse
from fastapi import FastAPI
import uvicorn
import psutil

IPC_HOST, IPC_PORT = "127.0.0.1", 9712
CFG = r"C:\jarvis-voice\sys_monitor.json"
CNW = 0x08000000  # CREATE_NO_WINDOW

app = FastAPI()
_state = {"cpu": 0, "ram": 0, "gpu": None, "top": "", "ts": 0}

_DEF = {"cpu_thr": 90, "ram_thr": 90, "gpu_thr": 95, "sustain": 4, "cooldown": 3600, "mon": True}
def load():
    try: d = json.load(open(CFG, encoding="utf-8"))
    except Exception: d = {}
    for k, v in _DEF.items(): d.setdefault(k, v)
    return d
def save(d):
    try: json.dump(d, open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception: pass

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

# ------------------------------------------------------------------ метрики
def _gpu():
    exe = shutil.which("nvidia-smi")
    if not exe: return None
    try:
        out = subprocess.run([exe, "--query-gpu=utilization.gpu,memory.used,memory.total",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True,
                             timeout=5, creationflags=CNW).stdout.strip().splitlines()
        if out:
            u, mu, mt = [x.strip() for x in out[0].split(",")]
            return {"util": int(float(u)), "mem_used": int(float(mu)), "mem_total": int(float(mt)),
                    "mem_pct": round(int(float(mu)) / max(1, int(float(mt))) * 100)}
    except Exception:
        pass
    return None

_IDLE = {"system idle process", "idle", ""}
def _top_proc(n=1):
    procs = []
    for p in psutil.process_iter(["name", "cpu_percent"]):
        try:
            nm = (p.info["name"] or "?")
            if nm.lower() in _IDLE: continue
            procs.append((p.info["cpu_percent"] or 0, nm))
        except Exception: pass
    procs.sort(reverse=True)
    return procs[:n]

def _sample():
    cpu = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory().percent
    gpu = _gpu()
    _state.update(cpu=round(cpu), ram=round(ram), gpu=gpu, ts=time.time())
    return cpu, ram, gpu

# ------------------------------------------------------------------ монитор нагрузок
def _mon_loop():
    psutil.cpu_percent(interval=None)                 # прогрев счётчика
    for p in psutil.process_iter(["cpu_percent"]):    # прогрев по процессам
        try: p.cpu_percent()
        except Exception: pass
    hi_cpu = hi_ram = hi_gpu = 0
    last_alert = {"cpu": 0, "ram": 0, "gpu": 0}
    while True:
        try:
            cfg = load()
            cpu, ram, gpu = _sample()
            hi_cpu = hi_cpu + 1 if cpu >= cfg["cpu_thr"] else 0
            hi_ram = hi_ram + 1 if ram >= cfg["ram_thr"] else 0
            hi_gpu = hi_gpu + 1 if (gpu and gpu["util"] >= cfg["gpu_thr"]) else 0
            now = time.time()
            if cfg.get("mon", True):
                if hi_cpu >= cfg["sustain"] and now - last_alert["cpu"] > cfg["cooldown"]:
                    last_alert["cpu"] = now
                    tp = _top_proc(1); who = tp[0][1] if tp else ""
                    _announce(f"Внимание, сэр: загрузка процессора {round(cpu)} процентов"
                              + (f", больше всего грузит {who}." if who else "."))
                if hi_ram >= cfg["sustain"] and now - last_alert["ram"] > cfg["cooldown"]:
                    last_alert["ram"] = now
                    _announce(f"Внимание, сэр: память загружена на {round(ram)} процентов.")
                if hi_gpu >= cfg["sustain"] and now - last_alert["gpu"] > cfg["cooldown"]:
                    last_alert["gpu"] = now
                    _announce(f"Внимание, сэр: видеокарта загружена на {gpu['util']} процентов.")
        except Exception:
            pass
        time.sleep(3)

# ------------------------------------------------------------------ открытие программ / сайтов / поиск
APPS = {
    "блокнот": "notepad", "калькулятор": "calc", "проводник": "explorer",
    "диспетчер задач": "taskmgr", "диспетчер": "taskmgr", "ножницы": "snippingtool",
    "paint": "mspaint", "пейнт": "mspaint", "командная строка": "cmd", "терминал": "wt",
    "параметры": "ms-settings:", "настройки windows": "ms-settings:",
}
SITES = {
    "ютуб": "https://youtube.com", "youtube": "https://youtube.com",
    "гугл": "https://google.com", "google": "https://google.com",
    "гмейл": "https://mail.google.com", "почта": "https://mail.google.com", "gmail": "https://mail.google.com",
    "телеграм": "https://web.telegram.org", "telegram": "https://web.telegram.org",
    "трейдингвью": "https://tradingview.com", "tradingview": "https://tradingview.com",
    "тбанк": "https://www.tbank.ru/invest/terminal/", "терминал тбанк": "https://www.tbank.ru/invest/terminal/",
    "мосбиржа": "https://moex.com", "смартлаб": "https://smart-lab.ru", "smartlab": "https://smart-lab.ru",
    "википедия": "https://ru.wikipedia.org", "хабр": "https://habr.com",
    "гитхаб": "https://github.com", "github": "https://github.com", "chatgpt": "https://chatgpt.com",
}

def _open(q):
    s = (q or "").strip()
    low = s.lower()
    # 1) явный поиск: «найди … [в ютубе/в гугле]»
    for trig in ("найди в ютубе ", "найди на ютубе ", "ютуб найди ", "поиск в ютубе "):
        if low.startswith(trig):
            term = s[len(trig):].strip()
            webbrowser.open("https://www.youtube.com/results?search_query=" + urllib.parse.quote(term))
            return {"ok": True, "text": f"Ищу «{term}» на YouTube, сэр."}
    for trig in ("найди ", "поиск ", "загугли ", "найти "):
        if low.startswith(trig):
            term = s[len(trig):].strip()
            # «… в гугле/в интернете» — вычистим хвост
            for suf in (" в гугле", " в google", " в интернете", " в сети"):
                if term.lower().endswith(suf): term = term[:-len(suf)].strip()
            webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote(term))
            return {"ok": True, "text": f"Ищу «{term}» в Google, сэр."}
    # 2) «открой/запусти <что-то>»
    for trig in ("открой ", "запусти ", "open ", "start "):
        if low.startswith(trig):
            low = low[len(trig):].strip(); s = s[len(trig):].strip()
            break
    # 3) известный сайт?
    if low in SITES:
        webbrowser.open(SITES[low]); return {"ok": True, "text": f"Открываю {low}, сэр."}
    # 4) известная программа?
    if low in APPS:
        try:
            if APPS[low].startswith("ms-settings"): os.startfile(APPS[low])
            else: subprocess.Popen(APPS[low], creationflags=CNW, shell=True)
            return {"ok": True, "text": f"Открываю {low}, сэр."}
        except Exception as e:
            return {"ok": False, "text": f"Не смог открыть {low}: {type(e).__name__}"}
    # 5) похоже на домен/URL?
    if "." in low and " " not in low:
        url = s if low.startswith("http") else "https://" + s
        webbrowser.open(url); return {"ok": True, "text": f"Открываю {s}, сэр."}
    # 6) попытка как программа, иначе — поиск в Google
    try:
        subprocess.Popen(s, creationflags=CNW, shell=True)
        return {"ok": True, "text": f"Запускаю {s}, сэр."}
    except Exception:
        webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote(s))
        return {"ok": True, "text": f"Не нашёл программу, ищу «{s}» в Google, сэр."}

# ------------------------------------------------------------------ HTTP
@app.on_event("startup")
def _startup():
    threading.Thread(target=_mon_loop, daemon=True).start()

@app.get("/health")
def health():
    return {"status": "ok", "mon": load().get("mon", True), "gpu": _state["gpu"] is not None}

@app.get("/stats")
def stats():
    cpu = psutil.cpu_percent(interval=0.3)            # мгновенный замер с интервалом
    ram = psutil.virtual_memory().percent; gpu = _gpu()
    _state.update(cpu=round(cpu), ram=round(ram), gpu=gpu, ts=time.time())
    tp = _top_proc(1); who = tp[0][1] if tp else ""
    parts = [f"Процессор {round(cpu)} процентов", f"память {round(ram)} процентов"]
    if gpu: parts.append(f"видеокарта {gpu['util']} процентов")
    txt = ", ".join(parts) + "."
    if who: txt += f" Больше всего грузит {who}."
    return {"text": txt, "cpu": round(cpu), "ram": round(ram), "gpu": gpu, "top": who}

@app.get("/open")
def open_q(q: str = ""):
    if not q.strip(): return {"ok": False, "text": "Что открыть, сэр?"}
    return _open(q)

@app.get("/mon")
def mon(on: int = -1):
    cfg = load()
    if on in (0, 1): cfg["mon"] = bool(on); save(cfg)
    return {"on": cfg.get("mon", True),
            "text": "Слежу за нагрузкой компьютера, сэр." if cfg.get("mon", True) else "Слежка за нагрузкой выключена, сэр."}

@app.get("/thresholds")
def thresholds(cpu: int = -1, ram: int = -1, gpu: int = -1):
    cfg = load()
    if cpu >= 0: cfg["cpu_thr"] = cpu
    if ram >= 0: cfg["ram_thr"] = ram
    if gpu >= 0: cfg["gpu_thr"] = gpu
    save(cfg)
    return {"cpu_thr": cfg["cpu_thr"], "ram_thr": cfg["ram_thr"], "gpu_thr": cfg["gpu_thr"]}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8132, log_level="warning")
