# -*- coding: utf-8 -*-
"""Проверка проверочной установки в C:\\noxtest (после tests\\test_install.ps1). Пишет tests\\results\\verify.txt."""
import hashlib, json, os, subprocess, sys, time, urllib.request, wave, io, winreg

sys.stdout.reconfigure(encoding="utf-8")
R = r"C:\noxtest"
LAB, VO = os.path.join(R, "jarvis-lab"), os.path.join(R, "jarvis-voice")
PAYLOAD = r"C:\nox-release\payload"
OUT = r"C:\nox-release\tests\results"
VPY = os.path.join(VO, ".venv", "Scripts", "python.exe")
NOWIN = 0x08000000
res = []


def check(name, ok, info=""):
    res.append((name, bool(ok), info))
    print(("OK   " if ok else "FAIL ") + name + (f" — {info}" if info else ""), flush=True)


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def run(args, timeout=600, cwd=None):
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, cwd=cwd, creationflags=NOWIN)
    return p.returncode, p.stdout + p.stderr


# 1. файлы: всё из нагрузки на месте, exe/dll побайтно совпадают
missing, diff, n = [], [], 0
for top in ("jarvis-lab", "jarvis-voice"):
    src_root = os.path.join(PAYLOAD, top)
    for base, dirs, files in os.walk(src_root):
        rel_base = os.path.relpath(base, src_root)
        if rel_base.startswith(os.path.join("models", "ruaccent")):
            continue                              # переносятся в .venv (проверка ниже)
        for f in files:
            n += 1
            s = os.path.join(base, f)
            d = os.path.join(R, top, rel_base, f)
            if not os.path.isfile(d):
                missing.append(os.path.join(top, rel_base, f)); continue
            if os.path.getsize(d) != os.path.getsize(s):
                diff.append(f)
            elif f.endswith((".exe", ".dll")) and sha(d) != sha(s):
                diff.append(f)
check("файлы программы на месте", not missing and not diff,
      f"{n} файлов" + (f"; нет: {missing[:5]}" if missing else "") + (f"; отличаются: {diff[:5]}" if diff else ""))

# 2. журнал донастройки
log = open(os.path.join(VO, "install.log"), encoding="utf-8").read() if os.path.isfile(os.path.join(VO, "install.log")) else ""
warn = [l for l in log.splitlines() if "ВНИМАНИЕ" in l]
check("донастройка без замечаний", log and not warn and "библиотеки Python: ОК" in log, "; ".join(warn)[:300])

# 3. окружение Python
rc, out = run([VPY, "-c", "import sys,torch,ruaccent,fastapi,uvicorn,playwright,pymorphy3,websockets,cryptography,PIL,"
               "psutil,comtypes,soundfile,num2words,onnxruntime; print(sys.version.split()[0], torch.__version__)"])
check("окружение .venv и библиотеки", rc == 0 and out.startswith("3.11"), out.strip()[-200:])
ra = os.path.join(VO, ".venv", "Lib", "site-packages", "ruaccent")
check("модели ударений RUAccent в .venv", all(os.path.isdir(os.path.join(ra, d)) for d in ("nn", "koziev", "dictionary"))
      and os.path.isdir(os.path.join(ra, "nn", "nn_omograph", "turbo")))
bad = []
for f in os.listdir(VO):
    if f.endswith(".py"):
        rc, out = run([VPY, "-m", "py_compile", os.path.join(VO, f)], timeout=60)
        if rc: bad.append(f)
check("все .py компилируются", not bad, ", ".join(bad))

# 4. настройки
db = os.path.join(R, "appdata", "com.priler.jarvis", "app.db")
ok = os.path.isfile(db)
if ok:
    raw = open(db, "rb").read()
    d = json.loads(raw.decode("utf-8"))
    ok = not raw.startswith(b"\xef\xbb\xbf") and d.get("wake_word") == "нокс" and not d.get("invest_token") \
        and d.get("tterminal_exec_enabled") is False and isinstance(d.get("api_keys"), dict)
