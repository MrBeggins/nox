# -*- coding: utf-8 -*-
"""
Решения по дивидендам из раскрытий ЦРКИ (e-disclosure) в ленте: «Решения совета директоров (наблюдательного
совета)» — заголовок пустой, суть в длинном теле (до нескольких тысяч символов):
  «выплатить (объявить) дивиденды … в размере 33 (Тридцать три) рубля 00 копеек на одну обыкновенную акцию»,
  «владельцам привилегированных акций … в размере 20 руб 00 коп на одну акцию»,
  «утвердить 1 декабря 2026 года … в качестве даты, на которую определяются лица, имеющие право на получение
   дивидендов» / «… даты составления списка лиц, имеющих право на получение дивидендов 30 ноября 2026 года».
Общая сумма («3 595 746 500 (три миллиарда …) рублей») — не на акцию, её не берём.
parse(text, body, tickers) -> {who, ticker, amount, by_type, period, record, verb, none} | None.
"""
import re

_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
           "ноября", "декабря")
# сумма НА АКЦИЮ: число [ (прописью) ] руб[ NN коп] … на одну/каждую [обыкновенную|привилегированную] акцию
_PER_SHARE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:\([^)]{0,120}\)\s*)?(?:руб\w*|₽|р\.)\s*(?:(\d{1,2})\s*коп\w*\s*)?"
    r"(?:\([^)]{0,60}\)\s*)?на\s+(?:одну|каждую|1|одну\s+размещенную)?\s*"
    r"(?:(обыкновенн|привилегированн)\w*\s+)?(?:именн\w+\s+)?акци", re.I)
_TYPE_NEAR = re.compile(r"(обыкновенн|привилегированн)\w*", re.I)
_NONE = re.compile(r"не\s+(?:выплачивать|объявлять|выплатить|объявить)\s+дивиденд|дивиденд\w*\s+не\s+(?:выплачивать|объявлять)"
                   r"|не\s+направлять\s+\w*\s*(?:прибыль|средства)\w*\s+на\s+выплату\s+дивиденд|без\s+выплаты\s+дивиденд", re.I)
_RIGHT = re.compile(r"(?:лиц\w*,?\s+имеющ\w+\s+право\s+на\s+получение\s+дивиденд|дат\w*\s+отсечк|"
                    r"закрыти\w+\s+реестр)", re.I)
_DATE_W = re.compile(r"(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})", re.I)
_DATE_N = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{4})(?![\d.])")


def _dates(s):
    """[(позиция, «30 ноября 2026»)] из текста — словами и цифрами."""
    out = [(m.start(), f"{int(m.group(1))} {m.group(2).lower()} {m.group(3)}") for m in _DATE_W.finditer(s)]
    for m in _DATE_N.finditer(s):
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if 1 <= mo <= 12 and 1 <= d <= 31:
            out.append((m.start(), f"{d} {_MONTHS[mo - 1]} {y}"))
    return out


def record_date(s):
    """Дата отсечки: ближайшая дата к «лица, имеющие право на получение дивидендов» (до или после)."""
    best = None
    ds = _dates(s)
    for m in _RIGHT.finditer(s):
        for pos, d in ds:
            dist = abs(pos - m.start())
            if dist <= 260 and (best is None or dist < best[0]):
                best = (dist, d)
    return best[1] if best else ""


_PER = r"((?:\d+|перв\w+|втор\w+|трет\w+|четверт\w+|девят\w+)\s+(?:месяц\w*|квартал\w*|полугоди\w*)\s+\d{4}\s+года|\d{4}\s+год\w*)"


def period(low):
    """Период выплаты: «по результатам/по итогам …» важнее всего; затем «прибыль прошлых лет»; затем «за …»
    (иначе у Самараэнерго «- за 2007 год в размере …» — разбивка суммы прошлых лет — становилась периодом)."""
    m = re.search(r"(?:по результатам|по итогам)\s+" + _PER, low)
    if not m and re.search(r"нераспредел\w+\s+прибыл\w*\s+(?:общества\s+)?прошлых\s+лет", low):
        return "из нераспределённой прибыли прошлых лет"
    if not m:
        m = re.search(r"за\s+" + _PER, low)
    if m:
        per = m.group(1)
        for a, b in (("первого полугодия", "первое полугодие"), ("второго полугодия", "второе полугодие"),
                     ("первого квартала", "первый квартал"), ("второго квартала", "второй квартал"),
                     ("третьего квартала", "третий квартал"), ("четвертого квартала", "четвертый квартал"),
                     ("девяти месяцев", "9 месяцев")):
            per = per.replace(a, b)
        per = re.sub(r"^(\d{4}) года$", r"\1 год", per)
        return "за " + per
    return ""


