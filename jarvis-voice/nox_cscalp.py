"""CScalp без API: Нокс сам находит стакан по тикеру и жмёт горячую клавишу CScalp.

Как это устроено:
  * раскладку (все стаканы: тикер, окно, прямоугольник) даёт UI Automation — cscalp_scan.ps1,
    проход ~4 с, поэтому держим кэш и обновляем его в фоне;
  * исполнение — НЕ клик по цене (так ставится лимитка по случайной цене), а горячая клавиша
    CScalp: окно вперёд → курсор над нужным стаканом → клавиша (одним пакетом SendInput,
    чужое движение мыши не вклинится) → курсор и прежнее окно возвращаются;
  * клавиши берутся из журналов CScalp (HotKeysHandler: Key=…; Command=…), можно переопределить
    в cscalp_keys.json ({"SELL_BY_MARKET": "G"});
  * подтверждение — по журналам CScalp: HotKeysHandler показывает, КАКОЙ стакан принял клавишу,
    Trader_N — что ушло на биржу (направление, объём, цена) и за сколько мс.

CLI:
  python nox_cscalp.py scan                      — какие стаканы видит
  python nox_cscalp.py keys                      — какие клавиши знает
  python nox_cscalp.py test SILVM-12.26 SILVM-3.27     — НЕТОРГОВАЯ проверка: агрегация +, затем − (вернуть)
  python nox_cscalp.py buy SILVM-12.26 SILVM-3.27      — план без нажатия (сухой прогон)
  python nox_cscalp.py buy SILVM-12.26 SILVM-3.27 --go — настоящие заявки во все стаканы; только по рынку
                                                         (T купить, Y продать)
"""
import ctypes
import ctypes.wintypes as wt
import datetime
import html
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CS_DIR = r"C:\Program Files (x86)\FSR Launcher\SubApps\CS"
LOG_ROOT = os.path.join(CS_DIR, "Log")
SCAN_PS = os.path.join(HERE, "cscalp_scan.ps1")
SCAN_OUT = os.path.join(HERE, "cscalp_layout.json")
KEYS_FILE = os.path.join(HERE, "cscalp_keys.json")
SPEED_LOG = os.path.join(HERE, "speed_log.jsonl")
CREATE_NO_WINDOW = 0x08000000

# наша команда → команда CScalp. Торгуем ТОЛЬКО по рынку (решение пользователя 2026-10-12)
COMMANDS = {
    "buy_market": "BUY_BY_MARKET",
    "sell_market": "SELL_BY_MARKET",
    "agg_plus": "PRICE_AGGREGATION_PLUS",
    "agg_minus": "PRICE_AGGREGATION_MINUS",
}
TRADE_COMMANDS = {"buy_market", "sell_market"}
_DIRECTION = {"buy_market": "Buy", "sell_market": "Sell"}

# имя клавиши WPF (как в журнале CScalp) → виртуальный код
_VK = {"Space": 0x20, "Tab": 0x09, "Return": 0x0D, "Enter": 0x0D, "Escape": 0x1B,
       "OemPlus": 0xBB, "OemMinus": 0xBD, "OemComma": 0xBC, "OemPeriod": 0xBE,
       "Oem1": 0xBA, "OemQuestion": 0xBF, "Oem3": 0xC0, "OemOpenBrackets": 0xDB,
       "Oem5": 0xDC, "Oem6": 0xDD, "OemQuotes": 0xDE,
       "Left": 0x25, "Up": 0x26, "Right": 0x27, "Down": 0x28,
       "Insert": 0x2D, "Delete": 0x2E, "Home": 0x24, "End": 0x23, "PageUp": 0x21, "PageDown": 0x22}
_VK.update({chr(c): c for c in range(ord("A"), ord("Z") + 1)})
_VK.update({f"D{i}": 0x30 + i for i in range(10)})
_VK.update({f"NumPad{i}": 0x60 + i for i in range(10)})
_VK.update({f"F{i}": 0x6F + i for i in range(1, 25)})
_MODIFIER_KEYS = {"LeftCtrl", "RightCtrl", "LeftShift", "RightShift", "LeftAlt", "RightAlt", "LWin", "RWin"}

# ── WinAPI ──────────────────────────────────────────────────────────────────
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
try:  # координаты UIA — физические пиксели; работаем в них же
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    pass
user32.WindowFromPoint.argtypes = [wt.POINT]
user32.WindowFromPoint.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetAncestor.restype = wt.HWND
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class _IU(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _IU)]


MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x0001, 0x8000, 0x4000
KEYEVENTF_KEYUP = 0x0002


def _mouse_move(x, y):
    vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
    vw, vh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
    i = INPUT(type=0)
    i.u.mi = MOUSEINPUT(int(round((x - vx) * 65535 / max(vw - 1, 1))), int(round((y - vy) * 65535 / max(vh - 1, 1))),
                        0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, 0, 0)
    return i


def _key(vk, up=False):
    i = INPUT(type=1)
    i.u.ki = KEYBDINPUT(vk, user32.MapVirtualKeyW(vk, 0), KEYEVENTF_KEYUP if up else 0, 0, 0)
    return i


def _send(inputs):
    arr = (INPUT * len(inputs))(*inputs)
    return user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def _cursor():
    p = wt.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def _root_at(x, y):
    h = user32.WindowFromPoint(wt.POINT(int(x), int(y)))
    return user32.GetAncestor(h, 2) if h else None  # GA_ROOT


