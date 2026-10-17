# -*- coding: utf-8 -*-
"""
Лёгкий CPU-голос для Nox на Silero TTS (для ноутбуков без видеокарты).
Тот же HTTP-API, что и tts_server.py (порт 8123) — jarvis-app работает с ним без изменений.
На CPU синтез почти мгновенный. Модель: silero v4_ru (мужской голос 'aidar' по умолчанию).
Запуск: python tts_silero_server.py  ->  http://127.0.0.1:8123
Модель: C:\\jarvis-voice\\models\\silero\\v4_ru.pt (качает установщик; при отсутствии — тянется с models.silero.ai).
"""
import io, os, re, time, threading, urllib.request
import numpy as np
import soundfile as sf
import torch

MODEL_PATH = r"C:\jarvis-voice\models\silero\v4_ru.pt"
MODEL_URL  = "https://models.silero.ai/models/tts/ru/v4_ru.pt"
_SPEAKER_FILE = r"C:\jarvis-voice\tts_silero_speaker.txt"
SPEAKERS = ["aidar", "eugene", "baya", "kseniya", "xenia"]   # aidar/eugene — муж., остальные — жен.
def _load_speaker():
    try:
        v = open(_SPEAKER_FILE, encoding="utf-8").read().strip()
        if v in SPEAKERS:
            return v
    except Exception:
        pass
    return os.environ.get("NOX_SILERO_SPEAKER", "aidar")
SPEAKER = _load_speaker()
SAMPLE_RATE = 48000
_SPEED_FILE = r"C:\jarvis-voice\tts_speed.txt"

torch.set_num_threads(max(1, (os.cpu_count() or 4)))
_device = torch.device("cpu")
_model = None
_lock = threading.Lock()

def _ensure_model():
    global _model
    if _model is not None:
        return
    if not os.path.exists(MODEL_PATH):
        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        print("[init] скачиваю модель Silero v4_ru...")
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    print("[init] загружаю Silero на CPU...")
    m = torch.package.PackageImporter(MODEL_PATH).load_pickle("tts_models", "model")
    m.to(_device)
    _model = m
    print("[ready] Silero голос:", SPEAKER)

# --- числа и символы -> слова (Silero лучше читает слова) ---
try:
    from num2words import num2words
    def _n2w(n):
        try: return num2words(int(n), lang="ru")
        except Exception: return str(n)
except Exception:
    def _n2w(n): return str(n)

try:
    import nox_textnorm as _ntn
except Exception:
    _ntn = None
try:
    import nox_accent as _nacc      # ударения по смыслу (замок/замок, стоит/стоит) — пометки «+» для Silero
except Exception:
    _nacc = None

_DEC = re.compile(r"\d+[.,]\d+")
_INT = re.compile(r"\d+")
def _normalize(text):
    if _ntn:
        try:
            return _ntn.normalize(text)     # единый русский нормализатор (числа/падежи/аббр/латиница)
        except Exception:
            pass
    t = text.replace("%", " процентов").replace("№", " номер ")
    t = t.replace("$", " долларов").replace("₽", " рублей").replace("€", " евро")
    def dec(m):
        a, b = re.split(r"[.,]", m.group(0))
        return f"{_n2w(a)} целых {_n2w(b)}"
    t = _DEC.sub(dec, t)
    t = _INT.sub(lambda m: _n2w(m.group(0)), t)
    return t

def _volume():
    try:
        import nox_volume
        return nox_volume.get()
    except Exception:
        return 1.0

def _load_user_speed():
    try:
        v = float(open(_SPEED_FILE, encoding="utf-8").read().strip())
        return v if 0.6 <= v <= 1.6 else None
    except Exception:
        return None
_user_speed = _load_user_speed()

# Тон голоса: сдвиг в процентах через SSML <prosody pitch="-25%"> (родной для Silero,
# без артефактов). Только отрицательные/положительные проценты со знаком — «80%» модель
# игнорирует. aidar: 0 → ~122 Гц, -22% → ~100, -25% → ~95, -30% → ~86 (замер 2026-10-12).
_PITCH_FILE = r"C:\jarvis-voice\tts_silero_pitch.txt"
def _load_pitch():
    try:
        v = int(float(open(_PITCH_FILE, encoding="utf-8").read().strip()))
        return v if -40 <= v <= 30 else 0
    except Exception:
        return 0
_pitch = _load_pitch()

