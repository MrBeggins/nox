# -*- coding: utf-8 -*-
"""
Доступ к T-Invest API (REST) с ЗАКРЕПЛЁННЫМ ключом сервера.

Сертификат invest-public-api.tinkoff.ru выпущен УЦ Минцифры (Russian Trusted Sub CA), его корня нет ни в Python,
ни в Windows — invest_reader из-за этого молча ходил БЕЗ проверки сервера, а токен у пользователя с полным
доступом (торговля на всех счетах). Теперь: TLS-рукопожатие -> SHA-256 открытого ключа сервера (SPKI) сверяется
с tinvest_pin.json -> только потом уходит запрос с токеном. Не совпало — запрос НЕ отправляется (PinError).
Ключ закреплён 2026-10-16 по решению пользователя; при смене ключа сервера закрепление обновляет пользователь
(pin_now() — после проверки, что сеть своя).

Соединение держится открытым (keep-alive) — запрос без нового рукопожатия ~в разы быстрее; своё на каждый поток.
"""
import concurrent.futures
import hashlib
import http.client
import json
import os
import socket
import ssl
import threading
import time

HOST = "invest-public-api.tinkoff.ru"
PREFIX = "/rest/tinkoff.public.invest.api.contract.v1."
HERE = os.path.dirname(os.path.abspath(__file__))
PIN_FILE = os.path.join(HERE, "tinvest_pin.json")
APP_DB = os.path.join(os.environ.get("APPDATA") or os.path.expanduser(r"~\AppData\Roaming"), "com.priler.jarvis", "app.db")


class PinError(Exception):
    pass


class ApiError(Exception):
    def __init__(self, status, message, body=""):
        super().__init__(f"HTTP {status}: {message}")
        self.status, self.message, self.body = status, message, body


def token():
    t = os.environ.get("JARVIS_INVEST_TOKEN", "").strip()
    if t:
        return t
    try:
        d = json.loads(open(APP_DB, "rb").read().decode("utf-8-sig"))
        return (d.get("invest_token") or "").strip()
    except Exception:
        return ""


