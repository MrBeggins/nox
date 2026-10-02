# -*- coding: utf-8 -*-
"""
Nox watchdog — сам следит за стеком и чинит упавшее, без участия пользователя.
Проверяет порты/процессы каждые ~20с и перезапускает то, что умерло (идемпотентно,
порт-гардед — дублей не плодит). О перезапусках уведомляет голосом (если jarvis-app жив).
Один экземпляр (singleton через привязку к 127.0.0.1:8130).
Запуск: pythonw nox_watchdog.py  (или через start-jarvis.ps1)
"""
import os, sys, time, socket, subprocess, base64, struct, json

VENV_PY  = r"C:\jarvis-voice\.venv\Scripts\python.exe"
VOICE_DIR = r"C:\jarvis-voice"
LAB_DIR  = r"C:\jarvis-lab"
OLLAMA   = os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\Ollama\ollama.exe")
YANDEX   = r"C:\Program Files\Yandex\YandexBrowser\Application\browser.exe"
CREATE_NO_WINDOW = 0x08000000
IPC_HOST, IPC_PORT = "127.0.0.1", 9712

def _port_up(port, host="127.0.0.1", timeout=1.5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False

def _proc_running(image):
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=CREATE_NO_WINDOW).stdout
        return image.lower() in out.lower()
    except Exception:
        return False

def _spawn(exe, args, cwd, out_log=None, err_log=None):
    so = open(out_log, "ab") if out_log else subprocess.DEVNULL
    se = open(err_log, "ab") if err_log else subprocess.DEVNULL
    subprocess.Popen([exe] + args, cwd=cwd, stdout=so, stderr=se,
                     creationflags=CREATE_NO_WINDOW, close_fds=True)

def _announce(text):
    """Проактивная озвучка через jarvis-app IPC (best-effort)."""
    try:
        s = socket.create_connection((IPC_HOST, IPC_PORT), timeout=3)
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

# Сервисы: имя, проверка «жив», как поднять, сколько ждать после старта (grace, сек)
def ensure_ollama():
    if not _port_up(11434) and os.path.exists(OLLAMA):
        _spawn(OLLAMA, ["serve"], VOICE_DIR); return True
    return False

def ensure_py(script, port, name, grace):
    if _port_up(port):
        return False
    _spawn(VENV_PY, [script], VOICE_DIR,
           os.path.join(VOICE_DIR, f"{name}_out.log"),
           os.path.join(VOICE_DIR, f"{name}_err.log"))
    return True

def ensure_yandex():
    if _port_up(9222):
        return False
    if os.path.exists(YANDEX):
        _spawn(YANDEX, ["--user-data-dir=" + os.path.join(VOICE_DIR, "yandex_jarvis"),
                        "--remote-debugging-port=9222", "--no-first-run",
                        "--no-default-browser-check", "https://www.tbank.ru/terminal/"], VOICE_DIR)
        return True
    return False

def ensure_app():
    if _port_up(9712):
        return False
    exe = os.path.join(LAB_DIR, r"target\release\jarvis-app.exe")
    if os.path.exists(exe):
        _spawn(exe, [], os.path.dirname(exe)); return True
    return False

def ensure_gui():
    if _proc_running("jarvis-gui.exe"):
        return False
    exe = os.path.join(LAB_DIR, r"target\release\jarvis-gui.exe")
    if os.path.exists(exe):
        _spawn(exe, [], os.path.dirname(exe)); return True
    return False

# grace-таймеры: не считать сервис «мёртвым» сразу после запуска (модель/страница грузятся)
_last_start = {}
GRACE = {"tts": 70, "yandex": 25, "app": 20, "gui": 15, "ollama": 20,
         "invest": 15, "terminal": 25, "magellan": 20, "bls": 15, "bea": 15, "default": 15}

def _grace_ok(key):
    return time.time() - _last_start.get(key, 0) > GRACE.get(key, GRACE["default"])

_TTS_SCRIPT = None
def _tts_script():
    """Выбор движка голоса: файл tts_engine.txt ('silero'/'f5'); иначе автодетект NVIDIA.
    Результат кешируется (не дёргаем nvidia-smi каждый тик → без мельканий консоли)."""
    global _TTS_SCRIPT
    if _TTS_SCRIPT:
        return _TTS_SCRIPT
    try:
        eng = open(r"C:\jarvis-voice\tts_engine.txt", encoding="utf-8").read().strip().lower()
    except Exception:
        eng = ""
    if eng == "silero":
        _TTS_SCRIPT = "tts_silero_server.py"
    elif eng == "f5":
        _TTS_SCRIPT = "tts_server.py"
    else:
        _TTS_SCRIPT = "tts_server.py"  # по умолчанию F5
        try:
            import shutil
            if not shutil.which("nvidia-smi"):
                _TTS_SCRIPT = "tts_silero_server.py"  # нет GPU -> Silero
        except Exception:
            pass
    return _TTS_SCRIPT

def _tick():
    restarted = []
    checks = [
        ("ollama",   lambda: ensure_ollama(),                                   "Ollama"),
        ("tts",      lambda: ensure_py(_tts_script(), 8123, "tts", 70),         "озвучка"),
        ("invest",   lambda: ensure_py("invest_reader.py", 8124, "ir", 15),     "портфель"),
        ("yandex",   lambda: ensure_yandex(),                                   "окно терминала"),
        ("terminal", lambda: ensure_py("terminal_reader.py", 8126, "tr", 25),   "читатель терминала"),
        ("magellan", lambda: ensure_py("magellan_reader.py", 8127, "mag", 20),  "Магеллан"),
        ("bls",      lambda: ensure_py("bls_reader.py", 8128, "bls", 15),       "статистика США"),
        ("bea",      lambda: ensure_py("bea_reader.py", 8129, "bea", 15),       "ВВП и PCE США"),
        ("app",      lambda: ensure_app(),                                      "голосовой движок"),
        ("gui",      lambda: ensure_gui(),                                      "интерфейс"),
    ]
    for key, fn, label in checks:
        if not _grace_ok(key):
            continue
        try:
            if fn():
                _last_start[key] = time.time()
                restarted.append(label)
        except Exception:
            pass
    return restarted

def main():
    # singleton
    guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        guard.bind(("127.0.0.1", 8130)); guard.listen(1)
    except OSError:
        print("watchdog already running"); return
    # первый проход — поднять всё
    time.sleep(2)
    while True:
        done = _tick()
        if done:
            # дать подняться и уведомить (если движок жив)
            time.sleep(8)
            if _port_up(9712):
                _announce("Восстановил: " + ", ".join(done) + ".")
        time.sleep(20)

if __name__ == "__main__":
    main()
