# -*- coding: utf-8 -*-
"""
Виджеты «Заявка» T-терминала для наблюдателей Nox: найти, прочитать, разложить, подписать и нажать.

Наблюдатель = вкладка терминала. На ней виджеты «Заявка» (заполненные заранее — вами или Ноксом).
Ветка наблюдателя = список (тикер, №, сторона): «NVTK №1 → Купить», «GAZP №1 → Продать».
№ — порядковый номер виджета с этим тикером на вкладке (слева направо, сверху вниз).

Все функции принимают playwright-страницу терминала и вызываются в потоке CDP-воркера terminal_reader.
"""
import time
from collections import Counter

# ------------------------------------------------------------------ чтение виджетов
# Корень виджета — [class*=WidgetBody-widgetBody]; первая строка его текста — тикер из шапки.
LIST_JS = r"""() => {
  const out = [];
  // вкладка заявки (Market / Limit / …) хранится в настройках виджета терминала
  const otabs = {};
  try {
    for (const s of Object.values(window.store.getState().settings.spaces))
      for (const [id, c] of Object.entries((s.widgets || {}).custom || {})) {
        const t = ((c || {}).combinedOrderSettings || {}).selectedOrderTab; if (t) otabs[id] = t;
      }
  } catch (e) {}
  for (const root of document.querySelectorAll('[class*="WidgetBody-widgetBody"]')) {
    const r = root.getBoundingClientRect(); if (r.width <= 0 || r.height <= 0) continue;
    const txt = root.innerText || '';
    if (!/Рыночная/.test(txt) || !/Лимитная/.test(txt)) continue;
    const btns = [...root.querySelectorAll('button,[role=button]')];
    const buy = btns.find(e => (e.innerText || '').trim() === 'Купить');
    const sell = btns.find(e => (e.innerText || '').trim() === 'Продать');
    if (!buy || !sell) continue;
    const ticker = (txt.split('\n')[0] || '').trim().toUpperCase();
    const inps = [...root.querySelectorAll('input')].filter(e => e.getBoundingClientRect().width > 0 && /InlineInput-inp/i.test(e.className || ''));
    inps.sort((a, b) => a.getBoundingClientRect().x - b.getBoundingClientRect().x);
    const price = inps.length >= 2 ? inps[0].value : '';
    const qty = inps.length ? inps[inps.length - 1].value : '';
    const m = txt.match(/(?:Примерная стоимость|Стоимость)\s*([\d\s ]+(?:,\d+)?)\s*₽/);
    const cost = m ? parseFloat(m[1].replace(/[\s ]/g, '').replace(',', '.')) : null;
    const c = e => { const b = e.getBoundingClientRect();
      return {x: Math.round(b.x + b.width / 2), y: Math.round(b.y + b.height / 2),
              dis: !!(e.disabled || e.getAttribute('aria-disabled') === 'true')}; };
    const box = root.closest('[data-widget-id]');
    const id = box ? box.getAttribute('data-widget-id') : '';
    out.push({ticker, x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
              type: price ? 'limit' : 'market', price, qty, cost, buy: c(buy), sell: c(sell), id, otab: otabs[id] || ''});
  }
  out.sort((a, b) => (a.x - b.x) || (a.y - b.y));
  const cnt = {};
  for (const w of out) { cnt[w.ticker] = (cnt[w.ticker] || 0) + 1; w.n = cnt[w.ticker]; }
  return out;
}"""

ACTIVE_TAB_JS = r"""() => ((document.querySelector('.pro-editable-tab-selected .pro-editable-tab-title') || {}).innerText || '').trim()"""

TAB_POS_JS = r"""(name) => {
  const t = [...document.querySelectorAll('.pro-editable-tab-title')].find(e => (e.innerText || '').trim() === name);
  if (!t) return null; const b = t.getBoundingClientRect();
  return [Math.round(b.x + b.width / 2), Math.round(b.y + b.height / 2)];
}"""

# не даём ни одному «служебному» клику (вкладки, поиск, меню) попасть в кнопку исполнения
GUARD_JS = r"""([x, y]) => {
  let e = document.elementFromPoint(x, y);
  for (let i = 0; e && i < 8; i++, e = e.parentElement) {
    const t = (e.innerText || '').trim();
    if ((e.tagName === 'BUTTON' || e.getAttribute('role') === 'button') && /^(Купить|Продать|Подтвердить)/.test(t)) return t;
  }
  return null;
}"""

def _num(s):
    try:
        return float(str(s).replace(" ", "").replace(" ", "").replace(",", "."))
    except Exception:
        return 0.0

def widgets(pg):
    return pg.evaluate(LIST_JS) or []

def pick(ws, ticker, n=1, lots=None, wid=None):
    """Виджет «Заявка» по тикеру. wid задан и виджет на месте — ИМЕННО он, с любым объёмом и типом (16.10: объём,
    рыночная/лимитная и цена управляются в самом виджете). Иначе: lots задано — виджет с РОВНО таким количеством
    (номер по порядку на вкладке нестабилен: при разных объёмах одного тикера «№» менялся между проходами — 55 ₽
    нажали бы 2 лота вместо 3); несколько таких — n-й из них; нет такого — None (ничего не нажимать). Без lots — по №."""
    if wid:
        w = next((x for x in ws if x.get("id") == wid), None)
        if w:
            return w
    ticker = (ticker or "").upper().strip()
    if lots:
        same = [w for w in ws if w["ticker"] == ticker and int(_num(w.get("qty")) or 0) == int(lots)]
        if len(same) == 1:
            return same[0]
        if len(same) > 1:
            return next((w for w in same if int(w.get("n", 1)) == int(n or 1)), same[0])
        return None
    for w in ws:
        if w["ticker"] == ticker and int(w.get("n", 1)) == int(n or 1):
            return w
    return None

