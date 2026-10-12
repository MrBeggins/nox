# -*- coding: utf-8 -*-
"""
Голос Nox на XTTS v2 (coqui-tts): КЛОНИРУЕТ голос по образцу ref_jarvis_og.wav,
но заметно стабильнее F5 (меньше «белиберды»). Тот же HTTP-API (порт 8123) —
jarvis-app работает без изменений.
Запускать интерпретатором ИЗОЛИРОВАННОГО окружения:
   C:\\jarvis-voice\\.venv-xtts\\Scripts\\python.exe tts_xtts_server.py

GPU: в .venv-xtts стоит torch 2.8 CPU. Чтобы не качать ещё 3 ГБ, папка
C:\\jarvis-voice\\.xtts-gpu содержит ССЫЛКИ (junction) на torch/torchaudio/torchcodec
2.11+cu128 из основного .venv — если она есть, грузимся с неё (RTX 5050).
Модель — локальная папка (качает xtts_selftest.py / скрипт докачки), без ModelManager.
"""
import os, sys, io, re, time, threading
os.environ.setdefault("COQUI_TOS_AGREED", "1")
_GPU_TORCH = r"C:\jarvis-voice\.xtts-gpu"
if os.path.isdir(os.path.join(_GPU_TORCH, "torch")):
    sys.path.insert(0, _GPU_TORCH)

import numpy as np
import soundfile as sf
import torch

# Обойти требование torchcodec/FFmpeg при чтении: читаем аудио через soundfile.
try:
    import torchaudio
    def _sf_load(path, *a, **k):
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        return torch.from_numpy(data.T.copy()), sr
    torchaudio.load = _sf_load
except Exception:
    pass

MODEL_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                         "tts", "tts_models--multilingual--multi-dataset--xtts_v2")
REF_AUDIO  = r"C:\jarvis-voice\ref_jarvis_og.wav"
LANG       = "ru"
_SPEED_FILE = r"C:\jarvis-voice\tts_speed.txt"

_device = "cuda" if torch.cuda.is_available() else "cpu"
_model = None
_latents = None          # (gpt_cond_latent, speaker_embedding) — считаем ОДИН раз
_lock = threading.Lock()

def load_xtts(device=None):
    """Загрузить XTTS v2 из локальной папки (без скачивания)."""
    from TTS.tts.configs.xtts_config import XttsConfig
    from TTS.tts.models.xtts import Xtts
    cfg = XttsConfig()
    cfg.load_json(os.path.join(MODEL_DIR, "config.json"))
    m = Xtts.init_from_config(cfg)
    spk = os.path.join(MODEL_DIR, "speakers_xtts.pth")
    m.load_checkpoint(cfg, checkpoint_dir=MODEL_DIR, use_deepspeed=False,
                      speaker_file_path=spk if os.path.exists(spk) else None)
    return m.to(device or _device)

_init_lock = threading.Lock()

def _ensure_model():
    with _init_lock:       # прогрев и первый запрос не грузят модель дважды
        _load_once()

def _load_once():
    global _model, _latents
    if _model is not None:
        return
    t0 = time.time()
    print(f"[init] загружаю XTTS v2 на {_device} из {MODEL_DIR}...")
    m = load_xtts()
    with torch.inference_mode():
        lat = m.get_conditioning_latents(audio_path=[REF_AUDIO])
    _model, _latents = m, lat
    print(f"[ready] XTTS v2 готов за {time.time()-t0:.0f}с, голос из", os.path.basename(REF_AUDIO))

try:
    import nox_textnorm as _ntn
except Exception:
    _ntn = None

def _normalize(text):
    if _ntn:
        try:
            return _ntn.normalize(text) or "."   # единый русский нормализатор
        except Exception:
            pass
    return (text or "").strip() or "."

def _load_user_speed():
    try:
        v = float(open(_SPEED_FILE, encoding="utf-8").read().strip())
        return v if 0.6 <= v <= 1.6 else None
    except Exception:
        return None
_user_speed = _load_user_speed()

def synth(text: str, speed: float = 1.0) -> bytes:
    _ensure_model()
    norm = _normalize(text)
    if _user_speed:
        speed = _user_speed
    speed = max(0.7, min(1.5, float(speed)))
    gpt, spk = _latents
    with _lock, torch.inference_mode():
        out = _model.inference(norm, LANG, gpt, spk, temperature=0.65,
                               speed=speed, enable_text_splitting=True)
    wav = np.asarray(out["wav"], dtype=np.float32).reshape(-1)
    peak = float(np.max(np.abs(wav))) or 1.0
    wav = wav / peak * 0.95
    buf = io.BytesIO()
    sf.write(buf, wav, 24000, format="WAV", subtype="PCM_16")   # XTTS отдаёт 24 кГц
    return buf.getvalue()

# --- HTTP (совместим с tts_server.py / silero) ---
from fastapi import FastAPI, Response
from pydantic import BaseModel
import uvicorn
app = FastAPI(title="Nox XTTS")

class Req(BaseModel):
    text: str
    speed: float | None = None
    nfe: int | None = None   # игнор (совместимость)

@app.on_event("startup")
def _warmup():
    def _w():
        try:
            _ensure_model()
            synth("Готов к работе, сэр.", 1.0)   # прогрев (первый вызов CUDA медленный)
            print("[warmup] готово")
        except Exception as e:
            print("[warmup] ошибка:", e)
    threading.Thread(target=_w, daemon=True).start()

@app.get("/health")
def health():
    return {"status": "ok" if _model is not None else "starting",
            "device": _device, "engine": "xtts", "ref": os.path.basename(REF_AUDIO)}

@app.get("/speed")
def get_speed():
    return {"value": _user_speed or 0.0}

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
    print("[ready] http://127.0.0.1:8123 (XTTS v2,", _device + ")")
    uvicorn.run(app, host="127.0.0.1", port=8123, log_level="warning")
