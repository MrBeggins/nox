# -*- coding: utf-8 -*-
"""
Единый русский нормализатор текста для ВСЕХ голосовых движков Nox (Silero, XTTS, F5).
Задача — чтобы голос не нёс белиберду и говорил грамотно:
  * латиница -> по-русски: словарь известных названий (Reuters -> Рейтер), тикеры по буквам
    (GAZP -> Джи Эй Зед Пи), остальное — транслитерация (Silero латиницу не читает вовсе);
  * ЗАГЛАВНЫЕ заголовки ленты -> обычный регистр (аббревиатуры ЦБ/США/ОФЗ остаются);
  * числа -> слова С ПАДЕЖАМИ по предлогу и согласованием существительного:
    «до 21%» -> «до двадцати одного процента», «10 лот» -> «десять лотов»,
    «в 2026 году» -> «в две тысячи двадцать шестом году», «8 октября» -> «восьмого октября»;
  * разряды «10 639» -> одно число; валюты/сокращения (₽, $, руб., млрд, млн, тыс., п.п.);
  * мусор для голоса (ссылки, эмодзи, #, @) убирается.
Морфология — pymorphy3 (если не установлен — упрощённый режим без падежей).
Использование:  import nox_textnorm;  text = nox_textnorm.normalize(text)
"""
import re
import time

try:
    from num2words import num2words as _n2w_raw
except Exception:
    _n2w_raw = None

try:
    import pymorphy3
    _MORPH = pymorphy3.MorphAnalyzer()
except Exception:
    _MORPH = None


def _n2w(n, ordinal=False):
    if _n2w_raw is None:
        return str(n)
    try:
        w = _n2w_raw(int(n), lang="ru", to="ordinal" if ordinal else "cardinal")
    except Exception:
        return str(n)
    # «одна тысяча шестьдесят три» -> «тысяча шестьдесят три»
    return re.sub(r"^одна тысяча", "тысяча", w) if not ordinal else re.sub(r"^одна тысяча", "тысяча", w)


# ------------------------------------------------------------------ мусор для голоса
_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]+")


def _clean(t):
    t = _URL.sub(" ", t)
    t = _EMOJI.sub(" ", t)
    t = re.sub(r"[#@](?=\w)", "", t)                       # #дивиденды -> дивиденды
    t = t.replace(" ", " ").replace(" ", " ").replace(" ", " ")
    t = re.sub(r"\$(?=[A-Za-z])", "", t)                    # $SRU6 -> SRU6 (тикер, а не доллар)
    t = re.sub(r"\s+[-–]\s+", " — ", t)                     # дефис-тире -> пауза
    return t


# ------------------------------------------------------------------ ЗАГЛАВНЫЕ -> обычный регистр
_ABBR = {
    "РФ": "Эр Эф", "СМИ": "сми", "СБП": "Эс Бэ Пэ", "НПЗ": "Эн Пэ Зэ",
    "ФРС": "Эф Эр Эс", "ВВП": "Вэ Вэ Пэ", "ВПК": "Вэ Пэ Ка", "ВСУ": "Вэ Эс У",
    "МИД": "Мид", "ФНС": "Эф Эн Эс", "ФСБ": "Эф Эс Бэ", "МВД": "Эм Вэ Дэ",
    "ЦБ": "Центробанк", "ГД": "Госдума", "ЕС": "Евросоюз", "ЖКХ": "Жэ Ка Ха",
    "ОФЗ": "О Эф Зэ", "НКЦ": "Эн Ка Цэ", "МСФО": "Эм Эс Эф О", "РСБУ": "Эр Эс Бэ У",
    "НДФЛ": "Эн Дэ Эф Эл", "НДС": "Эн Дэ Эс", "ЦФА": "Цэ Эф А", "ЕЦБ": "Е Цэ Бэ",
    "ОПЕК": "ОПЕК", "МОЭКС": "Мосбиржа", "СД": "совет директоров", "США": "Сэ Шэ А",
    "ВТО": "Вэ Тэ О", "АЭС": "А Э Эс", "ГЭС": "Гэ Э Эс", "ЕАЭС": "Е А Э Эс",
    "МВФ": "Эм Вэ Эф", "ООН": "О О Н", "НАТО": "НАТО", "ФАС": "Фас", "ГОСА": "Госа",
    "ВОСА": "Воса", "ЗПИФ": "Зэ Пиф", "ПИФ": "Пиф", "ИИС": "И И Эс", "ГК": "Гэ Ка",
    "ПАО": "Пао", "АО": "А О", "ООО": "О О О", "ТАСС": "ТАСС", "РБК": "Эр Бэ Ка",
    "ЦИК": "Цик", "МЭР": "Мэр", "ДТП": "Дэ Тэ Пэ",
}
_ABBR_RE = [(re.compile(r"(?<![А-Яа-яЁёA-Za-z])" + re.escape(k) + r"(?![А-Яа-яЁёA-Za-z])"), v)
            for k, v in _ABBR.items()]