def describe(w):
    """«NVTK №1 · лимит 1063,9 × 10 лот (10 639 ₽)»"""
    px = f"лимит {w['price']}" if w.get("type") == "limit" else "по рынку"
    cost = f" ({w['cost']:,.0f} ₽)".replace(",", " ") if w.get("cost") else ""
    q = int(_num(w.get("qty")))
    lots = ("лот" if q % 10 == 1 and q % 100 != 11 else
            "лота" if 2 <= q % 10 <= 4 and not 12 <= q % 100 <= 14 else "лотов")
    return f"{w['ticker']} №{w.get('n', 1)} · {px} × {w.get('qty') or '?'} {lots}{cost}"

def active_tab(pg):
    return pg.evaluate(ACTIVE_TAB_JS) or ""

def _safe_click(pg, x, y):
    if pg.evaluate(GUARD_JS, [x, y]):
        raise RuntimeError("служебный клик попал в кнопку исполнения — отменён")
    pg.mouse.click(x, y)

def ensure_tab(pg, tab, wait_ms=700):
    """Открыть вкладку терминала по имени (если не открыта). True — открыта."""
    if not tab or active_tab(pg) == tab:
        return bool(tab)
    pos = pg.evaluate(TAB_POS_JS, tab)
    if not pos:
        return False
    _safe_click(pg, pos[0], pos[1])
    for _ in range(30):
        pg.wait_for_timeout(100)
        if active_tab(pg) == tab:
            pg.wait_for_timeout(wait_ms)   # дать виджетам отрисоваться
            return True
    return False

# ------------------------------------------------------------------ нажатие кнопок ветки
CONFIRM_JS = r"""(btnText) => {
  const dlg = [...document.querySelectorAll('[role=dialog],[role=alertdialog],[class*=Modal],[class*=modal]')]
    .filter(d => d.getBoundingClientRect().width > 0);
  for (const d of dlg) {
    const b = [...d.querySelectorAll('button,[role=button]')].find(e => /^(Подтвердить|Отправить|Выставить|Да)/.test((e.innerText || '').trim())
          || (e.innerText || '').trim().startsWith(btnText));
    if (b) { const r = b.getBoundingClientRect(); return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)}; }
  }
  return null;
}"""

NOTES_JS = r"""() => {
  const rx = /заявк|исполнен|выставлен|отклон|ошибк|недостаточно|не удалось|запрещ|недоступ|лимит|маржин/i;
  const out = [];
  for (const e of document.querySelectorAll('[role=alert],[role=status],[class*=otification],[class*=oast],[class*=nackbar]')) {
    const t = (e.innerText || '').replace(/\s+/g, ' ').trim();
    if (t && t.length < 300 && rx.test(t) && !out.includes(t)) out.push(t);
  }
  // вложенные элементы одного уведомления дают куски одного текста — оставляем только самый полный
  return out.filter(t => !out.some(o => o !== t && o.includes(t))).slice(0, 6);
}"""

def check_order(w, side, max_lots, max_rub):
    """None — можно жать; иначе причина отказа."""
    lots = int(_num(w.get("qty")))
    if lots <= 0:
        return "в виджете не указано количество"
    if max_lots and lots > max_lots:
        return f"{lots} лот больше лимита {max_lots}"
    if max_rub:
        if not w.get("cost"):
            return "не вижу стоимость заявки в виджете"
        if w["cost"] > max_rub:
            return f"сумма {w['cost']:,.0f} ₽ больше лимита {max_rub:,} ₽".replace(",", " ")
    btn = w["buy"] if side == "buy" else w["sell"]
    if btn.get("dis"):
        return "кнопка неактивна"
    return None

def press_orders(pg, tab, orders, max_lots=0, max_rub=0, dry=False):
    """Нажать кнопки ветки. orders: [{ticker, n, side}]. Ошибка одной заявки не останавливает остальные.
    dry=True — только проверить (ничего не нажимать). Возвращает [{order, ok, err?, widget?}]."""
    res = []
    if not ensure_tab(pg, tab):
        return [{"order": o, "ok": False, "err": f"нет вкладки «{tab}»"} for o in orders]
    ws = widgets(pg)
    for o in orders:
        side = o.get("side", "buy")
        w = pick(ws, o.get("ticker"), o.get("n", 1), o.get("lots"), o.get("wid"))
        if not w:
            what = f"на {o['lots']} лот." if o.get("lots") else f"№{o.get('n', 1)}"
            res.append({"order": o, "ok": False, "err": f"виджет {o.get('ticker')} {what} не найден — не нажимаю"})
            continue
        why = check_order(w, side, max_lots, max_rub)
        if why:
            res.append({"order": o, "ok": False, "err": why, "widget": describe(w)})
            continue
        if dry:
            res.append({"order": o, "ok": True, "dry": True, "widget": describe(w)})
            continue
        btn = w["buy"] if side == "buy" else w["sell"]
        try:
            pg.mouse.click(btn["x"], btn["y"])
            t_click = time.time()
            pg.wait_for_timeout(250)
            cf = pg.evaluate(CONFIRM_JS, "Купить" if side == "buy" else "Продать")
            if cf:
                pg.mouse.click(cf["x"], cf["y"])
                pg.wait_for_timeout(200)
            res.append({"order": o, "ok": True, "widget": describe(w), "confirmed": bool(cf), "t_click": t_click})
        except Exception as e:
            res.append({"order": o, "ok": False, "err": f"сбой нажатия: {e}", "widget": describe(w)})
    if not dry and any(r.get("ok") for r in res):
        pg.wait_for_timeout(700)
        try:
            notes = pg.evaluate(NOTES_JS) or []
        except Exception:
            notes = []
        if notes and res:
            res[-1]["notes"] = notes
    return res

# ------------------------------------------------------------------ заявки через API: пульт — виджет «Заявка»
ACCOUNT_JS = r"""() => { try { return String(window.store.getState().settings.nonShared.accountId || ''); } catch (e) { return ''; } }"""