def _pid_of(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _activate(hwnd):
    """Вывести окно CScalp вперёд без клика (AttachThreadInput — обход запрета SetForegroundWindow)."""
    if user32.GetForegroundWindow() == hwnd:
        return True
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    fg = user32.GetForegroundWindow()
    cur = kernel32.GetCurrentThreadId()
    fgt = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(fgt and fgt != cur and user32.AttachThreadInput(cur, fgt, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(cur, fgt, False)
    for _ in range(20):
        if user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.005)
    return False


def _busy_reason():
    """Не жать, пока пользователь держит кнопку мыши или модификатор (Ctrl у вас = закрыть позицию)."""
    for vk, name in ((0x01, "левая кнопка мыши"), (0x02, "правая кнопка мыши"), (0x10, "Shift"),
                     (0x11, "Ctrl"), (0x12, "Alt")):
        if user32.GetAsyncKeyState(vk) & 0x8000:
            return f"зажата {name}"
    return None


# ── Раскладка ───────────────────────────────────────────────────────────────
_layout = {"t": 0.0, "data": None}
_layout_lock = threading.Lock()


def _prep(data):
    for d in data.get("doms") or []:
        d["ticker"] = norm(d.get("ticker"))
    data["hidden"] = [h for h in data.get("hidden") or [] if h.get("ticker") and "," not in h["ticker"]]
    return data


def scan(max_age=20.0):
    """Все стаканы CScalp. Кэш max_age секунд (в памяти, затем cscalp_layout.json); max_age=0 — пересканировать."""
    with _layout_lock:
        if _layout["data"] is None and max_age > 0:
            try:  # сохранённая раскладка прошлого скана (новый процесс не ждёт 4 с)
                with open(SCAN_OUT, encoding="utf-8-sig") as f:
                    # положения окон на момент того скана неизвестны — фоновая сверка пересканирует при простое
                    _layout.update(t=os.path.getmtime(SCAN_OUT), data=_prep(json.load(f)), rects=None)
            except (OSError, ValueError):
                pass
        if _layout["data"] is not None and time.time() - _layout["t"] < max_age:
            return _layout["data"]
        try:
            os.remove(SCAN_OUT)
        except OSError:
            pass
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SCAN_PS, "-Out", SCAN_OUT],
                       capture_output=True, timeout=40, creationflags=CREATE_NO_WINDOW)
        try:
            with open(SCAN_OUT, encoding="utf-8-sig") as f:
                data = json.load(f)
        except Exception as e:
            data = {"ok": False, "error": f"скан не удался: {e}", "doms": [], "hidden": []}
        _layout.update(t=time.time(), data=_prep(data))
        _layout["rects"] = _win_rects({int(d["hwnd"]) for d in data.get("doms") or [] if d.get("hwnd")})
        return data


def _win_rects(hwnds):
    """{окно: (лево, верх, право, низ) | None — окна нет}. Дёшево: без UI Automation."""
    out = {}
    for h in hwnds:
        r = wt.RECT()
        out[h] = (r.left, r.top, r.right, r.bottom) if user32.IsWindow(h) and user32.GetWindowRect(h, ctypes.byref(r)) else None
    return out


_bg = {"check": 0.0, "scan": 0.0}


def _bg_refresh():
    """Простой рабочего потока: окна CScalp перенесли/развернули/закрыли — пересканировать заранее, чтобы клик стрелки
    шёл по свежей раскладке (17.10: устаревшие точки после переноса окна стоили 3–28 с на клике)."""
    now = time.time()
    if not _bg.get("on", True) or now - _bg["check"] < 1.0:
        return
    _bg["check"] = now
    if _layout.get("data") is None:
        scan(float("inf"))           # после старта: подхватить сохранённую раскладку (положения окон — сверим ниже)
    data = _layout.get("data")
    if not data or not data.get("doms"):
        return
    hw = {int(d["hwnd"]) for d in data["doms"] if d.get("hwnd")}
    if _layout.get("rects") is not None and _win_rects(hw) == _layout["rects"]:
        return
    if now - _bg["scan"] < 5.0:
        return
    _bg["scan"] = now
    scan(0)


# Быстрая проверка «какой стакан сейчас под точкой» (UI Automation, ~10–30 мс) — вместо 4-секундного скана
_uia_local = threading.local()


def _uia():
    u = getattr(_uia_local, "uia", None)
    if u is None:
        import comtypes
        import comtypes.client
        comtypes.CoInitialize()
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as U
        u = comtypes.client.CreateObject(U.CUIAutomation, interface=U.IUIAutomation)
        _uia_local.uia = u
        _uia_local.walker = u.ControlViewWalker
    return u, _uia_local.walker


def ticker_at(x, y):
    """Тикер стакана CScalp под точкой экрана (None — там не стакан)."""
    try:
        u, walker = _uia()
        el = u.ElementFromPoint(wt.POINT(int(x), int(y)))
        for _ in range(12):
            if el is None:
                return None
            if el.CurrentClassName == "MainControl":
                hdr = walker.GetFirstChildElement(walker.GetParentElement(el))
                while hdr is not None:
                    tb = walker.GetFirstChildElement(hdr)
                    while tb is not None:
                        if tb.CurrentClassName == "TextBlock" and tb.CurrentName:
                            return norm(tb.CurrentName)
                        tb = walker.GetNextSiblingElement(tb)
                    hdr = walker.GetNextSiblingElement(hdr)
                return None
            el = walker.GetParentElement(el)
    except Exception:
        return None
    return None


def norm(t):
    return re.sub(r"\s+", "", str(t or "")).upper()


