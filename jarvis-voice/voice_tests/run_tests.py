# -*- coding: utf-8 -*-
"""
Голосовые регрессионные проверки Нокса — прогонять после КАЖДОЙ правки разбора команд.

  .venv\\Scripts\\python.exe voice_tests\\run_tests.py            — всё (Rust + Python)
  .venv\\Scripts\\python.exe voice_tests\\run_tests.py --no-rust  — без пересборки ядра (быстро)

Слои:
  rust      — настоящий terminal.rs::try_handle на фразах из rust_cases.json (сеть подменена, ничего не исполняется)
  route     — куда уходит фраза в ридере (terminal_reader.route_kind), без исполнения
  numwords / when — числа словами и время напоминаний
  resolve / order_plan — поиск инструмента и расчёт заявки (T-Invest, только чтение)
  apps      — поиск программ для «открой …» (без запуска)
  memory / watch_edit — память и правка наблюдателей голосом (на временных копиях хранилищ)
  dedup     — смысловой фильтр повторов (нужен Ollama, иначе пропуск)
Отчёт: voice_tests\\report.txt
"""
import json
import os
import subprocess
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = r"C:\jarvis-voice"
sys.path.insert(0, ROOT)
os.chdir(ROOT)
C = json.load(open(os.path.join(HERE, "py_cases.json"), encoding="utf-8"))
results = []          # (слой, ок, описание)


def check(layer, ok, what):
    results.append((layer, bool(ok), what))


def run_rust():
    env = dict(os.environ)
    env["PATH"] = env.get("PATH", "") + r";C:\jarvis-lab\target\release;C:\jarvis-lab\lib\windows\amd64"
    t0 = time.time()
    p = subprocess.run(["cargo", "test", "--release", "-p", "jarvis-core", "--lib", "--", "voice_cases", "numwords", "speechfix"],
                       cwd=r"C:\jarvis-lab", env=env, capture_output=True, timeout=1800)
    out = (p.stdout + p.stderr).decode("utf-8", "replace")
    rep = os.path.join(ROOT, "voice_tests", "rust_report.json")
    if "error[" in out or ("could not compile" in out and "test failed" not in out):
        check("rust", False, "ядро не собралось: " + next((l for l in out.splitlines() if "error" in l), "")[:200])
        return
    try:
        if os.path.getmtime(rep) < t0:
            raise RuntimeError("отчёт не обновился")
        for x in json.load(open(rep, encoding="utf-8")):
            check("rust", x["ok"], f"«{x['say']}»" + ("" if x["ok"] else f" — {'; '.join(x['problems'])} | ушло: {x['calls'][:2]}"))
    except Exception as e:
        check("rust", False, f"нет отчёта Rust: {e}")
    nw = [l for l in out.splitlines() if ("numwords" in l or "speechfix" in l) and ("ok" in l or "FAILED" in l)]
    for l in nw:
        check("rust", "ok" in l and "FAILED" not in l, l.strip())


def run_route():
    import terminal_reader as R
    for say, want in C["route"]:
        got = R.route_kind(say)
        check("route", got == want, f"«{say}» → {got}" + ("" if got == want else f" (ждали {want})"))


def run_numwords_when():
    import nox_numwords as W, nox_notes as N
    for say, want in C["numwords"]:
        got = W.digits(say)
        check("numwords", got == want, f"«{say}» → «{got}»" + ("" if got == want else f" (ждали «{want}»)"))
    for say, hhmm, body in C["when"]:
        due, b = N.parse_when(W.digits(say))
        got = due.strftime("%H:%M") if due else None
        check("when", got == hhmm and b == body, f"«{say}» → {got} «{b}»" + ("" if got == hhmm and b == body else f" (ждали {hhmm} «{body}»)"))


