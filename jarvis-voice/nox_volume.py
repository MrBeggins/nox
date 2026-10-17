# -*- coding: utf-8 -*-
"""
Громкость речи Нокса (ползунок рядом со скоростью на главной странице окна).
Общий файл для всех движков озвучки (Silero / F5 / XTTS): C:\\jarvis-voice\\tts_volume.txt, 0.1…1.0.
Нет файла — 1.0 (как было: пик 95%). Движок умножает уже нормированный звук на уровень перед записью WAV.
"""
import os

VOLUME_FILE = r"C:\jarvis-voice\tts_volume.txt"
_cache = {"mt": None, "v": 1.0}


def get():
    try:
        mt = os.path.getmtime(VOLUME_FILE)
    except OSError:
        return 1.0
    if mt != _cache["mt"]:
        try:
            v = float(open(VOLUME_FILE, encoding="utf-8").read().strip().replace(",", "."))
            _cache.update(mt=mt, v=max(0.1, min(1.0, v)))
        except Exception:
            _cache.update(mt=mt, v=1.0)
    return _cache["v"]


def set(v):
    v = max(0.1, min(1.0, float(v)))
    tmp = VOLUME_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(f"{v:.2f}")
    os.replace(tmp, VOLUME_FILE)
    return v