def find(ticker, index=None, max_age=20.0, hwnd=None):
    """Видимый стакан с тикером. index — какой по счёту при дублях (слева направо, сверху вниз).
    hwnd — только в этом окне CScalp (группа стрелки/ветки: все её стаканы — из одного окна)."""
    t = norm(ticker)
    data = scan(max_age)
    hits = sorted((d for d in data.get("doms") or [] if d["ticker"] == t and d.get("main")
                   and (hwnd is None or int(d.get("hwnd") or 0) == int(hwnd))),
                  key=lambda d: (d["main"][1] < -500, d["main"][0], d["main"][1]))
    if not hits and max_age > 0 and time.time() - _layout["t"] > 3:
        # в сохранённой раскладке нет — вдруг стакан только что открыли (свежую раскладку заново не сканируем)
        return find(ticker, index, 0, hwnd)
    if not hits:
        hidden = [h for h in data.get("hidden") or [] if norm(h["ticker"]) == t]
        why = "есть на скрытой вкладке — откройте её" if hidden else "не открыт в CScalp"
        return None, f"стакан {t} {why}"
    if index is None:
        return hits[0], (f"стаканов {t} несколько ({len(hits)}), беру первый" if len(hits) > 1 else "")
    if index >= len(hits):
        return None, f"стакана {t} №{index + 1} нет (всего {len(hits)})"
    return hits[index], ""


def _target_point(d):
    x, y, w, h = d.get("dom") or d["main"]
    return x + w // 2, y + min(h // 2, 300)


# ── Клавиши ─────────────────────────────────────────────────────────────────
def _strip(s):
    return html.unescape(re.sub(r"<[^>]*>", " ", s))


def _log_dirs():
    out = []
    try:
        for n in os.listdir(LOG_ROOT):
            try:
                out.append((datetime.datetime.strptime(n, "%d.%m.%Y"), os.path.join(LOG_ROOT, n)))
            except ValueError:
                pass
    except OSError:
        pass
    return [p for _, p in sorted(out)]


def keys():
    """Команда CScalp → клавиша: из журналов (последнее нажатие побеждает) + cscalp_keys.json."""
    m = {}
    for d in _log_dirs():
        try:
            with open(os.path.join(d, "HotKeysHandler.html"), encoding="utf-8", errors="ignore") as f:
                for k, c in re.findall(r"Key=(\w+); Command=(\w+)", f.read()):
                    m[c] = k
        except OSError:
            pass
    try:
        with open(KEYS_FILE, encoding="utf-8") as f:
            m.update(json.load(f))
    except (OSError, ValueError):
        pass
    return m


# ── Журналы CScalp: подтверждение ───────────────────────────────────────────
def _today_dir():
    d = os.path.join(LOG_ROOT, datetime.date.today().strftime("%d.%m.%Y"))
    if os.path.isdir(d):
        return d
    dirs = _log_dirs()
    return dirs[-1] if dirs else None


_tickers_cache = {}  # путь Trader_N → (размер, тикер)


def _watch_paths(d, ticker):
    """Журнал клавиш + журналы стаканов с этим тикером (тикер файла = последний Ticker= в нём)."""
    paths = [os.path.join(d, "HotKeysHandler.html")]
    try:
        names = [n for n in os.listdir(d) if n.startswith("Trader_")]
    except OSError:
        names = []
    for n in names:
        p = os.path.join(d, n)
        try:
            size = os.stat(p).st_size
        except OSError:
            continue
        hit = _tickers_cache.get(p)
        if not hit or hit[0] != size:
            hit = (size, _file_ticker(p))
            _tickers_cache[p] = hit
        if hit[1] == ticker:
            paths.append(p)
    return paths


def _snapshot(paths):
    # os.stat, не scandir: у файла, который CScalp держит открытым, размер в листинге папки отстаёт
    sizes = {}
    for p in paths:
        try:
            sizes[p] = os.stat(p).st_size
        except OSError:
            pass
    return sizes


def _read_from(path, off):
    try:
        with open(path, "rb") as f:
            f.seek(off)
            return _strip(f.read().decode("utf-8", "ignore"))
    except OSError:
        return ""


def _file_ticker(path):
    try:
        with open(path, "rb") as f:
            found = re.findall(rb"Ticker=[A-Z]+\.([A-Za-z0-9.\-]+)", f.read())
        return found[-1].decode().upper() if found else ""
    except OSError:
        return ""


_TS = re.compile(r"\[(\d\d):(\d\d):(\d\d)\.(\d{3})\]")


def _ms_after(line, t0):
    m = _TS.search(line)
    if not m:
        return None
    h, mi, s, ms = map(int, m.groups())
    ts = t0.replace(hour=h, minute=mi, second=s, microsecond=ms * 1000)
    return int((ts - t0).total_seconds() * 1000)




def _watch(before, t0, tickers, want_trade, timeout):
    """Ждём записи CScalp по всем стаканам: кто принял клавишу и что ушло в заявку."""
    res = {"routes": {}, "stray": [], "orders": {t: [] for t in tickers}}
    seen = set()
    end = time.time() + timeout
    finished = False
    while time.time() < end:
        now = _snapshot(list(before))
        for path, size in now.items():
            old = before.get(path, 0)
            if size <= old:
                continue
            # CScalp переписывает хвост HTML — читаем с перекрытием, старое отсекаем по времени и дублям
            text = _read_from(path, max(0, old - 2048))
            before[path] = size
            fresh = []
            for line in text.splitlines():
                s = line.strip()
                ms = _ms_after(s, t0)
                if ms is None or ms < -20 or s in seen:
                    continue
                seen.add(s)
                fresh.append((s, ms))
            if path.endswith("HotKeysHandler.html"):
                for s, ms in fresh:
                    m = re.search(r"MainControl\(([^)]*)\): Key=(\w+); Command=(\w+)", s)
                    if not m:
                        continue
                    tk = norm(m.group(1))
                    if tk in res["orders"] and tk not in res["routes"]:
                        res["routes"][tk] = (m.group(3), ms)
                    elif tk not in res["orders"]:
                        res["stray"].append((tk, m.group(3), ms))
            else:
                tk = _file_ticker(path)
                if tk not in res["orders"]:
                    continue
                for s, ms in fresh:
                    if re.search(r"ExecuteTradeCommandReq|order Amount=|SendLimitOrder:|SendMarket|Success=|not ready|"
                                 r"Error|Ошибк|отклон", s, re.I):
                        res["orders"][tk].append({"ms": ms, "text": re.sub(r"^\s*\[[^\]]*\]\s*\[\w+\]\s*", "", s)})
        done = all(t in res["routes"] for t in tickers) and (
            not want_trade or all(any("Amount=" in o["text"] or "not ready" in o["text"] for o in res["orders"][t])
                                  for t in tickers))
        if done and not finished:
            finished = True
            end = min(end, time.time() + 0.2)  # дочитать Success=/ошибку
        time.sleep(0.01)
    return res