def api_plan(pg, tab, orders, max_lots=0, max_rub=0):
    """Заявки наблюдателя через T-Invest API (16.10): кнопку НЕ нажимаем, а берём из виджета «Заявка» его тип
    (рыночная / лимитная), лимитную цену и количество — их можно в любой момент поменять в терминале.
    Счёт — выбранный в терминале. -> {"account": id, "plans": [{order, ok, err?, widget, wid, ticker, side,
    otype, price, qty, cost, bind?}]} (bind — id найденного по тикеру+объёму виджета: запомнить в заявке)."""
    if not ensure_tab(pg, tab):
        return {"account": "", "plans": [{"order": o, "ok": False, "err": f"нет вкладки «{tab}»"} for o in orders]}
    ws = widgets(pg)
    try:
        acct = pg.evaluate(ACCOUNT_JS) or ""
    except Exception:
        acct = ""
    plans = []
    for o in orders:
        side = o.get("side", "buy")
        w = pick(ws, o.get("ticker"), o.get("n", 1), o.get("lots"), o.get("wid"))
        if not w:
            plans.append({"order": o, "ok": False, "err": f"виджет {o.get('ticker')} не найден — не отправляю"})
            continue
        otab = w.get("otab") or ("Limit" if w.get("price") else "Market")
        qty = int(_num(w.get("qty")))
        price = _num(w.get("price")) if otab == "Limit" else None
        p = {"order": o, "widget": describe(w), "wid": w.get("id"), "ticker": w["ticker"], "side": side,
             "otype": "limit" if otab == "Limit" else "market", "price": price, "qty": qty, "cost": w.get("cost")}
        if w.get("id") and o.get("wid") != w.get("id"):
            p["bind"] = w["id"]
        err = None
        if otab not in ("Market", "Limit"):
            err = f"в виджете вкладка «{otab}» — через API отправляю только рыночную и лимитную"
        elif qty <= 0:
            err = "в виджете не указано количество"
        elif max_lots and qty > max_lots:
            err = f"{qty} лот больше лимита {max_lots}"
        elif otab == "Limit" and not price:
            err = "лимитная заявка без цены"
        elif max_rub and not w.get("cost"):
            err = "не вижу стоимость заявки в виджете"
        elif max_rub and w["cost"] > max_rub:
            err = f"сумма {w['cost']:,.0f} ₽ больше лимита {max_rub:,} ₽".replace(",", " ")
        p["ok"] = not err
        if err:
            p["err"] = err
        plans.append(p)
    return {"account": acct, "plans": plans}


def bind_plan(pg, watchers):
    """Какие заявки наблюдателей открытой вкладки ещё не привязаны к своему виджету (по id): [(id наблюдателя,
    № ветки, № заявки, id виджета)]. Привязка — по тикеру и объёму, пока их не меняли; дальше виджет узнаётся по id."""
    ws = widgets(pg)
    ids = {x.get("id") for x in ws if x.get("id")}
    out, taken = [], set()
    for w in watchers or []:
        for bi, b in enumerate(w.get("branches") or []):
            for oi, o in enumerate(b.get("orders") or []):
                if o.get("venue") == "cscalp" or not o.get("ticker"):
                    continue
                if o.get("wid") in ids:
                    taken.add(o["wid"])
                    continue
                x = pick(ws, o["ticker"], o.get("n", 1), o.get("lots"))
                if x and x.get("id") and x["id"] not in taken:
                    taken.add(x["id"])
                    out.append((w.get("id"), bi, oi, x["id"]))
    return out


# ------------------------------------------------------------------ подписи веток поверх терминала
OVERLAY_JS = r"""([banner, marks]) => {
  document.getElementById('nox-watch')?.remove();
  if (!marks) return 0;
  const LIST = """ + LIST_JS + r""";
  const ws = LIST();
  const root = document.createElement('div');
  root.id = 'nox-watch';
  root.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:99999;font-family:Segoe UI,Arial,sans-serif;';
  document.body.appendChild(root);
  const el = (css, html) => { const d = document.createElement('div'); d.style.cssText = 'position:fixed;' + css; d.innerHTML = html || ''; root.appendChild(d); return d; };
  if (banner && ws.length) {
    const x0 = Math.min(...ws.map(w => w.x)), y0 = Math.min(...ws.map(w => w.y));
    el(`left:${x0}px;top:${Math.max(98, y0 - 30)}px;padding:4px 12px;border-radius:8px;background:rgba(53,224,208,.16);
        border:1px solid #35e0d0;color:#eafeff;font-size:13px;font-weight:600;white-space:nowrap;`, banner);
  }
  // объём задан — виджет с ровно таким количеством (порядковый № нестабилен), иначе — по №
  const q = v => Math.round(+String(v || '').replace(/[\s ]/g, '').replace(',', '.'));
  const hits = marks.map(m => [m, ws.find(w => w.ticker === m.ticker && (m.lots ? q(w.qty) === m.lots : w.n === m.n))]).filter(p => p[1]);
  const sells = new Set(hits.filter(([m]) => m.side === 'sell').map(([m, w]) => w.x + ':' + w.y));
  const cols = {};
  for (const [m, w] of hits) {
    const b = m.side === 'buy' ? w.buy : w.sell, key = w.x + ':' + w.y + m.side;
    if (!cols[key]) {
      el(`left:${b.x - 66}px;top:${b.y - 20}px;width:132px;height:40px;border:3px solid ${m.color};border-radius:9px;box-shadow:0 0 14px ${m.color};box-sizing:border-box;`);
      // подписи кнопки — столбиком; у «Купить» при подписанной «Продать» — не шире промежутка, чтобы не наезжали
      const lim = m.side === 'buy' && sells.has(w.x + ':' + w.y) ? `max-width:${w.sell.x - w.buy.x - 8}px;` : 'white-space:nowrap;';
      cols[key] = el(`left:${b.x - 66}px;top:${b.y + 22}px;display:flex;flex-direction:column;align-items:flex-start;gap:2px;${lim}`);
    }
    const d = document.createElement('div');
    d.style.cssText = `padding:2px 7px;border-radius:6px;background:${m.color};color:#fff;font-size:11px;font-weight:700;line-height:14px;`;
    d.textContent = m.label; cols[key].appendChild(d);
  }
  return hits.length;
}"""

BRANCH_COLORS = ["#22c55e", "#ef4444", "#f59e0b", "#3b82f6", "#a855f7", "#14b8a6"]