def _decaps(t):
    """Слова ЗАГЛАВНЫМИ -> строчные: 5+ букв всегда, 3-4 буквы — если это обычное слово (НЕТ, ДЛЯ),
    а не аббревиатура (ЦБ, США, ОФЗ остаются)."""
    def low(m):
        w = m.group(0)
        if w in _ABBR:
            return w
        if len(w) >= 5:
            return w.lower()
        if _MORPH:
            p = _MORPH.parse(w.lower())[0]
            if p.is_known and "Abbr" not in p.tag and "Init" not in p.tag:
                return w.lower()
        return w
    letters = [c for c in t if c.isalpha() and "а" <= c.lower() <= "я"]
    caps = len(letters) >= 12 and sum(c.isupper() for c in letters) / len(letters) > 0.7
    t = re.sub(r"(?<![А-Яа-яЁё])[А-ЯЁ]{3,}(?![а-яё])", low, t)
    if caps:
        # заголовок капсом: «С ИРАНОМ», «НА 2%» — предлоги и союзы тоже строчными; ЦБ, ЕС, РФ — как есть
        t = re.sub(r"(?<![А-Яа-яЁё])[А-ЯЁ]{1,2}(?![А-Яа-яЁё])", lambda m: m.group(0) if m.group(0) in _ABBR else m.group(0).lower(), t)
        t = t[:1].upper() + t[1:]
    return t


def _expand_abbr(t):
    # «ЦБ РФ», «Минфин РФ» -> «… России» (раньше «Центробанк Эр Эф»)
    t = re.sub(r"(?<![А-Яа-яЁё])(ЦБ|Банк|Минфин|МИД|Минэнерго|Минэкономразвития|Минпромторг|Минтранс|Минцифры|"
               r"Правительство|Президент|Госдума|Генпрокуратура)\s+РФ(?![А-Яа-яЁё])", r"\1 России", t)
    # «в РФ», «против РФ», «с РФ» — по падежу предлога; без предлога — «Россия»
    _rf = {"России": ("в", "во", "о", "об", "при", "из", "для", "у", "от", "до", "против", "кроме", "без", "после",
                      "вокруг", "к", "ко", "по", "территории", "экономики", "президент", "граждан"),
           "Россией": ("с", "со", "между", "над", "под", "перед"), "Россию": ("на", "про", "через", "в", "за")}
    def _rf_case(m):
        w = m.group(1).lower()
        for form, preps in _rf.items():
            if w in preps:
                return f"{m.group(1)} {form}"
        return f"{m.group(1)} России"
    t = re.sub(r"(?<![А-Яа-яЁё])([А-Яа-яЁё]+)\s+РФ(?![А-Яа-яЁё])", _rf_case, t)
    t = re.sub(r"(?<![А-Яа-яЁё])РФ(?![А-Яа-яЁё])", "Россия", t)
    for rx, rep in _ABBR_RE:
        t = rx.sub(rep, t)
    return t


