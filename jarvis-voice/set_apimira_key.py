# -*- coding: utf-8 -*-
"""Переносит ключ APIMIRA из apimira_key.txt в app.db (api_keys.openai).
Ключ нигде не печатается. Выставляет backend=openai и URL APIMIRA.
Запуск:  python set_apimira_key.py
"""
import json, os, sys

KEY_FILE = r"C:\jarvis-voice\apimira_key.txt"
DB       = r"C:\Users\Дамир\AppData\Roaming\com.priler.jarvis\app.db"
URL      = "https://apimira.com/v1"

def main():
    if not os.path.exists(KEY_FILE):
        print("НЕТ ФАЙЛА:", KEY_FILE, "— создайте его и вставьте ключ am-...")
        return 1
    key = open(KEY_FILE, encoding="utf-8-sig").read().strip()
    if not key or not key.startswith("am-"):
        print("Ключ не похож на APIMIRA (ожидался префикс 'am-'). Длина:", len(key))
        return 2
    d = json.loads(open(DB, "rb").read().decode("utf-8-sig"))
    d.setdefault("api_keys", {})["openai"] = key
    d["brain_backend"] = "openai"
    d["openai_url"]   = URL
    if not d.get("openai_model"):
        d["openai_model"] = "openai/gpt-5.4-mini"
    open(DB, "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False, indent=1))  # без BOM!
    print(f"OK: ключ APIMIRA записан в app.db (длина {len(key)}), backend=openai, URL={URL}, model={d['openai_model']}.")
    return 0

sys.exit(main())
