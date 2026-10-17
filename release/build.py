# -*- coding: utf-8 -*-
"""
Сборка раздачи Nox.

  py -3.11 build.py                 — всё: исходники + нагрузка + проверка + Nox-Setup.exe
  py -3.11 build.py --no-compile    — без компиляции установщика (только папки и проверка)
  py -3.11 build.py --version 1.2.3 — своя версия (по умолчанию — сегодняшняя дата ГГГГ.ММ.ДД)

Откуда берётся:
  • список файлов кода — из git-копии C:\\nox (то, что уже проверено и лежит на GitHub);
    содержимое — из рабочих папок C:\\jarvis-lab и C:\\jarvis-voice (самое свежее);
  • готовые exe/DLL/ресурсы — из C:\\jarvis-lab\\target\\release (собраны cargo, см. README);
  • модели Silero и RUAccent — из C:\\jarvis-voice.
Куда:
  source\\   — чистые исходники (можно заливать на GitHub как есть);
  payload\\  — то, что упаковывается в установщик;
  dist\\     — Nox-Setup-<версия>.exe и его SHA-256.
"""
import argparse, datetime, filecmp, hashlib, json, os, re, shutil, subprocess, sys

ROOT    = os.path.dirname(os.path.abspath(__file__))
LAB     = r"C:\jarvis-lab"
VOICE   = r"C:\jarvis-voice"
GITREPO = r"C:\nox"
SRC_OUT = os.path.join(ROOT, "source")
PAYLOAD = os.path.join(ROOT, "payload")
DIST    = os.path.join(ROOT, "dist")
INST    = os.path.join(ROOT, "installer")
RELEASE = os.path.join(LAB, "target", "release")
RELEASE_ALT = os.path.join(LAB, "target-dist", "release")

# --- что НЕ берём в раздачу (черновики, внутренние заметки, отладочные скрипты) ---
EXCLUDE_SOURCE = {
    "jarvis-lab/HANDOFF.md", "jarvis-lab/VIDEO-ANALYSIS.md", "jarvis-lab/video-transcript.txt",
    "jarvis-lab/claude-login.cmd", "jarvis-lab/_diag.cmd", "jarvis-lab/test_client.mjs",
    "jarvis-voice/analyze.py", "jarvis-voice/mock_openai.py",
}
# новые файлы, которых ещё нет в git-копии C:\nox
EXTRA_SOURCE = [
    "jarvis-voice/ref_jarvis_og.txt",          # текст образца голоса для F5 (без Whisper)
    "jarvis-voice/ref_jarvis_og.wav",          # образец голоса Джарвиса для клонов F5/XTTS (в git не лежит: *.wav)
]
# --- что из исходников кладём в установщик (рантайм) ---
LAB_RUNTIME = ["start-jarvis.ps1", "Jarvis.vbs", "nox.ico", "nox_orb.png", "LICENSE.txt", "README.md",
               "Терминал-Яндекс.cmd", "Вход-MindStocks.cmd", "Запустить-календарь.cmd"]
VOICE_RUNTIME_SKIP = {"test_calendar.py", "start-voice-server.cmd", "requirements.txt"}
RELEASE_FILES = ["jarvis-app.exe", "jarvis-gui.exe", "DirectML.dll", "libgcc_s_seh-1.dll",
                 "libpv_recorder.dll", "libstdc++-6.dll", "libvosk.dll", "libwinpthread-1.dll"]
# Visual C++ runtime рядом с exe (app-local): окно запустится, даже если vc_redist не встал
VC_DLLS = ["msvcp140.dll", "msvcp140_1.dll", "vcruntime140.dll", "vcruntime140_1.dll"]
# распознавание речи — только русская модель (en/uk не нужны русскоязычному Nox, −330 МБ)
VOSK_KEEP = {"vosk-model-small-ru-0.22"}