# ------------------------------------------------------------------ латиница -> по-русски
_LATIN_WORDS = {
    # источники и СМИ
    "reuters": "Рейтер", "bloomberg": "Блумберг", "interfax": "Интерфакс", "tass": "ТАСС",
    "rbc": "Эр Бэ Ка", "cnbc": "Си Эн Би Си", "cnn": "Си Эн Эн", "bbc": "Би Би Си", "ap": "Эй Пи",
    "the new york times": "Нью-Йорк Таймс", "new york times": "Нью-Йорк Таймс", "nyt": "Нью-Йорк Таймс",
    "financial times": "Файнэншл Таймс", "wall street journal": "Уолл-стрит джорнал", "wsj": "Уолл-стрит джорнал",
    "the economist": "Экономист", "politico": "Политико", "axios": "Аксиос", "frankmedia": "Фрэнк медиа",
    "brief": "бриф", "breaking": "срочно", "update": "обновление", "telegram": "телеграм",
    "smart-lab": "Смартлаб", "smartlab": "Смартлаб", "tradingview": "Трейдинг вью", "pulse": "Пульс",
    # рынки и инструменты
    "brent": "Брент", "wti": "Дабл Ю Ти Ай", "urals": "Юралс", "imoex": "индекс Мосбиржи",
    "imoex2": "индекс Мосбиржи", "rts": "РТС", "rtsi": "индекс РТС", "moex": "Мосбиржа",
    "nasdaq": "Насдак", "s&p": "Эс энд Пи", "s&p 500": "Эс энд Пи пятьсот", "dow jones": "Доу-Джонс",
    "usd": "доллар", "eur": "евро", "cny": "юань", "rub": "рубль", "btc": "биткоин", "bitcoin": "биткоин",
    "eth": "эфир", "ethereum": "эфириум", "opec": "ОПЕК", "opec+": "ОПЕК плюс", "fed": "ФРС", "ecb": "ЕЦБ",
    "ipo": "Ай Пи О", "spo": "Эс Пи О", "etf": "И Ти Эф", "ceo": "гендиректор", "gdp": "ВВП", "cpi": "Си Пи Ай",
    "ppi": "Пи Пи Ай", "pce": "Пи Си И", "jolts": "Джолтс", "nonfarm": "нонфарм", "nfp": "нонфарм",
    # компании
    "apple": "Эппл", "tesla": "Тесла", "nvidia": "Энвидиа", "microsoft": "Майкрософт", "google": "Гугл",
    "alphabet": "Альфабет", "amazon": "Амазон", "meta": "Мета", "openai": "Опен Эй Ай", "intel": "Интел",
    "amd": "Эй Эм Ди", "boeing": "Боинг", "netflix": "Нетфликс", "yandex": "Яндекс", "ozon": "Озон",
    "vk": "ВК", "x5": "Икс пять", "trump": "Трамп", "biden": "Байден", "putin": "Путин", "iphone": "айфон", "android": "андроид", "visa": "Виза", "mastercard": "Мастеркард",
}
_TICKERS = {
    "sber": "Сбер", "sberp": "Сбер префы", "gazp": "Газпром", "lkoh": "Лукойл", "nvtk": "Новатэк",
    "rosn": "Роснефть", "gmkn": "Норникель", "tatn": "Татнефть", "tatnp": "Татнефть префы", "snGs": "Сургут",
    "sngs": "Сургутнефтегаз", "sngsp": "Сургут префы", "plzl": "Полюс", "vtbr": "ВТБ", "magn": "ММК",
    "chmf": "Северсталь", "nlmk": "НЛМК", "alrs": "Алроса", "mtss": "МТС", "moex": "Мосбиржа",
    "ydex": "Яндекс", "ozon": "Озон", "t": "Т-Технологии", "tcsg": "Т-Технологии", "smlt": "Самолёт",
    "pikk": "ПИК", "afks": "АФК Система", "irao": "Интер РАО", "hydr": "РусГидро", "fees": "Россети",
    "aflt": "Аэрофлот", "phor": "ФосАгро", "rual": "Русал", "mgnt": "Магнит", "five": "Икс пять",
    "x5": "Икс пять", "sibn": "Газпром нефть", "trnfp": "Транснефть", "rtkm": "Ростелеком", "segz": "Сегежа",
    "posi": "Позитив", "astr": "Астра", "hhru": "ХэдХантер", "head": "ХэдХантер", "spbe": "СПБ Биржа",
    "flot": "Совкомфлот", "upro": "Юнипро", "mtlr": "Мечел", "mtlrp": "Мечел префы", "bspb": "Банк Санкт-Петербург",
    "svcb": "Совкомбанк", "rnft": "Русснефть", "ugld": "ЮГК", "vkco": "ВК", "lsrg": "ЛСР", "fesh": "ДВМП",
    "lent": "Лента", "mvid": "М.Видео", "aqua": "Инарктика", "beld": "Белуга", "rage": "Ренессанс",
}
_LATIN_WORDS.update({k.lower(): v for k, v in _TICKERS.items()})

