# -*- coding: utf-8 -*-
"""
Обработка голоса Джарвиса после синтеза: подгонка под образец «Jarvis New».

Цепочка (каждое звено можно выключить):
  1) форманты/тон — Praat «Change gender» (PSOLA): форманты ×ratio без изменения тона, тон к медиане образца;
  2) эквалайзер-слепок — FIR по разнице усреднённых спектров образца и клона (сглажено по 1/3 октавы, ±6 дБ);
  3) комнатный «хвост» — короткая искусственная реверберация (RT60, уровень).
Настройки: C:\\jarvis-voice\\voicefx.json, слепок эквалайзера: C:\\jarvis-voice\\voicefx_eq.npy
"""
import json
import os

import numpy as np

CFG_FILE = r"C:\jarvis-voice\voicefx.json"
EQ_FILE = r"C:\jarvis-voice\voicefx_eq.npy"
# Замеры 2026-10-17 (Resemblyzer, потолок «New сам с собой» 0.910): клон XTTS на образце New = 0.918, +эквалайзер = 0.921;
# сдвиг формант 0.94 + тон 119 Гц → 0.857, только тон → 0.903 (PSOLA портит тембр). Поэтому по умолчанию — только эквалайзер.
DEFAULT = {"enabled": True, "formant": 1.0, "pitch_hz": 0.0, "range": 1.0, "eq": True, "eq_max_db": 6.0,
           "reverb_rt60": 0.0, "reverb_db": -20.0}


def load_cfg():
    try:
        d = json.load(open(CFG_FILE, encoding="utf-8"))
    except Exception:
        d = {}
    cfg = dict(DEFAULT)
    cfg.update({k: d[k] for k in DEFAULT if k in d})
    return cfg


# ------------------------------------------------------------------ эквалайзер-слепок
def ltas_db(y, sr, n=2048):
    import scipy.signal as sg
    f, p = sg.welch(y, sr, nperseg=n)
    return f, 10 * np.log10(p + 1e-12)


def _smooth_oct(f, db, frac=3):
    out = np.empty_like(db)
    for i, fc in enumerate(f):
        if fc <= 0:
            out[i] = db[i]; continue
        lo, hi = fc * 2 ** (-0.5 / frac), fc * 2 ** (0.5 / frac)
        m = (f >= lo) & (f <= hi)
        out[i] = 10 * np.log10(np.mean(10 ** (db[m] / 10)))
    return out


def design_eq(ref_wav, src_wav, sr, max_db=6.0, taps=1025):
    """FIR, который делает средний спектр src похожим на ref. Ниже 70 Гц и выше 11 кГц — не трогаем."""
    import scipy.signal as sg
    f, r = ltas_db(ref_wav, sr)
    _, s = ltas_db(src_wav, sr)
    d = _smooth_oct(f, r) - _smooth_oct(f, s)
    band = (f > 90) & (f < 10000)
    d -= np.average(d[band], weights=1 / np.maximum(f[band], 1))   # общая громкость не меняется
    d = np.clip(d, -max_db, max_db)
    d[f < 70] = 0; d[f > 11000] = 0
    gain = 10 ** (d / 20)
    fir = sg.firwin2(taps, f / (sr / 2), gain)
    return fir.astype(np.float32), f, d


def apply_eq(y, fir):
    import scipy.signal as sg
    return sg.fftconvolve(y, fir, mode="same").astype(np.float32)


# ------------------------------------------------------------------ форманты и тон
def praat_shift(y, sr, formant=1.0, pitch_hz=0.0, rng=1.0):
    if abs(formant - 1) < 1e-3 and not pitch_hz and abs(rng - 1) < 1e-3:
        return y
    import parselmouth
    from parselmouth.praat import call
    snd = parselmouth.Sound(y.astype(np.float64), sampling_frequency=sr)
    out = call(snd, "Change gender", 70, 400, float(formant), float(pitch_hz or 0), float(rng), 1.0)
    return np.asarray(out.values[0], np.float32)


# ------------------------------------------------------------------ комнатный хвост
_IR = {}


def _ir(sr, rt60):
    key = (sr, round(rt60, 2))
    if key not in _IR:
        rs = np.random.RandomState(3)
        n = int(sr * rt60 * 1.2)
        t = np.arange(n) / sr
        ir = rs.randn(n) * np.exp(-6.91 * t / rt60)
        import scipy.signal as sg
        b, a = sg.butter(2, 4500 / (sr / 2))          # тёплая комната: верх гаснет быстрее
        ir = sg.lfilter(b, a, ir)
        ir[: int(0.012 * sr)] *= np.linspace(0, 1, int(0.012 * sr))   # предзадержка ~12 мс
        _IR[key] = (ir / np.sqrt((ir ** 2).sum())).astype(np.float32)
    return _IR[key]


def reverb(y, sr, rt60=0.4, wet_db=-20.0):
    if rt60 <= 0:
        return y
    import scipy.signal as sg
    wet = sg.fftconvolve(y, _ir(sr, rt60))[: len(y) + int(sr * rt60 * 0.6)]
    out = np.zeros(len(wet), np.float32)
    out[: len(y)] = y
    return out + wet.astype(np.float32) * (10 ** (wet_db / 20))


# ------------------------------------------------------------------ вся цепочка
_EQ = {"mtime": None, "fir": None}


def _eq_fir():
    try:
        mt = os.path.getmtime(EQ_FILE)
    except OSError:
        return None
    if _EQ["mtime"] != mt:
        _EQ["fir"], _EQ["mtime"] = np.load(EQ_FILE).astype(np.float32), mt
    return _EQ["fir"]


def process(y, sr, cfg=None):
    cfg = cfg or load_cfg()
    if not cfg.get("enabled"):
        return y
    y = np.asarray(y, np.float32).reshape(-1)
    try:
        y = praat_shift(y, sr, cfg["formant"], cfg["pitch_hz"], cfg["range"])
    except Exception as e:
        print("[voicefx] praat:", e)
    if cfg.get("eq"):
        fir = _eq_fir()
        if fir is not None:
            y = apply_eq(y, fir)
    y = reverb(y, sr, cfg.get("reverb_rt60", 0), cfg.get("reverb_db", -20))
    return y
