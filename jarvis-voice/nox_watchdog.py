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
XTTS_PY  = r"C:\jarvis-voice\.venv-xtts\Scripts\python.exe"   # изолированное окружение для XTTS
VOICE_DIR = r"C:\jarvis-voice"
LAB_DIR  = r"C:\jarvis-lab"
OLLAMA   = os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\Ollama\ollama.exe")
YANDEX   = next((p for p in (
    r"C:\Program Files\Yandex\YandexBrowser\Application\browser.exe",
    r"C:\Program Files (x86)\Yandex\YandexBrowser\Application\browser.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Yandex\YandexBrowser\Application\browser.exe"),
) if os.path.exists(p)), r"C:\Program Files\Yandex\YandexBrowser\Application\browser.exe")
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
                        "--no-default-browser-check"], VOICE_DIR)   # вкладку терминала откроет ридер (одну)
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
         "invest": 15, "terminal": 25, "magellan": 20, "bls": 15, "bea": 15, "tg": 15,
         "sys": 12, "notes": 12, "default": 15}

def _grace_ok(key):
    return time.time() - _last_start.get(key, 0) > GRACE.get(key, GRACE["default"])

def _tts_choice():
    """Движок голоса из tts_engine.txt ('silero'/'xtts'/'f5'); иначе автодетект NVIDIA.
    Возвращает (интерпретатор, скрипт). XTTS — в своём окружении. Без кеша: смена движка
    подхватывается на следующем тике (чтение файла дешёвое)."""
    try:
        eng = open(r"C:\jarvis-voice\tts_engine.txt", encoding="utf-8").read().strip().lower()
    except Exception:
        eng = ""
    if eng == "xtts" and os.path.exists(XTTS_PY):
        return (XTTS_PY, "tts_xtts_server.py")
    if eng == "silero":
        return (VENV_PY, "tts_silero_server.py")
    if eng == "f5":
        return (VENV_PY, "tts_server.py")
    try:
        import shutil
        if not shutil.which("nvidia-smi"):
            return (VENV_PY, "tts_silero_server.py")   # нет GPU -> Silero
    except Exception:
        pass
    return (VENV_PY, "tts_server.py")                   # по умолчанию F5

def ensure_tts():
    if _port_up(8123):
        return False
    py, script = _tts_choice()
    _spawn(py, [script], VOICE_DIR,
           os.path.join(VOICE_DIR, "tts_out.log"), os.path.join(VOICE_DIR, "tts_err.log"))
    return True

STOPPED_FLAG = r"C:\jarvis-voice\nox_stopped.flag"   # пользователь остановил Nox (трей «Выход» / кнопка)

# «Фичи» (nox_features.json): служба выключена — не поднимать и погасить, если работает
_SERVICE_FEATURE = {"magellan": ("magellan", [8127]), "bls": ("us_macro", [8128]), "bea": ("us_macro", [8129]),
                    "notes": ("notes", [8133]), "ollama": ("ollama", [])}   # Ollama — только не запускаем


def _feature_on(key):
    try:
        sys.path.insert(0, VOICE_DIR) if VOICE_DIR not in sys.path else None
        import nox_features
        return nox_features.enabled(key)
    except Exception:
        return True


def _stop_port(port):
    """Погасить нашу службу, слушающую порт (python из .venv запускает дочерний python — гасим обоих)."""
    try:
        import psutil
        for c in psutil.net_connections(kind="tcp"):
            if c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN and c.pid:
                p = psutil.Process(c.pid)
                parent = p.parent()
                p.kill()
                if parent and "python" in (parent.name() or "").lower() and \
                        any(a.endswith(".py") for a in (parent.cmdline() or [])):
                    parent.kill()
                return True
    except Exception:
        pass
    return False


def _focus_quiet():
    try:
        import nox_features
        return nox_features.focus_active()
    except Exception:
        return False


def _user_stopped():
    return os.path.exists(STOPPED_FLAG)

def _tick(include_gui=True):
    restarted = []
    checks = [
        ("ollama",   lambda: ensure_ollama(),                                   "Ollama"),
        ("tts",      lambda: ensure_tts(),                                      "озвучка"),
        ("invest",   lambda: ensure_py("invest_reader.py", 8124, "ir", 15),     "портфель"),
        ("yandex",   lambda: ensure_yandex(),                                   "окно терминала"),
        ("terminal", lambda: ensure_py("terminal_reader.py", 8126, "tr", 25),   "читатель терминала"),
        ("magellan", lambda: ensure_py("magellan_reader.py", 8127, "mag", 20),  "Магеллан"),
        ("bls",      lambda: ensure_py("bls_reader.py", 8128, "bls", 15),       "статистика США"),
        ("bea",      lambda: ensure_py("bea_reader.py", 8129, "bea", 15),       "ВВП и PCE США"),
        ("sys",      lambda: ensure_py("sys_monitor.py", 8132, "sys", 12),      "монитор ПК"),
        ("notes",    lambda: ensure_py("nox_notes.py", 8133, "notes", 12),      "заметки"),
        ("app",      lambda: ensure_app(),                                      "голосовой движок"),
        ("gui",      lambda: ensure_gui(),                                      "интерфейс"),
    ]
    for key, fn, label in checks:
        # окно GUI поднимаем только при запуске: если пользователь его закрыл — не переоткрываем
        if key == "gui" and not include_gui:
            continue
        feat = _SERVICE_FEATURE.get(key)
        if feat and not _feature_on(feat[0]):
            for port in feat[1]:
                _stop_port(port)
            continue
        if not _grace_ok(key):
            continue
        try:
            if fn():
                _last_start[key] = time.time()
                restarted.append(label)
        except Exception:
            pass
    return restarted

def _voice_ready():
    # голосовой сервер (F5/Silero) слушает 8123 и реально готов отвечать
    return _port_up(8123)

def main():
    # singleton
    guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        guard.bind(("127.0.0.1", 8130)); guard.listen(1)
    except OSError:
        print("watchdog already running"); return
    time.sleep(2)
    first = True
    while True:
        if _user_stopped():
            # «Выход»/«Остановить Nox»: ничего не поднимаем назад, сторож завершается
            return
        done = _tick(include_gui=first)
        if first:
            # ПЕРВЫЙ запуск: молча поднимаем всё, ждём движок + готовый голос (до ~2.5 мин),
            # затем ОДНА фраза вместо спама «запустил то, запустил это».
            for _ in range(75):
                if _port_up(9712) and _voice_ready():
                    break
                time.sleep(2)
            time.sleep(3)   # дать F5 прогреться (первый инференс)
            if _port_up(9712) and _feature_on("greeting"):
                _announce("Все системы работают в норме, сэр.")
            first = False
        elif done and _feature_on("restart_notices") and not _focus_quiet():
            # уже после старта: если что-то упало и поднялось — краткое уведомление (голосом F5)
            time.sleep(8)
            if _port_up(9712) and _voice_ready():
                _announce("Восстановил: " + ", ".join(done) + ".")
        time.sleep(20)

if __name__ == "__main__":
    main()