# ── Исполнение ──────────────────────────────────────────────────────────────
_exec_lock = threading.Lock()


def group_window(pairs, max_age=20.0):
    """Окно CScalp, где видны ВСЕ стаканы группы (с дублями: «MIX ×2» — два MIX). Один и тот же тикер бывает
    открыт и в главном окне, и во вкладке «нокс» отдельного окна — без этого жался бы самый левый, то есть чужой.
    Нет такого окна — None (как раньше: самый левый стакан)."""
    need = {}
    for t, _a, idx in pairs:
        need[t] = max(need.get(t, 0), (idx + 1) if idx is not None else 1)
    have = {}
    for d in scan(max_age).get("doms") or []:
        if d.get("main") and d.get("hwnd"):
            h = have.setdefault(int(d["hwnd"]), {})
            h[d["ticker"]] = h.get(d["ticker"], 0) + 1
    for hw, cnt in have.items():
        if all(cnt.get(t, 0) >= n for t, n in need.items()):
            return hw
    return None


def plan(ticker, action, index=None, max_age=20.0, hwnd=None):
    """Проверки без нажатий: стакан найден, клавиша известна, окно CScalp доступно."""
    if action not in COMMANDS:
        return {"ok": False, "error": f"неизвестное действие {action}"}
    d, note = find(ticker, index, max_age, hwnd)
    if not d:
        return {"ok": False, "ticker": norm(ticker), "error": note}
    cs_cmd = COMMANDS[action]
    key = keys().get(cs_cmd)
    if not key:
        return {"ok": False, "error": f"в CScalp не назначена клавиша для {cs_cmd} — назначьте её в настройках горячих клавиш"
                                      f" (или впишите в cscalp_keys.json)"}
    if key in _MODIFIER_KEYS or key not in _VK:
        return {"ok": False, "error": f"клавишу {key} для {cs_cmd} не умею нажимать"}
    x, y = _target_point(d)
    return {"ok": True, "ticker": d["ticker"], "window": d.get("window"), "hwnd": int(d["hwnd"]), "point": [x, y],
            "action": action, "cs_command": cs_cmd, "key": key, "note": note}


def _check_targets(plans):
    """Перед ПЕРВЫМ нажатием убедиться, что под каждой точкой сейчас нужный стакан. Возвращает список проблем."""
    problems, active = [], None
    for p in plans:
        if p["hwnd"] != active:
            if not _activate(p["hwnd"]):
                problems.append(f"окно CScalp со стаканом {p['ticker']} не выводится вперёд")
                continue
            active = p["hwnd"]
        x, y = p["point"]
        root = _root_at(x, y)
        if root != p["hwnd"]:
            r = wt.RECT()
            user32.GetWindowRect(p["hwnd"], ctypes.byref(r))
            if not (r.left <= x < r.right and r.top <= y < r.bottom):
                # точка вне окна CScalp: окно перенесли/развернули — раскладка устарела (нужен скан, это не перекрытие)
                problems.append(f"окно CScalp со стаканом {p['ticker']} сдвинулось")
                continue
            title = ""
            try:
                buf = ctypes.create_unicode_buffer(120)
                user32.GetWindowTextW(root, buf, 120)
                title = buf.value
            except Exception:
                pass
            problems.append(f"стакан {p['ticker']} перекрыт окном «{title or 'без названия'}»")
            continue
        under = ticker_at(x, y)
        if under != p["ticker"]:
            problems.append(f"под точкой {p['ticker']} сейчас {under or 'не стакан'}")
    return problems


def _verdict(r, action, res):
    """Итог по одному стакану по журналам CScalp."""
    t = r["ticker"]
    route = res["routes"].get(t)
    orders = res["orders"].get(t) or []
    r.update(command=route[0] if route else None, hotkey_ms=route[1] if route else None, orders=orders)
    if not route:
        r.update(ok=False, error=f"{t}: CScalp не записал нажатие — заявка, скорее всего, не ушла")
        return r
    if route[0] != COMMANDS[action]:
        r.update(ok=False, error=f"{t}: CScalp выполнил {route[0]}, а не {COMMANDS[action]} — проверьте клавишу!")
        return r
    r["ok"] = True
    if action in TRADE_COMMANDS:
        want = _DIRECTION[action]
        dirs = [m.group(1) for o in orders for m in [re.search(r"Direction=(\w+)", o["text"])] if m]
        fail = [o["text"] for o in orders if re.search(r"Success=False|not ready|Error|Ошибк|отклон", o["text"], re.I)]
        r["direction"] = dirs[0] if dirs else None
        if any(x != want for x in dirs):
            r.update(ok=False, error=f"{t}: заявка ушла в другую сторону ({', '.join(dirs)}), ожидалось {want}!")
        elif fail:
            r.update(ok=False, error=f"{t}: CScalp: {fail[0]}")
        elif not dirs:
            r.update(ok=False, error=f"{t}: клавиша принята, но заявки в журнале нет за 2 с")
    return r


