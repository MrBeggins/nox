"""Jarvis MindStocks reader — календарь событий + лента новостей (Mind News).

Держит фоновый (видимый за экраном) браузер, залогиненный на mindstocks.ru.
Оттуда:
  - Календарь событий: POST https://api.mindstocks.ru/api/events-more  (токен mind_token)
  - Новости (Mind News): WebSocket wss://mindstocks.ru:8090

ВНИМАНИЕ: один раз нужно войти на mindstocks.ru в этом браузере (видимое окно
через login-режим). Токен хранится в профиле, дальше всё работает без участия.

Запуск: python mind_server.py  ->  http://127.0.0.1:8126
Вход (видимое окно):  python mind_server.py --login
"""
import sys, threading, queue, time, json, re, os
from fastapi import FastAPI
import uvicorn

MIND_URL = "https://mindstocks.ru/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
PROFILE_DIR = r"C:\jarvis-voice\mind_profile"
CHANNELS = ["msedge", "chrome", None]
LOGIN_MODE = "--login" in sys.argv
# видимое окно для входа; иначе за экраном
WIN_ARGS_HIDDEN = ["--window-position=-32000,-32000", "--window-size=1280,900"]
WIN_ARGS_SHOWN = ["--window-position=120,80", "--window-size=1200,900"]
COMMON_ARGS = ["--disable-blink-features=AutomationControlled", "--disable-infobars",
               "--no-first-run", "--no-default-browser-check"]

_cmd_q = queue.Queue()
_ready = threading.Event()
_state = {"ok": False, "err": "", "browser": "", "logged_in": False}
_worker_started = False

def _launch(p):
    args = (WIN_ARGS_SHOWN if LOGIN_MODE else WIN_ARGS_HIDDEN) + COMMON_ARGS
    last = None
    for ch in CHANNELS:
        try:
            kw = dict(user_data_dir=PROFILE_DIR, headless=False, args=args, user_agent=UA,
                      viewport={"width": 1200, "height": 860}, locale="ru-RU",
                      ignore_https_errors=True)
            if ch:
                kw["channel"] = ch
            ctx = p.chromium.launch_persistent_context(**kw)
            _state["browser"] = ch or "chromium"
            return ctx
        except Exception as e:
            last = e
    raise last or RuntimeError("no browser")

# JS: залогинены ли (есть ли токен). Возвращает только bool, не сам токен.
JS_LOGGED = "() => !!localStorage.getItem('mind_token')"

def _worker():
    from playwright.sync_api import sync_playwright
    os.makedirs(PROFILE_DIR, exist_ok=True)
    attempt = 0
    while True:
        attempt += 1
        try:
            with sync_playwright() as p:
                ctx = _launch(p)
                pg = ctx.pages[0] if ctx.pages else ctx.new_page()
                pg.goto(MIND_URL, wait_until="domcontentloaded", timeout=60000)
                pg.wait_for_timeout(3500)
                try:
                    _state["logged_in"] = bool(pg.evaluate(JS_LOGGED))
                except Exception:
                    _state["logged_in"] = False
                _state["ok"] = True
                _state["err"] = ""
                _ready.set()
                last_refresh = time.time()
                while True:
                    try:
                        fn, resp = _cmd_q.get(timeout=30)
                    except queue.Empty:
                        if time.time() - last_refresh > 900:
                            last_refresh = time.time()
                            try:
                                pg.goto(MIND_URL, wait_until="domcontentloaded", timeout=60000)
                                pg.wait_for_timeout(2500)
                                _state["logged_in"] = bool(pg.evaluate(JS_LOGGED))
                            except Exception as e:
                                if _is_closed(e):
                                    raise
                        continue
                    try:
                        resp["result"] = fn(pg)
                    except Exception as e:
                        resp["error"] = str(e)
                        if _is_closed(e):
                            resp["done"].set(); raise
                    resp["done"].set()
                    last_refresh = time.time()
        except Exception as e:
            _state["ok"] = False
            _state["err"] = f"try {attempt}: {e}"
            _ready.set()
            time.sleep(8)

def _is_closed(e):
    s = str(e).lower()
    return "closed" in s or "target page" in s or "crash" in s

def start_worker():
    global _worker_started
    if not _worker_started:
        _worker_started = True
        threading.Thread(target=_worker, daemon=True).start()

def _submit(fn, timeout=40):
    resp = {"done": threading.Event()}
    _cmd_q.put((fn, resp))
    if not resp["done"].wait(timeout):
        return None, "timeout"
    return resp.get("result"), resp.get("error")

# ------------------------------------------------------------------ HTTP
app = FastAPI(title="Jarvis MindStocks")

@app.on_event("startup")
def _startup():
    start_worker()

@app.get("/health")
def health():
    return {"status": "ok" if _state["ok"] else "starting", "err": _state["err"],
            "browser": _state["browser"], "logged_in": _state["logged_in"]}

