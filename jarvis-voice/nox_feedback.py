# -*- coding: utf-8 -*-
"""
Обучение фильтра новостей на реакциях пользователя.

«Нокс, такие не читай» / «это не интересно» / «эту не надо» — про новость, прочитанную последней:
она становится примером «НЕ нужно» для слова белого списка, по которому прошла («переговоры», «помощь»…).
«Такие читай всегда» / «это важно» — пример «нужно». Примеры уходят в вопрос умного отбора вместе
с критерием слова: мозг отсекает похожие новости, даже сформулированные иначе. Это не стоп-слова.

Хранится в news_filter.json: "examples": {"<слово>": {"no": [...], "yes": [...]}}, "__any__" — новость прошла
не по слову (источник, тикер). По MAX_EX последних примеров на слово.
"""
import re
import time

MAX_EX = 8
WINDOW = 10 * 60          # «такие» = новость, прочитанная за последние 10 минут

NO_RE = re.compile(r"(?:таки[ея]|такое|эт[уоа]|подобн\w+)\s+(?:новост\w*\s+)?(?:больше\s+)?(?:не\s+(?:читай|надо|нужн\w*|озвучивай)|не\s+интересн\w*)|"
                   r"не\s+(?:читай|озвучивай)\s+(?:больше\s+)?(?:таки[ея]|такое|подобн\w+|эт[уоа])|"
                   r"(?:это|такое)\s+(?:мне\s+)?не\s+(?:интересно|нужно|важно)|(?:таки[ея]|эт[уоа])\s+не\s+нужн\w*|"
                   r"(?:эта|такая)\s+новость\s+(?:мне\s+)?не\s+нужн\w*")
YES_RE = re.compile(r"(?:таки[ея]|такое|подобн\w+)\s+(?:новост\w*\s+)?(?:читай|озвучивай)(?:\s+всегда)?(?!\s*не)|"
                    r"(?:читай|озвучивай)\s+(?:всегда\s+)?(?:таки[ея]|такое|подобн\w+)|"
                    r"(?:это|такое)\s+(?:очень\s+)?(?:важно|интересно|нужно)(?!\w)|(?:таки[ея])\s+(?:мне\s+)?нужн\w*")


def kind(text):
    low = (text or "").lower().replace("ё", "е")
    if "запомни" in low or "новостей" in low and "чтение" in low:
        return None                      # «запомни, что это важно» — это память, не реакция на новость
    if NO_RE.search(low):
        return "no"
    if YES_RE.search(low):
        return "yes"
    return None


def _keys(hits):
    ks = [h for h in (hits or []) if h and not h.startswith(("тикер ", "источник "))]
    return ks or ["__any__"]


def apply(cfg, text, hits, k):
    """Записать пример в cfg (сохраняет вызывающий). Возвращает слова, к которым привязан пример."""
    ex = cfg.setdefault("examples", {})
    text = re.sub(r"\s+", " ", (text or "").strip())[:220]
    keys = _keys(hits)
    other = "yes" if k == "no" else "no"
    for key in keys:
        e = ex.setdefault(key, {"no": [], "yes": []})
        e.setdefault("no", []); e.setdefault("yes", [])
        e[other] = [x for x in e[other] if x != text]
        if text not in e[k]:
            e[k].append(text)
        e[k] = e[k][-MAX_EX:]
    return keys


def prompt_examples(cfg, hits, n=4):
    """Добавка к критерию для умного отбора: примеры «не нужно» / «нужно» по совпавшим словам (+ общие)."""
    ex = cfg.get("examples") or {}
    parts = []
    for key in _keys(hits) + (["__any__"] if "__any__" not in _keys(hits) else []):
        e = ex.get(key) or {}
        no, yes = (e.get("no") or [])[-n:], (e.get("yes") or [])[-n:]
        who = "" if key == "__any__" else f" (слово «{key}»)"
        if no:
            parts.append(f"Пользователь просил НЕ читать такие{who}: " + "; ".join(f"«{x}»" for x in no))
        if yes:
            parts.append(f"Пользователь просил читать такие{who}: " + "; ".join(f"«{x}»" for x in yes))
    if not parts:
        return ""
    return ("\n" + "\n".join(parts) +
            "\nЕсли новость по смыслу похожа на «НЕ читать» — ответь НЕТ, даже если формулировка другая; "
            "похожа на «читать» — ДА.")


def reply(k, keys, text):
    w = ", ".join(f"«{x}»" for x in keys if x != "__any__")
    about = f" по слову {w}" if w else ""
    short = text[:70] + ("…" if len(text) > 70 else "")
    if k == "no":
        return f"Понял, сэр: такие{about} больше не читаю — «{short}»."
    return f"Понял, сэр: такие{about} буду читать — «{short}»."