def branch_label(b):
    t, mn, mx = b.get("type"), b.get("min"), b.get("max")
    f = lambda v: ("%g" % v) if isinstance(v, (int, float)) else str(v)
    if t == "words":   # ветка по словам: «не пострадал…», «удар…»
        ws = b.get("words") or ["?"]
        return f"«{ws[0]}{'…' if len(ws) > 1 else ''}»"
    return {"none": "нет выплаты", "gt": f"> {f(mn)}", "ge": f"≥ {f(mn)}", "lt": f"< {f(mx)}",
            "le": f"≤ {f(mx)}", "range": f"{f(mn)}…{f(mx)}", "any": "любое"}.get(t, t or "?")

def overlay_marks(w, color_shift=0):
    marks = []
    branches = w.get("branches") or []
    word = branches and all(b.get("type") == "any" for b in branches)   # словесный наблюдатель
    for i, b in enumerate(branches):
        color = BRANCH_COLORS[(i + color_shift) % len(BRANCH_COLORS)]
        if word:   # покупка — зелёная, продажа — красная
            color = None
        for o in b.get("orders") or []:
            if o.get("venue") == "cscalp":   # стакан CScalp, не виджет терминала
                continue
            act = "КУПИТЬ" if o.get("side") == "buy" else "ПРОДАТЬ"
            c = color or ("#22c55e" if o.get("side") == "buy" else "#ef4444")
            label = (f"«{' + '.join(w.get('keywords') or [])}» → {act}" if word
                     else f"Ветка {i + 1} ({branch_label(b)}): {act}")
            if not w.get("enabled", True):
                label += " (выкл)"
            marks.append({"ticker": (o.get("ticker") or "").upper(), "n": int(o.get("n", 1)), "side": o.get("side", "buy"),
                          "lots": int(o["lots"]) if str(o.get("lots") or "").isdigit() else None,
                          "color": c, "label": label})
    return marks

def draw_overlay(pg, ws):
    """Подписи кнопок для ВСЕХ наблюдателей вкладки (ws — наблюдатель или список)."""
    ws = ws if isinstance(ws, list) else [ws]
    marks, names = [], []
    for k, w in enumerate(ws):
        marks += overlay_marks(w, color_shift=k * 3)
        names.append(f"«{w.get('name', '')}»" + ("" if w.get("enabled", True) else " (выкл)"))
    on = any(w.get("enabled", True) for w in ws)
    banner = ("НАБЛЮДАТЕЛИ " if len(ws) > 1 else "НАБЛЮДАТЕЛЬ ") + ", ".join(names) + \
             (" — ждут новость" if on else " — ВЫКЛЮЧЕНЫ, ничего не нажмут")
    return pg.evaluate(OVERLAY_JS, [banner, marks])

def clear_overlay(pg):
    pg.evaluate("() => { document.getElementById('nox-watch')?.remove(); return 1 }")

# ------------------------------------------------------------------ утренняя раскладка (Нокс сам создаёт виджеты)
def _leaf_pos(pg, text, near=None, within=None):
    """Центр видимого «листового» элемента с точным текстом. near=[x,y] — ближайший; within=[x,y,w,h] — внутри."""
    return pg.evaluate(r"""([txt, near, within]) => {
      let els = [...document.querySelectorAll('*')].filter(e => {
        if ((e.innerText || '').trim() !== txt) return false;
        const b = e.getBoundingClientRect(); if (b.width <= 0 || b.height <= 0) return false;
        if (within && (b.x < within[0] || b.y < within[1] || b.x > within[0] + within[2] || b.y > within[1] + within[3])) return false;
        return ![...e.children].some(c => (c.innerText || '').trim() === txt); });
      if (!els.length) return null;
      const ctr = e => { const b = e.getBoundingClientRect(); return [b.x + b.width / 2, b.y + b.height / 2]; };
      if (near) els.sort((a, b) => { const A = ctr(a), B = ctr(b);
        return Math.hypot(A[0] - near[0], A[1] - near[1]) - Math.hypot(B[0] - near[0], B[1] - near[1]); });
      const c = ctr(els[0]); return [Math.round(c[0]), Math.round(c[1])];
    }""", [text, near, within])

def create_tab(pg, name):
    """Создать пустую вкладку терминала с именем name (если её ещё нет) и открыть её."""
    if pg.evaluate(TAB_POS_JS, name):
        return ensure_tab(pg, name)
    prev = active_tab(pg)
    plus = pg.evaluate(r"""() => {
      const tabs = [...document.querySelectorAll('.pro-editable-tab')]; if (!tabs.length) return null;
      const last = tabs[tabs.length - 1].getBoundingClientRect();
      const c = [...document.querySelectorAll('svg')].map(s => s.getBoundingClientRect())
        .filter(b => b.width > 0 && b.x > last.right - 2 && b.x < last.right + 80 && Math.abs((b.y + b.height / 2) - (last.y + last.height / 2)) < 14);
      if (!c.length) return null; c.sort((a, b) => a.x - b.x);
      return [Math.round(c[0].x + c[0].width / 2), Math.round(c[0].y + c[0].height / 2)];
    }""")
    if not plus:
        return False
    _safe_click(pg, *plus); pg.wait_for_timeout(900)
    empty = _leaf_pos(pg, "Пустая вкладка")
    if not empty:
        return False
    _safe_click(pg, *empty); pg.wait_for_timeout(1200)
    pos = pg.evaluate(r"""() => { const t = [...document.querySelectorAll('.pro-editable-tab-title')].pop();
      const b = t.getBoundingClientRect(); return [Math.round(b.x + b.width / 2), Math.round(b.y + b.height / 2)]; }""")
    pg.mouse.dblclick(pos[0], pos[1]); pg.wait_for_timeout(400)
    pg.keyboard.press("Control+a"); pg.keyboard.type(name, delay=25); pg.keyboard.press("Enter")
    pg.wait_for_timeout(1500)
    # Сбой терминала: после создания вкладки его «сохранятель» ждёт сигнал «вкладка захвачена» и может
    # его пропустить — тогда до перезагрузки страницы НИЧЕГО не сохраняется (виджеты пропадают при F5).
    # Переход на другую вкладку и обратно присылает сигнал заново; новая вкладка пуста — терять нечего.
    if prev and prev != name and ensure_tab(pg, prev, wait_ms=900):
        ensure_tab(pg, name, wait_ms=900)
    return active_tab(pg) == name