# --- личные файлы и секреты: если найдутся в source/payload — сборка останавливается ---
FORBIDDEN_NAMES = re.compile(
    r"(^app\.db$|_key\.txt$|\.jsonl$|^nox_(watchers|notes|calendar|agenda|focus|scenarios)\.json$|"
    r"^cscalp_(keys|layout)\.json$|^news_filter\.json$|^magellan_filter\.json$|^nox_features\.json$|"
    r"^(bls|bea|nsd|copy)_state\.json$|^nox_memory\.json$|^nox_memory_prompt\.txt$|^nox_arrows\.json$|"
    r"^tinvest_pin\.json$|^nox_copytrade\.json$|^(health_)?report\.txt$|^rust_report\.json$|"
    r"\.bak|\.log$|_gmail_url\.txt$|\.session$|^Cookies$|^Login Data$)", re.I)
FORBIDDEN_DIRS = {"yandex_jarvis", "browser_profile", "mind_profile", "astra_chats", ".venv", ".venv-xtts",
                  "node_modules", "__pycache__", "voice_samples", "voice_ref", "compare"}
SECRET_PATTERNS = [
    (re.compile(r"\bam-[A-Za-z0-9_\-]{16,}"), "ключ APIMIRA"),
    (re.compile(r"\bt\.[A-Za-z0-9_\-]{60,}"), "токен T-Invest"),
    (re.compile(r"\b(ghp|gho|ghs)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"), "токен GitHub"),
    (re.compile(r"\bsk-(ant-)?[A-Za-z0-9_\-]{20,}"), "ключ OpenAI/Anthropic"),
    (re.compile(r"api_hash\s*=\s*['\"][0-9a-f]{32}", re.I), "Telegram api_hash"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@gmail\.com", re.I), "адрес gmail"),
]
TEXT_EXT = {".py", ".ps1", ".cmd", ".bat", ".vbs", ".rs", ".toml", ".json", ".md", ".txt", ".ts", ".js",
            ".svelte", ".html", ".css", ".scss", ".iss", ".ftl", ".lua", ".yaml", ".yml", ".template", ".mjs"}


def log(m):
    print(m, flush=True)


def copy(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst) and filecmp.cmp(src, dst, shallow=False):
        return False
    shutil.copy2(src, dst)
    return True


def mirror_dir(src, dst, skip_dirs=()):
    """Зеркало каталога: копирует новое/изменённое, удаляет лишнее."""
    os.makedirs(dst, exist_ok=True)
    want = set()
    for base, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in skip_dirs and d != "__pycache__"]
        rel = os.path.relpath(base, src)
        for f in files:
            if ".bak" in f.lower():
                continue                                      # резервные копии не раздаём
            r = os.path.normpath(os.path.join(rel, f))
            want.add(r.lower())
            s, d = os.path.join(src, r), os.path.join(dst, r)
            if not (os.path.exists(d) and os.path.getsize(d) == os.path.getsize(s)
                    and int(os.path.getmtime(d)) == int(os.path.getmtime(s))):
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copy2(s, d)
    for base, dirs, files in os.walk(dst, topdown=False):
        for f in files:
            p = os.path.join(base, f)
            if os.path.relpath(p, dst).lower() not in want:
                os.remove(p)
        if base != dst and not os.listdir(base):
            os.rmdir(base)