def execute_many(tickers, action, go=False, restore=True):
    """Одна команда над несколькими стаканами (CLI «купить оба»)."""
    return execute_orders([(t, action) for t in tickers], go=go, restore=restore)


_last_trace = {"t0": 0.0, "steps": []}     # шаги последнего execute_orders (видно и посреди зависания)
_warm_hwnds = set()                         # окна CScalp, в которые уже жали в этом запуске


def execute_orders(pairs, go=False, restore=True, deadline=None, probe=False, hwnd=None):
    """Заявки [(тикер, действие)] над несколькими стаканами — ветка наблюдателя / сценарий на N стаканов.

    Раскладка — из кэша/сохранённого файла (без 4-секундного скана). До первого нажатия проверяются ВСЕ
    стаканы (под точкой именно нужный тикер, ~10–30 мс на стакан); если хоть один не на месте —
    пересканировать и проверить снова, не вышло — не жать НИ ОДИН (чтобы не исполнить полсценария).
    Нажатия идут подряд: [курсор → клавиша] одним пакетом SendInput на каждый стакан.
    """
    t_start = time.perf_counter()
    _last_trace.update(t0=t_start, steps=[], at=time.strftime("%H:%M:%S"))
    mark = lambda n: _last_trace["steps"].append((n, round((time.perf_counter() - t_start) * 1000)))
    # (тикер, действие[, № стакана с 1]) — один тикер дважды («MIX ×2» на вкладке «нокс»): без № — по порядку слева
    norm_pairs, seen_n, used = [], {}, set()
    for p in pairs:
        t, a = norm(p[0]), p[1]
        n = p[2] if len(p) > 2 and p[2] else None
        idx = int(n) - 1 if n else 0
        while (t, idx) in used:          # тот же тикер ещё раз с тем же № — следующий стакан (его нет — отказ всей группе)
            idx += 1
        used.add((t, idx))
        seen_n[t] = max(seen_n.get(t, 0), idx + 1)
        norm_pairs.append((t, a, idx))
    if len({(t, i) for t, _, i in norm_pairs}) != len(norm_pairs):
        return {"ok": False, "error": "один и тот же стакан указан дважды", "results": []}
    pairs = [(t, a) for t, a, _ in norm_pairs]
    tickers = sorted(set(t for t, _ in pairs))
    age = float("inf") if go else 20.0
    dup = any(n > 1 for n in seen_n.values())
    mark("разбор заявок")
    # hwnd задан (окно вкладки «нокс») — жмём только в нём, ничего не угадываем
    hw = int(hwnd) if hwnd else (group_window(norm_pairs, age) if len(norm_pairs) > 1 else None)
    mark("окно группы")
    if dup and hw is None:
        hw = group_window(norm_pairs, 0)        # раскладка могла поменяться — пересканировать
        if hw is None:
            # «MIX №2» без общего окна нашёлся бы в ЧУЖОМ окне — не жать ни один
            return {"ok": False, "error": "нет окна CScalp, где открыты все стаканы группы (" +
                    ", ".join(f"{t} ×{n}" for t, n in seen_n.items()) + ") — не нажимал ни один", "results": []}
    plans = [plan(t, a, i if (i or seen_n.get(t, 0) > 1) else None, max_age=age, hwnd=hw) for t, a, i in norm_pairs]
    mark("планы стаканов")
    bad = [p for p in plans if not p["ok"]]
    if bad or not go:
        return {"ok": not bad, "error": "; ".join(p["error"] for p in bad), "results": plans}
    with _exec_lock:
        mark("замок исполнения")
        why = _busy_reason()
        if why:
            return {"ok": False, "error": f"не нажимаю: {why}", "results": plans}
        prev_fg, prev_xy = user32.GetForegroundWindow(), _cursor()
        problems = _check_targets(plans)
        mark("проверка целей" + (f" (проблемы: {'; '.join(problems)})" if problems else ""))
        if problems and all("перекрыт" in x for x in problems):
            # стаканы на месте, но поверх — другое окно (всплывашка, браузер): скан не поможет. Секунду дать
            # окну исчезнуть и проверить ещё раз; нет — отказ с названием мешающего окна
            time.sleep(0.15)
            problems = _check_targets(plans)
            mark("повторная проверка")
            if problems and all("перекрыт" in x for x in problems):
                return {"ok": False, "error": "; ".join(sorted(set(problems))) + " — не нажимал ни один",
                        "results": plans, "steps": list(_last_trace["steps"])}
        if problems:
            # стаканы сдвинулись / окно CScalp пересоздано — ОДИН скан (~4 с) и дальше только по свежей раскладке.
            # 16-17.10: здесь были 7 полных сканов (окно группы + каждый стакан с max_age=0) — 25–30 с ожидания
            scan(0)
            mark("пересканирование")
            if not hwnd:
                hw = group_window(norm_pairs, float("inf")) if len(norm_pairs) > 1 else None
            plans = [plan(t, a, i if (i or seen_n.get(t, 0) > 1) else None, max_age=float("inf"), hwnd=hw)
                     for t, a, i in norm_pairs]
            bad = [p for p in plans if not p["ok"]]
            if bad:
                return {"ok": False, "error": "; ".join(p["error"] for p in bad) + " — не нажимал ни один", "results": plans}
            problems = _check_targets(plans)
            if problems:
                return {"ok": False, "error": "; ".join(problems) + " — не нажимал ни один", "results": plans}
        mark("готов к нажатию")
        if deadline and time.time() > deadline:
            # 16.10: окно Нокса держало фокус, пока ждало ответа, — нажатие ушло через 25 с, а отчёт сказал
            # «ничего не нажато». Опоздавшую рыночную заявку не жмём вовсе
            return {"ok": False, "error": f"опоздал на {time.time() - deadline + 20:.0f} с от команды — не нажимал ни один",
                    "results": plans, "steps": list(_last_trace["steps"])}
        if probe:                               # проба: всё, что перед нажатием, — без клавиш
            if restore and prev_fg:
                _activate(prev_fg)
            return {"ok": True, "probe": True, "results": plans, "steps": list(_last_trace["steps"])}
        # первое нажатие в это окно CScalp после запуска: 17.10 из 6 стаканов клавишу принял только первый —
        # окно ещё не было готово к быстрым нажатиям. Один раз дать ему 60 мс
        cold = [h for h in {p["hwnd"] for p in plans} if h not in _warm_hwnds]
        if cold:
            time.sleep(0.06)
            _warm_hwnds.update(cold)
            mark("прогрев окна")
        t0 = datetime.datetime.now()
        tp = time.perf_counter()
        active = user32.GetForegroundWindow()
        results = []
        for k, p in enumerate(plans):
            if p["hwnd"] != active:
                _activate(p["hwnd"])
                active = p["hwnd"]
            if k:
                time.sleep(0.002)           # 2 мс между стаканами: CScalp успевает понять, какой стакан под курсором
            vk = _VK[p["key"]]
            sent = _send([_mouse_move(*p["point"]), _key(vk), _key(vk, up=True)])
            results.append({**p, "sent": sent, "t_click": time.time(),
                            "press_at_ms": round((time.perf_counter() - tp) * 1000, 1)})
        t_sent = time.perf_counter()
        if restore:
            time.sleep(0.03)
            _send([_mouse_move(*prev_xy)])
            if prev_fg and prev_fg != active:
                _activate(prev_fg)
        # журналы читаем уже после нажатий (хвост + отсечка по времени) — на скорость сделки не влияет
        d = _today_dir()
        before = {}
        for t in tickers:
            for path in _watch_paths(d, t) if d else []:
                try:
                    before[path] = max(0, os.stat(path).st_size - 16384)
                except OSError:
                    pass
        trade = any(a in TRADE_COMMANDS for _, a in pairs)
        res = _watch(before, t0, tickers, trade, 2.0 if trade else 1.0)
        for r in results:
            _verdict(r, r["action"], res)
        # один тикер в нескольких стаканах (MIX ×2): журнал CScalp общий на тикер — считаем, сколько нажатий он
        # реально принял; лишние «успехи» — неправда (17.10 отчёт сказал «купил MIX, купил MIX» при одном)
        for t in tickers:
            same = [r for r in results if r["ticker"] == t]
            if len(same) < 2 or not trade:
                continue
            got = sum(1 for o in res["orders"].get(t) or [] if "ExecuteTradeCommandReq" in o["text"])
            for k, r in enumerate(same):
                if k >= got and r.get("ok"):
                    r.update(ok=False, error=f"{t} №{k + 1}: CScalp не записал нажатие — заявка, скорее всего, не ушла")
        out = {"ok": all(r["ok"] for r in results), "results": results, "prep_ms": round((tp - t_start) * 1000),
               "press_ms": round((t_sent - tp) * 1000, 1), "stray": res["stray"]}
        if res["stray"]:
            out["ok"] = False
            out["error"] = "клавишу принял чужой стакан: " + ", ".join(f"{t} ({c})" for t, c, _ in res["stray"]) + "!"
        else:
            out["error"] = "; ".join(r["error"] for r in results if not r["ok"])
        if trade:
            try:
                with open(SPEED_LOG, "a", encoding="utf-8") as f:
                    for r in results:
                        f.write(json.dumps({"time": t0.strftime("%Y-%m-%d %H:%M:%S"), "venue": "cscalp", "ticker": r["ticker"],
                                            "action": r["action"], "press_at_ms": r["press_at_ms"],
                                            "hotkey_ms": r.get("hotkey_ms"), "orders": r.get("orders")},
                                           ensure_ascii=False) + "\n")
            except OSError:
                pass
        return out