# пустые заготовки «Заявка» (Выберите инструмент), которые пользователь расставил заранее: сверху вниз, слева направо
EMPTY_JS = r"""() => [...document.querySelectorAll('[class*="WidgetBody-widgetBody"]')]
  .filter(e => /Выберите инструмент/.test(e.innerText || '') && e.getBoundingClientRect().width > 0)
  .map(e => { const b = e.getBoundingClientRect(); return {x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height)}; })
  .sort((a, b) => (a.y - b.y) || (a.x - b.x))"""

def add_order_widget(pg, ticker, side=None, lots=None, price=None):
    """Заполнить на открытой вкладке виджет «Заявка»: сначала ПУСТУЮ ЗАГОТОВКУ пользователя (верхний ряд
    первым), а если их нет — добавить новый виджет через «Виджеты → Заявка». Кнопки НЕ нажимаются."""
    before = {(w["x"], w["y"]) for w in widgets(pg)}
    empties = pg.evaluate(EMPTY_JS) or []
    reused = bool(empties)
    if reused:
        e = empties[0]
        head = _leaf_pos(pg, "Заявка", within=[e["x"], e["y"], e["w"], 40])
        if not head:
            return {"ok": False, "err": "не нашёл поиск в пустом виджете"}
    else:
        btn = _leaf_pos(pg, "Виджеты")
        if not btn:
            return {"ok": False, "err": "не нашёл кнопку «Виджеты»"}
        _safe_click(pg, *btn); pg.wait_for_timeout(800)
        item = _leaf_pos(pg, "Заявка", near=btn)
        if not item:
            return {"ok": False, "err": "в меню виджетов нет «Заявка»"}
        _safe_click(pg, *item); pg.wait_for_timeout(1300)
        # новый пустой виджет: шапка с текстом «Заявка» -> поиск инструмента
        head = _leaf_pos(pg, "Заявка", near=[pg.viewport_size["width"] / 2 if pg.viewport_size else 900, 300])
        if not head:
            return {"ok": False, "err": "новый виджет не появился"}
    _safe_click(pg, *head); pg.wait_for_timeout(600)
    pg.keyboard.type(ticker, delay=40); pg.wait_for_timeout(1500)
    pg.keyboard.press("Enter"); pg.wait_for_timeout(1600)
    new = [w for w in widgets(pg) if (w["x"], w["y"]) not in before and w["ticker"] == ticker.upper()]
    if not new:
        return {"ok": False, "err": f"не выбрался инструмент {ticker}"}
    w = new[0]
    box = [w["x"], w["y"], w["w"], w["h"]]
    # тип и значения
    tab = "Лимитная" if price else "Рыночная"
    tp = _leaf_pos(pg, tab, within=box)
    if tp:
        _safe_click(pg, *tp); pg.wait_for_timeout(500)
    def _type_into(idx, val):
        ok = pg.evaluate(r"""([box, idx]) => {
          const root = [...document.querySelectorAll('[class*="WidgetBody-widgetBody"]')].find(e => { const b = e.getBoundingClientRect();
            return Math.abs(b.x - box[0]) < 3 && Math.abs(b.y - box[1]) < 3; });
          if (!root) return false;
          const inps = [...root.querySelectorAll('input')].filter(e => e.getBoundingClientRect().width > 0 && /InlineInput-inp/i.test(e.className || ''));
          inps.sort((a, b) => a.getBoundingClientRect().x - b.getBoundingClientRect().x);
          const e = idx < 0 ? inps[inps.length - 1] : inps[idx]; if (!e) return false;
          e.focus(); e.select(); return true; }""", [box, idx])
        if ok:
            pg.keyboard.type(str(val).replace(",", "."), delay=30)
        return ok
    if price:
        _type_into(0, price)
    if lots:
        _type_into(-1, int(lots))
    pg.keyboard.press("Tab"); pg.wait_for_timeout(400)
    if reused:
        return {"ok": True, "reused": True}      # заготовка уже стоит на своём месте
    _to_free_spot(pg, w)   # убрать с места появления; в ряд по веткам выстроит arrange_rows
    return {"ok": True}

ROOTS_JS = r"""() => [...document.querySelectorAll('[class*="WidgetBody-widgetBody"]')].map(e => { const b = e.getBoundingClientRect();
  return {x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height),
          news: !!e.querySelector('.news_row')}; }).filter(r => r.w > 0)"""

WRAP_JS = r"""() => { const e = document.querySelector('[class*="widgetsWrap"]'); if (!e) return null; const b = e.getBoundingClientRect();
  return {x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height)}; }"""

# ручка перетаскивания — «draggableArea» в шапке виджета (курсор move); null — её перекрыл другой виджет
HANDLE_JS = r"""([x, y]) => {
  const root = [...document.querySelectorAll('[class*="WidgetBody-widgetBody"]')].find(e => { const b = e.getBoundingClientRect();
    return Math.abs(b.x - x) < 3 && Math.abs(b.y - y) < 3; });
  const h = root && root.querySelector('[class*="draggableArea"]'); if (!h) return null;
  const b = h.getBoundingClientRect(); if (b.width < 8) return null;
  for (const f of [0.5, 0.3, 0.7, 0.15, 0.85]) {
    const px = b.x + b.width * f, py = b.y + b.height / 2, e = document.elementFromPoint(px, py);
    if (e && h.contains(e)) return [Math.round(px), Math.round(py)];
  }
  return null;
}"""

def _drag(pg, r, tx, ty):
    """Перетащить виджет (прямоугольник r) за ручку шапки: левый верхний угол -> (tx, ty). Кнопки не задеваются."""
    hp = pg.evaluate(HANDLE_JS, [r["x"], r["y"]])
    if not hp or pg.evaluate(GUARD_JS, hp) is not None:
        return False
    dx, dy = tx - r["x"], ty - r["y"]
    pg.mouse.move(*hp); pg.mouse.down()
    for k in range(1, 21):
        pg.mouse.move(hp[0] + dx * k / 20, hp[1] + dy * k / 20)
        pg.wait_for_timeout(12)
    pg.mouse.up(); pg.wait_for_timeout(250)
    return True

