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

    from telethon import TelegramClient
    with TelegramClient(SESSION, int(api_id), api_hash) as client:
        me = client.get_me()
        uname = getattr(me, "username", None) or getattr(me, "first_name", "") or "аккаунт"
        print(f"\nУспешный вход как: {uname}")
        print("Сессия сохранена. Теперь запусти tg_reader (или перезапусти Nox) и выбери чаты в настройках.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Ошибка входа:", e)
        sys.exit(1)
