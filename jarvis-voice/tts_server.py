"""Jarvis RU voice server (F5-TTS + RUAccent), модель держится в памяти на GPU.
Запуск:  python tts_server.py   ->  http://127.0.0.1:8123
API:     POST /tts  {"text": "...", "speed": 1.2}  -> audio/wav
         GET  /health
"""
import os, io, time, threading

# ffmpeg.exe на PATH (для разовой транскрипции референса при старте)
os.environ["PATH"] = r"C:\Users\Дамир\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.1-full_build\bin" + os.pathsep + os.environ.get("PATH", "")

# обход torchcodec: torchaudio.load через soundfile
import torch, torchaudio, soundfile as sf, numpy as np
def _sf_load(path, *a, **k):
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return torch.from_numpy(data.T.copy()), sr
torchaudio.load = _sf_load

import librosa
from ruaccent import RUAccent
from f5_tts.api import F5TTS

MODEL_CKPT = r"C:\jarvis-voice\models\ru\F5TTS_v1_Base_v2\model_last_inference.safetensors"
VOCAB      = r"C:\jarvis-voice\models\ru\F5TTS_v1_Base\vocab.txt"
REF_AUDIO  = r"C:\jarvis-voice\ref_jarvis_og.wav"
SPEED      = 1.2
NFE        = 14
SEED       = 42

print("[init] RUAccent...")
_acc = RUAccent()
_acc.load(omograph_model_size="turbo", use_dictionary=True, tiny_mode=False)

print("[init] F5-TTS (RU) на GPU...")
_tts = F5TTS(model="F5TTS_v1_Base", ckpt_file=MODEL_CKPT, vocab_file=VOCAB, device="cuda")

print("[init] транскрипция референса (один раз)...")
REF_TEXT = _tts.transcribe(REF_AUDIO)
print("[init] ref_text =", REF_TEXT)

_lock = threading.Lock()   # F5 не потокобезопасен -> сериализуем запросы

# --- Числа -> слова (иначе F5/RUAccent коверкают цифры) ---
import re as _re
from num2words import num2words as _n2w

