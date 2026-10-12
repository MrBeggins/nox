# -*- coding: utf-8 -*-
"""
Ударения по смыслу для голоса Нокса (Silero понимает пометку «+» перед ударной гласной).
Омографы различаются по контексту: «в з+амке» / «тяжёлый зам+ок», «ск+олько ст+оит» / «он сто+ит у окна».
Основа — RUAccent (turbo). Его модель для незнакомых слов несовместима с текущим onnxruntime (просит вход
token_type_ids) — подставляем его здесь, не трогая библиотеку. Любая ошибка -> текст без пометок
(тогда ударения расставит сам Silero, как раньше).
Использование:  nox_accent.start_loading();  text = nox_accent.accent(text)
"""
import re, threading

_acc = None
_lock = threading.Lock()


def _fix_onnx(acc):
    """Добавить token_type_ids в запросы модели ударений для незнакомых слов."""
    am = getattr(acc, "accent_model", None)
    sess = getattr(am, "session", None)
    if sess is None:
        return
    import numpy as np
    need = {i.name for i in sess.get_inputs()}
    if "token_type_ids" not in need:
        return
    orig_run = sess.run

    def run(output_names, feed, *a, **k):
        if "token_type_ids" not in feed and "input_ids" in feed:
            feed = dict(feed)
            feed["token_type_ids"] = np.zeros_like(feed["input_ids"])
        return orig_run(output_names, feed, *a, **k)

    sess.run = run


def _load():
    global _acc
    try:
        from ruaccent import RUAccent
        a = RUAccent()
        a.load(omograph_model_size="turbo", use_dictionary=True, tiny_mode=False)
        _fix_onnx(a)
        _acc = a
        print("[accent] RUAccent готов")
    except Exception as e:
        print("[accent] RUAccent недоступен, ударения ставит Silero:", e)


def start_loading():
    """Загрузка ~30-40 с — в фоне, чтобы голос был готов сразу (до загрузки — без пометок)."""
    threading.Thread(target=_load, daemon=True).start()


def ready():
    return _acc is not None


# правки там, где RUAccent ошибается
_PREP_LOC = re.compile(r"(?:^|\s)(?:в|во|на|о|об|при)\s+$", re.I)


def _fix(t):
    t = re.sub(r"\bл\+?от\+?ов\b", "лот+ов", t, flags=re.I)          # лото́в, а не ло́тов
    t = re.sub(r"\bл\+?отам\b", "лот+ам", t, flags=re.I)
    t = re.sub(r"\bл\+?отами\b", "лот+ами", t, flags=re.I)

    # «покрыто з+олотом» (существительное), но «о золот+ом кольце» (прилагательное после предлога)
    def gold(m):
        before = t[:m.start() + len(m.group(1))]          # текст до самого слова (с пробелом перед ним)
        return m.group(1) + "золот+ом" if _PREP_LOC.search(before) else m.group(1) + "з+олотом"
    t = re.sub(r"(^|[^А-Яа-яЁё+])\+?з\+?о\+?л\+?о\+?т\+?о\+?м(?![А-Яа-яЁё])", gold, t, flags=re.I)
    return t


def accent(text):
    if not text or _acc is None:
        return text
    try:
        with _lock:                                   # модель не потокобезопасна
            out = _acc.process_all(text)
        return _fix(out) if out and out.strip() else text
    except Exception:
        return text
