# -*- coding: utf-8 -*-
"""
Поиск установленных программ по названию голосом: «открой калибр» → Caliber, «открой макс» → MAX.

Откуда список: меню «Пуск» (Get-StartApps: имя + AppID, и обычные программы, и из Магазина) и ярлыки на рабочих
столах. Русское слово сравниваем с латинским названием через транслитерацию в нескольких вариантах
(к→k/c, кс→x, и→i/y …). Свои названия — C:\\jarvis-voice\\nox_apps.json: {"калибр": "Caliber", "стим": "Steam"}.
Запуск: `explorer shell:AppsFolder\\<AppID>` (так же, как клик в «Пуске»), ярлык — os.startfile.
Не нашли — честно «не нашёл» (раньше команда тихо падала в cmd, а Нокс говорил «Запускаю»).
"""
import difflib
import glob
import json
import os
import re
import subprocess
import threading
import time

CNW = 0x08000000
ALIAS_FILE = r"C:\jarvis-voice\nox_apps.json"
DEFAULT_ALIASES = {"калибр": "Caliber", "макс": "MAX", "стим": "Steam", "телега": "Telegram", "дискорд": "Discord",
                   "яндекс": "Яндекс Браузер", "браузер": "Яндекс Браузер", "хапп": "Happ", "хэпп": "Happ",
                   "впн": "Happ", "сискальп": "FSR Launcher", "скальп": "FSR Launcher", "лолка": "Lolka"}
SKIP = re.compile(r"деинсталл|uninstall|удалить|unins\d*|readme|справка|help|документац|website|support center", re.I)
_lock = threading.Lock()
_cache = {"t": 0.0, "apps": []}          # [(имя, AppID или путь .lnk, вид 'app'|'lnk'|'url')]

_TR = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y",
       "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
       "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"}
# варианты чтения латиницы: «калибр» = calibr(e), «макс» = max, «дискорд» = discord, «хэпп» = happ
_VARIANTS = [{}, {"к": "c"}, {"кс": "x"}, {"к": "c", "кс": "x"}, {"и": "y"}, {"э": "a", "е": "a"}, {"в": "w"},
             {"ж": "j", "й": "i"}, {"х": "kh"}, {"с": "c"}]


def _norm(s):
    return re.sub(r"[^0-9a-zа-яё]+", "", (s or "").lower().replace("ё", "е"))


def translit(s, extra=None):
    s = (s or "").lower()
    for k, v in sorted((extra or {}).items(), key=lambda kv: -len(kv[0])):
        s = s.replace(k, v)
    return "".join(_TR.get(ch, ch) for ch in s)


def _start_apps():
    ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
          "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=25,
                         creationflags=CNW).stdout.decode("utf-8", "replace").strip()
    data = json.loads(out) if out else []
    if isinstance(data, dict):
        data = [data]
    res = []
    for d in data:
        n, a = (d.get("Name") or "").strip(), (d.get("AppID") or "").strip()
        if n and a and not SKIP.search(n) and not SKIP.search(a):
            res.append((n, a, "url" if a.startswith("http") else "app"))
    return res


def _desktop_links():
    res = []
    dirs = [os.path.join(os.environ.get("USERPROFILE", ""), "Desktop"), os.path.join(os.environ.get("PUBLIC", ""), "Desktop"),
            os.path.join(os.environ.get("USERPROFILE", ""), "OneDrive", "Desktop")]
    for d in dirs:
        for p in glob.glob(os.path.join(d, "*.lnk")) + glob.glob(os.path.join(d, "*.url")):
            n = os.path.splitext(os.path.basename(p))[0]
            if not SKIP.search(n):
                res.append((n, p, "lnk"))
    return res


def apps(refresh=False):
    with _lock:
        if refresh or not _cache["apps"] or time.time() - _cache["t"] > 600:
            lst = []
            try:
                lst += _start_apps()
            except Exception as e:
                print("[apps] Get-StartApps:", e)
            lst += _desktop_links()
            seen, uniq = set(), []
            for n, a, k in lst:
                key = (_norm(n), a.lower())
                if key not in seen:
                    seen.add(key); uniq.append((n, a, k))
            _cache.update(t=time.time(), apps=uniq)
        return list(_cache["apps"])


def aliases():
    try:
        d = json.load(open(ALIAS_FILE, encoding="utf-8"))
    except Exception:
        d = {}
    a = dict(DEFAULT_ALIASES)
    a.update({str(k).lower().strip(): str(v) for k, v in d.items()})
    return a


def _score(q, name):
    """Насколько фраза q похожа на название программы (0..1): по-русски напрямую и через транслитерацию."""
    qn, nn = _norm(q), _norm(name)
    if not qn or not nn:
        return 0.0
    words = [w for w in re.split(r"[^0-9a-zа-яё]+", name.lower().replace("ё", "е")) if w]
    best = 0.0
    qs = {translit(q, v) for v in _VARIANTS} | {qn}
    qs = {_norm(x) for x in qs}
    ns = {nn, _norm(translit(name))} | {_norm(w) for w in words} | {_norm(translit(w)) for w in words}
    if words:
        ns.add(_norm(words[0]))
    for a in qs:
        for b in ns:
            if not a or not b:
                continue
            if a == b:
                return 1.0
            r = difflib.SequenceMatcher(None, a, b).ratio()
            if len(a) >= 4 and b.startswith(a):
                r = max(r, 0.9)            # «телеграм» → «telegram desktop»
            best = max(best, r)
    return best


def find(query):
    """(имя, AppID/путь, вид, оценка) лучшей программы или None."""
    q = re.sub(r"^(открой|открыть|запусти|запустить|включи|open|start)\s+", "", (query or "").lower().strip())
    q = re.sub(r"^(программу|приложение|игру|мессенджер|браузер\s+(?=\S))\s*", "", q).strip(" .,!?")
    if not q:
        return None
    al = aliases()
    target = al.get(q)
    lst = apps()
    for attempt in (0, 1):
        cands = []
        for n, a, k in lst:
            if target:
                s = 1.0 if _norm(n).startswith(_norm(target)) else _score(target, n) * 0.95
            else:
                s = _score(q, n)
            cands.append((s, -len(n), n, a, k))
        cands.sort(reverse=True)
        if cands and cands[0][0] >= 0.8:
            s, _l, n, a, k = cands[0]
            return n, a, k, round(s, 2)
        if attempt == 0:
            lst = apps(refresh=True)        # программу могли только что поставить
    return None


def launch(name, target, kind):
    if kind == "url":
        import webbrowser
        webbrowser.open(target)
    elif kind == "lnk":
        os.startfile(target)
    else:
        subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + target], creationflags=CNW)
    return True


def open_app(query):
    """Для голоса: {'ok', 'text', 'app'}; ok=False — не нашли (вызывающий решает, искать ли в интернете)."""
    hit = find(query)
    if not hit:
        return {"ok": False, "text": ""}
    n, a, k, s = hit
    try:
        launch(n, a, k)
        return {"ok": True, "text": f"Открываю {n}, сэр.", "app": n, "id": a, "score": s}
    except Exception as e:
        return {"ok": False, "text": f"Не смог открыть {n}, сэр: {type(e).__name__}.", "app": n, "error": repr(e)}
