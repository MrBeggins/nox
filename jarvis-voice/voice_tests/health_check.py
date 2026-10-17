# -*- coding: utf-8 -*-
"""
Проверка работающего Нокса: все службы и их функции — ТОЛЬКО чтение (заявки, нажатия, смена настроек не вызываются).
  .venv\\Scripts\\python.exe voice_tests\\health_check.py
Отчёт: voice_tests\\health_report.txt
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
R, I, B, E, N, S, T = ("http://127.0.0.1:%d" % p for p in (8126, 8124, 8128, 8129, 8133, 8132, 8123))
rows = []


def get(url, timeout=40):
    t = time.time()
    raw = urllib.request.urlopen(url, timeout=timeout).read()
    ms = int((time.time() - t) * 1000)
    try:
        return json.loads(raw.decode("utf-8")), ms
    except Exception:
        return {"_raw": raw[:200]}, ms


def q(base, path, **params):
    return base + path + ("?" + urllib.parse.urlencode(params) if params else "")


def check(name, url, test, slow_ms=None, timeout=40):
    try:
        d, ms = get(url, timeout)
        ok, note = test(d)
        if ok and slow_ms and ms > slow_ms:
            note = f"МЕДЛЕННО {ms} мс (норма ≤{slow_ms}) · " + note
            ok = "slow"
        rows.append((name, ok, ms, note))
    except Exception as e:
        rows.append((name, False, 0, f"{type(e).__name__}: {str(e)[:120]}"))


txt = lambda d: (d.get("text") or "") if isinstance(d, dict) else ""
has = lambda *ws: (lambda d: (all(w.lower() in json.dumps(d, ensure_ascii=False).lower() for w in ws),
                              txt(d)[:110] or json.dumps(d, ensure_ascii=False)[:110]))

# --- ридер терминала
check("ридер: подключён к терминалу", q(R, "/health"), lambda d: (d.get("status") == "ok", d.get("url", d.get("err", ""))[:80]))
check("ридер: лента новостей", q(R, "/news", n=3), lambda d: (len(txt(d)) > 20 and "недоступ" not in txt(d), txt(d)[:110]), 8000)
check("ридер: последняя новость", q(R, "/last_news"), lambda d: (bool(txt(d)), txt(d)[:110]), 8000)
check("ридер: элементы обеих лент", q(R, "/mon_items"), lambda d: (bool(d), json.dumps(d, ensure_ascii=False)[:110]), 8000)
check("ридер: гонка лент TT/Stocksi", q(R, "/feed_race"), lambda d: ("first" in d, f"пар {d.get('pairs')}, первой {d.get('first')}"))
check("ридер: события календаря", q(R, "/events", q="дивиденды"), lambda d: (bool(txt(d)) and "ошибк" not in txt(d).lower(), txt(d)[:110]), 15000, 60)
check("ридер: календарь на сегодня", q(R, "/cal_today"), lambda d: (isinstance(d, dict), json.dumps(d, ensure_ascii=False)[:110]))
check("ридер: фильтр новостей", q(R, "/filter"), lambda d: (bool(txt(d)) or "phrases" in d, (txt(d) or str(d))[:110]))
check("ридер: исполнение заявок (состояние)", q(R, "/exec_state"), lambda d: (isinstance(d, dict) and not d.get("error"), json.dumps(d, ensure_ascii=False)[:110]))
check("ридер: наблюдатели", q(R, "/watch_list"), lambda d: ("watchers" in d, f"наблюдателей {len(d.get('watchers', []))}"))
check("ридер: сценарии стакана", q(R, "/scn_list"), lambda d: (isinstance(d, dict), json.dumps(d, ensure_ascii=False)[:110]))
check("ридер: стрелки оверлея", q(R, "/arrows_get"), lambda d: (d.get("ok") is not False, json.dumps(d.get("cscalp", d), ensure_ascii=False)[:110]), 15000)
check("ридер: стрелка ▲ (проверка без нажатий)", q(R, "/arrow_dry", side="buy"), lambda d: (bool(txt(d)), txt(d)[:110]), 15000, 60)
check("ридер: повтор сделок ProfitKing", q(R, "/copy_get"), lambda d: (isinstance(d, dict), json.dumps(d, ensure_ascii=False)[:110]))
check("инструменты: котировка «пик» = акция", q(I, "/quote", q="пик"), has("ПИК-Специализированный"), 3000)
check("ридер: опрос НРД", q(R, "/nsd_status"), lambda d: (bool(d.get("ok")) and d.get("alive") is not False,
      f"последний проход {d.get('ago_s')} с назад, проходов {d.get('passes')}, вкладка {d.get('alive')}, ошибка «{d.get('last_err')}»"))
check("ридер: память", q(R, "/memory_get"), lambda d: (len(d.get("rules", [])) > 0, f"правил {len(d.get('rules', []))}, фактов {len(d.get('facts', []))}"))
check("ридер: данные для мозга", q(R, "/brain_context", text="что с газпромом"), lambda d: ("Газпром" in d.get("request", ""), d.get("request", "")[-110:]), 3000)
check("ридер: зрение (снимок без отправки)", q(R, "/vision_shot", q="что на экране"), lambda d: (d.get("ok") and d.get("kb", 0) > 20, f"{d.get('where')}, {d.get('kb')} КБ, {d.get('ms')} мс"), 4000)
check("ридер: каталог мозгов", q(R, "/brain_models"), lambda d: (d.get("cloud_ok") and d.get("ollama_ok"), f"облако {len(d.get('cloud', []))}, Ollama {d.get('ollama')}"), 12000)
check("ридер: голоса", q(R, "/voice_get"), lambda d: (isinstance(d, dict), json.dumps(d, ensure_ascii=False)[:110]))
check("ридер: поиск в интернете", q(R, "/web_answer", q="курс юаня к рублю сегодня"), lambda d: (len(txt(d)) > 15 and "Не смог" not in txt(d), txt(d)[:110]), 15000, 90)
check("ридер: ответ мозга по данным", q(R, "/route", text="как там сбер"), lambda d: ("сбер" in txt(d).lower() and len(txt(d)) > 30, txt(d)[:110]), 9000, 90)
check("ридер: ответ мозга без данных", q(R, "/route", text="что такое ликвидность простыми словами"), lambda d: (len(txt(d)) > 30, txt(d)[:110]), 12000, 90)
# --- инструменты и портфель (T-Invest, чтение)
check("инструменты: счета и доступ токена", q(I, "/accounts"), lambda d: (d.get("ok"), f"счетов {len(d.get('accounts', []))}, торговля {d.get('can_trade')}, TLS-пин {d.get('pinned')}"))
check("инструменты: портфель", q(I, "/portfolio"), has("Портфель"), 5000)
check("инструменты: котировка Сбера", q(I, "/quote", q="сбер"), has("Сбербанк"), 3000)
check("инструменты: стакан Сбера", q(I, "/orderbook", q="сбер"), lambda d: (bool(txt(d)) and "Не нашёл" not in txt(d), txt(d)[:110]), 3000)
check("инструменты: статус торгов", q(I, "/status", q="сбер"), lambda d: (bool(txt(d)), txt(d)[:110]), 3000)
check("инструменты: фьючерс «микс»", q(I, "/quote", q="микс"), has("MIX"), 3000)
check("инструменты: живые данные", q(I, "/context", text="что с нефтью и долларом"), lambda d: (len(d.get("items", [])) == 2, d.get("text", "")[:110]), 3000)
check("инструменты: НРД — позиции по эмитенту", q(I, "/nsd_exposure", isin="RU000A10CZA1", issuer="Самолет"), lambda d: (d.get("ok"), d.get("text", "")[:110]), 5000)
check("инструменты: расчёт заявки ПИК (акция)", q(I, "/order_plan", q="пик", qty=1, price=500), has("ПИК-Специализированный"), 5000)
# --- статистика США
check("BLS: ближайший релиз", q(B, "/next"), lambda d: (bool(txt(d)), txt(d)[:110]), 8000)
check("BEA: ближайший релиз", q(E, "/next"), lambda d: (bool(txt(d)), txt(d)[:110]), 8000)
# --- помощник
check("заметки: список", q(N, "/notes"), lambda d: (bool(txt(d)), txt(d)[:110]))
check("заметки: напоминания", q(N, "/reminders"), lambda d: (bool(txt(d)), txt(d)[:110]))
check("монитор ПК: нагрузка", q(S, "/stats"), lambda d: (d.get("cpu") is not None, txt(d)[:110]), 3000)
# --- голос: синтез без проигрывания
try:
    t0 = time.time()
    req = urllib.request.Request(T + "/tts", data=json.dumps({"text": "Проверка голоса."}).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    wav = urllib.request.urlopen(req, timeout=30).read()
    ms = int((time.time() - t0) * 1000)
    rows.append(("голос: синтез фразы (без проигрывания)", wav[:4] == b"RIFF" and len(wav) > 10000 if ms < 1500 else "slow", ms,
                 f"{len(wav) // 1024} КБ WAV"))
except Exception as e:
    rows.append(("голос: синтез фразы", False, 0, repr(e)[:120]))

bad = [r for r in rows if r[1] is False]
slow = [r for r in rows if r[1] == "slow"]
lines = [f"Проверка Нокса: {len(rows) - len(bad) - len(slow)} ок, {len(slow)} медленно, {len(bad)} ошибок из {len(rows)}"]
for name, ok, ms, note in rows:
    mark = "✓" if ok is True else ("◐" if ok == "slow" else "✗")
    lines.append(f" {mark} {name:42s} {ms:6d} мс  {note}")
out = "\n".join(lines)
print(out)
open(os.path.join(HERE, "health_report.txt"), "w", encoding="utf-8").write(out)
