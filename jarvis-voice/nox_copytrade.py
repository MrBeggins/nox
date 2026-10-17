# -*- coding: utf-8 -*-
"""
Копирование сделок трейдера (по умолчанию ProfitKing) из ленты Stocksi Ultimate «Сделки»:
  «ProfitKing купил ROSN» (#сделка, тикер в шапке, «Цена на момент сделки: 370,5 руб.»), «ProfitKing закрыл PLZL».
Его сигналы сносят стаканы — важна каждая сотня миллисекунд:
  - в странице терминала MutationObserver на ленте Stocksi: новая строка с сигналом -> window.noxCopySig2(json)
    (Runtime.addBinding — событие в Python за 2–3 мс, без опроса);
  - заявка — официальный T-Invest API (OrdersService/PostOrder, рыночная, 1 лот) через nox_tinvest: закреплённый
    ключ сервера, соединение держится тёплым (GetInfo раз в 20 с) — заявка ~30–60 мс.
Правила (решение пользователя 16.10): счёт «Брокерский счет …5143»; 1 лот; «купил» — если ещё не держим;
«закрыл/продал» — продаём только купленное НАМИ (copy_state.json) и только если лот есть на счёте — в шорт не идём;
старые сигналы и повторы — мимо; не больше max_day сделок в день.
Исполнение — только при включённых копировании (nox_copytrade.json enabled) И тумблере исполнения
(Настройки -> Т-Терминал), сумма лота — в пределах лимита оттуда же.
"""
import asyncio
import concurrent.futures
import json
import os
import re
import time
import uuid
import urllib.request

import nox_tinvest

CDP_URL = "http://127.0.0.1:9222"
HERE = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(HERE, "nox_copytrade.json")
STATE_FILE = os.path.join(HERE, "copy_state.json")
CATALOG_FILE = os.path.join(HERE, "instruments.json")

DEFAULT = {"enabled": False, "trader": "ProfitKing", "account_suffix": "5143", "lots": 1, "max_pos": 1,
           "buy": True, "close": True, "short": False, "fresh_min": 2, "max_day": 20}

# ------------------------------------------------------------------ страница: только увидеть сигнал
PAGE_JS = r"""((trader) => {
  const W = window;
  if (W.__noxCopy) { try { W.__noxCopy.obs && W.__noxCopy.obs.disconnect(); } catch (e) {} delete W.__noxCopy; }   // прежняя версия
  if (!W.__noxCopy2) W.__noxCopy2 = {seen: new Set(), box: null, obs: null, n: 0, q: []};
  const C = W.__noxCopy2;
  C.trader = String(trader || '').toLowerCase();
  const rx = /^\s*([A-Za-z0-9_.\-]{3,40})\s+(купил|докупил|закрыл|продал|сократил|зафиксировал|шорт\S*|открыл\S*)\s+([A-Za-z0-9]{2,14})/i;
  const sid = (p) => ((String(p.className).match(/stocksiNews-(\d+)/) || [])[1] || '');
  const scan = (initial) => {
    const box = C.box; if (!box) return;
    for (const p of box.querySelectorAll('.stocksiNewsPreview')) {
      const id = sid(p); if (!id || C.seen.has(id)) continue;
      C.seen.add(id);
      if (initial) continue;
      const title = ((p.querySelector('.stocksiNewsTitle') || {}).textContent || '').replace(/\s+/g, ' ').trim();
      const m = title.match(rx); if (!m || m[1].toLowerCase() !== C.trader) continue;
      const tkEl = p.querySelector('.ticker[data-link-widget]');
      const sig = {id, t: Date.now(), trader: m[1], verb: m[2].toLowerCase(),
                   ticker: ((tkEl && tkEl.getAttribute('data-link-widget')) || m[3]).toUpperCase(), title: title.slice(0, 200),
                   time: ((p.querySelector('.time-section') || {}).textContent || '').trim(),
                   short: ((p.querySelector('.stocksiNewsShort') || {}).textContent || '').replace(/\s+/g, ' ').trim().slice(0, 300),
                   src: ((p.querySelector('.NewsSource') || {}).textContent || '').trim()};
      C.n++;
      try { W.noxCopySig2(JSON.stringify(sig)); } catch (e) { C.q.push(sig); }
    }
    if (C.seen.size > 20000) C.seen = new Set([...C.seen].slice(-5000));
  };
  const box = document.querySelector('.stocksiWidget .newsListContainer');
  if (box && C.box !== box) {
    if (C.obs) C.obs.disconnect();
    const first = C.seen.size === 0;            // перезагрузка страницы: текущая лента — старая
    C.box = box;
    scan(first);
    C.obs = new MutationObserver(() => scan(false));
    C.obs.observe(box, {childList: true, subtree: true});
  }
  const q = C.q; C.q = [];
  return {box: !!box, seen: C.seen.size, n: C.n, q, vis: document.visibilityState};
})"""