def run_resolve():
    import invest_reader as I
    I.ensure_catalog()
    for q, tk in C["resolve"]:
        r = I.resolve(q) or {}
        check("resolve", r.get("ticker") == tk, f"«{q}» → {r.get('ticker')} {r.get('name', '')}" + ("" if r.get("ticker") == tk else f" (ждали {tk})"))
    for p, want in C["order_plan"]:
        try:
            r = I.order_plan(q=p["q"], side="buy", qty=p.get("qty", 1), price=p.get("price", 0), close=0)
            txt = r.get("text", "")
        except Exception as e:
            txt = f"{type(e).__name__}: {e}"
        check("order_plan", want in txt, f"«{p['q']}» → {txt[:120]}")


def run_apps():
    import nox_apps as A
    for say, want in C["apps"]:
        hit = A.find(say)
        got = hit[0] if hit else None
        check("apps", got == want, f"«{say}» → {got}" + ("" if got == want else f" (ждали {want})"))


def run_memory():
    import nox_memory as M
    tmp = tempfile.mkdtemp()
    M.STORE, M.PROMPT_FILE = os.path.join(tmp, "m.json"), os.path.join(tmp, "p.txt")
    for say, want in C["memory"]:
        txt = M.voice(say).get("text", "")
        check("memory", want in txt, f"«{say}» → {txt[:110]}")


def run_watch_edit():
    import nox_watchers as Wt, nox_scenarios as S
    tmp = tempfile.mkdtemp()
    Wt.STORE, S.STORE = os.path.join(tmp, "w.json"), os.path.join(tmp, "s.json")
    Wt.LAYOUT = None
    Wt.upsert({"name": "Газпром санкции", "keywords": ["санкции"], "enabled": True, "branches": [{"type": "any", "orders": [
        {"ticker": "GAZP", "side": "sell", "lots": 5, "price": 120}, {"ticker": "SBER", "side": "sell", "lots": 3},
        {"ticker": "MIX-12.26", "side": "sell", "venue": "cscalp"}]}]})
    for say, want in C["watch_edit"]:
        txt = Wt.edit_from_voice(say).get("text", "")
        check("watch_edit", want in txt, f"«{say}» → {txt[:110]}")


def run_dedup():
    import nox_dedup as D
    if D.judge("тест", "тест", timeout=60) is None:
        check("dedup", True, "пропуск: Ollama недоступен")
        return
    for a, b, want in C["dedup"]:
        D.reset()
        D.is_dup(a, ttl=3600)
        got = D.is_dup(b, ttl=3600)
        check("dedup", got == want, f"«{a[:45]}» / «{b[:45]}» → {'повтор' if got else 'разные'}" + ("" if got == want else " ✗"))
    D.reset()


def run_brain_ctx():
    """История разговора и живые данные для мозга — на подставном диалоге (мозг не вызывается)."""
    import terminal_reader as R
    R._dialog.clear(); R._announced.clear()
    R._dialog_add("user", "что с газпромом")
    R._dialog_add("nox", "Газпром растёт после новости про налог.")
    check("brain_ctx", R._dialog[-1][1] == "nox", f"ответ сразу после вопроса — «nox» ({R._dialog[-1][1]})")
    R._dialog_add("nox", "Сбербанк отчитался о прибыли за сентябрь.")
    check("brain_ctx", R._dialog[-1][1] == "news", f"второй ответ без нового вопроса — «news» ({R._dialog[-1][1]})")
    R._announced.append(R._dkey("ЦБ сохранил ставку"))
    R._dialog_add("user", "а почему он растёт")
    R._dialog_add("nox", "ЦБ сохранил ставку, сэр.")
    check("brain_ctx", R._dialog[-1][1] == "news", f"озвученное ридером — «news» даже после вопроса ({R._dialog[-1][1]})")
    R._dialog.pop()
    req = R._brain_request("а почему он растёт")
    check("brain_ctx", "ИСТОРИЯ РАЗГОВОРА" in req and "что с газпромом" in req, "история попала в запрос")
    check("brain_ctx", "а почему он растёт" not in req.split("ЗАПРОС:")[0], "текущая фраза не дублируется в истории")
    check("brain_ctx", "Газпром (GAZP)" in req, "продолжение «а почему он…» — данные по Газпрому из прошлой фразы")
    req2 = R._brain_request("что с нефтью и долларом")
    check("brain_ctx", "BR-" in req2 and "USD" in req2, "нефть и доллар — оба инструмента в данных")
    req3 = R._brain_request("Си Цзиньпин встретился с Путиным")
    check("brain_ctx", "ДАННЫЕ ТЕРМИНАЛА" not in req3.split("ИСТОРИЯ")[0] or "Си-" not in req3, "«Си Цзиньпин» — не фьючерс Si")
    R._dialog.clear()