# многословные названия — до разбора по словам
_LATIN_PHRASES = sorted((k for k in _LATIN_WORDS if " " in k), key=len, reverse=True)

_LETTER = {"a": "Эй", "b": "Би", "c": "Си", "d": "Ди", "e": "И", "f": "Эф", "g": "Джи",
           "h": "Эйч", "i": "Ай", "j": "Джей", "k": "Кей", "l": "Эл", "m": "Эм", "n": "Эн",
           "o": "Оу", "p": "Пи", "q": "Кью", "r": "Ар", "s": "Эс", "t": "Ти", "u": "Ю",
           "v": "Ви", "w": "Дабл Ю", "x": "Экс", "y": "Уай", "z": "Зед"}

# грубая фонетическая транслитерация (лучше, чем латиница, которую Silero просто не читает)
_TR2 = [("sch", "ш"), ("tch", "ч"), ("sh", "ш"), ("ch", "ч"), ("th", "т"), ("ph", "ф"), ("kh", "х"),
        ("zh", "ж"), ("ts", "ц"), ("oo", "у"), ("ee", "и"), ("ea", "и"), ("ou", "ау"), ("ay", "эй"),
        ("ey", "эй"), ("ck", "к"), ("qu", "кв"), ("ie", "и"), ("ai", "эй"), ("oa", "оу"), ("wh", "у")]
_TR1 = {"a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х", "i": "и",
        "j": "дж", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "q": "к", "r": "р",
        "s": "с", "t": "т", "u": "у", "v": "в", "w": "у", "x": "кс", "y": "и", "z": "з"}


def _translit(w):
    s = w.lower()
    out, i = "", 0
    while i < len(s):
        for a, b in _TR2:
            if s.startswith(a, i):
                out += b; i += len(a); break
        else:
            ch = s[i]
            if ch == "c" and i + 1 < len(s) and s[i + 1] in "eiy":
                out += "с"
            elif ch == "e" and i == len(s) - 1 and len(s) > 3:
                pass                                   # немая e в конце: Rose -> Роз
            elif ch == "y" and i == 0:
                out += "й"
            else:
                out += _TR1.get(ch, ch)
            i += 1
    return out[:1].upper() + out[1:] if w[:1].isupper() else out


def _spell(tok):
    """Тикер/аббревиатура: GAZP -> «Джи Эй Зед Пи», SRU6 -> «Эс Ар Ю шесть»."""
    parts = []
    for chunk in re.findall(r"[A-Za-z]+|\d+", tok):
        if chunk.isdigit():
            parts.append(_n2w(int(chunk)))
        else:
            parts.extend(_LETTER.get(c.lower(), c) for c in chunk)
    return " ".join(parts)


def _latin(t):
    t = re.sub(r"(?i)индекс[ау]?\s+imoex2?(?![A-Za-z0-9])", "индекс Мосбиржи", t)
    for ph in _LATIN_PHRASES:
        t = re.sub(r"(?<![A-Za-z])" + re.escape(ph) + r"(?![A-Za-z])", _LATIN_WORDS[ph], t, flags=re.I)

    def word(m):
        w = m.group(0)
        known = _LATIN_WORDS.get(w.lower())
        if known:
            return known
        has_digit = any(c.isdigit() for c in w)
        letters = re.sub(r"[^A-Za-z]", "", w)
        vowels = sum(c in "aeiouyAEIOUY" for c in letters)
        # тикеры/аббревиатуры: заглавные до 5 букв, буквы с цифрами, слова без гласных
        if has_digit or (w.isupper() and len(letters) <= 5) or vowels == 0:
            return _spell(w)
        return _translit(w)

    return re.sub(r"(?<![A-Za-zА-Яа-яЁё])[A-Za-z][A-Za-z0-9&+'’-]*(?<![-'’])", word, t)