# Диагностика: перебрать варианты запроса events-more (в среде пользователя — без скрытий).
DEBUG_JS = r"""
async () => {
  const tok = localStorage.getItem('mind_token');
  if (!tok) return {logged_in:false};
  const url = 'https://api.mindstocks.ru/api/events-more';
  const headerVariants = {
    bearer: {'Authorization':'Bearer '+tok},
    auth_raw: {'Authorization':tok},
    token: {'token':tok},
    mind_token: {'mind-token':tok},
    xtoken: {'x-token':tok},
  };
  const today = new Date().toISOString().slice(0,10);
  const bodyVariants = { empty:{}, offset:{offset:0}, date:{date:today}, range:{date_from:today,date_to:today} };
  const results = [];
  for (const [hn,h] of Object.entries(headerVariants)) {
    for (const [bn,b] of Object.entries(bodyVariants)) {
      try {
        const r = await fetch(url, {method:'POST', headers:Object.assign({'Content-Type':'application/json'},h), body:JSON.stringify(b)});
        const t = await r.text();
        results.push({h:hn, b:bn, status:r.status, len:t.length, sample:t.slice(0,300)});
        if (r.status===200 && t.length>5) return {logged_in:true, ok:{h:hn,b:bn}, sample:t.slice(0,1200), tried:results.length};
      } catch(e) { results.push({h:hn,b:bn,err:String(e).slice(0,80)}); }
    }
  }
  return {logged_in:true, ok:null, results};
}
"""

RAW_JS = r"""
async (args) => {
  const tok = localStorage.getItem('mind_token');
  try {
    const r = await fetch('https://api.mindstocks.ru/api/'+args.path, {method:'POST',
      headers:{'Content-Type':'application/json','Authorization':'Bearer '+tok}, body: args.body});
    const t = await r.text();
    return {status:r.status, len:t.length, sample:t.slice(0,1800)};
  } catch(e){ return {err:String(e)}; }
}
"""

@app.get("/raw")
def raw(path: str = "events-more", body: str = "{}"):
    res, err = _submit(lambda pg: pg.evaluate(RAW_JS, {"path": path, "body": body}), timeout=30)
    return {"result": res, "error": err}

@app.get("/debug")
def debug():
    if not _state["ok"]:
        return {"text": "MindStocks ещё запускается."}
    res, err = _submit(lambda pg: pg.evaluate(DEBUG_JS), timeout=60)
    return {"result": res, "error": err}

# WS-проба новостей: открыть wss://mindstocks.ru:8090 и собрать первые сообщения.
WS_PROBE_JS = r"""
async () => {
  const tok = localStorage.getItem('mind_token');
  async function tryWs(url, opts){
    opts = opts||{};
    return await new Promise((resolve)=>{
      let ws, msgs=[], opened=false, closed=null;
      try{ ws=new WebSocket(url); }catch(e){ return resolve({err:String(e)}); }
      const fin=()=>{ try{ws.close();}catch(e){} resolve({opened, closed, count:msgs.length, msgs}); };
      ws.onopen=async ()=>{ opened=true;
        if(opts.sendToken){ try{ ws.send(JSON.stringify({token:tok})); }catch(e){} }
        if(opts.post){ try{ await fetch('https://api.mindstocks.ru/api/events',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+tok},body:JSON.stringify({type:'all'})}); }catch(e){} }
      };
      ws.onmessage=(ev)=>{ try{ msgs.push(String(ev.data).slice(0,400)); }catch(e){} if(msgs.length>=10) fin(); };
      ws.onclose=(ev)=>{ closed={code:ev.code, reason:String(ev.reason||'').slice(0,80)}; };
      ws.onerror=()=>{};
      setTimeout(fin, 7000);
    });
  }
  const out={};
  out.queryTokenPost = await tryWs('wss://mindstocks.ru:8090/?token='+encodeURIComponent(tok), {post:true});
  if(!out.queryTokenPost.opened){ out.sendToken = await tryWs('wss://mindstocks.ru:8090', {sendToken:true, post:true}); }
  return out;
}
"""

@app.get("/debug_ws")
def debug_ws():
    if not _state["ok"]:
        return {"text": "MindStocks ещё запускается."}
    res, err = _submit(lambda pg: pg.evaluate(WS_PROBE_JS), timeout=15)
    return {"result": res, "error": err}

if __name__ == "__main__":
    if LOGIN_MODE:
        # только поднять браузер для входа; сервер не обязателен
        start_worker()
        print("[login] Otkrylos okno mindstocks.ru. Voydite v svoy akkaunt MindStocks.")
        _ready.wait(60)
        # подождать и проверить статус входа
        for _ in range(600):
            time.sleep(2)
            if _state.get("logged_in"):
                print("[login] Vhod obnaruzhen (token sohranyon v profile).")
                break
        try:
            input("[login] Kogda voydete — nazhmite Enter chtoby zakryt...")
        except Exception:
            time.sleep(60)
    else:
        uvicorn.run(app, host="127.0.0.1", port=8126, log_level="warning")
