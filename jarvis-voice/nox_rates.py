# -*- coding: utf-8 -*-
"""Решения по ставке ЦБ РФ и ФРС — разбор заголовка ПРАВИЛАМИ, без мозга (~1 мс, всегда одинаково).

parse_decision(text, rate_now=None) -> None | {
    "bank":   "cbr" | "fed" | "other" | None,   # чей это банк (ЕЦБ/ЦБ Турции/Банк Англии -> other)
    "action": "cut" | "hold" | "hike",
    "change_bp": int | None,   # изменение в б.п. со знаком: снижение — минус, сохранение — 0
    "level":  float | None,    # новая ставка, %; у диапазона ФРС — ВЕРХНЯЯ граница
    "low":    float | None,    # нижняя граница диапазона (ФРС)
    "prev":   float | None,    # прежняя ставка, если названа («пред.»)
}
None — это не решение: прогноз, ожидания, анонс заседания, мнение, чужая «ставка» (вклады, прогноз ставки).

Правила (проверены на официальных формулировках cbr.ru и federalreserve.gov + вариантах лент):
  * решение = глагол в прошедшем времени («снизил/сохранил/повысил», «принял решение снизить»,
    «decided to lower», «cuts/holds/raises») РЯДОМ со словом «ставка»/«rate»;
  * маркеры прогноза («ожидают», «может», «аналитики», «прогноз», «expect», «may»…) мешают, только
    если стоят ДО глагола: «снизил до 15,5%, прогноз инфляции сохранён» — решение;
  * между глаголом и «ставкой» не должно быть другого объекта («повысил ПРОГНОЗ ставки» — не решение);
  * размер: «на 50 б.п.», «на 0,5 п.п.», «by 1/4 percentage point», «на четверть процентного пункта»;
    уровень: «до 16,5%», «на уровне 21%», «to 3-3/4 to 4 percent», «3,75-4%».
"""
import re

_CBR = re.compile(r"банк\w* росси|\bцб рф\b|\bцб росси|центробанк\w* (рф|росси)|\bцб\b|\bцентробанк|\bрегулятор|"
                  r"bank of russia|russia'?s central bank|russian central bank|\bcbr\b")
_FED = re.compile(r"\bфрс\b|федрезерв|федеральн\w* резерв|\bfomc\b|\bfed\b|federal reserve|federal funds|"
                  r"комитет\w* по открытым рынкам|\bthe committee\b")
# чужие центробанки: «ЕЦБ», «ЦБ Турции», «Банк Англии», «Нацбанк» ...
_OTHER = re.compile(r"\bецб\b|\becb\b|\bboe\b|\bboj\b|\bsnb\b|\brba\b|\brbnz\b|\bpboc\b|\bнбк\b|нацбанк|"
                    r"народн\w* банк|national bank|bank of (england|japan|canada|korea|china)|"
                    r"(цб|центробанк\w*|банк\w*) (турции|китая|японии|индии|бразилии|англии|канады|кореи|"
                    r"казахстана|беларуси|белоруссии|украины|узбекистана|армении|грузии|швейцарии|австралии|"
                    r"новой зеландии|мексики|чили|польши|венгрии|чехии|израиля|ирана|египта|юар|аргентины|"
                    r"нигерии|индонезии|филиппин|таиланда|малайзии|вьетнама|пакистана|норвегии|швеции)")

# \b после русских глаголов: «снизилась (доходность)» — не «снизила»
# английский глагол — только с объектом: «cuts RATES» (а не «rate cuts could come»)
_OBJ_EN = r"(?= (?:its |the |key |benchmark |policy |interest )?(?:rates?|fed funds|target|borrowing))"
_CUT = r"((?:снизил[аио]?|понизил[аио]?|сократил[аио]?|уменьшил[аио]?|опустил[аио]?|"\
       r"снижен[аоы]?|понижен[аоы]?|сокращен[аоы]?|уменьшен[аоы]?)\b|"\
       r"решени\w* (?:о )?(?:снизить|понизить|сократить|уменьшить|снижении|понижении)|"\
       r"(?:объявил|заявил)\w* о (?:снижении|понижении)|"\
       r"decided to (?:lower|reduce|cut)|votes? to (?:lower|reduce|cut)|voted to (?:lower|reduce|cut)|"\
       r"\b(?:lowered|reduced|cut)\b(?= (?:to|by)\b)|"\
       r"\b(?:cuts?|lowers|lowered|reduces|reduced)\b" + _OBJ_EN + ")"