# ------------------------------------------------------------------ настройки и состояние
def load_cfg():
    try:
        d = json.load(open(CFG_FILE, encoding="utf-8"))
    except Exception:
        d = {}
    return {**DEFAULT, **{k: v for k, v in d.items() if k in DEFAULT}}


def save_cfg(d):
    cfg = {**load_cfg(), **{k: v for k, v in d.items() if k in DEFAULT}}
    # свой объём повтора (не лимиты наблюдателей: «в новость можно кликнуть много, а в участника нельзя»)
    try:
        cfg["lots"] = max(1, min(int(cfg.get("lots") or 1), 100))
    except Exception:
        cfg["lots"] = 1
    tmp = CFG_FILE + ".tmp"
    json.dump(cfg, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, CFG_FILE)
    return cfg


def load_state():
    try:
        d = json.load(open(STATE_FILE, encoding="utf-8"))
    except Exception:
        d = {}
    d.setdefault("positions", {}); d.setdefault("done", {}); d.setdefault("log", [])
    return d


def save_state(st):
    now = time.time()
    st["done"] = {k: v for k, v in st["done"].items() if now - v < 3 * 86400}
    st["log"] = st["log"][-200:]
    tmp = STATE_FILE + ".tmp"
    json.dump(st, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_FILE)


def his_price(sig):
    m = re.search(r"Цена на момент сделки:\s*([\d\s ]+(?:[.,]\d+)?)", sig.get("short") or "")
    if not m:
        return None
    try:
        return float(re.sub(r"[\s ]", "", m.group(1)).replace(",", "."))
    except Exception:
        return None


def fresh(sig, fresh_min):
    """«Сегодня, 12:26» не старше fresh_min минут (лента показывает минуты). Вчера / дата — старая."""
    ts = sig.get("time") or ""
    if re.search(r"вчера|\d{1,2}\.\d{1,2}|январ|феврал|март|апрел|ма[яй]|июн|июл|август|сентябр|октябр|ноябр|декабр", ts, re.I):
        return False
    m = re.search(r"(\d{1,2}):(\d{2})", ts)
    if not m:
        return True
    lt = time.localtime()
    return (lt.tm_hour * 60 + lt.tm_min) - (int(m.group(1)) * 60 + int(m.group(2))) <= fresh_min


def _lots_word(n):
    n = abs(int(n))
    if 11 <= n % 100 <= 14:
        return "лотов"
    return {1: "лот", 2: "лота", 3: "лота", 4: "лота"}.get(n % 10, "лотов")


def say_action(action, qty=1):
    """«купил 3 лота», «открыл шорт 1 лот»."""
    verb = {"buy": "купил", "cover": "закрыл шорт", "sell": "продал", "short": "открыл шорт"}.get(action, "")
    return f"{verb} {qty} {_lots_word(qty)}".strip()


def decide(sig, cfg, pos, own=None):
    """Каждый сигнал — отдельная отработка участника (пользователь 16.10: позиции раздаёт сразу сам, лонг или шорт):
    «купил» -> лонг 1 лот, «закрыл/продал» -> шорт 1 лот. pos — НЕ розданная ещё позиция от копирования (+1/0/−1,
    уже сверена со счётом), own — лоты на счёте помимо копирования (None — неизвестно).
    -> (действие | None, причина): buy, cover (купить — закрыть наш шорт), sell (продать — закрыть наш лонг), short."""
    verb, tk = sig["verb"], sig["ticker"]
    if re.match(r"(купил|докупил)", verb):
        if not cfg.get("buy", True):
            return None, "покупки выключены"
        if pos >= 1:
            return None, f"лонг {tk} от копирования ещё не роздан"
        if pos <= -1:
            return "cover", ""
        if own is not None and own < 0:
            return None, f"на счёте ваш шорт {tk} — не трогаю"
        return "buy", ""
    if re.match(r"(закрыл|продал|сократил|зафиксировал)", verb):
        if not cfg.get("close", True):
            return None, "продажи выключены"
        if pos >= 1:
            return "sell", ""
        if pos <= -1:
            return None, f"шорт {tk} от копирования ещё не роздан"
        if not cfg.get("short"):
            return None, "шорт выключен"
        if own is not None and own > 0:
            return None, f"на счёте ваши акции {tk} — шорт не открываю, чтобы не продать их"
        return "short", ""
    return None, f"«{verb}» не копируем"