# ------------------------------------------------------------------ единицы и сокращения -> слова
def _units(t):
    num = r"(\d[\d ]*(?:[.,]\d+)?)"
    # время 18:30 -> «18 час 30 минута» (дальше согласуется с числом и падежом: «до восемнадцати часов тридцати минут»)
    t = re.sub(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])",
               lambda m: f"{int(m.group(1))} час" + ("" if m.group(2) == "00" else f" {int(m.group(2))} минута"), t)
    t = re.sub(r"(?<=\d)\s*[-–—]\s*(?=\d)", " — ", t)             # 480-600 -> «480 — 600» (пауза)
    t = re.sub(r"(?<![\w])\+\s*(?=\d)", "плюс ", t)              # +2,3% -> плюс ...
    t = re.sub(r"(?<![\w\d])[-−–]\s*(?=\d)", "минус ", t)        # -1,5% -> минус ... (диапазон 480-600 не трогаем)
    def _rub(m):
        k = (m.group(2) + "0")[:2]
        if int(m.group(1).replace(" ", "") or 0) == 0:
            return f"{int(k)} копейка"                    # «0,5 руб.» -> «пятьдесят копеек»
        return f"{m.group(1)} рубль" + ("" if k == "00" else f" {int(k)} копейка")
    t = re.sub(r"(?<![\d.,])(\d[\d ]*)[.,](\d{1,2})\s*(?:руб\.?|рубл[а-яё]*|₽)(?![А-Яа-яЁё])", _rub, t)
    t = re.sub(num + r"\s*%", r"\1 процент", t)
    t = re.sub(r"\$\s*" + num, r"\1 доллар", t)                 # $103,91 -> 103,91 доллар
    t = re.sub(r"€\s*" + num, r"\1 евро", t)
    t = re.sub(num + r"\s*\$", r"\1 доллар", t)
    t = re.sub(num + r"\s*₽", r"\1 рубль", t)
    t = re.sub(num + r"\s*€", r"\1 евро", t)
    t = re.sub(r"(?<!\d)((?:19|20)\d{2})\s*г\.(?=\s+[а-яё])", r"\1 год", t)
    t = re.sub(r"(?<!\d)((?:19|20)\d{2})\s*г\.?(?![А-Яа-яЁё])", r"\1 год", t)
    abbr = [(r"трлн\.?", "триллион"), (r"млрд\.?", "миллиард"), (r"млн\.?", "миллион"), (r"тыс\.", "тысяча"),
            (r"руб\.?", "рубль"), (r"коп\.", "копейка"), (r"долл\.", "доллар"), (r"шт\.", "штука"), (r"мес\.", "месяц"),
            (r"п\.\s?п\.", "процентный пункт"), (r"б\.\s?п\.", "базисный пункт"), (r"гг\.", "годы"), (r"г\.", "год")]
    for a, b in abbr:
        t = re.sub(r"(\d\s*)" + a + r"(?![А-Яа-яЁё])", r"\1" + b, t)
    for a, b in ((r"руб\.?", "рублей"), (r"долл\.", "долларов"), (r"коп\.", "копеек"), (r"млрд\.?", "миллиардов"),
                 (r"млн\.?", "миллионов"), (r"трлн\.?", "триллионов"), (r"тыс\.", "тысяч"),
                 (r"[Мм][Сс][Кк]", "по Москве")):
        t = re.sub(r"(?<![А-Яа-яЁё])" + a + r"(?![А-Яа-яЁё])", b, t)
    t = t.replace("%", " процентов").replace("₽", " рублей").replace("$", " долларов").replace("€", " евро")
    return t.replace("№", " номер ")


# ------------------------------------------------------------------ числа -> слова с падежами
_PREP_CASE = {
    "до": "gent", "от": "gent", "из": "gent", "без": "gent", "у": "gent", "около": "gent", "более": "gent",
    "менее": "gent", "свыше": "gent", "после": "gent", "для": "gent", "кроме": "gent", "против": "gent",
    "с": "gent", "со": "gent", "порядка": "gent", "выше": "gent", "ниже": "gent", "вместо": "gent",
    "к": "datv", "ко": "datv", "благодаря": "datv", "согласно": "datv",
    "о": "loct", "об": "loct", "при": "loct",
    "между": "ablt", "над": "ablt", "под": "ablt", "перед": "ablt",
}
_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
           "сентября", "октября", "ноября", "декабря")