_HIKE = r"((?:повысил[аио]?|поднял[аио]?|увеличил[аио]?|повышен[аоы]?|увеличен[аоы]?|поднят[аоы]?)\b|"\
        r"решени\w* (?:о )?(?:повысить|поднять|увеличить|повышении)|"\
        r"(?:объявил|заявил)\w* о повышении|"\
        r"decided to (?:raise|increase)|votes? to (?:raise|increase)|voted to (?:raise|increase)|"\
        r"\b(?:raised|increased|hiked)\b(?= (?:to|by)\b)|"\
        r"\b(?:raises|raised|hikes|hiked|increases|increased)\b" + _OBJ_EN + ")"
_HOLD = r"((?:сохранил[аио]?|оставил[аио]?|не (?:стал[аио]? )?(?:изменил[аио]?|изменять|менять|менял[аио]?)|"\
        r"сохранен[аоы]?|оставлен[аоы]?|не изменен[аоы]?|остал(?:ась|ся|ись|ось))\b|"\
        r"решени\w* (?:о )?(?:сохранить|оставить|не менять|сохранении)|(?:объявил|заявил)\w* о сохранении|"\
        r"decided to (?:maintain|hold|keep|leave)|votes? to (?:maintain|hold|keep|leave)|"\
        r"voted to (?:maintain|hold|keep|leave)|\b(?:held|kept|maintained|left)\b(?= (?:at|unchanged|steady)\b)|"\
        r"\b(?:holds|held|maintains|maintained|keeps|kept|leaves|left)\b" + _OBJ_EN + ")"
_VERBS = [("cut", re.compile(_CUT)), ("hike", re.compile(_HIKE)), ("hold", re.compile(_HOLD))]
_RATE = re.compile(r"ставк|\brates?\b|target range|federal funds|fed funds|процентн\w* диапазон|диапазон\w* ставк")
# «прогноз/ожидания» ДО глагола -> не решение
_FORECAST = re.compile(r"ожида|прогноз|прогн\.|\bмож(ет|ут)\b|вероятн|источник|аналитик|экономист|слух|"
                       r"\bжд(ет|ут)\b|опрос|консенсус|рассматрива|обсужда|допуска|не исключ|\bесли\b|намерен|"
                       r"планир|готов|сигнализир|объявит|\bwould\b|\bcould\b|\bmay\b|\bmight\b|expect|forecast|"
                       r"\bpoll\b|economists|analysts|\bseen\b|likely|\bif\b|signal|price[sd]? in|bets?\b|futures|"
                       r"\bsays?\b|\bsaid\b|считает|полагает|по мнению")
# между глаголом и «ставкой» — другой объект: «повысил ПРОГНОЗ ставки», «снизил ставки ПО ВКЛАДАМ»
_WRONG_OBJ = re.compile(r"прогноз|оценк|ожидани|требовани|курс|доходност|норматив|сигнал|возможност|риторик|"
                        r"projection|outlook|forecast|signal|door|option")
# не ключевая ставка — проверяется РЯДОМ с найденной «ставкой» (не по всему тексту: «ЦБ снизил ключевую…,
# вслед за ним Сбер снизил ставки по вкладам» — решение ЦБ остаётся решением)
_NOT_KEY = re.compile(r"ставк\w* (?:\w+ )?(?:по|на) (?:вклад|депозит|ипотек|кредит|операци|репо|ломбард|своп|"
                      r"облигац|купон|займ)|ставк\w* дисконт|депозитн\w* ставк|ипотечн\w* ставк|"
                      r"мортгейдж|deposit rates?|mortgage|repo rate")