_catalog = {"ts": 0, "by": {}}


def instrument(ticker):
    """Тикер -> акция Мосбиржи (TQBR) из каталога invest_reader: {uid, figi, lot, name}."""
    try:
        mt = os.path.getmtime(CATALOG_FILE)
        if mt != _catalog["ts"]:
            by = {}
            for x in json.load(open(CATALOG_FILE, encoding="utf-8"))["items"]:
                if x.get("type") == "share" and x.get("class") == "TQBR":
                    by[str(x.get("ticker") or "").upper()] = x
            _catalog.update(ts=mt, by=by)
    except Exception:
        pass
    return _catalog["by"].get((ticker or "").upper())


# ------------------------------------------------------------------ API (через nox_tinvest, свой поток — соединение тёплое)
_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="copy-api")   # заявки и цены
_bg = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="copy-bg")      # счёт и позиции
_acc = {"id": "", "name": "", "access": "", "pos": {}, "pos_ts": 0.0, "rtt": None, "err": "",
        "short_ok": {}, "short_ts": 0.0}


async def _api(method, body, bg=False, timeout=8.0):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_bg if bg else _pool, lambda: nox_tinvest.call(method, body, timeout))


async def load_account(bg=True):
    r = await _api("UsersService/GetAccounts", {}, bg=bg)
    suf = str(load_cfg().get("account_suffix") or "")
    a = next((a for a in r.get("accounts") or [] if str(a.get("id", "")).endswith(suf) and suf
              and a.get("status") == "ACCOUNT_STATUS_OPEN"), None)
    _acc.update(id=(a or {}).get("id", ""), name=(a or {}).get("name", ""), access=(a or {}).get("accessLevel", ""))


async def refresh_positions(bg=True):
    if not _acc["id"]:
        return
    r = await _api("OperationsService/GetPositions", {"accountId": _acc["id"]}, bg=bg)
    pos = {}
    for s in r.get("securities") or []:
        pos[s.get("instrumentUid") or s.get("figi")] = float(s.get("balance", 0) or 0) + float(s.get("blocked", 0) or 0)
    _acc.update(pos=pos, pos_ts=time.time())


async def load_short_flags():
    """По каким акциям Мосбиржи брокер разрешает шорт (shortEnabledFlag) — раз в сутки, в фоне."""
    r = await _api("InstrumentsService/Shares", {"instrumentStatus": "INSTRUMENT_STATUS_BASE"}, bg=True, timeout=30)
    flags = {str(x.get("ticker") or "").upper(): bool(x.get("shortEnabledFlag"))
             for x in r.get("instruments") or [] if x.get("classCode") == "TQBR"}
    if flags:
        _acc.update(short_ok=flags, short_ts=time.time())


async def _keeper():
    """Счёт — раз в 10 мин, позиции — раз в 15 с (фон), соединение для заявок — тёплое (GetInfo раз в 20 с),
    флаги шорта — раз в сутки."""
    last_acc = last_warm = last_pos = 0.0
    while True:
        now = time.time()
        try:
            if now - last_acc > 600 or not _acc["id"]:
                await load_account(); last_acc = now
            if now - _acc["short_ts"] > 86400:
                _acc["short_ts"] = now - 86400 + 300     # сбой — повтор через 5 мин; удача ставит время сама
                await load_short_flags()
            if now - last_pos > 5:                 # позиции раздаются руками сразу — сверка должна быть свежей
                await refresh_positions(); last_pos = now
            if now - last_warm > 20:
                t0 = time.perf_counter()
                await _api("UsersService/GetInfo", {})
                _acc["rtt"] = round((time.perf_counter() - t0) * 1000, 1); last_warm = now
            _acc["err"] = ""
        except Exception as e:
            _acc["err"] = f"{type(e).__name__}: {str(e)[:160]}"
            last_warm = last_pos = now          # не долбить API при ошибке
        await asyncio.sleep(1)