# ------------------------------------------------------------------ ровный ряд: виджеты наблюдателя по порядку веток
GAP = 2   # зазор между виджетами ряда (как ставит пользователь: вплотную)

def _overlap(a, b, tol=4):
    return (a["x"] < b["x"] + b["w"] - tol and b["x"] < a["x"] + a["w"] - tol and
            a["y"] < b["y"] + b["h"] - tol and b["y"] < a["y"] + a["h"] - tol)

def _row_widgets(pg, w):
    """Виджеты наблюдателя в порядке веток, без повторов. None — есть заявка без объёма: её виджет выбирается
    по № (порядку на вкладке), и перестановка поменяла бы, какую кнопку нажмёт ветка."""
    ws, out = widgets(pg), []
    for b in w.get("branches") or []:
        for o in b.get("orders") or []:
            if o.get("venue") == "cscalp" or not o.get("ticker"):
                continue
            if not str(o.get("lots") or "").strip().isdigit():
                return None
            x = pick(ws, o["ticker"], o.get("n", 1), o.get("lots"))
            if x and not any(x is y for y in out):
                out.append(x)
    return out

def _free_spot(W, busy, ww, hh, x_first=None):
    """Левый верхний угол свободного места ww×hh: сначала «этажи» в колонке x_first (справа от ленты),
    потом любые места сверху вниз, слева направо. Стыкуется к краям уже стоящих виджетов."""
    ys = sorted({W["y"]} | {o["y"] + o["h"] + GAP for o in busy})
    xs = sorted({W["x"]} | {o["x"] + o["w"] + GAP for o in busy})
    fits = lambda x, y, full: (x + ww <= W["x"] + W["w"] and (y + hh if full else y + 40) <= W["y"] + W["h"] and
                               not any(_overlap({"x": x, "y": y, "w": ww, "h": hh}, o) for o in busy))
    for full in (True, False):
        if x_first is not None:
            for y in ys:
                if fits(x_first, y, full):
                    return x_first, y
        for y in ys:
            for x in xs:
                if fits(x, y, full):
                    return x, y
    return None

def _to_free_spot(pg, r):
    """Новый виджет — на первое свободное место (вплотную к остальным, без наложений)."""
    W = pg.evaluate(WRAP_JS)
    busy = [o for o in pg.evaluate(ROOTS_JS) if (o["x"], o["y"]) != (r["x"], r["y"])]
    spot = W and _free_spot(W, busy, r["w"], r["h"])
    if spot and (abs(spot[0] - r["x"]) > 4 or abs(spot[1] - r["y"]) > 4):
        _drag(pg, r, *spot)

def arrange_rows(pg, ws):
    """Виджеты наблюдателей вкладки — ровными рядами: у каждого наблюдателя свой ряд слева направо в порядке
    веток (ветка 1 — левый), ряды друг под другом справа от ленты новостей, без наложений. ws — наблюдатель
    или список (в порядке списка). Виджеты по № (заявки без объёма) и чужие не двигаются — их обходим.
    Уже стоящие на местах не трогаются. Возвращает {"moved": n} или {"moved": n, "err": …}."""
    ws = ws if isinstance(ws, list) else [ws]
    rows, seen = [], set()
    for w in ws:
        row = [r for r in (_row_widgets(pg, w) or []) if (r["x"], r["y"]) not in seen]
        seen |= {(r["x"], r["y"]) for r in row}
        if row:
            rows.append(row)
    if not rows:
        return {"moved": 0}
    W = pg.evaluate(WRAP_JS)
    if not W:
        return {"moved": 0, "err": "не нашёл поле виджетов"}
    others = [r for r in pg.evaluate(ROOTS_JS) if (r["x"], r["y"]) not in seen]
    news = [o for o in others if o.get("news") and o["x"] <= W["x"] + 12]
    x0 = max(o["x"] + o["w"] for o in news) + GAP if news else W["x"]
    cur, tgt = [], []
    for row in rows:
        rw, rh = sum(r["w"] for r in row) + GAP * (len(row) - 1), max(r["h"] for r in row)
        spot = _free_spot(W, others + tgt, rw, rh, x0)
        if not spot:
            return {"moved": 0, "err": "на вкладке нет места для ряда виджетов"}
        x = spot[0]
        for r in row:
            cur.append({"x": r["x"], "y": r["y"], "w": r["w"], "h": r["h"]})
            tgt.append({"x": x, "y": spot[1], "w": r["w"], "h": r["h"]}); x += r["w"] + GAP
    pending = [i for i in range(len(cur)) if abs(cur[i]["x"] - tgt[i]["x"]) > 4 or abs(cur[i]["y"] - tgt[i]["y"]) > 4]
    moved, parked = 0, set()

    def _landed(k, x, y):
        got = min(pg.evaluate(ROOTS_JS), key=lambda r: abs(r["x"] - x) + abs(r["y"] - y))
        if abs(got["x"] - x) > 8 or abs(got["y"] - y) > 8:
            return False
        cur[k] = {**cur[k], "x": got["x"], "y": got["y"]}
        return True

    while pending:   # каждый шаг ставит виджет на место или отводит мешающий (по разу) — не больше 2n шагов
        step = False
        # на место — тот, чьё место не накрывает ещё не переставленный виджет (иначе закроем его ручку)
        for i in [i for i in pending if not any(_overlap(tgt[i], cur[k]) for k in pending if k != i)]:
            if _drag(pg, cur[i], tgt[i]["x"], tgt[i]["y"]):   # False — шапку перекрыл другой, пробуем следующий
                if not _landed(i, tgt[i]["x"], tgt[i]["y"]):
                    return {"moved": moved, "err": "виджет не встал на место"}
                pending.remove(i); moved += 1; step = True
                break
        if step:
            continue
        # места заняты своими же (перестановка, свалка) — отвести мешающий в сторону, вне будущих рядов
        for k in pending:
            if k in parked or not any(_overlap(tgt[i], cur[k]) for i in pending if i != k):
                continue
            tmp = _free_spot(W, others + [c for n, c in enumerate(cur) if n != k] + tgt, cur[k]["w"], cur[k]["h"])
            if tmp and _drag(pg, cur[k], *tmp) and _landed(k, *tmp):
                parked.add(k); step = True
                break
        if not step:
            return {"moved": moved, "err": "не смог развести виджеты — шапки перекрыты"}
    return {"moved": moved}