# «неожиданно / вопреки ожиданиям / как и ожидалось» — это про решение, а не прогноз: убрать перед проверкой
_EXPECT_OK = re.compile(r"неожиданн\w*|вопреки (?:\w+ )?ожидани\w*(?: рынка| аналитиков| экономистов)?|"
                        r"против (?:\w+ )?ожидани\w*|в соответствии с (?:\w+ )?ожидани\w*|в рамках ожидани\w*|"
                        r"как и ожидал\w*|как ожидал\w*|(?:сильнее|выше|ниже|слабее|больше|меньше) (?:\w+ )?ожидани\w*|"
                        r"unexpected\w*|as expected|than expected|in line with (?:market )?expectations|"
                        r"against expectations|surpris\w*")
_UNCHANGED = re.compile(r"без изменени|неизменн|unchanged|steady|on hold")
# упоминание ПРОШЛОГО решения, а не сегодняшнего: до глагола — «напомним, в июле ЦБ снизил…»;
# сразу после глагола — «снизил ставку на прошлом заседании…»
_MONTHS = r"(?:январе|феврале|марте|апреле|мае|июне|июле|августе|сентябре|октябре|ноябре|декабре)"
_HIST_BEFORE = re.compile(r"напомн\w*|\bранее\b|с момента|в прошлый раз|на прошлом заседании|\bв " + _MONTHS +
                          r"|\bin (?:january|february|march|april|may|june|july|august|september|october|november|"
                          r"december)\b|last (?:time|meeting|month)|at (?:the|its) (?:last|previous) meeting|since")
_HIST_AFTER = re.compile(r"^.{0,40}?(?:на прошлом заседании|в прошлый раз|\bв " + _MONTHS +
                         r"|at (?:the|its) (?:last|previous) meeting|last (?:time|month))")

_FRAC = {"1/4": 0.25, "1/2": 0.5, "3/4": 0.75, "1/8": 0.125, "3/8": 0.375, "5/8": 0.625, "7/8": 0.875}
_WORD_PP = {"четверть": 25, "половин": 50}


def _num(s):
    """'4-1/4' -> 4.25, '3,75' -> 3.75, '1/4' -> 0.25, '5' -> 5.0"""
    s = s.strip().replace(",", ".")
    m = re.fullmatch(r"(\d+)[-\s](\d/\d)", s)
    if m:
        return float(m.group(1)) + _FRAC.get(m.group(2), 0.0)
    if s in _FRAC:
        return _FRAC[s]
    try:
        return float(s)
    except ValueError:
        return None


_N = r"(\d+(?:[.,]\d+)?(?:-\d/\d)?|\d/\d)"


def _norm(text):
    t = (text or "").lower().replace("ё", "е")
    t = re.sub(r"[‐‑‒–—−]", "-", t)       # все тире/дефисы -> «-»
    t = t.replace(" ", " ").replace(" ", " ")
    return re.sub(r"\s+", " ", t)


def _bank(t):
    if _OTHER.search(t) and not re.search(r"банк\w* росси|\bцб рф\b|\bфрс\b|federal reserve|\bfomc\b|"
                                          r"bank of russia|russia'?s central bank", t):
        return "other"
    if _FED.search(t):
        return "fed"
    if _CBR.search(t):
        return "cbr"
    return None