_ACC_PREPS = ("на", "в", "во", "за", "про", "через")
# «год» после предлога: «в 2026 году», «до 2028 года», «к 2030 году», «за 2027 год»
_YEAR_PREP = {"в": "loct", "во": "loct", "о": "loct", "об": "loct", "при": "loct",
              "на": "accs", "за": "accs", "про": "accs", "через": "accs", "по": "accs",
              "до": "gent", "с": "gent", "со": "gent", "от": "gent", "после": "gent", "около": "gent", "для": "gent",
              "без": "gent", "из": "gent", "кроме": "gent", "начала": "gent", "конца": "gent", "середины": "gent",
              "к": "datv", "ко": "datv", "перед": "ablt", "между": "ablt", "над": "ablt", "под": "ablt"}
_YEAR_FORM = {"nomn": "год", "accs": "год", "gent": "года", "datv": "году", "loct": "году", "ablt": "годом"}
_NUM_RE = re.compile(r"(?<![\w.,])(\d{1,3}(?: \d{3})+|\d+)(?:[.,](\d+))?(?![\w])")

# даты: «28.09.2026» читалось «двадцать восемь целых девять сотых.2026»
_DATE = r"(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s*(?:года|г\.?)(?![А-Яа-яЁё]))?"
_DATE_RANGE_RE = re.compile(r"(?<![\d.])" + _DATE + r"\s*[-–—]\s*" + _DATE + r"(?!\.?\d)")   # точка конца фразы — можно
_DATE_RE = re.compile(r"(?<![\d.])" + _DATE + r"(?!\.?\d)")


def _date_words(d, m, y, with_year):
    d, m, y = int(d), int(m), int(y)
    if not (1 <= d <= 31 and 1 <= m <= 12):
        return None
    return f"{d} {_MONTHS[m - 1]}" + (f" {y} года" if with_year else "")


def _dates(t):
    """«28.09.2026» -> «28 сентября» (год — только если не текущий), «28.09.2026 - 09.10.2026» ->
    «с 28 сентября по 9 октября». Числа и падеж («с двадцать восьмого», «по девятое») — дальше в _numbers."""
    cur = time.localtime().tm_year

    def rng(m):
        y1, y2 = int(m.group(3)), int(m.group(6))
        a = _date_words(*m.group(1, 2, 3), with_year=y1 != y2)
        b = _date_words(*m.group(4, 5, 6), with_year=y1 != y2 or y2 != cur)
        if not a or not b:
            return m.group(0)
        prev = re.search(r"([А-Яа-яЁё]+)\s*$", t[:m.start()])
        prev = prev.group(1).lower() if prev else ""
        lead = "" if prev in ("с", "со") else "период с " if prev in ("за", "в", "во", "на") else "с "
        return f"{lead}{a} по {b}"

    t = _DATE_RANGE_RE.sub(rng, t)
    return _DATE_RE.sub(lambda m: _date_words(*m.group(1, 2, 3), with_year=int(m.group(3)) != cur) or m.group(0), t)


def _parse(word, pos=None):
    """Разбор слова; pos — части речи в порядке ПРЕДПОЧТЕНИЯ («сто» — числительное, а не СТО)."""
    if not _MORPH:
        return None
    ps = _MORPH.parse(word)
    if pos is None:
        return ps[0] if ps else None
    for want in pos:
        for p in ps:
            if p.tag.POS == want and "Abbr" not in p.tag:
                return p
    return None


def _is_one(k):
    return k % 10 == 1 and k % 100 != 11


def _small(k, case, gender=None):
    """Число < 1000 в падеже case; gender — род для один/два (femn/neut)."""
    out = []
    for w in _n2w(k).split():
        p = _parse(w, ("NUMR", "ADJF"))
        if not p:
            out.append(w); continue
        nf = p.normal_form
        if nf == "один":
            g = gender or "masc"
            tags = {"nomn", g, "sing"} if (case == "accs" and g != "femn") else {case, g, "sing"}
        elif nf == "два" and gender == "femn":
            tags = {case, "femn"} if case in ("nomn", "accs") else {case}
        elif case in ("nomn", "accs"):
            out.append(w); continue                     # «на пять», «за двадцать» — как именительный
        else:
            tags = {case}
        f = p.inflect(tags)
        out.append(f.word if f else w)
    return " ".join(out)


