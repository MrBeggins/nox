# -*- coding: utf-8 -*-
"""
Сценарии по облигациям: что делать, когда НРД сообщил о неисполнении обязательств эмитентом.

Правило (C:\\jarvis-voice\\nox_bond_rules.json):
  {"id", "issuer": "самолет" | "*" (любой), "statuses": ["late", "overdue", "default", "disclosed"],
   "action": "alert" | "sell_share" | "short_share" | "close_bonds", "lots": 1, "exec": false, "enabled": false}
  alert       — громкое предупреждение (повтор) с тем, что у пользователя есть по эмитенту;
  sell_share  — продать акции эмитента (закрыть лонг, объём — из портфеля); short_share — шорт lots лотов;
  close_bonds — закрыть позицию по облигациям этого выпуска.
Заявки идут ТЕМ ЖЕ путём, что голосовые («выставь заявку»): /fill_order ридера — тумблер «Исполнять заявки голосом»
и лимиты Т-Терминала действуют. exec=false — только заполнить виджет «Заявка» (жмёт пользователь).
Новое правило создаётся ВЫКЛЮЧЕННЫМ; включает и разрешает исполнять сразу только сам пользователь.

Голос: «при дефолте самолета продай акции», «при техдефолте облигаций самолета закрой облигации»,
«при любом дефолте облигаций предупреди», «какие правила по облигациям», «включи / выключи / удали правило по
облигациям самолета», «правило по облигациям самолета исполнять сразу».
"""
import json
import os
import re
import time
import uuid

STORE = r"C:\jarvis-voice\nox_bond_rules.json"
STATUSES = ("late", "overdue", "default", "disclosed")
ACT_RU = {"alert": "предупредить", "sell_share": "продать акции эмитента", "short_share": "шорт акций эмитента",
          "close_bonds": "закрыть облигации выпуска"}
ST_RU = {"late": "опоздание выплаты", "overdue": "просрочка", "default": "дефолт", "disclosed": "раскрытие эмитентом"}

VOICE_RE = re.compile(r"(?:при|если|когда)\s+(?:\w+\s+){0,2}(?:тех\w*\s*)?(?:дефолт|неисполнен|просрочк|неуплат)|"
                      r"правил\w*\s+(?:по\s+)?облигаци|облигаци\w*\s+правил")