def git_file_list():
    out = subprocess.run(["git", "-c", "core.quotePath=false", "-C", GITREPO, "ls-files"],
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    files = [l.strip() for l in out.splitlines()
             if l.startswith(("jarvis-lab/", "jarvis-voice/")) and l.strip() not in EXCLUDE_SOURCE]
    return files + [f for f in EXTRA_SOURCE if f not in files]


# ---------------------------------------------------------------------------
def build_source():
    log("\n=== 1. Исходники -> source\\ ===")
    files = git_file_list()
    want, changed, missing = set(), 0, []
    for rel in files:
        top, rest = rel.split("/", 1)
        src = os.path.join(LAB if top == "jarvis-lab" else VOICE, *rest.split("/"))
        if not os.path.isfile(src):
            src = os.path.join(GITREPO, *rel.split("/"))      # есть только в git-копии (примеры конфигов)
            if not os.path.isfile(src):
                missing.append(rel); continue
        dst = os.path.join(SRC_OUT, *rel.split("/"))
        want.add(os.path.normcase(dst))
        changed += copy(src, dst)
    # установщик тоже часть исходников
    for f in os.listdir(INST):
        dst = os.path.join(SRC_OUT, "installer", f)
        want.add(os.path.normcase(dst)); changed += copy(os.path.join(INST, f), dst)
    for f in ("build.py", "sync_github.py", "README.md", ".gitignore", "Собрать-установщик.cmd"):
        if os.path.isfile(os.path.join(ROOT, f)):
            dst = os.path.join(SRC_OUT, f); want.add(os.path.normcase(dst)); changed += copy(os.path.join(ROOT, f), dst)
    # убрать то, чего больше нет в списке
    for base, dirs, fs in os.walk(SRC_OUT, topdown=False):
        for f in fs:
            p = os.path.join(base, f)
            if os.path.normcase(p) not in want:
                os.remove(p)
        if base != SRC_OUT and not os.listdir(base):
            os.rmdir(base)
    log(f"файлов: {len(want)}, обновлено: {changed}")
    for m in missing:
        log(f"  (нет в рабочей папке, пропущен: {m})")


def build_payload():
    log("\n=== 2. Нагрузка установщика -> payload\\ ===")
    pl_lab, pl_voice = os.path.join(PAYLOAD, "jarvis-lab"), os.path.join(PAYLOAD, "jarvis-voice")
    # jarvis-lab: скрипты запуска + готовая сборка
    for f in LAB_RUNTIME:
        copy(os.path.join(SRC_OUT, "jarvis-lab", f), os.path.join(pl_lab, f))
    rel_dst = os.path.join(pl_lab, "target", "release")
    for f in RELEASE_FILES:
        # exe могли собрать в отдельную папку (target-dist), пока рабочий Nox запущен и держит target\release
        cands = [p for p in (os.path.join(RELEASE, f), os.path.join(RELEASE_ALT, f)) if os.path.isfile(p)]
        src = max(cands, key=os.path.getmtime)
        if src.startswith(RELEASE_ALT):
            log(f"  {f}: из {RELEASE_ALT}")
        copy(src, os.path.join(rel_dst, f))
    for f in VC_DLLS:
        copy(os.path.join(os.environ["SystemRoot"], "System32", f), os.path.join(rel_dst, f))
    vosk_skip = {d for d in os.listdir(os.path.join(RELEASE, "resources", "vosk")) if d not in VOSK_KEEP}
    mirror_dir(os.path.join(RELEASE, "resources"), os.path.join(rel_dst, "resources"), skip_dirs=vosk_skip)
    # jarvis-voice: код служб + установочные скрипты + модели
    src_voice = os.path.join(SRC_OUT, "jarvis-voice")
    for base, dirs, files in os.walk(src_voice):
        for f in files:
            rel = os.path.relpath(os.path.join(base, f), src_voice)
            if rel in VOICE_RUNTIME_SKIP:
                continue
            copy(os.path.join(base, f), os.path.join(pl_voice, rel))
    for f in ("setup-nox.ps1", "stop-nox.ps1", "requirements-nox.txt", "app.db.template",
              "nox_fetch.py", "requirements-xtts.txt", "constraints-f5.txt"):
        copy(os.path.join(INST, f), os.path.join(pl_voice, "installer", f))
    copy(os.path.join(VOICE, "models", "silero", "v4_ru.pt"), os.path.join(pl_voice, "models", "silero", "v4_ru.pt"))
    ra = os.path.join(VOICE, ".venv", "Lib", "site-packages", "ruaccent")
    for d in ("nn", "koziev", "dictionary"):
        mirror_dir(os.path.join(ra, d), os.path.join(pl_voice, "models", "ruaccent", d))
    # убрать из нагрузки файлы, которых больше нет в исходниках
    for base, dirs, files in os.walk(pl_voice):
        dirs[:] = [d for d in dirs if d not in ("models", "installer")]
        for f in files:
            rel = os.path.relpath(os.path.join(base, f), pl_voice)
            if not os.path.isfile(os.path.join(src_voice, rel)):
                os.remove(os.path.join(base, f))
    size = sum(os.path.getsize(os.path.join(b, f)) for b, _, fs in os.walk(PAYLOAD) for f in fs)
    log(f"нагрузка: {size / 2**30:.2f} ГБ")


def check():
    log("\n=== 3. Проверка: секреты, личные файлы, шаблон настроек ===")
    bad = []
    for top in (SRC_OUT, PAYLOAD):
        for base, dirs, files in os.walk(top):
            for d in dirs:
                if d in FORBIDDEN_DIRS:
                    bad.append(f"личная/служебная папка: {os.path.join(base, d)}")
            for f in files:
                p = os.path.join(base, f)
                if FORBIDDEN_NAMES.search(f) and not f.endswith(".example"):
                    bad.append(f"личный файл: {p}")
                if os.path.splitext(f)[1].lower() in TEXT_EXT and os.path.getsize(p) < 5_000_000:
                    txt = open(p, "rb").read().decode("utf-8", "replace")
                    for rx, what in SECRET_PATTERNS:
                        m = rx.search(txt)
                        if m:
                            bad.append(f"{what} в {p}: {m.group(0)[:6]}…")
    # шаблон app.db: JSON без BOM, пустые ключи, исполнение сделок выключено
    raw = open(os.path.join(INST, "app.db.template"), "rb").read()
    if raw.startswith(b"\xef\xbb\xbf"):
        bad.append("app.db.template с BOM — программа его не прочитает")
    db = json.loads(raw.decode("utf-8"))
    for k in ("invest_token", "fmp_key", "tt_token", "stocksi_key", "user_name"):
        if db.get(k):
            bad.append(f"app.db.template: поле {k} не пустое")
    if any(db["api_keys"].values()):
        bad.append("app.db.template: api_keys не пустые")
    if db.get("tterminal_exec_enabled"):
        bad.append("app.db.template: исполнение сделок включено — должно быть выключено")
    if not isinstance(db.get("api_keys"), dict):
        bad.append("app.db.template: api_keys должен быть объектом {picovoice, openai}")
    for k in ("setup-nox.ps1", "stop-nox.ps1"):
        if not open(os.path.join(INST, k), "rb").read().startswith(b"\xef\xbb\xbf"):
            bad.append(f"{k} без BOM — PowerShell 5.1 испортит кириллицу")
    if bad:
        log("СТОП, найдено:")
        for b in bad:
            log("  - " + b)
        sys.exit(2)
    log("чисто")


def find_iscc():
    for p in (os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\Inno Setup 6\ISCC.exe"),
              r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", r"C:\Program Files\Inno Setup 6\ISCC.exe"):
        if os.path.isfile(p):
            return p
    return shutil.which("iscc")


def compile_installer(version):
    log("\n=== 4. Компиляция установщика (Inno Setup) ===")
    iscc = find_iscc()
    if not iscc:
        log("Inno Setup 6 не найден: winget install JRSoftware.InnoSetup"); sys.exit(3)
    os.makedirs(DIST, exist_ok=True)
    r = subprocess.run([iscc, f"/DAppVersion={version}", "/Qp", os.path.join(INST, "Nox.iss")])
    if r.returncode != 0:
        log("ошибка компиляции"); sys.exit(r.returncode)
    exe = os.path.join(DIST, f"Nox-Setup-{version}.exe")
    h = hashlib.sha256()
    with open(exe, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    with open(exe + ".sha256", "w", encoding="ascii") as fh:
        fh.write(f"{h.hexdigest()}  {os.path.basename(exe)}\n")
    log(f"готово: {exe} ({os.path.getsize(exe) / 2**30:.2f} ГБ)\nSHA-256: {h.hexdigest()}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=datetime.date.today().strftime("%Y.%m.%d"))
    ap.add_argument("--no-compile", action="store_true")
    a = ap.parse_args()
    build_source()
    build_payload()
    check()
    if not a.no_compile:
        compile_installer(a.version)
