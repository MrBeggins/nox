# Диагностика календаря investing.com.
# Открывает ВИДИМОЕ окно системного Edge/Chrome в ПОСТОЯННОМ профиле.
# Тот же профиль использует calendar_server.py: если тут один раз пройти
# Cloudflare (само пройдёт в реальной сессии), сервер потом читает сразу.
from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
# Отдельный профиль от сервера: Edge объединяет процессы по одному профилю,
# поэтому у диагностики свой каталог — закрытие её окна не убьёт браузер сервера.
PROFILE_DIR = r"C:\jarvis-voice\browser_profile_diag"
ARGS = ["--window-size=1300,950", "--disable-blink-features=AutomationControlled",
        "--disable-infobars", "--no-first-run", "--no-default-browser-check"]

def launch(p, channel):
    kw = dict(user_data_dir=PROFILE_DIR, headless=False, args=ARGS, user_agent=UA,
              viewport={"width": 1280, "height": 900}, locale="en-US", ignore_https_errors=True)
    if channel:
        kw["channel"] = channel
    return p.chromium.launch_persistent_context(**kw)

ok = False
for channel in ["msedge", "chrome", None]:
    label = channel or "vstroennyj chromium"
    print("\n>> Probuyu brauzer:", label)
    try:
        with sync_playwright() as p:
            ctx = launch(p, channel)
            pg = ctx.pages[0] if ctx.pages else ctx.new_page()
            print(">> Otkryvayu investing.com/economic-calendar ...")
            pg.goto("https://www.investing.com/economic-calendar/", wait_until="domcontentloaded", timeout=60000)
            print(">> Zhdu do 40s poka poyavyatsya stroki (Cloudflare proydyot sam)...")
            n = 0
            for _ in range(40):
                try:
                    n = pg.evaluate("()=>document.querySelectorAll('tr[class*=\"datatable-v2_row\"]').length")
                except Exception:
                    n = 0
                if n:
                    break
                pg.wait_for_timeout(1000)
            print(">> Zagolovok:", repr(pg.title()))
            print(">> Strok sobytij:", n)
            if n:
                s = pg.evaluate("""()=>{const r=[...document.querySelectorAll('tr[class*="datatable-v2_row"]')].filter(x=>x.querySelectorAll('td[class*=align-end]').length>=3).slice(0,6);return r.map(x=>{const v=[...x.querySelectorAll('td[class*=align-end]')].map(t=>t.textContent.trim());const nm=x.querySelector('td[class*=w-full]');return (nm?nm.textContent.trim().slice(0,32):'')+' | fakt='+(v[0]||'-')+' prognoz='+(v[1]||'-');});}""")
                for line in s:
                    print("   ", line)
                print(">> [OK] USPEH cherez", label, "- kalendar chitaetsya! Profil sohranyon.")
                ok = True
                ctx.close()
                break
            else:
                cf = pg.title().strip() == "" or "403" in (pg.evaluate("()=>document.body?document.body.innerText.slice(0,20):''") or "")
                if cf:
                    print(">>", label, ": Cloudflare blok (pustoj zagolovok/403). Projdite proverku v okne.")
                else:
                    print(">>", label, ": zagolovok zagruzhen (Cloudflare OK), no sobytij 0 — vozmozhno pusto na 'Today'.")
                    print(">> Kliknite 'This Week' ili 'Tomorrow' v okne, chtoby uvidet sobytiya.")
                print(">> Ostavlyayu okno otkrytym. Potom Enter dlya povtornoj proverki.")
                input(">> Enter posle togo kak stranica zagruzilas...")
                n = pg.evaluate("()=>document.querySelectorAll('tr[class*=\"datatable-v2_row\"]').length")
                print(">> Teper strok:", n)
                if n:
                    ok = True
                ctx.close()
                if ok:
                    break
    except Exception as e:
        print(">>", label, ": ne udalos -", type(e).__name__, str(e)[:150])

print("\n>> ITOG:", "KALENDAR RABOTAET" if ok else "NE UDALOS prochitat kalendar")
input(">> Nazhmite Enter chtoby zakryt...")
