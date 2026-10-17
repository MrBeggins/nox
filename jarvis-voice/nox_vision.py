# -*- coding: utf-8 -*-
"""
Зрение Нокса: «Нокс, что на экране?» → снимок экрана → облачная модель по ключу APIMIRA → короткий ответ голосом.

Снимок делается ТОЛЬКО по прямому запросу голосом/текстом (никакого фонового наблюдения) и уходит в облако
вместе с вопросом. Какой экран: тот, где сейчас курсор мыши; «левый/правый экран» — по расположению;
«верхний/главный экран»; «все экраны» — все мониторы одной картинкой.
Модель: app.db `vision_model` (по умолчанию openai/gpt-5.5 — в тесте верно читает стакан, ~5 с).
Выключатель: «Фичи» → «Зрение» (ключ vision в nox_features.json).
"""
import base64
import ctypes
import ctypes.wintypes as wt
import io
import json
import re
import time
import urllib.request

DEFAULT_MODEL = "openai/gpt-5.5"
MAX_SIDE = 1920
TIMEOUT = 25

VISION_RE = re.compile(
    r"(что|чё|че)\s+(у\s+меня\s+|сейчас\s+|там\s+|тут\s+)*на\s+(\w+\s+)?(экран|монитор|график|скрин)|"
    r"(посмотри|глянь|взгляни)\s+(на|в)\s+(экран|монитор|график|окно)|"
    r"что\s+ты\s+видишь|что\s+видишь|опиши\s+(экран|что\s+на)|"
    r"(прочитай|прочти|зачитай)\s+(с|что\s+на)\s+экран|"
    r"что\s+(тут|здесь|там)\s+написано|посмотри\s+на\s+это")

_SYS = ("Ты — Нокс, голосовой помощник трейдера. Тебе дают снимок экрана пользователя и его вопрос. "
        "Ответь по-русски коротко, 1–3 предложения, только суть того, о чём спросили; если вопрос общий "
        "(«что на экране») — скажи, какая программа открыта и главное, что на ней видно (инструмент, цены, "
        "позиции, заголовки). Числа бери точно с экрана, ничего не выдумывай; если не видно — так и скажи. "
        "Обращайся «сэр».")


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", wt.DWORD)]


def monitors():
    """Прямоугольники мониторов (физические пиксели), слева направо."""
    out = []
    cb = ctypes.WINFUNCTYPE(ctypes.c_int, wt.HMONITOR, wt.HDC, ctypes.POINTER(_RECT), wt.LPARAM)

    def f(hm, hdc, rc, lp):
        r = rc.contents
        out.append((r.left, r.top, r.right, r.bottom))
        return 1
    ctypes.windll.user32.EnumDisplayMonitors(None, None, cb(f), 0)
    return sorted(out)


def _cursor_monitor():
    pt = wt.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    hm = ctypes.windll.user32.MonitorFromPoint(pt, 2)            # MONITOR_DEFAULTTONEAREST
    mi = _MONINFO(); mi.cbSize = ctypes.sizeof(_MONINFO)
    ctypes.windll.user32.GetMonitorInfoW(hm, ctypes.byref(mi))
    r = mi.rcMonitor
    return (r.left, r.top, r.right, r.bottom)


def pick_area(text):
    low = (text or "").lower()
    mons = monitors()
    if re.search(r"вс(е|ех)\s+(экран|монитор)", low) and len(mons) > 1:
        return (min(m[0] for m in mons), min(m[1] for m in mons), max(m[2] for m in mons), max(m[3] for m in mons)), "все экраны"
    if len(mons) > 1:
        if re.search(r"(верхн|сверху)\w*\s*(экран|монитор)", low):
            return min(mons, key=lambda m: m[1]), "верхний экран"
        if re.search(r"(главн|основн|центральн|средн)\w*\s+(экран|монитор)", low):
            return next((m for m in mons if m[0] == 0 and m[1] == 0), mons[0]), "главный экран"
        if re.search(r"лев\w*\s+(экран|монитор)", low):
            return mons[0], "левый экран"
        if re.search(r"прав\w*\s+(экран|монитор)", low):
            return mons[-1], "правый экран"
    return _cursor_monitor(), "экран с курсором"


def grab(text=""):
    """JPEG-байты снимка + описание области."""
    from PIL import ImageGrab
    box, where = pick_area(text)
    img = ImageGrab.grab(bbox=box, all_screens=True).convert("RGB")
    w, h = img.size
    k = MAX_SIDE / max(w, h)
    if k < 1:
        img = img.resize((int(w * k), int(h * k)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82)
    return buf.getvalue(), where


def _cloud():
    import nox_scenarios as ns
    key, url, _model = ns._cloud()
    model = (ns._appdb().get("vision_model") or "").strip() or DEFAULT_MODEL
    return key, url, model


def ask(question, jpeg, model=None):
    key, url, dm = _cloud()
    if not key:
        return None, "нет ключа облака (страница «Мозг»)"
    try:
        import nox_scenarios as ns
        rules = ns.SPEECH_RULES
    except Exception:
        rules = ""
    body = {"model": model or dm, "max_tokens": 300, "temperature": 0, "messages": [
        {"role": "system", "content": _SYS + rules},
        {"role": "user", "content": [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode(),
                                                "detail": "high"}}]}]}
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    r = json.loads(urllib.request.urlopen(req, timeout=TIMEOUT).read().decode("utf-8"))
    return (r["choices"][0]["message"]["content"] or "").strip(), None


def look(question):
    """Полный путь для голоса: снимок → облако → текст ответа (или понятная ошибка)."""
    t0 = time.time()
    try:
        jpeg, where = grab(question)
    except Exception as e:
        return {"text": "Не получилось сделать снимок экрана, сэр.", "error": repr(e)}
    t1 = time.time()
    try:
        txt, err = ask(question, jpeg)
    except Exception as e:
        txt, err = None, repr(e)
    if not txt:
        return {"text": "Не смог разглядеть экран, сэр: облако не ответило.", "error": err,
                "ms": int((time.time() - t0) * 1000)}
    return {"text": txt, "where": where, "kb": len(jpeg) // 1024, "shot_ms": int((t1 - t0) * 1000),
            "ms": int((time.time() - t0) * 1000)}