def execute(ticker, action, index=None, go=False, restore=True):
    """Один стакан — частный случай execute_many."""
    m = execute_many([ticker], action, go=go, restore=restore)
    r = dict(m["results"][0]) if m.get("results") else {}
    r.update(ok=m["ok"], prep_ms=m.get("prep_ms"))
    if m.get("error"):
        r["error"] = m["error"]
    return r


# ── Рабочий поток для Нокса (ридер) ─────────────────────────────────────────
# Все обращения к CScalp — из одного потока: UI Automation (COM) инициализирован там один раз и прогрет,
# поэтому в момент новости подготовка ~20 мс, а не ~200. В простое поток сверяет взведённые стаканы:
# если стакан передвинули/заменили — пересканирует заранее, чтобы не тратить 4 с в момент сделки.
_tasks = queue.Queue()
_worker = {"thread": None}
_worker_lock = threading.Lock()
_armed = {"tickers": set()}
_verify = {"last_scan": 0.0, "last_check": 0.0, "status": {}}
VERIFY_EVERY = 10.0      # с — сверка взведённых стаканов
RESCAN_MIN_GAP = 60.0    # с — не чаще: полный скан нагружает интерфейс CScalp ~4 с


def _worker_loop():
    try:
        _uia()
    except Exception:
        pass
    while True:
        try:
            job = _tasks.get(timeout=1.0)
        except queue.Empty:
            job = None
        if job:
            fn, box, ev = job
            try:
                box["result"] = fn()
            except Exception as e:
                box["error"] = f"{type(e).__name__}: {e}"
            ev.set()
            continue
        try:
            _bg_refresh()
        except Exception:
            pass
        if _armed["tickers"] and time.time() - _verify["last_check"] > VERIFY_EVERY:
            try:
                _verify_armed()
            except Exception:
                pass


def _ensure_worker():
    with _worker_lock:
        if not (_worker["thread"] and _worker["thread"].is_alive()):
            _worker["thread"] = threading.Thread(target=_worker_loop, name="cscalp", daemon=True)
            _worker["thread"].start()