def spki_sha256(der):
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    c = x509.load_der_x509_certificate(der)
    return hashlib.sha256(c.public_key().public_bytes(serialization.Encoding.DER,
                                                      serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()


def load_pin():
    try:
        return json.load(open(PIN_FILE, encoding="utf-8"))
    except Exception:
        return {}


def _ctx():
    # цепочку проверить нечем (корня Минцифры нет) — проверяем закреплённый ключ сервера сами, сразу после рукопожатия
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


class _PinnedConn(http.client.HTTPSConnection):
    def connect(self):
        super().connect()
        pin = load_pin().get("spki_sha256")
        got = spki_sha256(self.sock.getpeercert(binary_form=True))
        if not pin or got != pin:
            self.sock.close()
            raise PinError(f"ключ сервера {HOST} не совпал с закреплённым ({got[:12]}… ≠ {(pin or 'нет')[:12]}…) — "
                           f"запрос не отправлен")


_tls = threading.local()


def _conn(fresh=False):
    c = getattr(_tls, "conn", None)
    if fresh or c is None:
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
        c = _tls.conn = _PinnedConn(HOST, 443, timeout=10, context=_ctx())
    return c


def call(method, body=None, timeout=8.0):
    """POST метода API (например «UsersService/GetAccounts»). Повтор один раз при обрыве keep-alive."""
    tok = token()
    if not tok:
        raise ApiError(0, "токен Invest API не задан (Профиль)")
    data = json.dumps(body or {}).encode("utf-8")
    headers = {"Authorization": "Bearer " + tok, "Content-Type": "application/json", "Accept": "application/json",
               "x-app-name": "nox"}
    for attempt in (0, 1):
        c = _conn(fresh=attempt == 1)
        c.timeout = timeout
        try:
            if c.sock is None:
                c.connect()                      # рукопожатие + сверка ключа ДО отправки токена
            else:
                c.sock.settimeout(timeout)
            c.request("POST", PREFIX + method, body=data, headers=headers)
            r = c.getresponse()
            raw = r.read().decode("utf-8", "replace")
        except PinError:
            _tls.conn = None
            raise
        except (http.client.RemoteDisconnected, ConnectionError, BrokenPipeError, http.client.CannotSendRequest,
                http.client.ResponseNotReady, socket.timeout, ssl.SSLError, OSError):
            if attempt == 0:
                continue
            raise
        if r.status != 200:
            try:
                j = json.loads(raw)
                msg = j.get("message") or j.get("description") or raw
            except Exception:
                msg = raw
            raise ApiError(r.status, str(msg)[:200], raw[:500])
        return json.loads(raw) if raw else {}
    raise ApiError(0, "нет соединения")


def pin_now():
    """Закрепить ТЕКУЩИЙ ключ сервера (делать только по решению пользователя)."""
    import datetime
    from cryptography import x509
    with socket.create_connection((HOST, 443), timeout=10) as s:
        with _ctx().wrap_socket(s, server_hostname=HOST) as ss:
            der = ss.getpeercert(binary_form=True)
    c = x509.load_der_x509_certificate(der)
    pin = {"host": HOST, "spki_sha256": spki_sha256(der), "cert_sha256": hashlib.sha256(der).hexdigest(),
           "subject": c.subject.rfc4514_string(), "issuer": c.issuer.rfc4514_string(),
           "valid_to": c.not_valid_after_utc.isoformat(), "pinned_at": datetime.datetime.now().isoformat(timespec="seconds")}
    json.dump(pin, open(PIN_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return pin


def q(v):
    """Quotation/MoneyValue -> float."""
    if not v:
        return None
    return float(v.get("units", 0) or 0) + float(v.get("nano", 0) or 0) / 1e9


def quotation(x):
    """float/«1 104,8» -> Quotation {units, nano} (точно, без двоичной погрешности)."""
    from decimal import Decimal
    d = Decimal(str(x).replace(" ", "").replace(" ", "").replace(",", "."))
    units = int(d)
    nano = int(((d - units) * 1_000_000_000).to_integral_value())
    return {"units": str(units), "nano": nano}


# ------------------------------------------------------------------ инструменты (каталог invest_reader)
CATALOG_FILE = os.path.join(HERE, "instruments.json")
_cat = {"mt": None, "by": {}}
_CLASS_RANK = {"TQBR": 0, "SPBFUT": 1, "TQTF": 2, "CETS": 3}


def find_instrument(ticker):
    """Тикер из шапки виджета -> инструмент каталога {uid, figi, lot, name, type, class}: акция Мосбиржи, фьючерс,
    фонд, валюта (облигации — последними)."""
    try:
        mt = os.path.getmtime(CATALOG_FILE)
        if mt != _cat["mt"]:
            by = {}
            for x in json.load(open(CATALOG_FILE, encoding="utf-8"))["items"]:
                tk = str(x.get("ticker") or "").upper()
                rank = _CLASS_RANK.get(x.get("class"), 9 if x.get("type") == "bond" else 5)
                if tk and (tk not in by or rank < by[tk][0]):
                    by[tk] = (rank, x)
            _cat.update(mt=mt, by=by)
    except Exception:
        pass
    r = _cat["by"].get((ticker or "").upper().strip())
    return r[1] if r else None


# ------------------------------------------------------------------ заявки: свой поток, соединение всегда тёплое
_orders = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="tinvest-orders")
_warm = {"started": False, "rtt": None, "err": ""}


def order_call(method, body, timeout=5.0):
    """Вызов в потоке заявок: его соединение прогрето (GetInfo раз в 20 с) — заявка без нового рукопожатия."""
    return _orders.submit(call, method, body, timeout).result(timeout + 3)


def start_warm():
    if _warm["started"]:
        return
    _warm["started"] = True

    def loop():
        while True:
            try:
                t0 = time.perf_counter()
                order_call("UsersService/GetInfo", {}, 8.0)
                _warm.update(rtt=round((time.perf_counter() - t0) * 1000, 1), err="")
            except Exception as e:
                _warm["err"] = f"{type(e).__name__}: {str(e)[:120]}"
            time.sleep(20)
    threading.Thread(target=loop, daemon=True, name="tinvest-warm").start()


def post_order(account_id, inst, side, qty, order_type="market", price=None):
    """Заявка через API. inst — из find_instrument; side buy/sell; qty — лоты; order_type market/limit (price)."""
    import uuid
    body = {"instrumentId": inst["uid"], "quantity": str(int(qty)), "accountId": account_id,
            "direction": "ORDER_DIRECTION_BUY" if side == "buy" else "ORDER_DIRECTION_SELL",
            "orderType": "ORDER_TYPE_LIMIT" if order_type == "limit" else "ORDER_TYPE_MARKET",
            "orderId": str(uuid.uuid4())}
    if order_type == "limit":
        body["price"] = quotation(price)
    return order_call("OrdersService/PostOrder", body)
