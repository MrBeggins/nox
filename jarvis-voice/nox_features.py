# -*- coding: utf-8 -*-
"""Выключатели функций Нокса — вкладка «Фичи» в Настройках (2026-10-13).

Файл C:\\jarvis-voice\\nox_features.json: {"ключ": true/false}; ключа нет — функция ВКЛЮЧЕНА.
Пишет его окно Нокса (Tauri features_set, атомарно), читают:
  * nox_watchdog — службы: magellan, us_macro (bls+bea), notes, ollama; голос при запуске/восстановлении;
  * terminal_reader — corp_events, watchers, scenarios, cscalp, calendar;
  * sys_monitor — pc_alerts;
  * голосовой движок (Rust, jarvis_core::features) — greeting.
Чтение кэшируется по времени изменения файла — дёшево звать хоть на каждой новости.
"""
import json
import os
import threading

FILE = r"C:\jarvis-voice\nox_features.json"

# ключ -> подпись (для логов/справки; подписи и описания для окна — в settings/index.svelte)
FEATURES = {
    "corp_events": "Корпсобытия и раскрытия",
    "watchers": "Наблюдатели",
    "scenarios": "Сценарии «стакан по новости»",
    "cscalp": "CScalp",
    "calendar": "Календарь событий",
    "us_macro": "Статистика США",
    "magellan": "Магеллан",
    "notes": "Заметки и напоминания",
    "pc_alerts": "Уведомления о загрузке ПК",
    "ollama": "Локальный мозг Ollama",
    "greeting": "Приветствие при запуске",
    "restart_notices": "Сообщения о восстановлении служб",
}

_cache = {"mtime": None, "data": {}}
_lock = threading.Lock()


def _load():
    try:
        m = os.path.getmtime(FILE)
    except OSError:
        return {}
    with _lock:
        if m != _cache["mtime"]:
            try:
                with open(FILE, encoding="utf-8-sig") as f:
                    _cache["data"] = json.load(f)
                _cache["mtime"] = m          # только после удачного чтения (файл мог писаться)
            except Exception:
                pass
        return _cache["data"]


def enabled(key):
    """Функция включена? Нет записи / нет файла — включена."""
    return bool(_load().get(key, True))


# Режим тишины перед новостью по времени (ставка ЦБ/ФРС, Минфин): ридер пишет {"until": unix, "reason": …},
# остальные службы (монитор ПК, Магеллан, watchdog) в это время молчат. Просроченный файл не действует.
FOCUS_FILE = r"C:\jarvis-voice\nox_focus.json"


def focus_active():
    try:
        import time
        with open(FOCUS_FILE, encoding="utf-8") as f:
            return float(json.load(f).get("until", 0)) > time.time()
    except Exception:
        return False


def state():
    d = _load()
    return {k: bool(d.get(k, True)) for k in FEATURES}