# ------------------------------------------------------------------ сигнал -> решение -> заявка
async def _plan(sig, cfg, st, hooks):
    """Всё, кроме самой заявки. -> (rec, side, inst). side=None — не отправлять (причина в rec['why'])."""
    rec = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "id": sig.get("id"), "ticker": sig.get("ticker"),
           "verb": sig.get("verb"), "title": sig.get("title"), "his_price": his_price(sig)}
    tk = sig["ticker"]
    inst = instrument(tk)
    if not inst:
        rec["why"] = f"нет акции {tk} в каталоге Мосбиржи"
        return rec, None, None
    rec["name"] = inst.get("name")
    lot = int(inst.get("lot") or 1)
    # позиция от копирования, сверенная со счётом (кэш позиций — раз в 5 с): вы раздали её руками -> 0,
    # и следующий сигнал по бумаге отрабатывается заново. Только что открытую (<30 с) не сверяем — счёт мог не успеть
    if time.time() - _acc["pos_ts"] > 30:
        await refresh_positions(bg=False)
    shares = _acc["pos"].get(inst.get("uid")) if inst.get("uid") in _acc["pos"] else _acc["pos"].get(inst.get("figi"), 0)
    net = (shares or 0) / lot if _acc["pos_ts"] else None
    pos = int(st["positions"].get(tk, 0) or 0)
    if pos and net is not None and time.time() - st.setdefault("opened", {}).get(tk, 0) > 30 \
            and ((pos > 0 and net < 1) or (pos < 0 and net > -1)):
        st["positions"].pop(tk, None)
        st["opened"].pop(tk, None)
        rec["reconciled"] = f"позиция {pos:+d} уже роздана"
        pos = 0
    elif pos and net is not None and time.time() - st.setdefault("opened", {}).get(tk, 0) > 30:
        # раздали частично — помним только остаток (закрывать будем его)
        pos = min(pos, int(net)) if pos > 0 else max(pos, -int(-net))
        st["positions"][tk] = pos
    own = None if net is None else net - pos
    act, why = decide(sig, cfg, pos, own)
    side = None if not act else ("buy" if act in ("buy", "cover") else "sell")
    rec["side"], rec["action"] = side, act
    if not side:
        rec["why"] = why
        return rec, None, None
    # объём: открытие — свой объём повтора (настройка «лотов на сделку», лимит лотов наблюдателей тут не действует);
    # закрытие — сколько от копирования ещё не роздано
    qty = abs(pos) if act in ("sell", "cover") else max(1, min(int(cfg.get("lots") or 1), 100))
    rec["qty"] = qty
    enabled, _max_lots, max_rub = hooks["exec_settings"]()
    if not _acc["id"]:
        rec["why"] = f"нет открытого счёта …{cfg.get('account_suffix')} у токена"
        return rec, None, None
    if _acc["access"] != "ACCOUNT_ACCESS_LEVEL_FULL_ACCESS":
        rec["why"] = "у токена нет права торговать на этом счёте"
        return rec, None, None
    if act == "short":
        if net is None:
            rec["why"] = "не знаю позиций на счёте — шорт не открываю (могу продать ваши акции)"
            return rec, None, None
        if not _acc["short_ok"]:
            rec["why"] = "нет данных, по каким бумагам разрешён шорт (ещё не загружены)"
            return rec, None, None
        if not _acc["short_ok"].get(tk):
            rec["why"] = f"шорт по {tk} брокер не разрешает"
            return rec, None, None
    if max_rub and act in ("buy", "short"):      # закрытие (sell/cover) лимитом суммы не держим — оно снижает риск
        px = rec["his_price"]
        if not px:                               # цены в сигнале нет — спросить (~30–60 мс)
            t0 = time.perf_counter()
            r = await _api("MarketDataService/GetLastPrices", {"instrumentId": [inst["uid"]]})
            rec["price_ms"] = round((time.perf_counter() - t0) * 1000)
            px = nox_tinvest.q(((r.get("lastPrices") or [{}])[0]).get("price"))
        cost = (px or 0) * lot * qty
        rec["est_cost"] = round(cost, 2)
        if not px:
            rec["why"] = "нет цены инструмента — не знаю сумму"
            return rec, None, None
        if cost > max_rub * 1.02:               # запас 2% на движение цены от его сделки
            rec["why"] = (f"сумма {qty} {_lots_word(qty)} {cost:,.0f} ₽ больше лимита {max_rub:,} ₽ "
                          f"(Настройки → Т-Терминал)").replace(",", " ")
            return rec, None, None
    today = time.strftime("%Y-%m-%d")
    if sum(1 for r in st["log"] if r.get("sent") and str(r.get("at", "")).startswith(today)) >= int(cfg.get("max_day") or 20):
        rec["why"] = "лимит сделок за день"
        return rec, None, None
    if not cfg.get("enabled") or not hooks.get("feat", lambda: True)():
        rec.update(dry=True, why="повтор сделок выключен")
        return rec, None, inst
    if not enabled:
        rec.update(dry=True, why="исполнение выключено в Настройках")
        return rec, None, inst
    return rec, side, inst


