# -*- coding: utf-8 -*-
"""
Публикация установщика в GitHub Releases (github.com/MrBeggins/nox).

  py -3.11 publish_release.py <версия> [--notes файл.md]   — черновик релиза + загрузка установщика
  py -3.11 publish_release.py <версия> --publish          — опубликовать (только если exe загружен полностью)

Токен берётся из сохранённых учётных данных git (git credential fill, host github.com) — не печатается.
Создаёт релиз v<версия> на текущем main (если его нет) и загружает dist\\Nox-Setup-<версия>.exe и .sha256.
Загрузка большого файла идёт потоком с прогрессом в release_upload.log; при сбое неполный файл
удаляется из релиза и загрузка повторяется (до 3 раз).
"""
import http.client, json, os, subprocess, sys, time, urllib.request

OWNER, REPO = "MrBeggins", "nox"
ROOT = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(ROOT, "release_upload.log")


def log(m):
    line = time.strftime("[%H:%M:%S] ") + m
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def token():
    r = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                       capture_output=True, text=True, timeout=60)
    for ln in r.stdout.splitlines():
        if ln.startswith("password="):
            return ln.split("=", 1)[1].strip()
    sys.exit("нет сохранённого токена GitHub (git credential)")


def api(method, url, tok, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "token " + tok, "Accept": "application/vnd.github+json",
        "User-Agent": "nox-release", "Content-Type": "application/json"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, raw.decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as e:      # таймаут/обрыв сети — повторить
            log(f"сеть: {e} — повтор {attempt + 1}")
            time.sleep(10 * (attempt + 1))
    sys.exit("GitHub API недоступен")


def upload(tok, release, path, ctype):
    name = os.path.basename(path)
    size = os.path.getsize(path)
    for attempt in range(1, 4):
        # неполная загрузка прошлой попытки — удалить
        st, assets = api("GET", release["assets_url"] + "?per_page=100", tok)
        for a in (assets or []):
            if a["name"] == name:
                if a.get("state") == "uploaded" and a.get("size") == size:
                    log(f"{name}: уже загружен"); return True
                api("DELETE", a["url"], tok)
                log(f"{name}: удалён неполный файл прошлой попытки")
        log(f"{name}: загрузка {size / 2**20:.0f} МБ, попытка {attempt}")
        try:
            conn = http.client.HTTPSConnection("uploads.github.com", timeout=600)
            conn.putrequest("POST", f"/repos/{OWNER}/{REPO}/releases/{release['id']}/assets?name={name}")
            for k, v in (("Authorization", "token " + tok), ("Content-Type", ctype), ("Content-Length", str(size)),
                         ("Accept", "application/vnd.github+json"), ("User-Agent", "nox-release")):
                conn.putheader(k, v)
            conn.endheaders()
            sent, t0, last = 0, time.time(), 0
            with open(path, "rb") as fh:
                while True:
                    b = fh.read(1 << 20)
                    if not b:
                        break
                    conn.send(b)
                    sent += len(b)
                    if time.time() - last > 30:
                        last = time.time()
                        speed = sent / max(1, last - t0) / 2**20
                        eta = (size - sent) / max(1, sent / max(1, last - t0)) / 60
                        log(f"  {name}: {sent * 100 // size}% ({sent >> 20} из {size >> 20} МБ, {speed:.2f} МБ/с, осталось ~{eta:.0f} мин)")
            r = conn.getresponse()
            body = r.read().decode("utf-8", "replace")
            if r.status in (200, 201):
                log(f"{name}: загружен за {(time.time() - t0) / 60:.1f} мин")
                return True
            log(f"{name}: ответ {r.status}: {body[:300]}")
        except Exception as e:
            log(f"{name}: сбой загрузки: {type(e).__name__}: {e}")
        time.sleep(15)
    return False


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ver = sys.argv[1]
    notes = ""
    if "--notes" in sys.argv:
        notes = open(sys.argv[sys.argv.index("--notes") + 1], encoding="utf-8").read()
    tok = token()
    base = f"https://api.github.com/repos/{OWNER}/{REPO}"
    tag = "v" + ver
    # релиз создаётся ЧЕРНОВИКОМ и публикуется отдельно (--publish), когда установщик проверен и загружен
    rel = None
    st, rels = api("GET", f"{base}/releases?per_page=50", tok)
    for r in (rels if isinstance(rels, list) else []):
        if r.get("tag_name") == tag:
            rel = r
    if "--publish" in sys.argv:
        if not rel:
            sys.exit("нет релиза " + tag)
        names = {a["name"]: a for a in rel.get("assets", [])}
        exe_name = f"Nox-Setup-{ver}.exe"
        exe_path = os.path.join(ROOT, "dist", exe_name)
        a = names.get(exe_name)
        if not a or a.get("state") != "uploaded" or a.get("size") != os.path.getsize(exe_path):
            sys.exit("установщик в релизе не загружен полностью — не публикую")
        st, r = api("PATCH", rel["url"], tok, {"draft": False, "make_latest": "true"})
        log(f"опубликован: {r.get('html_url') if isinstance(r, dict) else r} ({st})")
        sys.exit(0 if st == 200 else 1)
    if not rel:
        st, rel = api("POST", f"{base}/releases", tok, {
            "tag_name": tag, "target_commitish": "main", "name": f"Nox {ver}",
            "body": notes, "draft": True, "prerelease": False})
        if st not in (200, 201):
            sys.exit(f"не создан релиз: {st} {rel}")
        log(f"создан черновик релиза {tag}")
    else:
        if notes:
            api("PATCH", rel["url"], tok, {"body": notes, "name": f"Nox {ver}"})
        log(f"релиз {tag} уже есть (черновик: {rel.get('draft')})")
    exe = os.path.join(ROOT, "dist", f"Nox-Setup-{ver}.exe")
    ok = upload(tok, rel, exe + ".sha256", "text/plain") and upload(tok, rel, exe, "application/octet-stream")
    log("ЗАГРУЖЕНО" if ok else "НЕ ЗАГРУЖЕНО")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
