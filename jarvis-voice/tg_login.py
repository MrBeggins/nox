# -*- coding: utf-8 -*-
"""
РАЗОВЫЙ вход в Telegram для Nox. Запусти в обычном терминале:
    C:\\jarvis-voice\\.venv\\Scripts\\python.exe C:\\jarvis-voice\\tg_login.py
Нужны api_id и api_hash (my.telegram.org -> API development tools). Введёшь телефон и код из Telegram
(и пароль 2FA, если включён). Создаётся локальная сессия tg_session.session — больше вход не нужен.
Креды и код никуда не отправляются, сессия хранится только на этом компьютере.
"""
import json, os, sys

CONFIG  = r"C:\jarvis-voice\tg_config.json"
SESSION = r"C:\jarvis-voice\tg_session"

def _load():
    try:
        return json.load(open(CONFIG, encoding="utf-8"))
    except Exception:
        return {}

def _save(d):
    json.dump(d, open(CONFIG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

def main():
    cfg = _load()
    api_id = cfg.get("api_id") or 0
    api_hash = cfg.get("api_hash") or ""
    if not api_id:
        api_id = int(input("api_id (с my.telegram.org): ").strip())
    if not api_hash:
        api_hash = input("api_hash: ").strip()
    cfg["api_id"] = int(api_id); cfg["api_hash"] = api_hash
    cfg.setdefault("chats", []); cfg.setdefault("keywords", []); cfg.setdefault("match", "any")
    cfg.setdefault("mon", True)
    _save(cfg)

    from getpass import getpass
    from telethon import TelegramClient
    client = TelegramClient(SESSION, int(api_id), api_hash)
    client.start(phone=lambda: input("Телефон в международном формате (+7...): ").strip(),
                 code_callback=lambda: input("Код из Telegram (придёт в приложение): ").strip(),
                 password=lambda: getpass("Облачный пароль 2FA (символы не отображаются): "))
    try:
        me = client.get_me()
        uname = getattr(me, "username", None) or getattr(me, "first_name", "") or "аккаунт"
        print(f"\nУспешный вход как: {uname}")
    finally:
        client.disconnect()
        client.session.close()   # отпустить файл сессии для tg_reader
    # разбудить tg_reader — подхватит сессию без перезапуска Nox
    try:
        import urllib.request
        r = json.loads(urllib.request.urlopen("http://127.0.0.1:8131/reconnect", timeout=20).read())
        print("Нокс подключился к Телеграму." if r.get("authorized")
              else "Сессия сохранена; Нокс подключится при «Обновить список чатов».")
    except Exception:
        print("Сессия сохранена; Нокс подключится при следующем запуске.")
    print("Дальше: Настройки → Телеграм → «Обновить список чатов» → отметить чаты → «Сохранить выбор».")

if __name__ == "__main__":
    code = 0
    try:
        main()
    except Exception as e:
        print("Ошибка входа:", e)
        code = 1
    try: input("\nНажмите Enter, чтобы закрыть окно.")
    except Exception: pass
    sys.exit(code)