async def _on_signal(sig, hooks):
    t_recv = time.time()
    cfg, st = load_cfg(), load_state()
    log = hooks["log"]
    if sig["id"] in st["done"]:
        return
    st["done"][sig["id"]] = time.time()
    lag = (t_recv - sig["t"] / 1000) * 1000
    if not fresh(sig, int(cfg.get("fresh_min") or 2)) or lag > 10000:
        log(f"КОПИ {sig['trader']}: старый сигнал «{sig['title'][:60]}» ({sig.get('time')}) — пропуск")
        save_state(st)
        return
    try:
        rec, side, inst = await _plan(sig, cfg, st, hooks)
        if side:
            body = {"instrumentId": inst["uid"], "quantity": str(int(rec["qty"])), "accountId": _acc["id"],
                    "direction": "ORDER_DIRECTION_BUY" if side == "buy" else "ORDER_DIRECTION_SELL",
                    "orderType": "ORDER_TYPE_MARKET", "orderId": str(uuid.uuid4())}
            t0 = time.perf_counter()
            r = await _api("OrdersService/PostOrder", body, timeout=5.0)
            rec["api_ms"] = round((time.perf_counter() - t0) * 1000)
            rec["total_ms"] = round((time.time() - sig["t"] / 1000) * 1000)
            rec["status"] = r.get("executionReportStatus", "")
            rec["lots_done"] = int(r.get("lotsExecuted", 0) or 0)
            rec["price"] = nox_tinvest.q(r.get("executedOrderPrice")) or nox_tinvest.q(r.get("initialSecurityPrice"))
            rec["amount"] = nox_tinvest.q(r.get("totalOrderAmount"))
            rec["order_id"] = r.get("orderId", "")
            rec["sent"] = rec["status"] not in ("EXECUTION_REPORT_STATUS_REJECTED", "EXECUTION_REPORT_STATUS_CANCELLED")
            if rec["sent"]:
                done = rec["lots_done"] or rec["qty"]
                new = {"buy": done, "short": -done}.get(rec["action"], 0)   # cover / sell -> позиции нет
                if new:
                    st["positions"][sig["ticker"]] = new
                    st.setdefault("opened", {})[sig["ticker"]] = time.time()
                else:
                    st["positions"].pop(sig["ticker"], None)
                    st.setdefault("opened", {}).pop(sig["ticker"], None)
            else:
                rec["why"] = r.get("message") or rec["status"]
            asyncio.get_running_loop().create_task(refresh_positions())
    except Exception as e:
        rec = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "id": sig.get("id"), "ticker": sig.get("ticker"),
               "verb": sig.get("verb"), "title": sig.get("title"), "side": None, "why": f"сбой: {str(e)[:160]}"}
    rec["detect_ms"] = round(lag)
    st["log"].append(rec)
    save_state(st)
    if rec.get("sent"):
        hp, px = rec.get("his_price"), rec.get("price")
        slip = f", разница с ним {((px - hp) / hp * 100):+.2f}%" if hp and px else ""
        log(f"КОПИ {sig['trader']} {sig['verb']} {sig['ticker']}: {say_action(rec['action'], rec['qty']).upper()} "
            f"по {px} (его цена {hp}{slip}) — сигнал на странице -> ответ биржи {rec['total_ms']} мс "
            f"(до Нокса {rec['detect_ms']} мс, заявка {rec['api_ms']} мс), {rec['status']}")
    else:
        log(f"КОПИ {sig['trader']} {sig['verb']} {sig['ticker']}: {'не отправил' if rec.get('dry') else 'пропуск'} — "
            f"{rec.get('why')} (до Нокса {rec['detect_ms']} мс)")
    try:
        hooks["after"](sig, rec)
    except Exception:
        pass


# ------------------------------------------------------------------ CDP-канал (только события страницы)
def _page_ws():
    tabs = json.load(urllib.request.urlopen(CDP_URL + "/json", timeout=5))
    t = next((x for x in tabs if x.get("type") == "page" and "tbank.ru/terminal" in (x.get("url") or "")), None)
    return t and t.get("webSocketDebuggerUrl")


