# -*- coding: utf-8 -*-
"""
Синхронизация git-копии C:\\nox (github.com/MrBeggins/nox) с рабочими папками Nox.

  py -3.11 sync_github.py           — показать, что изменится (ничего не пишет)
  py -3.11 sync_github.py --apply   — скопировать, проверить на секреты и подготовить коммит (git add)

Не зеркало: в рабочих папках десятки черновиков и личных файлов. Берётся:
  • всё, что уже лежит в git, — если в рабочей папке изменилось (сравнение без учёта CRLF);
  • новые файлы кода — только из явного списка NEW_FILES;
  • инструменты раздачи (C:\\nox-release) — в папку release\\ репозитория.
Перед git add — поиск секретов по шаблонам и по РЕАЛЬНЫМ ключам пользователя (app.db, *_key.txt).
Коммит и push делаются отдельно (git commit / git push).
"""
import glob, json, os, re, shutil, subprocess, sys

REPO = r"C:\nox"
LAB, VOICE = r"C:\jarvis-lab", r"C:\jarvis-voice"
RELEASE_DIR = os.path.dirname(os.path.abspath(__file__))

# новые файлы кода (путь в репозитории). Личные данные сюда не добавлять!
NEW_FILES = [
    "jarvis-voice/nox_apps.py", "jarvis-voice/nox_arrows.py", "jarvis-voice/nox_bond_rules.py",
    "jarvis-voice/nox_copytrade.py", "jarvis-voice/nox_divdisc.py", "jarvis-voice/nox_feedback.py",
    "jarvis-voice/nox_memory.py", "jarvis-voice/nox_nsd.py", "jarvis-voice/nox_numwords.py",
    "jarvis-voice/nox_tinvest.py", "jarvis-voice/nox_vision.py", "jarvis-voice/nox_voicefx.py",
    "jarvis-voice/nox_volume.py",
    "jarvis-voice/ticker_names.txt", "jarvis-voice/nox_speech_fix.json", "jarvis-voice/ref_jarvis_og.txt",
    "jarvis-voice/voicefx_eq.npy", "jarvis-voice/xtts_voice_new.pt",
    "jarvis-voice/voice_tests/run_tests.py", "jarvis-voice/voice_tests/health_check.py",
    "jarvis-voice/voice_tests/py_cases.json", "jarvis-voice/voice_tests/rust_cases.json",
    "jarvis-voice/voice_tests/textnorm_golden.json",
    "jarvis-lab/crates/jarvis-core/build.rs", "jarvis-lab/crates/jarvis-core/src/numwords.rs",
    "jarvis-lab/crates/jarvis-core/src/speechfix.rs",
    "jarvis-lab/frontend/src/components/ArrowButton.svelte",
    "jarvis-lab/frontend/src/components/ArrowsPanel.svelte",
    "jarvis-lab/frontend/src/routes/memory/index.svelte",
]
# инструменты раздачи -> release/ в репозитории
RELEASE_FILES = ["build.py", "sync_github.py", "README.md", "Собрать-установщик.cmd", ".gitignore"]
# старый установщик, заменён release/installer
REMOVE = ["installer/install.ps1", "installer/Установить-Nox.bat", "installer/app.db.template", "installer/README.md"]

SECRET_PATTERNS = [
    (re.compile(rb"\bam-[A-Za-z0-9_\-]{16,}"), "ключ APIMIRA"),
    (re.compile(rb"\bt\.[A-Za-z0-9_\-]{60,}"), "токен T-Invest"),
    (re.compile(rb"\b(ghp|gho|ghs)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"), "токен GitHub"),
    (re.compile(rb"\bsk-(ant-)?[A-Za-z0-9_\-]{20,}"), "ключ OpenAI/Anthropic"),
    (re.compile(rb"api_hash\s*=\s*['\"][0-9a-f]{32}", re.I), "Telegram api_hash"),
    (re.compile(rb"[A-Za-z0-9._%+\-]+@gmail\.com", re.I), "адрес gmail"),
]
FORBIDDEN = re.compile(r"(app\.db$|_key\.txt$|\.jsonl$|nox_memory\.json$|nox_memory_prompt\.txt$|nox_arrows\.json$|"
                       r"tinvest_pin\.json$|nox_copytrade\.json$|copy_state\.json$|nox_watchers\.json$|nox_notes\.json$|"
                       r"cscalp_(keys|layout)\.json$|\.session$)", re.I)