def run_feedback():
    """«Такие не читай»: пример попадает в фильтр (временная копия news_filter.json), умный отбор отсекает перефраз."""
    import shutil, copy
    import terminal_reader as R, nox_feedback as F, nox_dedup as D
    tmp = os.path.join(tempfile.mkdtemp(), "f.json")
    shutil.copy(R.FILTER_FILE, tmp)
    real, R.FILTER_FILE = R.FILTER_FILE, tmp
    try:
        R._read_last.clear()
        txt = R.news_feedback("такие не читай")
        check("feedback", "Не помню" in txt, "без прочитанной новости — честно «не помню»")
        R._read_last.append({"ts": time.time(), "text": "Зеленский: Наш приоритет - прекращение войны, мы ведем переговоры",
                             "hits": ["переговоры"]})
        txt = R.news_feedback("такие не читай")
        ex = R._load_filter().get("examples", {}).get("переговоры", {})
        check("feedback", "больше не читаю" in txt and len(ex.get("no", [])) == 1, f"пример «не нужно» сохранён: {txt[:80]}")
        R.news_feedback("такие читай всегда")
        ex = R._load_filter().get("examples", {}).get("переговоры", {})
        check("feedback", len(ex.get("no", [])) == 0 and len(ex.get("yes", [])) == 1, "«читай всегда» переносит пример в «нужно»")
    finally:
        R.FILTER_FILE = real
    if D.judge("тест", "тест", timeout=60) is None:
        check("feedback", True, "пропуск проверки умного отбора: Ollama недоступен")
        return
    for sc in C.get("feedback", []):
        cfg = copy.deepcopy(R._load_filter())
        cfg["smart"] = True
        for t in sc["no"]:
            F.apply(cfg, t, [sc["hit"]], "no")
        for t, want in sc["check"]:
            ok, why = R._smart_check({"text": t, "tickers": [], "source": ""}, cfg)
            check("feedback", ok == want, f"«{t[:60]}» → {'читаю' if ok else 'НЕ читаю'}" + ("" if ok == want else " ✗"))


def run_bonds():
    """Сценарии по облигациям: голосовые правила (временное хранилище) и сквозной путь события НРД с ЗАГЛУШКОЙ заявки."""
    import nox_bond_rules as B, terminal_reader as R, nox_nsd
    B.STORE = os.path.join(tempfile.mkdtemp(), "b.json")
    for say, want in [("при дефолте самолета продай акции", "продать акции эмитента"),
                      ("при техдефолте облигаций пик закрой облигации", "закрыть облигации"),
                      ("при любом дефолте облигаций предупреди", "предупредить (вкл)"),
                      ("если просрочка по облигациям пик шорт два лота", "2 лот"),
                      ("при дефолте продай акции", "По какому эмитенту")]:
        txt = B.voice(say)["text"]
        check("bonds", want in txt, f"«{say}» → {txt[:90]}")
    d = B.load()
    check("bonds", all(not r["enabled"] for r in d["rules"] if r["action"] != "alert"), "правила с заявками созданы выключенными")
    check("bonds", all(not r["exec"] for r in d["rules"]), "правила только заполняют заявку (exec=false)")
    B.voice("включи правило по облигациям самолета")
    ev = {"status": "late", "issuer": 'ПАО "ГК "Самолет"', "isin": "RU000A10CZA1", "kind": "погашение облигаций",
          "series": "БО-П20", "late_days": 1, "id": "test", "url": "test"}
    sent, said = [], []
    real_fill, real_ann = R.fill_order, R._ipc_announce
    R.fill_order = lambda **k: (sent.append(k), {"text": "(заглушка: заявка не отправлялась)"})[1]
    R._ipc_announce = lambda t: said.append(t)
    import threading as _th
    started = []
    real_thread = _th.Thread
    try:
        R._nsd_event(ev)
        time.sleep(1.5)
    finally:
        R.fill_order, R._ipc_announce = real_fill, real_ann
    check("bonds", any("НРД: Самолет" in x for x in said), f"голос по событию: {said[:1]}")
    check("bonds", any("Акции эмитента" in x or "У вас" in x or "Позиций" in x for x in said), "в голосе — что у вас по эмитенту")
    # у пользователя нет лонга SMLT — правило «продать акции» заявку не ставит, а объясняет
    check("bonds", not sent and any("продавать нечего" in x for x in said), f"нет лонга — заявки нет: {[x[:60] for x in said]}")
    st = R.nsd_status()
    check("bonds", "alive" in st, f"/nsd_status: alive={st.get('alive')}, проходов {st.get('passes')}")