def ensure_news(pg):
    """Лента новостей на открытой вкладке: без неё наблюдатель не увидит новость. Нет — добавить «Новости»."""
    if pg.evaluate("() => document.querySelectorAll('.news_row').length") > 0:
        return {"ok": True, "added": False}
    before = {(r["x"], r["y"]) for r in pg.evaluate(ROOTS_JS)}
    btn = _leaf_pos(pg, "Виджеты")
    if not btn:
        return {"ok": False, "err": "не нашёл кнопку «Виджеты»"}
    _safe_click(pg, *btn); pg.wait_for_timeout(800)
    item = _leaf_pos(pg, "Новости", near=btn)
    if not item:
        pg.keyboard.press("Escape")
        return {"ok": False, "err": "в меню виджетов нет «Новости»"}
    _safe_click(pg, *item); pg.wait_for_timeout(1500)
    new = [r for r in pg.evaluate(ROOTS_JS) if (r["x"], r["y"]) not in before]
    if new:
        _to_free_spot(pg, new[0])   # лента — в левый верхний угол, если он свободен
    for _ in range(20):   # дать ленте загрузиться
        pg.wait_for_timeout(300)
        if pg.evaluate("() => document.querySelectorAll('.news_row').length") > 0:
            return {"ok": True, "added": True}
    return {"ok": False, "err": "виджет «Новости» добавлен, но лента не читается"}

# Сохранённая раскладка активной вкладки: заявки «тикер + количество». server=false — копия в браузере
# (IndexedDB терминала), true — копия на сервере Т-Банка (JSON, сжатый deflate): при загрузке страницы
# терминал берёт серверную, если её версия не ниже браузерной, — значит, заявки должны дойти и туда.
PERSIST_JS = r"""async (server) => {
  const st = window.store && window.store.getState && window.store.getState();
  const ns = st && st.settings && st.settings.nonShared, sid = ns && ns.activeSpaceId;
  if (!sid) return null;
  let rec = null;
  if (server) {
    const u = performance.getEntriesByType('resource').map(e => e.name)
      .find(u => u.includes('/settings/state') && u.includes('appMode=blob'));
    if (!u || typeof DecompressionStream === 'undefined') return null;
    const r = await fetch(u, {credentials: 'include'});
    if (!r.ok) return null;
    const s = JSON.parse(await new Response(r.body.pipeThrough(new DecompressionStream('deflate-raw'))).text());
    rec = (s.spaces || {})[sid] || null;
  } else {
    if (!indexedDB.databases) return null;
    const names = (await indexedDB.databases()).map(d => d.name).filter(n => /^invest_terminal_settings_\d+$/.test(n || ''));
    if (names.length !== 1) return null;
    const db = await new Promise((res, rej) => { const r = indexedDB.open(names[0]); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
    try {
      rec = await new Promise(res => { const r = db.transaction('store', 'readonly').objectStore('store').get(sid);
        r.onsuccess = () => res(r.result); r.onerror = () => res(null); });
    } finally { db.close(); }
  }
  {
    const w = (rec && rec.widgets) || {}, saved = [];
    for (const [id, c] of Object.entries(w.common || {})) {
      if ((w.idType || {})[id] !== 'COMBINED_ORDER_WIDGET') continue;
      const q = (((w.custom || {})[id] || {}).combinedOrderSettings || {}).quantity;
      saved.push([String(c.symbolId || '').toUpperCase(), Math.round(+q || 0)]);
    }
    return {space: sid, status: ns.activeSpaceStatus, saved};
  }
}"""

def unsaved(pg, server=False):
    """Заявки на экране, которых нет в сохранённой раскладке терминала (server — в копии на сервере):
    при перезагрузке страницы они пропадут. [] — всё сохранено, None — проверить нельзя."""
    try:
        r = pg.evaluate(PERSIST_JS, bool(server))
    except Exception:
        return None
    if not r:
        return None
    left = Counter(tuple(x) for x in r["saved"])
    out = []
    for x in widgets(pg):
        k = (x["ticker"], int(_num(x.get("qty"))))
        if left[k] > 0:
            left[k] -= 1
        else:
            out.append(k)
    return out

def wait_saved(pg, secs=6.0, server=False):
    """Терминал сохраняет в браузер за доли секунды, на сервер — через 2–10 с: ждём до secs совпадения."""
    end = time.time() + secs
    while True:
        u = unsaved(pg, server)
        if not u or time.time() > end:
            return u
        pg.wait_for_timeout(1500 if server else 500)

# Сохранённые заявки вкладок по названию (копия в браузере) — без переключения вкладок.
SAVED_TABS_JS = r"""async (titles) => {
  if (!indexedDB.databases) return null;
  const names = (await indexedDB.databases()).map(d => d.name).filter(n => /^invest_terminal_settings_\d+$/.test(n || ''));
  if (names.length !== 1) return null;
  const db = await new Promise((res, rej) => { const r = indexedDB.open(names[0]); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
  const get = k => new Promise(res => { const r = db.transaction('store', 'readonly').objectStore('store').get(k);
    r.onsuccess = () => res(r.result); r.onerror = () => res(null); });
  try {
    const shared = await get('shared'), byId = (shared && shared.spaces && shared.spaces.byId) || {}, out = {};
    for (const t of titles) {
      const sid = Object.keys(byId).find(k => (byId[k] || {}).title === t);
      if (!sid) { out[t] = null; continue; }
      const rec = await get(sid), w = (rec && rec.widgets) || {}, list = [];
      for (const [id, c] of Object.entries(w.common || {})) {
        if ((w.idType || {})[id] !== 'COMBINED_ORDER_WIDGET') continue;
        const q = (((w.custom || {})[id] || {}).combinedOrderSettings || {}).quantity;
        list.push([String(c.symbolId || '').toUpperCase(), Math.round(+q || 0), id]);
      }
      out[t] = list;
    }
    return out;
  } finally { db.close(); }
}"""