def _plural(n, forms):
    """forms=(один, два-четыре, пять+): 1 рубль / 2 рубля / 5 рублей."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]

def _dec_words(a, b):
    """3.7 -> «три целых семь десятых», 1.25 -> «одна целая двадцать пять сотых»."""
    ai, bf = int(a), int(b)
    cel = "целая" if (ai % 10 == 1 and ai % 100 != 11) else "целых"
    whole = _n2w(ai, lang="ru")
    if ai % 10 == 1 and ai % 100 != 11:
        whole = whole[:-3] + "одна" if whole.endswith("один") else whole
    units = {1: ("десятая", "десятых"), 2: ("сотая", "сотых"),
             3: ("тысячная", "тысячных")}.get(len(b), ("десятая", "десятых"))
    frac = _n2w(bf, lang="ru")
    u = units[0] if (bf % 10 == 1 and bf % 100 != 11) else units[1]
    return f"{whole} {cel} {frac} {u}"

def _num_repl(m):
    s = m.group(0).replace(",", ".")
    try:
        if "." in s:
            a, b = s.split(".", 1)
            b = b.rstrip("0") or "0"
            if int(b) == 0:
                return _n2w(int(a), lang="ru")
            return _dec_words(a, b)
        return _n2w(int(s), lang="ru")
    except Exception:
        return m.group(0)

def _pct_repl(m):
    """«5 %» -> пять процентов, «2 %» -> два процента, «3.7 %» -> три целых семь десятых процента."""
    num = m.group(1)
    words = _num_repl(_re.match(r"\d+(?:[.,]\d+)?", num))
    if "." in num or "," in num:
        unit = "процента"
    else:
        unit = _plural(int(num), ("процент", "процента", "процентов"))
    return f"{words} {unit}"

# Аббревиатуры, которые F5/RUAccent произносит неверно -> транскрипция/полное слово.
# Легко расширять: ключ (как в тексте) -> как произносить.
_ABBR = {
    "РФ": "Эр Эф", "СМИ": "Эс Эм И", "СБП": "Эс Бэ Пэ", "НПЗ": "Эн Пэ Зэ",
    "ФРС": "Эф Эр Эс", "ВВП": "Вэ Вэ Пэ", "ВПК": "Вэ Пэ Ка", "ВСУ": "Вэ Эс У",
    "МИД": "Эм И Дэ", "ФНС": "Эф Эн Эс", "ФСБ": "Эф Эс Бэ", "МВД": "Эм Вэ Дэ",
    "ЦБ": "Центробанк", "ГД": "Госдума", "ЕС": "Евросоюз", "ЖКХ": "Жэ Ка Ха",
    "ГОСА": "ГОСА", "ВОСА": "ВОСА",
    # финансы/рынок
    "IPO": "Ай Пи О", "SPO": "Эс Пи О", "ETF": "И Ти Эф", "ОФЗ": "О Эф Зэ",
    "НКЦ": "Эн Ка Цэ", "МСФО": "Эм Эс Эф О", "РСБУ": "Эр Эс Бэ У",
    "НДФЛ": "Эн Дэ Эф Эл", "НДС": "Эн Дэ Эс", "ЦФА": "Цэ Эф А", "ЕЦБ": "Е Цэ Бэ",
    "ОПЕК": "ОПЕК", "МОЭКС": "Мосбиржа", "MOEX": "Мосбиржа", "СД": "Эс Дэ",
}
_ABBR_RE = [(_re.compile(r"(?<![А-Яа-яЁёA-Za-z])" + _re.escape(k) + r"(?![А-Яа-яЁёA-Za-z])"), v)
            for k, v in _ABBR.items()]

def _expand_abbr(text: str) -> str:
    for rx, rep in _ABBR_RE:
        text = rx.sub(rep, text)
    return text

def _normalize_numbers(text: str) -> str:
    # НЕ трогаем дефисы (Як-130, Су-57 и т.п.) — только валюты/проценты и сами цифры.
    text = _expand_abbr(text)
    # проценты со согласованием (до общей замены цифр)
    text = _re.sub(r"(\d+(?:[.,]\d+)?)\s*%", _pct_repl, text)
    text = text.replace("%", " процентов").replace("₽", " рублей").replace("$", " долларов")
    return _re.sub(r"\d+(?:[.,]\d+)?", _num_repl, text)

# --- Разбивка длинного текста на короткие куски (F5 «плывёт» на длинном/быстром) ---
def _split_for_tts(text: str, max_len: int = 130):
    text = _re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    # режем по границам предложений, сохраняя знак
    sents = _re.split(r"(?<=[.!?…])\s+", text)
    chunks, cur = [], ""
    for s in sents:
        # слишком длинное предложение — дробим по запятым/точкам с запятой
        pieces = _re.split(r"(?<=[,;:])\s+", s) if len(s) > max_len else [s]
        for p in pieces:
            if not p:
                continue
            if len(cur) + len(p) + 1 <= max_len:
                cur = (cur + " " + p).strip()
            else:
                if cur:
                    chunks.append(cur)
                cur = p
    if cur:
        chunks.append(cur)
    return chunks or [text]

# Ручные правки ударений там, где RUAccent ошибается (применяются ПОСЛЕ него).
# Ключ — регэксп по слову (с возможным неверным «+»), значение — верная форма.
_STRESS_FIX = [
    (_re.compile(r"\bл\+?от\+?ов\b", _re.I), "лот+ов"),   # ло́тов -> лото́в
    (_re.compile(r"\bл\+?отам\b", _re.I), "лот+ам"),
    (_re.compile(r"\bл\+?отами\b", _re.I), "лот+ами"),
]

def _accent(s: str) -> str:
    try:
        g = _acc.process_all(s)          # расстановка ударений
    except Exception as e:
        print("[ruaccent] fallback без ударений:", e)
        g = s
    g = g if (g and str(g).strip()) else s
    for rx, rep in _STRESS_FIX:
        g = rx.sub(rep, g)
    return g

_SPEED_FILE = r"C:\jarvis-voice\tts_speed.txt"
def _load_user_speed():
    try:
        v = float(open(_SPEED_FILE, encoding="utf-8").read().strip())
        return v if 0.6 <= v <= 1.6 else None
    except Exception:
        return None
_user_speed = _load_user_speed()   # пользовательская скорость (слайдер в виджете «Голос»)

def synth(text: str, speed: float = SPEED, nfe: int = NFE) -> bytes:
    norm = _normalize_numbers(text)
    # Пользовательская скорость (слайдер) имеет приоритет над динамической.
    if _user_speed:
        speed = _user_speed
    # F5 коверкает речь на высокой скорости — держим в разумных рамках
    speed = max(0.7, min(1.5, float(speed)))
    chunks = _split_for_tts(norm)
    sr = 24000
    parts = []
    with _lock:                          # F5 не потокобезопасен -> один инференс за раз
        for ch in chunks:
            gen = _accent(ch)
            wav, sr, _ = _tts.infer(
                ref_file=REF_AUDIO, ref_text=REF_TEXT, gen_text=gen,
                nfe_step=nfe, cfg_strength=2, speed=speed,
                remove_silence=True, seed=SEED, file_wave=None,
            )
            wav = np.asarray(wav, dtype=np.float32)
            wav, _ = librosa.effects.trim(wav, top_db=32)
            parts.append(wav)
            parts.append(np.zeros(int(sr * 0.10), dtype=np.float32))  # пауза между кусками
    wav = np.concatenate(parts) if parts else np.zeros(1, dtype=np.float32)
    peak = float(np.max(np.abs(wav))) or 1.0
    wav = wav / peak * 0.95
    buf = io.BytesIO()
    sf.write(buf, wav, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()

# --- HTTP ---
from fastapi import FastAPI, Response
from pydantic import BaseModel
app = FastAPI(title="Jarvis TTS")

class Req(BaseModel):
    text: str
    speed: float | None = None
    nfe: int | None = None

@app.get("/health")
def health():
    return {"status": "ok", "device": _tts.device, "ref_text": REF_TEXT}

@app.get("/speed")
def get_speed():
    # 0 = «динамическая» (не задана пользователем)
    return {"value": _user_speed or 0.0}

@app.get("/set_speed")
def set_speed(v: float = 0.0):
    """v в [0.8..1.5] — зафиксировать скорость; 0 или вне диапазона -> вернуть динамическую."""
    global _user_speed
    if 0.8 <= v <= 1.5:
        _user_speed = v
        try: open(_SPEED_FILE, "w", encoding="utf-8").write(str(v))
        except Exception: pass
    else:
        _user_speed = None
        try: os.remove(_SPEED_FILE)
        except Exception: pass
    return {"value": _user_speed or 0.0}

@app.post("/tts")
def tts(req: Req):
    t0 = time.time()
    data = synth(req.text, req.speed or SPEED, req.nfe or NFE)
    print(f"[tts] {len(req.text)} симв -> {len(data)} байт за {time.time()-t0:.2f}s")
    return Response(content=data, media_type="audio/wav")

if __name__ == "__main__":
    import uvicorn
    print("[ready] http://127.0.0.1:8123")
    uvicorn.run(app, host="127.0.0.1", port=8123, log_level="warning")
