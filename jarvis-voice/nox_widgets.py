# -*- coding: utf-8 -*-
"""
Виджеты «Заявка» T-терминала для наблюдателей Nox: найти, прочитать, разложить, подписать и нажать.

Наблюдатель = вкладка терминала. На ней виджеты «Заявка» (заполненные заранее — вами или Ноксом).
Ветка наблюдателя = список (тикер, №, сторона): «NVTK №1 → Купить», «GAZP №1 → Продать».
№ — порядковый номер виджета с этим тикером на вкладке (слева направо, сверху вниз).

Все функции принимают playwright-страницу терминала и вызываются в потоке CDP-воркера terminal_reader.
"""
import time

# ------------------------------------------------------------------ чтение виджетов
# Корень виджета — [class*=WidgetBody-widgetBody]; первая строка его текста — тикер из шапки.
LIST_JS = r"""() => {
  const out = [];
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
    out.push({ticker, x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
              type: price ? 'limit' : 'market', price, qty, cost, buy: c(buy), sell: c(sell)});
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

def pick(ws, ticker, n=1):
    ticker = (ticker or "").upper().strip()
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
        w = pick(ws, o.get("ticker"), o.get("n", 1))
        if not w:
            res.append({"order": o, "ok": False, "err": f"виджет {o.get('ticker')} №{o.get('n', 1)} не найден"})
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
  let n = 0;
  const used = {};
  for (const m of marks) {
    const w = ws.find(w => w.ticker === m.ticker && w.n === m.n); if (!w) continue;
    const b = m.side === 'buy' ? w.buy : w.sell;
    const key = w.ticker + w.n + m.side; const k = used[key] = (used[key] || 0) + 1;
    if (k === 1) el(`left:${b.x - 66}px;top:${b.y - 20}px;width:132px;height:40px;border:3px solid ${m.color};border-radius:9px;box-shadow:0 0 14px ${m.color};box-sizing:border-box;`);
    el(`left:${b.x - 66}px;top:${b.y + 22 + (k - 1) * 20}px;padding:2px 7px;border-radius:6px;background:${m.color};color:#fff;font-size:11px;font-weight:700;white-space:nowrap;`, m.label);
    n++;
  }
  return n;
}"""

BRANCH_COLORS = ["#22c55e", "#ef4444", "#f59e0b", "#3b82f6", "#a855f7", "#14b8a6"]

def branch_label(b):
    t, mn, mx = b.get("type"), b.get("min"), b.get("max")
    f = lambda v: ("%g" % v) if isinstance(v, (int, float)) else str(v)
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
    pg.wait_for_timeout(500)
    return active_tab(pg) == name

FREE_SLOT_JS = r"""([w, h]) => {
  const wrap = document.querySelector('[class*="widgetsWrap"]'); if (!wrap) return [40, 140];
  const W = wrap.getBoundingClientRect();
  const taken = [...document.querySelectorAll('[class*="WidgetBody-widgetBody"]')].map(e => e.getBoundingClientRect()).filter(b => b.width > 0);
  const hit = (x, y) => taken.some(b => x < b.right + 8 && x + w + 8 > b.x && y < b.bottom + 8 && y + h + 8 > b.y);
  for (let y = W.y + 50; y + 60 < W.bottom; y += h + 16)
    for (let x = W.x + 16; x + w < W.right; x += w + 16)
      if (!hit(x, y)) return [Math.round(x), Math.round(y)];
  return [Math.round(W.x + 16), Math.round(W.y + 50)];
}"""

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
    # переставить в свободное место (за шапку)
    slot = pg.evaluate(FREE_SLOT_JS, [w["w"], w["h"]])
    hx, hy = w["x"] + 190, w["y"] + 12
    if pg.evaluate(GUARD_JS, [hx, hy]) is None:
        pg.mouse.move(hx, hy); pg.mouse.down()
        for k in range(1, 16):
            pg.mouse.move(hx + (slot[0] - w["x"]) * k / 15, hy + (slot[1] - w["y"]) * k / 15)
            pg.wait_for_timeout(12)
        pg.mouse.up(); pg.wait_for_timeout(300)
    return {"ok": True}

ROOTS_JS = r"""() => [...document.querySelectorAll('[class*="WidgetBody-widgetBody"]')].map(e => { const b = e.getBoundingClientRect();
  return {x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height)}; }).filter(r => r.w > 0)"""

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
        r = new[0]
        slot = pg.evaluate(FREE_SLOT_JS, [r["w"], r["h"]])
        hx, hy = r["x"] + 190, r["y"] + 12
        if pg.evaluate(GUARD_JS, [hx, hy]) is None:
            pg.mouse.move(hx, hy); pg.mouse.down()
            for k in range(1, 16):
                pg.mouse.move(hx + (slot[0] - r["x"]) * k / 15, hy + (slot[1] - r["y"]) * k / 15)
                pg.wait_for_timeout(12)
            pg.mouse.up()
    for _ in range(20):   # дать ленте загрузиться
        pg.wait_for_timeout(300)
        if pg.evaluate("() => document.querySelectorAll('.news_row').length") > 0:
            return {"ok": True, "added": True}
    return {"ok": False, "err": "виджет «Новости» добавлен, но лента не читается"}

def layout(pg, w):
    """Разложить наблюдателя на его вкладке: создать вкладку и недостающие виджеты по заявкам веток.
    Виджеты, которые уже есть (тикер + №), не трогаются. Возвращает текст-отчёт."""
    tab = w.get("tab") or w.get("name") or "Наблюдатель"
    if not create_tab(pg, tab):
        return {"ok": False, "text": f"Не смог открыть или создать вкладку «{tab}»."}
    made, errs = 0, []
    for b in w.get("branches") or []:
        for o in b.get("orders") or []:
            tk = (o.get("ticker") or "").upper()
            if o.get("venue") == "cscalp" or not tk or pick(widgets(pg), tk, o.get("n", 1)):
                continue
            r = add_order_widget(pg, tk, o.get("side"), o.get("lots"), o.get("price"))
            if r.get("ok"):
                made += 1
            else:
                errs.append(f"{tk}: {r.get('err')}")
    news = ensure_news(pg)
    if not news.get("ok"):
        errs.append(f"лента новостей: {news.get('err')}")
    draw_overlay(pg, w)
    txt = f"Вкладка «{tab}»: добавил виджетов заявок — {made}" + (", ленту новостей" if news.get("added") else "") + "."
    if errs:
        txt += " Не получилось: " + "; ".join(errs) + "."
    return {"ok": not errs, "text": txt + " Проверьте заявки, сэр."}