def _change(t):
    """Размер изменения в б.п. (без знака) или None."""
    m = re.search(r"\b(?:на|by)\s*" + _N + r"\s*(?:б\.?\s?п\b|б\.п\.|базисн|bps?\b|basis point)", t)
    if m:
        v = _num(m.group(1))
        return None if v is None else int(round(v))
    m = re.search(r"\b(?:на|by)\s*" + _N + r"\s*(?:п\.?\s?п\b|п\.п\.|процентн\w* пункт|percentage point|pp\b|"
                  r"процентн\w* point)", t)
    if m:
        v = _num(m.group(1))
        return None if v is None else int(round(v * 100))
    for w, bp in _WORD_PP.items():
        if re.search(r"на " + w + r"\w* процентн", t):
            return bp
    if re.search(r"by (?:a )?quarter[- ](?:of a )?(?:percentage[- ])?point", t):
        return 25
    if re.search(r"by (?:a )?half[- ](?:a )?(?:percentage[- ])?point", t):
        return 50
    # «(-50 б.п.)», «25 bps cut» — число прямо перед б.п./bps, без «на»
    m = re.search(r"(?:^|[\s(])[-+]?\s?" + _N + r"\s*(?:б\.?\s?п\b|б\.п\.|bps?\b|basis point)", t)
    if m:
        v = _num(m.group(1))
        return None if v is None else int(round(v))
    return None


def _levels(t):
    """(нижняя, верхняя) новой ставки или (None, None)."""
    rng = (r"(?:до|на уровне|в диапазоне|диапазон\w*|to|at|range of|range|:|-|без изменений,?)\s*:?\s*" + _N +
           r"\s*%?\s*(?:-|to|до|и)\s*" + _N + r"\s*(?:%|процент|percent)")
    m = re.search(rng, t)
    if m:
        a, b = _num(m.group(1)), _num(m.group(2))
        if a is not None and b is not None and 0 <= a < b <= 40 and b - a <= 1.0:
            return a, b
    m = re.search(r"(?:до|на уровне|уровн\w*|to|at|составит|составила|=|:|-|ставк\w*|без изменений,?)\s*"
                  + _N + r"\s*(?:%|процент|percent)", t)
    if m:
        v = _num(m.group(1))
        if v is not None and 0 <= v <= 40:
            return None, v
    return None, None


def _prev(t):
    m = re.search(r"(?:пред\w*\.?|prev\w*\.?|prior)\s*:?\s*" + _N + r"\s*%?", t)
    if not m:   # «снизил ставку с 21% до 20%», «from 4.25% to 4%»
        m = re.search(r"\b(?:с|from)\s*" + _N + r"\s*%?\s*(?:годовых\s*)?(?:до|to)\s*\d", t)
    if not m:   # «до 13,5% с 14%»
        m = re.search(r"\bс\s*" + _N + r"\s*%", t)
    return _num(m.group(1)) if m else None


