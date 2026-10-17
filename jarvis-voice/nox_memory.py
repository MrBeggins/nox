# -*- coding: utf-8 -*-
"""
Память Нокса: правила общения и факты о пользователе — подмешиваются в КАЖДЫЙ ответ мозга
(ответы маршрутизатора, поиск в интернете, объяснение новостей, основной мозг brain.rs).

Хранилище: C:\\jarvis-voice\\nox_memory.json
  {"rules": [{"id", "text", "on"}], "facts": [{"id", "text", "on", "created"}]}
Для Rust-мозга готовый текст лежит в C:\\jarvis-voice\\nox_memory_prompt.txt (перезаписывается при каждом изменении).
Голос: «запомни правило: …», «запомни обо мне: …» / «запомни, что я …», «что ты обо мне знаешь»,
«какие у тебя правила», «забудь, что …». Окно: вкладка «Память».
"""
import json
import os
import re
import threading
import time
import uuid

STORE = r"C:\jarvis-voice\nox_memory.json"
PROMPT_FILE = r"C:\jarvis-voice\nox_memory_prompt.txt"
MAX_CHARS = 2500            # блок памяти в подсказке — не больше (скорость и цена ответа)
_lock = threading.Lock()

# Стартовый набор — уклон в трейдинг. Пользователь правит его во вкладке «Память».
DEFAULT_RULES = [
    "Отвечай коротко и по делу: сначала главное, 1–3 предложения; подробнее — только если попрошу.",
    "Говори как опытный трейдер и аналитик Мосбиржи: что это значит для цены, риска и ликвидности, "
    "какие уровни и сценарии важны.",
    "Отделяй факты от мнения. Котировки, цифры и даты не выдумывай: нет свежих данных — так и скажи.",
    "Решения о сделках принимаю я: можешь давать мнение и сценарии, но не выдавай их за гарантию.",
    "Без лести, воды и лишних извинений. Если я ошибаюсь — скажи прямо.",
    "Компании называй по-русски (Сбербанк, Газпром), а не тикерами; числа — точно.",
]
DEFAULT_FACTS = [
    "Я частный трейдер на Мосбирже: акции и фьючерсы (индекс MIX, нефть, газ, валюта), часто торгую по новостям.",
    "Работаю в терминале Т-Инвестиций и в CScalp; новости читаю в Trading Tools и Stocksi Ultimate.",
    "Для меня важна скорость: реакция на новость за доли секунды.",
]


def _new(text):
    return {"id": uuid.uuid4().hex[:8], "text": text.strip(), "on": True, "created": time.time()}


def load():
    with _lock:
        try:
            d = json.load(open(STORE, encoding="utf-8"))
        except Exception:
            d = None
        if not isinstance(d, dict):
            d = {"rules": [_new(x) for x in DEFAULT_RULES], "facts": [_new(x) for x in DEFAULT_FACTS]}
            _write(d)
        d.setdefault("rules", []); d.setdefault("facts", [])
        return d


def _write(d):
    tmp = STORE + ".tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, STORE)
    try:
        open(PROMPT_FILE, "w", encoding="utf-8").write(_render(d))
    except Exception:
        pass


def save(d):
    """Целиком из окна: {"rules": [...], "facts": [...]} — пустые строки выкидываем."""
    out = {}
    for k in ("rules", "facts"):
        lst = []
        for x in (d or {}).get(k) or []:
            t = str((x or {}).get("text") or "").strip()
            if t:
                lst.append({"id": str(x.get("id") or uuid.uuid4().hex[:8]), "text": t[:400], "on": x.get("on", True) is not False,
                            "created": x.get("created") or time.time()})
        out[k] = lst
    with _lock:
        _write(out)
    return out


def _render(d):
    rules = [x["text"] for x in d.get("rules", []) if x.get("on", True)]
    facts = [x["text"] for x in d.get("facts", []) if x.get("on", True)]
    if not rules and not facts:
        return ""
    s = "\n\nПАМЯТЬ (учитывай всегда):"
    if rules:
        s += "\nПравила общения:\n" + "\n".join("- " + r for r in rules)
    if facts:
        s += "\nО пользователе:\n" + "\n".join("- " + f for f in facts)
    return s[:MAX_CHARS]


def block():
    """Текст для системной подсказки (пустая строка, если памяти нет)."""
    try:
        return _render(load())
    except Exception:
        return ""


# ------------------------------------------------------------------ голос
VOICE_RE = re.compile(r"запомни[,\s]+(?:правило|обо\s+мне|про\s+меня|что\s+я|что\s+мне|что\s+меня)|"
                      r"обращайся\s+ко\s+мне|называй\s+меня|что\s+ты\s+(?:обо|про)\s+мне\s+знаешь|"
                      r"какие\s+у\s+тебя\s+правила|твои\s+правила|забудь(?:,)?\s+(?:что|правило|про)")


def _stem_words(s):
    return {w[:5] for w in re.findall(r"[a-zа-я0-9]{3,}", (s or "").lower().replace("ё", "е"))}


def voice(text):
    t = (text or "").strip()
    low = t.lower().replace("ё", "е")
    d = load()
    if re.search(r"что\s+ты\s+(?:обо|про)\s+мне\s+знаешь", low):
        facts = [x["text"] for x in d["facts"] if x.get("on", True)]
        return {"ok": True, "text": ("Я помню: " + " ".join(facts)) if facts else "Пока я ничего о вас не запомнил, сэр."}
    if re.search(r"какие\s+у\s+тебя\s+правила|твои\s+правила", low):
        rules = [x["text"] for x in d["rules"] if x.get("on", True)]
        return {"ok": True, "text": ("Мои правила: " + " ".join(rules)) if rules else "Правил пока нет, сэр."}
    m = re.search(r"забудь(?:,)?\s+(?:что|правило|про)\s+(.+)$", low)
    if m:
        want = _stem_words(m.group(1))
        best, score, kind = None, 0.0, None
        for k in ("facts", "rules"):
            for x in d[k]:
                w = _stem_words(x["text"])
                sc = len(want & w) / (len(want) or 1)
                if sc > score:
                    best, score, kind = x, sc, k
        if not best or score < 0.5:
            return {"ok": False, "text": "Не нашёл в памяти, что забыть, сэр."}
        d[kind] = [x for x in d[kind] if x["id"] != best["id"]]
        with _lock:
            _write(d)
        return {"ok": True, "text": f"Забыл: «{best['text']}», сэр."}
    m = re.search(r"запомни\s+правило[:,]?\s*(.+)$", t, re.I)
    if m or re.search(r"обращайся\s+ко\s+мне|называй\s+меня", low):
        body = (m.group(1) if m else t).strip(" .")
        d["rules"].append(_new(body[0].upper() + body[1:] if body else body))
        with _lock:
            _write(d)
        return {"ok": True, "text": f"Запомнил правило: «{body}», сэр."}
    m = re.search(r"запомни\s+(?:обо\s+мне|про\s+меня)[:,]?\s*(.+)$", t, re.I) or \
        re.search(r"запомни[,]?\s+(что\s+(?:я|мне|меня)\s+.+)$", t, re.I)
    if m:
        body = re.sub(r"^что\s+", "", m.group(1).strip(" ."), flags=re.I)
        body = body[0].upper() + body[1:] if body else body
        d["facts"].append(_new(body))
        with _lock:
            _write(d)
        return {"ok": True, "text": f"Запомнил о вас: «{body}», сэр."}
    return {"ok": False, "text": "Что запомнить, сэр? Скажите «запомни правило: …» или «запомни обо мне: …»."}
