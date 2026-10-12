# -*- coding: utf-8 -*-
"""Самопроверка XTTS: модель на месте → грузится (GPU, если есть .xtts-gpu) → клон-сэмпл.
Успех → C:\\jarvis-voice\\xtts_ready.flag (XTTS появляется в списке голосов)."""
import os, sys, time, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_FLAG = r"C:\jarvis-voice\xtts_ready.flag"
_NEED = {"config.json": 4368, "vocab.json": 361219, "model.pth": 1867929118}
try:
    import tts_xtts_server as srv      # тот же код, что и у сервера (GPU-ссылки, загрузка, синтез)
    import torch
    print("[1] torch", torch.__version__, "| устройство:", srv._device)
    for f, sz in _NEED.items():
        p = os.path.join(srv.MODEL_DIR, f)
        have = os.path.getsize(p) if os.path.exists(p) else 0
        if have != sz:
            raise RuntimeError(f"{f}: {have} из {sz} байт — модель ещё не докачана")
    print("[2] файлы модели на месте")
    t0 = time.time()
    srv._ensure_model()
    print(f"[3] модель + образец голоса за {time.time()-t0:.0f}с")
    for i, text in enumerate(["Приветствую, сэр. Это голос Джарвиса на новом движке.",
                              "Индекс Мосбиржи вырос на полтора процента, сэр. Сбербанк торгуется по триста двенадцать рублей."]):
        t0 = time.time()
        data = srv.synth(text, 1.0)
        out = rf"C:\jarvis-voice\xtts_test{'' if i == 0 else i + 1}.wav"
        open(out, "wb").write(data)
        print(f"[4] {os.path.basename(out)}: {len(text)} симв за {time.time()-t0:.1f}с")
    open(_FLAG, "w", encoding="utf-8").write("ok")
    print("XTTS SELFTEST: SUCCESS (флаг xtts_ready.flag создан)")
except Exception as e:
    try: os.remove(_FLAG)
    except Exception: pass
    print("XTTS SELFTEST: FAIL ->", type(e).__name__, e)
    traceback.print_exc()