def load():
    try:
        d = json.load(open(STORE, encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("rules"), list) else {"rules": []}
    except Exception:
        return {"rules": []}


def save(d):
    tmp = STORE + ".tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, STORE)


def _norm(s):
    return re.sub(r"[^a-zа-я0-9 ]+", " ", (s or "").lower().replace("ё", "е")).strip()


def matches(rule, ev, issuer_short):
    if not rule.get("enabled"):
        return False
    if ev.get("status") not in (rule.get("statuses") or STATUSES):
        return False
    who = _norm(rule.get("issuer") or "*")
    if who in ("*", ""):
        return True
    hay = _norm(f"{ev.get('issuer', '')} {issuer_short} {ev.get('isin', '')}")
    return all(w[:5] in hay for w in who.split())


def describe(r):
    who = "любой эмитент" if (r.get("issuer") or "*") == "*" else f"«{r['issuer']}»"
    sts = ", ".join(ST_RU[x] for x in r.get("statuses") or STATUSES)
    lots = f" {r.get('lots', 1)} лот." if r.get("action") == "short_share" else ""
    mode = "" if r.get("action") == "alert" else ("исполнять сразу, " if r.get("exec") else "только заполнить заявку, ")
    return f"{who}: {sts} → {ACT_RU.get(r.get('action'), r.get('action'))}{lots} ({mode}{'вкл' if r.get('enabled') else 'выкл'})"


_SKIP = set("""облигаций облигациям облигации облигация облигациями по у эмитента эмитент эмитенту акций акции акциям бумаг
бумагам выпуска выпуску компании если при когда будет будут случится произойдет произойдёт тех техдефолт техдефолте дефолт
дефолте дефолта просрочка просрочке просрочки неисполнение неисполнении неисполнения обязательств выплаты выплате купона
погашения правило правила правилу по облигациям любом любой всех нокс случае наступлении наступит эмитентом эмитентов эмитенте""".split())
_STOP = re.compile(r"^(продай|продать|закрой|закрыть|предупреди|предупредить|шорт\w*|скинь|исполня\w*|включи|выключи|"
                   r"отключи|удали|сразу|и|то|тогда|—|-)$")


def _issuer_from(t):
    """«при дефолте самолета продай акции» -> «самолета»; «по облигациям пик шорт» -> «пик»."""
    words = re.findall(r"[а-яёa-z][а-яёa-z\-]*", t)
    out = []
    started = False
    for w in words:
        if _STOP.match(w):
            if out:
                break
            continue
        if w in _SKIP or re.match(r"(тех)?дефолт|просрочк|неисполнен|неуплат", w):
            started = True
            if out:
                break
            continue
        if started or not out:
            if started:
                out.append(w)
                if len(out) == 2:
                    break
    return " ".join(out)


def _find(d, t):
    who = _issuer_from(t)
    any_ = re.search(r"любо\w*|все\w*\s+эмитент|всех", t)
    for r in d["rules"]:
        rw = _norm(r.get("issuer") or "*")
        if any_ and rw == "*":
            return r
        if who and rw not in ("*", "") and rw.split()[0][:5] in _norm(who):
            return r
    return None


def voice(text):
    t = (text or "").lower().replace("ё", "е").strip()
    try:
        import nox_numwords
        t = nox_numwords.digits(t)             # «шорт два лота» -> «шорт 2 лота»
    except Exception:
        pass
    d = load()
    if re.search(r"какие\s+правила|список\s+правил|мои\s+правила\s+по\s+облиг", t):
        if not d["rules"]:
            return {"ok": True, "text": "Правил по облигациям пока нет, сэр."}
        return {"ok": True, "text": "Правила по облигациям: " + "; ".join(describe(r) for r in d["rules"]) + "."}
    m = re.match(r"(включи|выключи|отключи|удали)\w*\s+правил", t)
    ex = re.search(r"исполня\w*\s+сразу|сразу\s+исполня|без\s+подтвержден", t)
    if m or ex:
        r = _find(d, t)
        if not r:
            return {"ok": False, "text": "Не нашёл такое правило по облигациям, сэр. Скажите «какие правила по облигациям»."}
        if m and m.group(1).startswith("удал"):
            d["rules"] = [x for x in d["rules"] if x["id"] != r["id"]]; save(d)
            return {"ok": True, "text": f"Правило удалено: {describe(r)}."}
        if m:
            r["enabled"] = not m.group(1).startswith(("выкл", "откл"))
        if ex:
            r["exec"] = not re.search(r"не\s+исполня|только\s+заполн", t)
        save(d)
        return {"ok": True, "text": f"Правило: {describe(r)}."}
    # создание: «при дефолте самолета продай акции»
    statuses = []
    if re.search(r"тех\w*\s*дефолт", t):
        statuses = ["late", "overdue"]
    if re.search(r"(?<!тех)(?<!технический )дефолт|неисполнен|неуплат", t) and not re.search(r"тех\w*\s*дефолт", t):
        statuses = ["default", "overdue", "late", "disclosed"]
    if re.search(r"просрочк", t):
        statuses = sorted(set(statuses) | {"overdue"})
    statuses = statuses or list(STATUSES)
    if re.search(r"шорт", t):
        action = "short_share"
    elif re.search(r"продай\s+(?:\w+\s+)?акци|продать\s+акци|закрой\s+акци|скинь\s+акци", t):
        action = "sell_share"
    elif re.search(r"(?:закрой|продай|скинь)\s+(?:\w+\s+)?облигаци", t):
        action = "close_bonds"
    else:
        action = "alert"
    any_ = bool(re.search(r"любо\w*|всех|все\w*\s+эмитент|у\s+кого\s+угодно", t))
    issuer = "*" if any_ else _issuer_from(t)
    if not issuer:
        return {"ok": False, "text": "По какому эмитенту правило, сэр? Скажите, например: «при дефолте Самолета продай акции»."}
    m = re.search(r"(\d+)\s*(?:лот|плот|штук)", t)
    lots = int(m.group(1)) if m else 1
    r = {"id": uuid.uuid4().hex[:8], "issuer": issuer, "statuses": statuses, "action": action, "lots": lots,
         "exec": False, "enabled": action == "alert", "created": time.strftime("%Y-%m-%d %H:%M")}
    d["rules"].append(r); save(d)
    tail = "" if action == "alert" else (" Правило создано ВЫКЛЮЧЕННЫМ и только заполняет заявку. Скажите «включи правило "
                                          f"по облигациям {issuer}», а «исполнять сразу» — если нужно жать без вас.")
    return {"ok": True, "text": f"Правило по облигациям: {describe(r)}.{tail}"}


def plan(rule, ev, exposure):
    """Что сделать по правилу: [{"q", "side", "qty", "close"}] для /fill_order, + текст."""
    sh = (exposure or {}).get("share") or {}
    a = rule.get("action")
    if a == "alert":
        return [], "Предупреждение по правилу."
    if a == "sell_share":
        if not sh.get("ticker"):
            return [], "Акций эмитента на бирже не нашёл — продавать нечего."
        if not sh.get("qty") or sh["qty"] <= 0:
            return [], f"Длинной позиции по {sh.get('name')} нет — продавать нечего."
        return [{"q": sh["ticker"], "close": 1}], f"Закрываю лонг по {sh.get('name')}."
    if a == "short_share":
        if not sh.get("ticker"):
            return [], "Акций эмитента на бирже не нашёл — шорт невозможен."
        return [{"q": sh["ticker"], "side": "sell", "qty": max(1, int(rule.get("lots") or 1)), "close": 0}], \
            f"Шорт {rule.get('lots', 1)} лот. {sh.get('name')}."
    if a == "close_bonds":
        b = (exposure or {}).get("bond")
        if not b:
            return [], "Облигаций этого выпуска у вас нет."
        return [{"q": ev.get("isin"), "close": 1}], f"Закрываю облигации {ev.get('isin')}."
    return [], ""