def parse_decision(text, rate_now=None):
    t = _norm(text)
    if not _RATE.search(t):
        return None
    bank = _bank(t)
    # календарный формат «РЕШЕНИЕ ПО СТАВКЕ ФРС: 4,00% (ПРОГНОЗ 3,75%; ПРЕД. 3,75%)» — факт до скобки
    cal = re.search(r"(?:решени\w*(?: \w+)? по\w* (?:\w+ )?ставк\w*|ключев\w* ставк\w*|rate decision|interest rate decision)"
                    r"[^0-9%]{0,30}?" + _N + r"\s*%[^()]*\((?=[^)]*(?:прогн|пред|forecast|prev|cons))", t)
    if cal:
        level = _num(cal.group(1))
        prev = _prev(t)
        if level is not None and prev is not None:
            ch = int(round((level - prev) * 100))
            return {"bank": bank, "action": "cut" if ch < 0 else "hike" if ch > 0 else "hold",
                    "change_bp": ch, "level": level, "low": None, "prev": prev}
    # ближайший к «ставке» глагол решения
    best = None
    for action, rx in _VERBS:
        for m in rx.finditer(t):
            r = _RATE.search(t, m.end())
            r_before = None
            for rb in _RATE.finditer(t[:m.start()]):
                r_before = rb
            dist_after = (r.start() - m.end()) if r else 10 ** 6
            dist_before = (m.start() - r_before.end()) if r_before else 10 ** 6
            dist = min(dist_after, dist_before)
            if dist > 45:
                continue
            if dist_after <= dist_before and _WRONG_OBJ.search(t[m.end():r.start()]):
                continue      # «повысил прогноз ставки»
            rm = r if dist_after <= dist_before else r_before
            if _NOT_KEY.search(t[max(0, rm.start() - 20): rm.end() + 40]):
                continue      # «снизил ставки по вкладам/операциям репо», «ставку дисконта»
            if best is None or m.start() < best[1].start():
                best = (action, m)
    if not best:
        if _UNCHANGED.search(t) and bank in ("cbr", "fed") and not _FORECAST.search(_EXPECT_OK.sub(" ", t)):
            best = ("hold", _UNCHANGED.search(t))
        else:
            return None
    action, m = best
    if _FORECAST.search(_EXPECT_OK.sub(" ", t[:m.start()])):
        return None           # «аналитики ожидают, что ЦБ снизил бы…», «может снизить»
    if _HIST_BEFORE.search(t[:m.start()]) or _HIST_AFTER.search(t[m.end():]):
        return None           # «напомним, в июле ЦБ снизил…», «снизил на прошлом заседании…»
    after = t[m.start():]
    ch = _change(after)
    low, level = _levels(after)
    prev = _prev(t)
    if ch is None and action == "hold":
        ch = 0
    if ch is None and level is not None:
        base = prev if prev is not None else rate_now
        if base is not None:
            ch = int(round(abs(level - base) * 100))
    if ch is not None:
        ch = -abs(ch) if action == "cut" else abs(ch) if action == "hike" else 0
        if ch == 0 and action != "hold":
            ch = None         # «снизил», а уровень равен текущему — данные противоречат: размер неизвестен
    return {"bank": bank, "action": action, "change_bp": ch, "level": level, "low": low, "prev": prev}


_ACT_RU = {"cut": "снижение", "hold": "сохранение", "hike": "повышение"}


def describe(d):
    if not d:
        return "не решение по ставке"
    bank = {"cbr": "ЦБ", "fed": "ФРС", "other": "другой банк"}.get(d.get("bank"), "банк не назван")
    s = f"{bank}: {_ACT_RU[d['action']]}"
    if d.get("change_bp"):
        s += f" на {abs(d['change_bp'])} б.п."
    if d.get("level") is not None:
        lv = ("%g" % d["level"]).replace(".", ",")
        rng = ('%g' % d['low']).replace('.', ',') + '–' if d.get('low') is not None else ''
        s += f", {'на уровне' if d['action'] == 'hold' else 'до'} {rng}{lv}%"
    return s


# ── Строгая (официальная) формулировка: «Банк России принял решение…» и т.п. ──────────────────────
# Режим «только официальная формулировка» у наблюдателя: пересказы агентств («ЦБ РФ СНИЗИЛ…»)
# пропускаются, срабатывает только текст самого регулятора (заголовок или первое предложение тела).
_OFFICIAL = {
    "cbr": re.compile(r"банк\w* росси\w* приня\w* решени|совет\w* директоров банк\w* росси\w*.{0,60}?приня\w* решени"),
    "fed": re.compile(r"decided to (?:lower|raise|maintain|reduce|increase|keep|hold)|"
                      r"(?:the committee|fomc|\bфрс\b|федеральн\w* резервн\w*|комитет\w* по открытым рынкам)"
                      r".{0,80}?приня\w* решени|fomc statement"),
    "minfin": re.compile(r"о нефтегазовых доходах и проведении операций|минфин\w* росси\w* сообща|"
                         r"операци\w* будут проводиться в период|ежедневн\w* объем\w* (?:покупк|продаж)\w* иностранн"),
}


def official(text, bank):
    """Текст — официальная формулировка регулятора (а не пересказ агентства)?"""
    rx = _OFFICIAL.get(bank)
    return bool(rx and rx.search(_norm(text)))