def _xml_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def synth(text: str, speed: float = 1.0) -> bytes:
    _ensure_model()
    norm = _normalize(text).strip() or "."
    if _nacc:
        norm = _nacc.accent(norm)
    if _user_speed:
        speed = _user_speed
    speed = max(0.7, min(1.5, float(speed)))
    attrs = []
    if abs(speed - 1.0) > 0.02:
        attrs.append(f'rate="{int(round(speed*100))}%"')
    if _pitch:
        attrs.append(f'pitch="{_pitch:+d}%"')
    with _lock:
        if attrs:
            ssml = f'<speak><prosody {" ".join(attrs)}>{_xml_escape(norm)}</prosody></speak>'
            audio = _model.apply_tts(ssml_text=ssml, speaker=SPEAKER,
                                     sample_rate=SAMPLE_RATE, put_accent=True, put_yo=True)
        else:
            audio = _model.apply_tts(text=norm, speaker=SPEAKER,
                                     sample_rate=SAMPLE_RATE, put_accent=True, put_yo=True)
    wav = audio.numpy().astype(np.float32)
    peak = float(np.max(np.abs(wav))) or 1.0
    wav = wav / peak * 0.95 * _volume()          # громкость — ползунок в окне (tts_volume.txt)
    # тишина по краям: проигрыватель не «съедает» первый звук и последний слог («добрый вечер, с…»)
    wav = np.concatenate([np.zeros(int(SAMPLE_RATE * 0.05), np.float32), wav,
                          np.zeros(int(SAMPLE_RATE * 0.30), np.float32)])
    buf = io.BytesIO()
    sf.write(buf, wav, SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return buf.getvalue()

# --- HTTP (совместим с tts_server.py) ---
from fastapi import FastAPI, Response
from pydantic import BaseModel
import uvicorn
app = FastAPI(title="Nox Silero TTS")

class Req(BaseModel):
    text: str
    speed: float | None = None
    nfe: int | None = None   # игнорируется (для совместимости с F5-клиентом)

@app.on_event("startup")
def _warmup():
    if _nacc:
        _nacc.start_loading()        # RUAccent ~15-40 с в фоне; до загрузки ударения ставит сам Silero
    def _w():
        try:
            _ensure_model()
            synth("Готов к работе, сэр.", 1.0)   # прогрев JIT, чтобы первый ответ был быстрым
            print("[warmup] готово")
        except Exception as e:
            print("[warmup] ошибка:", e)
    threading.Thread(target=_w, daemon=True).start()

@app.get("/health")
def health():
    return {"status": "ok" if _model is not None else "starting",
            "device": "cpu", "engine": "silero", "speaker": SPEAKER,
            "accent": bool(_nacc and _nacc.ready())}   # ударения по смыслу (RUAccent) загружены

@app.get("/speakers")
def speakers():
    return {"current": SPEAKER, "available": SPEAKERS}

@app.get("/set_speaker")
def set_speaker(name: str = ""):
    global SPEAKER
    if name in SPEAKERS:
        SPEAKER = name
        try: open(_SPEAKER_FILE, "w", encoding="utf-8").write(name)
        except Exception: pass
        return {"ok": True, "speaker": SPEAKER}
    return {"ok": False, "speaker": SPEAKER, "available": SPEAKERS}

@app.get("/pitch")
def get_pitch():
    return {"value": _pitch}

@app.get("/set_pitch")
def set_pitch(v: int = 0):
    """Тон в процентах (-40..+30, 0 = как у диктора). Сохраняется в tts_silero_pitch.txt."""
    global _pitch
    _pitch = max(-40, min(30, int(v)))
    try:
        if _pitch: open(_PITCH_FILE, "w", encoding="utf-8").write(str(_pitch))
        elif os.path.exists(_PITCH_FILE): os.remove(_PITCH_FILE)
    except Exception:
        pass
    return {"value": _pitch}

@app.get("/speed")
def get_speed():
    return {"value": _user_speed or 0.0}

@app.get("/volume")
def get_volume():
    return {"value": _volume()}

@app.get("/set_volume")
def set_volume(v: float = 1.0):
    import nox_volume
    return {"value": nox_volume.set(v)}

@app.get("/set_speed")
def set_speed(v: float = 0.0):
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
    data = synth(req.text, req.speed or 1.0)
    print(f"[tts] {len(req.text)} симв -> {len(data)} байт за {time.time()-t0:.2f}s")
    return Response(content=data, media_type="audio/wav")

if __name__ == "__main__":
    _ensure_model()
    print("[ready] http://127.0.0.1:8123 (Silero CPU)")
    uvicorn.run(app, host="127.0.0.1", port=8123, log_level="warning")