def submit(fn, timeout=30.0):
    """Выполнить fn в рабочем потоке CScalp. Возвращает (результат, ошибка)."""
    _ensure_worker()
    box, ev = {}, threading.Event()
    t_put = time.time()
    _tasks.put((fn, box, ev))
    # самописец: задание идёт дольше 3 с — записать, где стоит рабочий поток (на 3, 8, 15 с)
    snaps, marks = [], [3, 8, 15]
    while not ev.wait(0.5):
        el = time.time() - t_put
        if marks and el >= marks[0]:
            marks.pop(0)
            try:
                import sys, traceback
                th = _worker.get("thread")
                fr = sys._current_frames().get(th.ident) if th else None
                snaps.append({"t": round(el, 1), "queue": _tasks.qsize(),
                              "stack": [s.strip().replace("\n", " | ")[:260] for s in traceback.format_stack(fr)[-8:]] if fr else []})
                _last_trace["stall"] = snaps
            except Exception:
                pass
        if el >= timeout:
            _last_trace["stall"] = snaps
            _log_stall(snaps)
            return None, "CScalp не ответил вовремя"
    if snaps:
        _log_stall(snaps)
    return box.get("result"), box.get("error")


def _log_stall(snaps):
    """Записать зависание рабочего потока CScalp в cscalp_stall.log (для разбора)."""
    try:
        with open(os.path.join(HERE, "cscalp_stall.log"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "steps": _last_trace.get("steps"),
                                "snaps": snaps}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def arm(tickers, watch=True):
    """Какие стаканы держать сверенными (из включённых наблюдателей). Запускает рабочий поток."""
    _armed["tickers"] = {norm(t) for t in tickers if t}
    _bg["on"] = bool(watch)
    if watch or _armed["tickers"]:
        _ensure_worker()      # в простое поток следит за положением окон CScalp (стрелки — без задержки)


def _verify_armed():
    """Сверить взведённые стаканы с экраном. Стакан перекрыт другим окном — пропуск (перед сделкой окно
    выводится вперёд). Не там/нет в раскладке — пересканировать (не чаще RESCAN_MIN_GAP)."""
    _verify["last_check"] = time.time()
    data = scan(float("inf"))
    doms = {}
    for d in data.get("doms") or []:
        doms.setdefault(d["ticker"], d)
    need, status = False, {}
    for t in _armed["tickers"]:
        d = doms.get(t)
        if not d:
            status[t] = "нет в раскладке"
            need = True
            continue
        x, y = _target_point(d)
        if _root_at(x, y) != int(d["hwnd"]):
            status[t] = "перекрыт другим окном"
            continue
        under = ticker_at(x, y)
        status[t] = "на месте" if under == t else f"под точкой {under or 'не стакан'}"
        need = need or under != t
    if need and time.time() - _verify["last_scan"] > RESCAN_MIN_GAP:
        _verify["last_scan"] = time.time()
        scan(0)
    _verify["status"] = status


# ── Вкладки CScalp: стрелки оверлея привязаны к вкладке («нокс»), а не к списку тикеров ──────────────
UI_DIR = os.path.join(CS_DIR, "Data", "UserInterface")
_tabs_cache = {"mt": None, "map": {}}


def cs_tabs():
    """{имя вкладки: [файлы раскладки DL.*.dat]} из MarketViewLayout.srz (имена могут повторяться)."""
    p = os.path.join(UI_DIR, "MarketViewLayout.srz")
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return {}
    if mt == _tabs_cache["mt"]:
        return _tabs_cache["map"]
    b = open(p, "rb").read()
    out = {}
    for m in re.finditer(rb"DL\.(-?\d+)\.dat", b):
        tid = m.group(1)
        i = b.rfind(tid, 0, m.start())               # id вкладки стоит перед её именем
        if i < 0:
            continue
        parts = [x.strip() for x in re.split(r"[\x00-\x1f]+", b[i + len(tid):m.start()].decode("utf-8", "ignore")) if x.strip()]
        name = max(parts, key=len) if parts else ""
        name = re.sub(r"^[^\wА-Яа-яЁё(]+|[^\wА-Яа-яЁё)]+$", "", name)
        if name:
            out.setdefault(name, []).append("DL." + tid.decode() + ".dat")
    _tabs_cache.update(mt=mt, map=out)
    return out


def tab_stacks(name):
    """Стаканы вкладки слева направо по её файлу раскладки (CScalp сам обновляет его при изменениях): ['MIX-12.26',
    'NVTK', …]. Графики («ROSN, 1h») и пустые («Выбрать инструмент») — мимо. -> (список | None, причина)."""
    files = cs_tabs().get(name)
    if not files:
        return None, f"в CScalp нет вкладки «{name}»"
    if len(files) > 1:
        return None, f"вкладок «{name}» в CScalp несколько ({len(files)}) — дайте ей уникальное имя"
    try:
        x = open(os.path.join(UI_DIR, files[0]), encoding="utf-8", errors="ignore").read()
    except OSError as e:
        return None, f"не читается раскладка вкладки «{name}»: {e}"
    st = [norm(t) for t in re.findall(r'<LayoutDocument\s+Title="([^"]+)"', x) if "," not in t]
    return [t for t in st if t and t != "ВЫБРАТЬИНСТРУМЕНТ"], ""


def tab_window(name, max_age=float("inf")):
    """Окно CScalp, где вкладка открыта СЕЙЧАС: в нём видны ровно её стаканы. -> (окно | None, стаканы, причина)."""
    from collections import Counter
    st, why = tab_stacks(name)
    if st is None:
        return None, [], why
    if not st:
        return None, [], f"на вкладке «{name}» нет стаканов"
    want = Counter(st)
    for attempt in (0, 1):
        data = scan(max_age if attempt == 0 else 0)
        wins = {}
        for d in data.get("doms") or []:
            t = d.get("ticker") or ""
            if d.get("main") and d.get("hwnd") and "," not in t and t != "ВЫБРАТЬИНСТРУМЕНТ":
                wins.setdefault(int(d["hwnd"]), Counter())[t] += 1
        for hw, cnt in wins.items():
            if cnt == want:
                return hw, st, ""
        if attempt == 0 and time.time() - _layout["t"] <= 3:
            break                                    # раскладка свежая — второй скан ничего не даст
    return None, st, f"вкладка «{name}» не открыта в CScalp"


def execute_tab(name, action, go=False, restore=True, deadline=None, probe=False):
    """Нажать action во ВСЕХ стаканах вкладки, какие на ней есть сейчас, и только в окне, где она открыта.
    Вкладка закрыта/не видна целиком — не жать ни один."""
    hw, st, why = tab_window(name, float("inf"))
    if not hw:
        return {"ok": False, "error": why + " — не нажимал ни один", "results": [], "tab": name, "stacks": st}
    seen, pairs = {}, []
    for t in st:
        seen[t] = seen.get(t, 0) + 1
        pairs.append((t, action, seen[t]))
    r = execute_orders(pairs, go=go, restore=restore, deadline=deadline, probe=probe, hwnd=hw)
    r.update(tab=name, stacks=st)
    return r


_acc_cache = {"mt": None, "path": None, "acc": {}}


def accounts():
    """Счета CScalp и подключены ли сейчас: {'00550': True, …}. По журналу диспетчера за сегодня: счёт подключён,
    если хоть один его канал (MCXD, P2ED, MXCR…) последним событием подключился."""
    d = _today_dir()
    p = os.path.join(d, "Dispatcher_XDSD.html") if d else None
    try:
        mt = os.path.getmtime(p)
    except (OSError, TypeError):
        return {}
    if (p, mt) == (_acc_cache["path"], _acc_cache["mt"]):
        return _acc_cache["acc"]
    txt = open(p, encoding="utf-8", errors="ignore").read()
    last = {}
    for m in re.finditer(r"Slot \((\w+)\): (Connect|Disconnect): Start \((\d+)\)", txt):
        last[(m.group(3), m.group(1))] = m.group(2) == "Connect"
    acc = {}
    for (a, _slot), on in last.items():
        acc[a] = acc.get(a, False) or on
    _acc_cache.update(path=p, mt=mt, acc=acc)
    return acc


def visible():
    """Стаканы CScalp для интерфейса: видимые + скрытые вкладки + состояние взведённых."""
    data = scan(20.0)
    return {"ok": bool(data.get("ok")), "error": data.get("error", ""),
            "doms": [{"ticker": d["ticker"], "window": d.get("window")} for d in data.get("doms") or []],
            "hidden": sorted({norm(h["ticker"]) for h in data.get("hidden") or []} - {"ВЫБРАТЬИНСТРУМЕНТ"}),
            "armed": _verify["status"]}


def describe(r):
    """Короткий отчёт по одному стакану для голоса/консоли."""
    if not r.get("ok"):
        return "CScalp: " + (r.get("error") or "не вышло")
    if "sent" not in r:
        return f"План: {r['ticker']} — клавиша {r['key']} ({r['cs_command']}), окно «{r['window']}»"
    side = {"Buy": "ПОКУПКА", "Sell": "ПРОДАЖА"}.get(r.get("direction"), "")
    parts = [f"{r['ticker']}: нажато на {r.get('press_at_ms')} мс, {r['command']} принят за {r.get('hotkey_ms')} мс"
             + (f" — {side} ✔" if side else "")]
    for o in r.get("orders") or []:
        if "Amount=" in o["text"] or "not ready" in o["text"] or "Success=" in o["text"]:
            parts.append(f"{o['ms']} мс — {o['text']}")
    return "; ".join(parts)


def describe_many(m):
    lines = []
    if "prep_ms" in m:
        lines.append(f"подготовка {m['prep_ms']} мс, все нажатия за {m['press_ms']} мс")
    for r in m.get("results") or []:
        lines.append("  " + describe(r))
    if m["ok"] and "prep_ms" not in m:
        lines.append("ИТОГ: план готов — без --go ничего не нажато")
    else:
        lines.append("ИТОГ: " + ("всё исполнено ✔" if m["ok"] else "ОШИБКА — " + (m.get("error") or "")))
    return "\n".join(lines)


def _main(argv):
    if not argv or argv[0] == "scan":
        data = scan(0)
        if not data.get("ok"):
            print(data.get("error"))
            return
        for d in data["doms"]:
            print(f"{d['ticker']:<14} окно «{d['window']}»  стакан {d.get('dom')}")
        print("скрытые вкладки:", ", ".join(sorted({norm(h['ticker']) for h in data['hidden']})))
        return
    if argv[0] == "keys":
        for c, k in sorted(keys().items()):
            print(f"{k:<10} {c}")
        return
    tickers = [a for a in argv[1:] if not a.startswith("--")]
    if argv[0] == "test":
        m = execute_many(tickers, "agg_plus", go=True)
        print("агрегация + :\n" + describe_many(m))
        if any("sent" in r for r in m.get("results") or []):  # вернуть агрегацию в любом случае
            time.sleep(0.3)
            print("агрегация − :\n" + describe_many(execute_many(tickers, "agg_minus", go=True)))
        return
    if argv[0] in ("buy", "sell"):
        m = execute_many(tickers, f"{argv[0]}_market", go="--go" in argv)
        print(describe_many(m))
        return
    print(__doc__)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    _main(sys.argv[1:])