_SCALES = [(10 ** 12, "триллион"), (10 ** 9, "миллиард"), (10 ** 6, "миллион"), (10 ** 3, "тысяча")]


def _scale_word(name, k, case):
    p = _parse(name, ("NOUN",))
    if not p:
        return name
    if case in ("nomn", "accs"):
        if name == "тысяча" and case == "accs" and _is_one(k):
            return "тысячу"
        f = p.make_agree_with_number(k)
    else:
        f = p.inflect({case, "sing" if _is_one(k) else "plur"})
    return f.word if f else name


def _cardinal(n, case="nomn", gender=None):
    """Количественное число словами в падеже: (2316, gent) -> «двух тысяч трёхсот шестнадцати»."""
    if not _MORPH:
        return _n2w(n)
    if n == 0:
        return "ноль"
    parts, rest = [], n
    for val, name in _SCALES:
        k, rest = divmod(rest, val)
        if k:
            if not (k == 1 and not parts):              # «тысяча», а не «одна тысяча» в начале
                parts.append(_small(k, case, "femn" if name == "тысяча" else "masc"))
            parts.append(_scale_word(name, k, case))
    if rest:
        parts.append(_small(rest, case, gender))
    return " ".join(parts)


def _frac_word(base, k, case):
    """«целая/десятая/сотая» при числе k: «одна целая», «две целых», «трёх целых», «одну десятую»."""
    p = _parse(base, ("ADJF",))
    if not p:
        return base
    if case in ("nomn", "accs"):
        f = p.inflect({case, "femn", "sing"}) if _is_one(k) else p.inflect({"gent", "plur"})
    else:
        f = p.inflect({case, "femn", "sing"} if _is_one(k) else {case, "plur"})
    return f.word if f else base


def _decimal(n, b, case):
    units = {1: "десятая", 2: "сотая", 3: "тысячная"}.get(len(b), "десятая")
    k = int(b)
    return (_cardinal(n, case, "femn") + " " + _frac_word("целая", n, case) + " "
            + _cardinal(k, case, "femn") + " " + _frac_word(units, k, case))


def _agree(word, n, case, is_decimal, gender_of_noun=None):
    """Существительное/прилагательное после числа: «10 лот» -> «лотов», «2 крупнейших» и т.п."""
    p = _parse(word, ("NOUN", "ADJF", "PRTF"))
    if not p:
        return word
    base = p.inflect({"nomn", "sing"}) or p                # согласуем от начальной формы
    if p.tag.POS == "NOUN":
        if is_decimal:
            f = base.inflect({"gent", "sing"})
        elif case in ("nomn", "accs"):
            f = base.make_agree_with_number(n)
            if f and case == "accs" and _is_one(n):
                f = base.inflect({"accs", "sing"}) or f    # «на одну акцию»
        else:
            f = base.inflect({case, "sing" if _is_one(n) else "plur"})
    else:                                                  # прилагательное перед существительным
        g = gender_of_noun or "masc"
        if is_decimal:
            f = p.inflect({"gent", g, "sing"})
        elif case in ("nomn", "accs"):
            if _is_one(n):
                f = p.inflect({"nomn" if (g != "femn" and case == "accs") else case, g, "sing"})
            elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 and g == "femn":
                f = p.inflect({"nomn", "plur"})
            else:
                f = p.inflect({"gent", "plur"})
        else:
            f = p.inflect({case, g, "sing"} if _is_one(n) else {case, "plur"})
    return f.word if f else word


def _context(text, start, end):
    """Предлог перед числом и до двух слов после него (прилагательное + существительное)."""
    m = re.search(r"([А-Яа-яЁё]+)\s+$", text[:start])
    prev = m.group(1).lower() if m else ""
    words, pos = [], end
    for _ in range(2):
        w = re.match(r"\s+([А-Яа-яЁё]+)", text[pos:])
        if not w:
            break
        words.append((w.group(1), pos + w.start(1), pos + w.end(1)))
        pos += w.end(1)
    return prev, words