_live = {"loop": None, "status": "не запущено", "page": {}}


async def _session(hooks):
    import websockets
    ws_url = _page_ws()
    if not ws_url:
        raise RuntimeError("вкладка терминала не найдена")
    keeper = asyncio.get_running_loop().create_task(_keeper())
    try:
        async with websockets.connect(ws_url, max_size=50_000_000, open_timeout=10, ping_interval=20) as ws:
            n, pending, events = 0, {}, asyncio.Queue()

            async def pump():
                async for raw in ws:
                    m = json.loads(raw)
                    if "id" in m and m["id"] in pending:
                        pending.pop(m["id"]).set_result(m)
                    elif m.get("method") == "Runtime.bindingCalled" and m["params"].get("name") == "noxCopySig2":
                        await events.put(m["params"]["payload"])

            async def cdp(method, params=None):
                nonlocal n
                n += 1
                f = asyncio.get_running_loop().create_future()
                pending[n] = f
                await ws.send(json.dumps({"id": n, "method": method, "params": params or {}}))
                return (await asyncio.wait_for(f, 10)).get("result", {})

            pt = asyncio.get_running_loop().create_task(pump())
            await cdp("Runtime.addBinding", {"name": "noxCopySig2"})
            _live["status"] = "подключено"
            last = 0.0
            while not pt.done():
                if not hooks.get("feat", lambda: True)():
                    _live["status"] = "выключено тумблером «Повтор сделок»"
                    break                            # тумблер выключен — сигналы больше не слушаем
                if time.time() - last > 3:           # переустановка наблюдателя после перезагрузки страницы
                    last = time.time()
                    r = await cdp("Runtime.evaluate", {"expression": f"({PAGE_JS})({json.dumps(load_cfg()['trader'])})",
                                                       "returnByValue": True})
                    page = (r.get("result") or {}).get("value") or {}
                    _live["page"] = {k: v for k, v in page.items() if k != "q"}
                    for o in page.get("q") or []:
                        await events.put(json.dumps(o))
                try:
                    payload = await asyncio.wait_for(events.get(), 0.5)
                except asyncio.TimeoutError:
                    continue
                try:
                    await _on_signal(json.loads(payload), hooks)
                except Exception as e:
                    hooks["log"](f"КОПИ: сбой — {type(e).__name__}: {e}")
    finally:
        keeper.cancel()


def run(hooks):
    """hooks: log(text), exec_settings() -> (enabled, max_lots, max_rub), after(sig, rec), feat() -> bool."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _live["loop"] = loop
    while True:
        try:
            if hooks.get("feat", lambda: True)():
                loop.run_until_complete(_session(hooks))
            _live["status"] = "выключено во вкладке «Фичи»" if not hooks.get("feat", lambda: True)() else "переподключение"
        except Exception as e:
            _live["status"] = f"ошибка: {type(e).__name__}: {str(e)[:100]}"
        time.sleep(3)


def call(coro, timeout=20):
    """Выполнить корутину в цикле копирования (для эндпоинтов ридера)."""
    loop = _live["loop"]
    if not loop:
        raise RuntimeError("копирование не запущено: " + _live["status"])
    return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)


def status():
    st = load_state()
    return {"cfg": load_cfg(), "status": _live["status"], "page": _live["page"],
            "account": {"id": ("…" + _acc["id"][-4:]) if _acc["id"] else "", "name": _acc["name"], "access": _acc["access"],
                        "rtt_ms": _acc["rtt"], "err": _acc["err"],
                        "short_allowed": sum(1 for v in _acc["short_ok"].values() if v), "shares": len(_acc["short_ok"])},
            "positions": st["positions"], "log": st["log"][-15:]}


async def dry_plan(ticker, verb, exec_settings):
    """Проверка без сделки: решение, счёт, инструмент, цена/сумма — всё, что перед заявкой. PostOrder НЕ вызывается."""
    sig = {"id": "test", "t": time.time() * 1000, "trader": load_cfg()["trader"], "verb": verb,
           "ticker": ticker.upper(), "title": f"проверка: {verb} {ticker.upper()}", "short": ""}
    cfg = {**load_cfg(), "enabled": False}          # проверка никогда не отправляет заявку
    t0 = time.perf_counter()
    rec, side, inst = await _plan(sig, cfg, load_state(), {"exec_settings": exec_settings})
    rec["plan_ms"] = round((time.perf_counter() - t0) * 1000)
    rec.pop("id", None)
    return rec