def parse(text, body, tickers=None, names=None):
    """Решение совета/собрания о дивидендах -> dict или None (не решение о дивидендах)."""
    full = re.sub(r"\s+", " ", f"{text or ''} {body or ''}")
    low = full.lower().replace("ё", "е")
    if "дивиденд" not in low:
        return None
    head = (text or "").lower()
    decided = bool(re.search(r"решени|рекомендова|утвердил|принят", head + " " + low[:400]))
    if not decided or re.search(r"рассмотрит|проведение заседания|повестк\w* дня", head):
        return None                      # анонс заседания / повестка — не решение
    gsm = bool(re.search(r"общ\w+\s+собрани|акционер\w+\s+(?:утвердил|принял)", head))
    tk = (tickers or [""])[0].upper() if tickers else ""
    who = (names or {}).get(tk) or ""
    if not who:
        m = re.search(r"(?:ПАО|МКПАО|АО|ООО)\s+[«\"]([^»\"]{2,40})[»\"]", full)
        who = m.group(1) if m else (tk or "")
    none = bool(_NONE.search(full))
    by_type = {}
    for m in _PER_SHARE.finditer(full):
        rub = float(m.group(1).replace(",", "."))
        if m.group(2):
            rub += int(m.group(2)) / 100
        kind = (m.group(3) or "").lower()
        if not kind:   # тип акции назван раньше: «владельцам привилегированных акций … 20 руб на одну акцию»
            before = full[max(0, m.start() - 160):m.start()]
            near = list(_TYPE_NEAR.finditer(before))
            kind = near[-1].group(1).lower() if near else ""
        key = "pref" if kind.startswith("привил") else "ord" if kind.startswith("обыкн") else "any"
        by_type.setdefault(key, rub)
    if not by_type and not none:
        return None                      # о дивидендах, но ни суммы на акцию, ни отказа — не наш случай
    amount = 0.0 if none and not by_type else (by_type.get("ord") or by_type.get("any") or by_type.get("pref"))
    rec = "" if none and not by_type else record_date(full)
    verb = ("акционеры утвердили" if gsm else
            "совет директоров рекомендовал" if re.search(r"рекоменд", low) else "совет директоров решил выплатить")
    return {"who": who, "ticker": tk, "amount": amount, "by_type": by_type, "period": period(low),
            "record": rec, "verb": verb, "none": none and not by_type}


def _rub(x):
    if 0 < x < 1:                        # 0,42 руб -> «42 коп» (иначе «ноль целых сорок две сотых рубля»)
        return f"{round(x * 100)} копеек"
    s = ("%.2f" % x).rstrip("0").rstrip(".").replace(".", ",")
    return f"{s} руб"


def speak(d):
    """«Циан: совет директоров рекомендовал дивиденды 33 руб на акцию за 9 месяцев 2026 года, отсечка 30 ноября.»"""
    if d["none"]:
        return f"{d['who']}: {d['verb']} не выплачивать дивиденды{(' ' + d['period']) if d['period'] else ''}."
    bt = d["by_type"]
    if "ord" in bt and "pref" in bt and abs(bt["ord"] - bt["pref"]) > 1e-9:
        amt = f"{_rub(bt['ord'])} на обыкновенную и {_rub(bt['pref'])} на привилегированную акцию"
    elif "ord" in bt and "pref" in bt:
        amt = f"{_rub(bt['ord'])} на обыкновенную и привилегированную акцию"
    else:
        amt = f"{_rub(d['amount'])} на акцию"
    s = f"{d['who']}: {d['verb']} дивиденды {amt}"
    if d["period"]:
        s += f" {d['period']}"
    if d["record"]:
        rec = re.sub(r"\s+\d{4}$", "", d["record"]) if d["record"].endswith(str(__import__('time').localtime().tm_year)) \
            else d["record"]
        s += f", отсечка {rec}"
    return s + "."