def saved_tabs(pg, titles):
    """{вкладка: [(тикер, количество, id виджета), …] | None — такой вкладки нет} или None — проверить нельзя."""
    try:
        r = pg.evaluate(SAVED_TABS_JS, list(titles))
    except Exception:
        return None
    return {t: (None if v is None else [tuple(x) for x in v]) for t, v in r.items()} if r else None

def needed_orders(w):
    """Заявки наблюдателя, которые нажимаются по виджету с объёмом: {(тикер, лоты)}."""
    return {((o.get("ticker") or "").upper(), int(o["lots"])) for b in w.get("branches") or []
            for o in b.get("orders") or [] if o.get("venue") != "cscalp" and o.get("ticker")
            and str(o.get("lots") or "").strip().isdigit()}

def missing_orders(w, saved):
    """Каких виджетов наблюдателя нет в сохранённой раскладке его вкладки (saved — список из saved_tabs).
    Заявка, привязанная к виджету по id, на месте, пока жив сам виджет — с любым объёмом (объём, тип и цену
    теперь меняют в виджете, и сторож не должен считать его пропавшим и раскладывать заново)."""
    saved = saved or []
    have = Counter((x[0], x[1]) for x in saved)
    ids = {x[2] for x in saved if len(x) > 2}
    need = {}
    for b in w.get("branches") or []:
        for o in b.get("orders") or []:
            if o.get("venue") == "cscalp" or not o.get("ticker") or not str(o.get("lots") or "").strip().isdigit():
                continue
            need.setdefault(((o.get("ticker") or "").upper(), int(o["lots"])), []).append(o)
    return sorted(k for k, os_ in need.items()
                  if have[k] == 0 and not any(o.get("wid") and o["wid"] in ids for o in os_))

def _reload_tab(pg, tab):
    """Перезагрузить страницу терминала и вернуться на вкладку (после F5 терминал снова сохраняет)."""
    pg.reload(timeout=30000)
    for _ in range(60):
        pg.wait_for_timeout(500)
        if pg.evaluate(TAB_POS_JS, tab):
            break
    pg.wait_for_timeout(2000)
    return ensure_tab(pg, tab, wait_ms=1500)

def layout(pg, w, peers=None, _retry=False):
    """Разложить наблюдателя на его вкладке: создать вкладку и недостающие виджеты по заявкам веток.
    Виджеты, которые уже есть (тикер + объём или №), не пересоздаются; виджеты с объёмом выстраиваются
    ровным рядом: ветка 1 — левый. peers — все наблюдатели этой вкладки по порядку (их ряды — друг под другом).
    Возвращает текст-отчёт."""
    tab = w.get("tab") or w.get("name") or "Наблюдатель"
    if not create_tab(pg, tab):
        return {"ok": False, "text": f"Не смог открыть или создать вкладку «{tab}»."}
    made, errs, reused = 0, [], False
    news = ensure_news(pg)   # первой: встаёт в левый верхний угол, ряды заявок — справа от неё
    if not news.get("ok"):
        errs.append(f"лента новостей: {news.get('err')}")
    for b in w.get("branches") or []:
        for o in b.get("orders") or []:
            tk = (o.get("ticker") or "").upper()
            if o.get("venue") == "cscalp" or not tk or pick(widgets(pg), tk, o.get("n", 1), o.get("lots"), o.get("wid")):
                continue
            r = add_order_widget(pg, tk, o.get("side"), o.get("lots"), o.get("price"))
            if r.get("ok"):
                made += 1
                reused |= bool(r.get("reused"))
            else:
                errs.append(f"{tk}: {r.get('err')}")
    # ровный ряд в порядке веток; заготовки пользователь расставил сам — их не двигаем
    # ряды в порядке наблюдателей вкладки (как в списке), этот — в своей версии; нового в списке ещё нет — в конец
    order = [w if p.get("id") == w.get("id") else p for p in peers or []]
    if not any(p.get("id") == w.get("id") for p in peers or []):
        order.append(w)
    row = {"moved": 0} if reused else arrange_rows(pg, order)
    if row.get("err"):
        errs.append(f"ряд: {row['err']}")
    draw_overlay(pg, w)
    lost = wait_saved(pg)
    srv = wait_saved(pg, secs=15, server=True) if lost == [] else None   # дошло ли до сервера Т-Банка
    if lost and not _retry and _reload_tab(pg, tab):
        # терминал не сохранял вкладку (его сбой) — после перезагрузки он снова сохраняет: разложить заново
        r = layout(pg, w, peers, _retry=True)
        r["text"] = "Терминал не сохранял вкладку — перезагрузил страницу и разложил заново. " + r["text"]
        return r
    txt = f"Вкладка «{tab}»: добавил виджетов заявок — {made}" + (", ленту новостей" if news.get("added") else "") +           (f", выстроил в ряд по веткам (передвинул {row['moved']})" if row.get("moved") else "") + "."
    if lost:
        errs.append(f"терминал не сохранил заявки ({', '.join(f'{t} ×{q}' for t, q in lost)}) — после перезагрузки "
                    f"страницы они пропадут; перезагрузите терминал и нажмите «Разложить» ещё раз")
    if errs:
        txt += " Не получилось: " + "; ".join(errs) + "."
    elif lost == [] and srv:
        txt += (" Раскладка сохранена в браузере, но на сервер терминала пока не ушла — если страница "
                "перезагрузится, заявки могут пропасть; я слежу и восстановлю их.")
    elif lost == []:
        txt += " Раскладка сохранена в терминале."
    return {"ok": not errs, "text": txt + " Проверьте заявки, сэр."}