check("стартовые настройки app.db (без ключей, сделки выкл.)", ok, db)
check("движок голоса Silero", open(os.path.join(VO, "tts_engine.txt")).read().strip() == "silero")

# 5. регистрация в «Приложениях»
try:
    k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                       r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{3B7D9E14-2C55-4F0A-8A11-7E6C4D2B9F01}_is1")
    name = winreg.QueryValueEx(k, "DisplayName")[0]
    check("запись в «Приложения» (удаление)", True, name)
except OSError as e:
    check("запись в «Приложения» (удаление)", False, str(e))

# 6. голос: служба Silero из установленного окружения на запасном порту
srv = subprocess.Popen([VPY, "-c", "import sys; sys.path.insert(0, r'%s'); import tts_silero_server as s, uvicorn; "
                        "uvicorn.run(s.app, host='127.0.0.1', port=18123, log_level='warning')" % VO],
                       cwd=VO, creationflags=NOWIN, stdout=subprocess.DEVNULL, stderr=open(os.path.join(OUT, "tts_test_err.log"), "w"))
wav_ok, info = False, ""
try:
    for _ in range(180):
        try:
            st = json.loads(urllib.request.urlopen("http://127.0.0.1:18123/health", timeout=2).read()).get("status")
            if st == "ok":
                break
        except Exception:
            pass
        time.sleep(1)
    t0 = time.time()
    req = urllib.request.Request("http://127.0.0.1:18123/tts", method="POST",
                                 data=json.dumps({"text": "Проверка установки Нокса. Сбербанк вырос на два и три десятых процента."}).encode(),
                                 headers={"Content-Type": "application/json"})
    data = urllib.request.urlopen(req, timeout=120).read()
    with wave.open(io.BytesIO(data)) as w:
        dur = w.getnframes() / w.getframerate()
    open(os.path.join(OUT, "tts_test.wav"), "wb").write(data)
    wav_ok, info = dur > 2, f"{dur:.1f} с речи за {time.time() - t0:.1f} с (tests\\results\\tts_test.wav)"
except Exception as e:
    info = f"{type(e).__name__}: {e}"
finally:
    srv.kill()
check("голос Silero синтезирует речь", wav_ok, info)

# 7. голосовые тесты Нокса на установленном окружении (эталон на рабочем: 141 из 144)
rc, out = run([VPY, r"C:\jarvis-voice\voice_tests\run_tests.py", "--no-rust"], timeout=1200)
line = next((l for l in out.splitlines() if "прошли" in l), out[-200:])
open(os.path.join(OUT, "voice_tests.txt"), "w", encoding="utf-8").write(out)
import re
m = re.search(r"(\d+) из (\d+)", line)
check("голосовые тесты (эталон 141/144)", m and int(m.group(1)) >= 141, line.strip())

# 8. окно Nox из установленной папки стартует (WebView2, DLL рядом с exe)
gui = os.path.join(LAB, "target", "release", "jarvis-gui.exe")
p = subprocess.Popen([gui], cwd=os.path.dirname(gui))
time.sleep(10)
alive = p.poll() is None
title = ""
if alive:
    rc, title = run(["powershell", "-NoProfile", "-Command", f"(Get-Process -Id {p.pid}).MainWindowTitle"], timeout=30)
    p.kill()
check("окно jarvis-gui запускается", alive, ("заголовок: " + title.strip()) if alive else f"код выхода {p.returncode}")

passed = sum(ok for _, ok, _ in res)
summary = f"ИТОГ: {passed} из {len(res)} проверок"
print(summary)
with open(os.path.join(OUT, "verify.txt"), "w", encoding="utf-8") as fh:
    for nme, ok, inf in res:
        fh.write(("OK   " if ok else "FAIL ") + nme + (f" — {inf}" if inf else "") + "\n")
    fh.write(summary + "\n")
sys.exit(0 if passed == len(res) else 1)