def _ordinal(n, case, gender):
    words = _n2w(n, ordinal=True).split()
    last = _parse(words[-1], ("ADJF",))
    f = last.inflect({case, gender, "sing"}) if last else None
    words[-1] = f.word if f else words[-1]
    return " ".join(words)


def _numbers(t):
    out, pos = [], 0
    for m in _NUM_RE.finditer(t):
        n = int(m.group(1).replace(" ", ""))
        frac = m.group(2).rstrip("0") if m.group(2) is not None else None
        prev, nxt = _context(t, m.start(), m.end())
        w1 = nxt[0][0] if nxt else ""
        case = _PREP_CASE.get(prev, "accs" if prev in _ACC_PREPS else "nomn")

        # после числа: существительное или «прилагательное + существительное»
        p1 = _parse(w1, ("NOUN", "ADJF", "PRTF")) if w1 else None
        targets, noun_p = [], None
        if p1 and p1.tag.POS == "NOUN":
            targets, noun_p = [nxt[0]], p1
        elif p1 and len(nxt) == 2 and _parse(nxt[1][0], ("NOUN",)):
            targets, noun_p = [nxt[0], nxt[1]], _parse(nxt[1][0], ("NOUN",))
        # падеж по существительному, если предлог его не задал: «с 10 акциями», «о 5 компаниях», «1 банке»
        if noun_p and case == "nomn" and noun_p.tag.case in ("datv", "ablt", "loct"):
            case = noun_p.tag.case
        gender = noun_p.tag.gender if noun_p else None

        year_noun = None
        if frac is None and 1900 <= n <= 2100 and w1.lower() in ("год", "года", "году") and prev in _YEAR_PREP:
            # «год» одинаков в им./вин. — падеж задаёт предлог; слово «год» тоже согласуем
            ycase = _YEAR_PREP[prev]
            spoken, targets = _ordinal(n, ycase, "masc"), []
            year_noun = (nxt[0][1], nxt[0][2], _YEAR_FORM[ycase]) if w1.lower() == "год" else None
        elif frac is None and 1900 <= n <= 2100 and w1.lower().startswith(("год", "гг")):
            yp = _parse(w1, ("NOUN",))
            ycase = yp.tag.case if yp else "nomn"
            if prev.startswith(("месяц", "квартал", "полугод", "мес", "кв")) and w1.lower() == "год":
                ycase = "gent"                    # «за девять месяцев две тысячи двадцать шестого года»
                year_noun = (nxt[0][1], nxt[0][2], "года")
            if ycase == "nomn" and (prev in _ACC_PREPS or prev in _PREP_CASE):
                ycase = "accs" if prev in _ACC_PREPS else _PREP_CASE[prev]
            spoken, targets = _ordinal(n, ycase, "masc"), []
        elif frac is None and 1 <= n <= 31 and w1.lower() in _MONTHS:
            spoken, targets = _ordinal(n, "accs" if prev in ("на", "в", "по") else "gent", "neut"), []
        elif frac:
            spoken = _decimal(n, frac, case)
        else:
            spoken = _cardinal(n, case, gender)

        out.append(t[pos:m.start()]); out.append(spoken); pos = m.end()
        if year_noun:
            out.append(t[pos:year_noun[0]]); out.append(year_noun[2]); pos = year_noun[1]
        for word, a, b in targets:
            out.append(t[pos:a])
            agreed = _agree(word, n, case, bool(frac), gender)
            out.append(agreed[:1].upper() + agreed[1:] if word[:1].isupper() else agreed)
            pos = b
    out.append(t[pos:])
    return "".join(out)


# ------------------------------------------------------------------ главная функция
def normalize(text: str) -> str:
    """Текст новости/ответа -> «проговариваемый» грамотный русский текст."""
    t = text or ""
    t = _clean(t)
    t = _decaps(t)
    t = _latin(t)
    t = _expand_abbr(t)
    t = _dates(t)
    t = _units(t)
    t = _numbers(t)
    t = re.sub(r"[A-Za-z]+", lambda m: _spell(m.group(0)), t)
    t = re.sub(r"\s+([,.:;!?])", r"\1", t)
    return re.sub(r"\s{2,}", " ", t).strip()
