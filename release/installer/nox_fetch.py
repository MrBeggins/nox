# -*- coding: utf-8 -*-
"""
Надёжная загрузка больших файлов (модели голоса, GPU-сборка torch) для установщика Nox.

  python nox_fetch.py URL OUT [--size N] [--sha256 HEX]

Качает кусками по 16 МБ (HTTP Range), каждый кусок проверяется по длине и хранится в OUT.parts\\,
поэтому обрыв связи не страшен: повторный запуск продолжит с первого недостающего куска.
В конце файл собирается и сверяется по SHA-256; при несовпадении куски удаляются (код 2).
Обычная докачка «curl -C -» на медленном канале однажды испортила модель — отсюда куски.
Только стандартная библиотека Python.
"""
import argparse, hashlib, os, shutil, sys, time, urllib.request

CHUNK = int(os.environ.get("NOX_FETCH_CHUNK_MB", "16")) * 1024 * 1024
UA = {"User-Agent": "nox-setup/1.0"}


def remote_size(url):
    req = urllib.request.Request(url, method="HEAD", headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return int(r.headers["Content-Length"])


def get_range(url, start, end):
    req = urllib.request.Request(url, headers={**UA, "Range": f"bytes={start}-{end}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        if r.status != 206 and not (r.status == 200 and start == 0):
            raise IOError(f"сервер не поддержал докачку (HTTP {r.status})")
        data = r.read()
    if r.status == 200:
        data = data[: end - start + 1]
    return data


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("out")
    ap.add_argument("--size", type=int, default=0)
    ap.add_argument("--sha256", default="")
    a = ap.parse_args()
    name = os.path.basename(a.out)
    sha = a.sha256.lower().strip()

    if os.path.isfile(a.out):
        if (not a.size or os.path.getsize(a.out) == a.size) and (not sha or sha256_of(a.out) == sha):
            print(f"  {name}: уже скачан")
            return 0
        os.remove(a.out)

    total = a.size
    for attempt in range(10):
        if total:
            break
        try:
            total = remote_size(a.url)
        except Exception as e:
            print(f"  {name}: нет ответа сервера ({e}), повтор...")
            time.sleep(min(60, 5 * (attempt + 1)))
    if not total:
        print(f"  {name}: не удалось узнать размер файла")
        return 1

    parts = a.out + ".parts"
    os.makedirs(parts, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    n = (total + CHUNK - 1) // CHUNK
    t0, fetched = time.time(), 0
    for i in range(n):
        start = i * CHUNK
        end = min(total, start + CHUNK) - 1
        need = end - start + 1
        p = os.path.join(parts, f"{i:05d}")
        if os.path.isfile(p) and os.path.getsize(p) == need:
            continue
        for attempt in range(12):
            try:
                data = get_range(a.url, start, end)
                if len(data) != need:
                    raise IOError(f"кусок {i}: {len(data)} из {need} байт")
                with open(p + ".tmp", "wb") as fh:
                    fh.write(data)
                os.replace(p + ".tmp", p)
                fetched += need
                break
            except Exception as e:
                wait = min(60, 3 * (attempt + 1))
                print(f"\n  {name}: сбой ({e}), повтор через {wait} с...")
                time.sleep(wait)
        else:
            print(f"\n  {name}: не скачался кусок {i}. Запустите установку ещё раз — продолжит с этого места.")
            return 1
        done = min(total, (i + 1) * CHUNK)
        bps = fetched / max(1e-6, time.time() - t0)
        speed = f"{bps / 2**20:.1f} МБ/с" if bps >= 2**20 else f"{bps / 1024:.0f} КБ/с"
        print(f"\r  {name}: {done * 100 // total}% ({done >> 20} из {total >> 20} МБ, {speed})   ",
              end="", flush=True)
    print()

    h = hashlib.sha256()
    tmp = a.out + ".tmp"
    with open(tmp, "wb") as out:
        for i in range(n):
            with open(os.path.join(parts, f"{i:05d}"), "rb") as fh:
                b = fh.read()
            h.update(b)
            out.write(b)
    if os.path.getsize(tmp) != total or (sha and h.hexdigest() != sha):
        os.remove(tmp)
        shutil.rmtree(parts, ignore_errors=True)
        print(f"  {name}: файл повреждён (не совпала контрольная сумма) — будет скачан заново")
        return 2
    os.replace(tmp, a.out)
    shutil.rmtree(parts, ignore_errors=True)
    print(f"  {name}: готово, проверен")
    return 0


if __name__ == "__main__":
    sys.exit(main())