def run_rates():
    """Решения ЦБ/ФРС по правилам (nox_rates) — то, по чему наблюдатель kind=rate жмёт заявки."""
    import nox_rates as RT
    for text, bank, action, bp in C.get("rates", []):
        d = RT.parse_decision(text, rate_now=16)
        got = (d or {}).get("bank"), (d or {}).get("action"), (d or {}).get("change_bp")
        want = (bank, action, bp)
        check("rates", got == want if bank else d is None, f"«{text[:60]}» → {got if d else 'не решение'}")


def run_textnorm():
    """Нормализатор речи (как Нокс произносит новости): сверка с эталоном voice_tests/textnorm_golden.json.
    Эталона нет — создаётся из текущих ответов (проверьте его на слух/глазами); изменилось — провал с «было/стало»."""
    import nox_textnorm as TN
    gp = os.path.join(HERE, "textnorm_golden.json")
    gold = json.load(open(gp, encoding="utf-8")) if os.path.exists(gp) else {}
    new = {}
    for t in C.get("textnorm", []):
        out = TN.normalize(t)
        new[t] = out
        if t in gold:
            check("textnorm", gold[t] == out, f"«{t}» → «{out}»" + ("" if gold[t] == out else f" (было «{gold[t]}»)"))
        else:
            check("textnorm", True, f"эталон записан: «{t}» → «{out}»")
    if any(t not in gold for t in new):
        json.dump({**gold, **{k: v for k, v in new.items() if k not in gold}}, open(gp, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)


LAYERS = [("route", run_route), ("brain_ctx", run_brain_ctx), ("feedback", run_feedback), ("bonds", run_bonds),
          ("rates", run_rates), ("textnorm", run_textnorm), ("numwords/when", run_numwords_when), ("resolve", run_resolve), ("apps", run_apps),
          ("memory", run_memory), ("watch_edit", run_watch_edit), ("dedup", run_dedup)]
if "--no-rust" not in sys.argv:
    LAYERS.insert(0, ("rust", run_rust))
t0 = time.time()
for name, fn in LAYERS:
    try:
        fn()
    except Exception as e:
        check(name, False, f"слой упал: {type(e).__name__}: {e}")
ok = sum(1 for r in results if r[1])
lines = [f"Голосовые проверки Нокса: {ok} из {len(results)} прошли за {time.time() - t0:.0f} с"]
by = {}
for layer, good, _w in results:
    by.setdefault(layer, [0, 0]); by[layer][0] += good; by[layer][1] += 1
lines.append("  " + " · ".join(f"{k} {v[0]}/{v[1]}" for k, v in by.items()))
bad = [r for r in results if not r[1]]
if bad:
    lines.append("ПРОВАЛЫ:")
    lines += [f"  [{layer}] {w}" for layer, _g, w in bad]
txt = "\n".join(lines)
print(txt)
open(os.path.join(HERE, "report.txt"), "w", encoding="utf-8").write(txt + "\n\nВСЕ:\n" + "\n".join(f"  {'✓' if g else '✗'} [{l}] {w}" for l, g, w in results))
sys.exit(0 if not bad else 1)
