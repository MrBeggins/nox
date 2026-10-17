# -*- coding: utf-8 -*-
"""Русские числительные словами -> цифры (как numwords.rs в ядре): Vosk пишет «пятьсот сорок пять», а разбор
команд ждёт 545. digits("поменяй объём на десять лотов") -> "поменяй объём на 10 лотов"."""
import re

_U = {}
for forms, v in [("ноль нуля нулю нулём нулем", 0), ("один одна одно одного одной одному одним одну", 1),
                 ("два две двух двум двумя", 2), ("три трёх трех трём трем тремя", 3),
                 ("четыре четырёх четырех четырём четырем четырьмя", 4), ("пять пяти пятью", 5),
                 ("шесть шести шестью", 6), ("семь семи семью", 7), ("восемь восьми восемью восьмью", 8),
                 ("девять девяти девятью", 9), ("десять десяти десятью", 10), ("одиннадцать одиннадцати", 11),
                 ("двенадцать двенадцати", 12), ("тринадцать тринадцати", 13), ("четырнадцать четырнадцати", 14),
                 ("пятнадцать пятнадцати", 15), ("шестнадцать шестнадцати", 16), ("семнадцать семнадцати", 17),
                 ("восемнадцать восемнадцати", 18), ("девятнадцать девятнадцати", 19)]:
    for f in forms.split():
        _U[f] = v
_T = {}
for forms, v in [("двадцать двадцати", 20), ("тридцать тридцати", 30), ("сорок сорока", 40),
                 ("пятьдесят пятидесяти", 50), ("шестьдесят шестидесяти", 60), ("семьдесят семидесяти", 70),
                 ("восемьдесят восьмидесяти", 80), ("девяносто девяноста", 90)]:
    for f in forms.split():
        _T[f] = v
_H = {}
for forms, v in [("сто ста", 100), ("двести двухсот", 200), ("триста трёхсот трехсот", 300),
                 ("четыреста четырёхсот четырехсот", 400), ("пятьсот пятисот", 500), ("шестьсот шестисот", 600),
                 ("семьсот семисот", 700), ("восемьсот восьмисот", 800), ("девятьсот девятисот", 900)]:
    for f in forms.split():
        _H[f] = v


def _scale(w):
    if w.startswith("тысяч") and not w.startswith("тысячн"):
        return 1000
    if w.startswith("миллион"):
        return 10 ** 6
    return None


def _isnum(w):
    return w in _U or w in _T or w in _H or _scale(w) is not None


def _read(ws, i):
    total, cur, j, last, any_ = 0, 0, i, 0, False
    while j < len(ws):
        w = ws[j]
        if w in _U and _U[w] == 0:
            # «ноль» — всегда отдельное число: «в девять ноль пять» = 9 0 5 (09:05), а не 9 5
            if any_: break
            return 0, 1
        if w in _H:
            if last and last <= 3 and cur: break
            cur += _H[w]; last = 3
        elif w in _T:
            if last and last <= 2 and cur: break
            cur += _T[w]; last = 2
        elif w in _U:
            if last and last <= 1 and cur: break
            if _U[w] >= 10 and last == 2: break
            cur += _U[w]; last = 1
        elif _scale(w):
            total += max(cur, 1) * _scale(w); cur, last = 0, 0
        else:
            break
        any_ = True; j += 1
    return (total + cur, j - i) if any_ else (None, 0)


def _fmt(v):
    return str(int(v)) if float(v).is_integer() else ("%.4f" % v).rstrip("0").rstrip(".")


def digits(text):
    ws = (text or "").split()
    out, i = [], 0
    while i < len(ws):
        w = ws[i].lower()
        if w in ("полтора", "полторы", "полутора"):
            if i + 1 < len(ws) and _scale(ws[i + 1].lower()):
                out.append(_fmt(1.5 * _scale(ws[i + 1].lower()))); i += 2; continue
            out.append("1.5"); i += 1; continue
        if not _isnum(w):
            out.append(ws[i]); i += 1; continue
        v, n = _read([x.lower() for x in ws], i)
        j = i + n
        low = [x.lower() for x in ws]
        if j + 1 < len(ws) and low[j] == "с" and low[j + 1].startswith("половин"):
            v += 0.5; j += 2
        if j < len(ws) and low[j] in ("запятая", "точка"):
            m, k = _read(low, j + 1)
            if m is not None:
                v = float(f"{_fmt(v)}.{_fmt(m)}"); j += 1 + k
        out.append(_fmt(v)); i = j
    s = " ".join(out)
    return re.sub(r"\b(\d+) (рубл\w*) (\d{1,2}) (копе\w*)", lambda m: f"{m[1]}.{int(m[3]):02d} {m[2]}", s)
