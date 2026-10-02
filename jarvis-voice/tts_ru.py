"""Jarvis RU voice synth via F5-TTS (Russian) + RUAccent, on GPU.
Usage: python tts_ru.py "текст" [out.wav]
"""
import sys, os, time

# ffmpeg.exe на PATH (нужен transformers-whisper для транскрипции референса)
os.environ["PATH"] = r"C:\Users\Дамир\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.1-full_build\bin" + os.pathsep + os.environ.get("PATH", "")

# Обходим torchcodec: torchaudio.load в 2.11 требует ffmpeg 4-7 DLL, но нам нужен
# только wav-референс -> читаем через soundfile.
import torch, torchaudio, soundfile as _sf, numpy as _np
def _sf_load(path, *a, **k):
    data, sr = _sf.read(str(path), dtype="float32", always_2d=True)  # (frames, ch)
    return torch.from_numpy(data.T.copy()), sr                       # (ch, frames), sr
def _sf_save(path, tensor, sr, *a, **k):
    arr = tensor.detach().cpu().numpy()
    if arr.ndim == 2:
        arr = arr.T
    _sf.write(str(path), arr, sr)
torchaudio.load = _sf_load
torchaudio.save = _sf_save

MODEL_CKPT = r"C:\jarvis-voice\models\ru\F5TTS_v1_Base_v2\model_last_inference.safetensors"
VOCAB      = r"C:\jarvis-voice\models\ru\F5TTS_v1_Base\vocab.txt"
REF_AUDIO  = r"C:\jarvis-voice\ref_jarvis_og.wav"
TARGET_F0  = 124.0   # Гц — характерная высота тона оригинального Джарвиса (jarvis-og)
FORMANT    = 0.95    # <1 = ниже форманты = взрослее/глубже тембр (убирает «детскость»)
SPEED      = 1.2     # скорость речи (1.2 = на 20% быстрее)
SEED       = 42      # фиксируем для воспроизводимости

gen_text = sys.argv[1] if len(sys.argv) > 1 else "Добрый вечер, сэр. Все системы работают нормально. Чем могу быть полезен?"
out_wav  = sys.argv[2] if len(sys.argv) > 2 else r"C:\jarvis-voice\out.wav"

t0 = time.time()
print("[1/4] RUAccent: расстановка ударений...")
from ruaccent import RUAccent
acc = RUAccent()
acc.load(omograph_model_size="turbo", use_dictionary=True, tiny_mode=False)
gen_acc = acc.process_all(gen_text)
print("     ->", gen_acc)

print("[2/4] Загрузка F5-TTS (RU) на GPU...")
from f5_tts.api import F5TTS
tts = F5TTS(model="F5TTS_v1_Base", ckpt_file=MODEL_CKPT, vocab_file=VOCAB, device="cuda")

print("[3/4] Синтез...")
wav, sr, _ = tts.infer(
    ref_file=REF_AUDIO,
    ref_text="",            # авто-транскрипция референса (whisper-large-v3-turbo)
    gen_text=gen_acc,
    nfe_step=48,
    cfg_strength=2,
    speed=SPEED,
    remove_silence=True,
    seed=SEED,
    file_wave=None,
)

# [4/4] Пост-обработка
import librosa, numpy as np, soundfile as sf
# По умолчанию — чистый F5 (звучит чище). JARVIS_WORLD=1 включает WORLD-коррекцию питча/формант.
USE_WORLD = os.environ.get("JARVIS_WORLD", "0") != "0"

def world_adjust(wav, sr, target_f0, formant):
    import pyworld as pw
    x = np.asarray(wav, dtype=np.float64)
    _f0, t = pw.harvest(x, sr, f0_floor=55.0, f0_ceil=350.0)
    f0 = pw.stonemask(x, _f0, t, sr)
    sp = pw.cheaptrick(x, f0, t, sr)     # спектр. огибающая = форманты
    ap = pw.d4c(x, f0, t, sr)            # апериодичность
    voiced = f0 > 0
    cur = float(np.median(f0[voiced])) if voiced.any() else target_f0
    f0_new = f0.copy()
    f0_new[voiced] *= (target_f0 / cur)
    if abs(formant - 1.0) > 1e-3:         # сдвиг формант, интерполяция в ЛОГ-домене
        nbin = sp.shape[1]; idx = np.arange(nbin)
        src = np.clip(idx / formant, 0, nbin - 1)
        logsp = np.log(sp + 1e-12)
        sp_new = np.empty_like(sp)
        for i in range(sp.shape[0]):
            sp_new[i] = np.exp(np.interp(src, idx, logsp[i]))
    else:
        sp_new = sp
    y = pw.synthesize(f0_new, sp_new, ap, sr).astype(np.float32)
    return y, cur

if USE_WORLD:
    wav, cur = world_adjust(wav, sr, TARGET_F0, FORMANT)
    print(f"[4/4] WORLD: F0 {cur:.1f}->~{TARGET_F0:.0f}Hz, форманты x{FORMANT}, скорость x{SPEED}")
else:
    wav = np.asarray(wav, dtype=np.float32)
    print(f"[4/4] Чистый F5 (без WORLD), скорость x{SPEED}")

wav, _ = librosa.effects.trim(wav, top_db=32)   # убрать тишину по краям
peak = float(np.max(np.abs(wav))) or 1.0
wav = wav / peak * 0.95
sf.write(out_wav, wav, sr)
print(f"Готово: {out_wav}  ({len(wav)/sr:.2f}s, sr={sr})  за {time.time()-t0:.1f}s")