# ── Минфин: операции с валютой и золотом по бюджетному правилу ────────────────────────────────────
# Официально (minfin.gov.ru, третий рабочий день месяца, 12:00 МСК): «О нефтегазовых доходах и проведении
# операций по покупке/продаже иностранной валюты и золота на внутреннем валютном рынке» — «…совокупный
# объем средств, направляемых на покупку иностранной валюты и золота, составит 136,17 млрд руб. Операции
# будут проводиться в период с 7 августа 2026 года по 4 сентября 2026 года, соответственно, ежедневный
# объем покупки иностранной валюты и золота составит в эквиваленте 6,5 млрд руб.»
def parse_minfin(text):
    """-> None | {"action": "buy"|"sell"|"pause", "daily": млрд руб/день со знаком (покупка +, продажа −),
    "total": млрд руб или None}."""
    t = _norm(text)
    # в официальном сообщении слова «Минфин» нет: «О нефтегазовых доходах и проведении операций по покупке/продаже…»
    if not re.search(r"минфин|министерств\w* финансов|бюджетн\w* правил|нефтегазов\w* доход|"
                     r"операци\w* по покупке/продаже иностранн", t):
        return None
    if not re.search(r"валют|юан|золот", t):
        return None
    if re.search(r"приостан|не будет (?:проводить|покупать|продавать)|отказ\w* от (?:покупк|продаж)", t) and \
            not re.search(r"возобнов", t):
        return {"action": "pause", "daily": 0.0, "total": 0.0}
    if _FORECAST.search(t.split("минфин")[0] if "минфин" in t else ""):
        return None
    # направление: сперва точные фразы официального текста («направляемых на покупку», «ежедневный объем
    # продажи»), т.к. в заголовке есть оба слова («по покупке/продаже»)
    m = re.search(r"направля\w* на (покупк|продаж)|ежедневн\w* объем\w* (покупк|продаж)", t)
    if m:
        action = "buy" if (m.group(1) or m.group(2)).startswith("покупк") else "sell"
    else:
        t_dir = t.replace("покупке/продаже", " ")
        sell = bool(re.search(r"продаж\w*|прода(?:ст|вать|ет)", t_dir))
        buy = bool(re.search(r"покупк\w*|купит|покупать|покупает|приобрет", t_dir))
        if not (buy or sell):
            return None
        action = "sell" if sell and not buy else "buy" if buy and not sell else (
            "sell" if t_dir.find("продаж") < t_dir.find("покупк") and "продаж" in t_dir else "buy")
    daily = None
    m = re.search(r"ежедневн\w* объем\w*[^0-9]{0,80}?" + _N + r"\s*млрд", t) or \
        re.search(_N + r"\s*млрд\.?\s*(?:руб\w*\.?)?\s*(?:в день|ежедневно|в сутки)", t) or \
        re.search(r"(?:в день|ежедневно)[^0-9]{0,30}?" + _N + r"\s*млрд", t)
    if m:
        daily = _num(m.group(1))
    total = None
    m = re.search(r"(?:совокупн\w* объем\w*|всего|итого)[^0-9]{0,80}?" + _N + r"\s*млрд(?!\.?\s*(?:руб\w*\.?)?\s*в день)", t)
    if m:
        total = _num(m.group(1))
    if daily is None:
        return {"action": action, "daily": None, "total": total}
    return {"action": action, "daily": daily if action == "buy" else -daily, "total": total}


def describe_minfin(d):
    if not d:
        return "не операции Минфина"
    if d["action"] == "pause":
        return "Минфин: операции с валютой приостановлены"
    act = "покупка" if d["action"] == "buy" else "продажа"
    s = f"Минфин: {act} валюты"
    if d.get("daily") is not None:
        s += f" {('%g' % abs(d['daily'])).replace('.', ',')} млрд руб. в день"
    if d.get("total"):
        s += f" (всего {('%g' % d['total']).replace('.', ',')} млрд)"
    return s
