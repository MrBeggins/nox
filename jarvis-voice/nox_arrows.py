# -*- coding: utf-8 -*-
"""
Стрелки «Купить» / «Продать» на оверлее Нокса — ручная корзина заявок на неожиданную новость.

Пользователь сам решает, когда жать: стрелка ▲ исполняет свой список заявок, ▼ — свой, сразу все (несколько
активов одним нажатием). Заявки — как в ветке наблюдателя: Т-Терминал (виджет «Заявка» на вкладке, по объёму)
или CScalp (стакан по тикеру, по рынку горячей клавишей). Исполняет тот же EXECUTOR, что у наблюдателей:
тумблер «исполнение» и лимиты из настроек Т-Терминала действуют и здесь.

Хранилище: C:\\jarvis-voice\\nox_arrows.json
  {"tab": "нокс", "buy": {"enabled": bool, "orders": [...]}, "sell": {"enabled": bool, "orders": [...]}}
"""
import copy
import json
import os

STORE = r"C:\jarvis-voice\nox_arrows.json"
SIDES = ("buy", "sell")
RU = {"buy": "Купить", "sell": "Продать"}
DEFAULT = {"tab": "нокс", "cscalp_tab": "", "style": "triangles", "buy": {"enabled": False, "orders": []},
           "sell": {"enabled": False, "orders": []}}
STYLES = ("triangles", "pills", "ring", "chevrons")   # А треугольники, Б капсулы, В сегменты кольца, Г шевроны
LAYOUTS = ("row", "stack")   # row — обе под шаром; stack — «купить» над шаром, «продать» под ним


def _look(src, cfg):
    """Внешний вид: стиль, расположение, размер виджета (60…160 %)."""
    cfg["style"] = src.get("style") if src.get("style") in STYLES else "triangles"
    cfg["layout"] = src.get("layout") if src.get("layout") in LAYOUTS else "row"
    try:
        cfg["size"] = min(160, max(60, int(round(float(src.get("size") or 100)))))
    except (TypeError, ValueError):
        cfg["size"] = 100


def _clean_orders(orders, side):
    out = []
    for o in orders or []:
        tk = str(o.get("ticker") or "").strip().upper()
        if not tk:
            continue
        cs = o.get("venue") == "cscalp"
        row = {"ticker": tk, "side": o.get("side") if o.get("side") in SIDES else side}
        if cs:
            row["venue"] = "cscalp"
            try:                      # № стакана с этим тикером в окне (слева направо): два MIX — №1 и №2
                if o.get("n"):
                    row["n"] = max(1, int(o["n"]))
            except (TypeError, ValueError):
                pass
        else:
            try:
                row["lots"] = int(str(o.get("lots") or "").strip()) if str(o.get("lots") or "").strip() else None
            except ValueError:
                row["lots"] = None
            try:
                row["n"] = max(1, int(o.get("n") or 1))
            except (TypeError, ValueError):
                row["n"] = 1
            if str(o.get("price") or "").strip():
                row["price"] = str(o["price"]).strip()
        out.append(row)
    return out


def load():
    try:
        d = json.load(open(STORE, encoding="utf-8"))
    except Exception:
        d = {}
    cfg = copy.deepcopy(DEFAULT)
    cfg["tab"] = str(d.get("tab") or cfg["tab"]).strip() or "нокс"
    cfg["cscalp_tab"] = str(d.get("cscalp_tab") or "").strip()   # вкладка CScalp: жать ВСЕ её стаканы
    _look(d, cfg)
    for s in SIDES:
        a = d.get(s) or {}
        cfg[s] = {"enabled": bool(a.get("enabled")), "orders": _clean_orders(a.get("orders"), s)}
    return cfg


def save(data):
    cfg = copy.deepcopy(DEFAULT)
    cfg["tab"] = str((data or {}).get("tab") or "нокс").strip() or "нокс"
    cfg["cscalp_tab"] = str((data or {}).get("cscalp_tab") or "").strip()
    _look(data or {}, cfg)
    for s in SIDES:
        a = (data or {}).get(s) or {}
        cfg[s] = {"enabled": bool(a.get("enabled")), "orders": _clean_orders(a.get("orders"), s)}
    tmp = STORE + ".tmp"
    json.dump(cfg, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, STORE)
    return cfg


def label(o):
    """«KAZT × 3 лота (Т)» / «SILVM-12.26 (CScalp)» — для подсказки стрелки."""
    act = "купить" if o.get("side") == "buy" else "продать"
    if o.get("venue") == "cscalp":
        return f"{act} {o['ticker']} (CScalp)"
    return f"{act} {o['ticker']}" + (f" × {o['lots']}" if o.get("lots") else f" №{o.get('n', 1)}")


def pseudo(cfg, sides=SIDES):
    """Наблюдатель-«двойник» для исполнителя и раскладки: по ветке на стрелку."""
    import nox_watchers
    branches = [{"type": "any", "orders": cfg[s]["orders"]} for s in sides]
    branches = nox_watchers._number_orders(nox_watchers._clean_branches(branches))
    name = "Стрелки оверлея" if len(sides) > 1 else f"Стрелка «{RU[sides[0]]}»"
    return {"id": "arrows-" + "-".join(sides), "name": name, "tab": cfg["tab"], "enabled": True, "branches": branches}