def work_path(rel):
    top, rest = rel.split("/", 1)
    return os.path.join(LAB if top == "jarvis-lab" else VOICE, *rest.split("/"))


def norm(b):
    return b.replace(b"\r\n", b"\n")


def real_secrets():
    out = {}
    dbs = [os.path.join(os.environ["APPDATA"], "com.priler.jarvis", "app.db")]
    dbs += glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Packages", "Claude_*", "LocalCache",
                                  "Roaming", "com.priler.jarvis", "app.db"))
    for p in dbs:
        try:
            d = json.loads(open(p, "rb").read().decode("utf-8-sig"))
        except Exception:
            continue
        for k in ("invest_token", "fmp_key", "tt_token", "stocksi_key"):
            if d.get(k): out[k] = d[k]
        for k, v in (d.get("api_keys") or {}).items():
            if v: out["api_keys." + k] = v
        try:
            for i, m in enumerate(json.loads(d.get("ai_models") or "[]")):
                if isinstance(m, dict) and m.get("key"): out[f"ai_models[{i}]"] = m["key"]
        except Exception:
            pass
    for p in glob.glob(os.path.join(VOICE, "*_key.txt")):
        v = open(p, "rb").read().decode("utf-8-sig", "replace").strip()
        if v: out[os.path.basename(p)] = v
    return {k: v.encode() for k, v in out.items() if len(v) >= 8}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    apply = "--apply" in sys.argv
    tracked = subprocess.run(["git", "-c", "core.quotePath=false", "-C", REPO, "ls-files"], capture_output=True,
                             text=True, encoding="utf-8", check=True).stdout.splitlines()
    plan = []   # (src, dst_rel)
    for rel in tracked:
        if not rel.startswith(("jarvis-lab/", "jarvis-voice/")):
            continue
        src = work_path(rel)
        dst = os.path.join(REPO, *rel.split("/"))
        if os.path.isfile(src) and os.path.isfile(dst) and norm(open(src, "rb").read()) != norm(open(dst, "rb").read()):
            plan.append((src, rel, "M"))
    for rel in NEW_FILES:
        src = work_path(rel)
        if not os.path.isfile(src):
            print("!! нет файла:", src); continue
        if rel not in tracked:
            plan.append((src, rel, "A"))
    for f in RELEASE_FILES:
        plan.append((os.path.join(RELEASE_DIR, f), "release/" + f, "R"))
    for f in os.listdir(os.path.join(RELEASE_DIR, "installer")):
        plan.append((os.path.join(RELEASE_DIR, "installer", f), "release/installer/" + f, "R"))

    # проверка до копирования
    secrets, bad = real_secrets(), []
    for src, rel, _ in plan:
        if FORBIDDEN.search(rel):
            bad.append(f"личный файл в плане: {rel}")
        data = open(src, "rb").read()
        for rx, what in SECRET_PATTERNS:
            m = rx.search(data)
            if m: bad.append(f"{what} в {rel}")
        for k, v in secrets.items():
            if v in data: bad.append(f"РЕАЛЬНЫЙ ключ {k} в {rel}")
    print(f"реальных ключей для сверки: {len(secrets)}")
    counts = {}
    for _, rel, kind in plan:
        counts[kind] = counts.get(kind, 0) + 1
    print("изменено:", counts.get("M", 0), "| новых:", counts.get("A", 0), "| файлов раздачи:", counts.get("R", 0),
          "| удалить старых:", sum(os.path.exists(os.path.join(REPO, r)) for r in REMOVE))
    for _, rel, kind in plan:
        if kind != "R":
            print(f"  {kind} {rel}")
    if bad:
        print("СТОП:"); [print("  - " + b) for b in bad]; sys.exit(2)
    print("секретов и личных файлов нет")
    if not apply:
        print("(просмотр; для записи: --apply)"); return
    changed = []
    for src, rel, kind in plan:
        dst = os.path.join(REPO, *rel.split("/"))
        if os.path.isfile(dst) and open(src, "rb").read() == open(dst, "rb").read():
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        changed.append(rel)
    for rel in REMOVE:
        if os.path.exists(os.path.join(REPO, rel)):
            subprocess.run(["git", "-C", REPO, "rm", "-q", rel], check=True)
    if changed:
        subprocess.run(["git", "-C", REPO, "add", "--"] + changed, check=True)
    subprocess.run(["git", "-C", REPO, "add", "-u"], check=True)
    print("скопировано и добавлено в индекс:", len(changed))


if __name__ == "__main__":
    main()
