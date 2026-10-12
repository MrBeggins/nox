# -*- coding: utf-8 -*-
"""
Чат с моделями APIMIRA (Astra 6 и др.) — отдельное окно, НЕ мозг Нокса.
Ключ берётся из apimira_key.txt. Ответ выводится потоково (по словам).
Запуск без консоли:  pythonw astra_chat.py   (или ярлык «Чат с Astra.bat»)
"""
import json, threading, time, urllib.request, urllib.error, os
import tkinter as tk
from tkinter import ttk, font as tkfont

BASE_URL = "https://apimira.com/v1"
KEY_FILE = r"C:\jarvis-voice\apimira_key.txt"
SYSTEM   = "Ты дружелюбный умный ассистент. Отвечай по-русски, ясно и по делу."

# модель -> подпись в выпадающем списке
MODELS = [
    ("openai/gpt-6-astra",          "Astra 6 (умная, медленная)"),
    ("openai/gpt-5.4-mini",         "GPT-5.4 mini (быстрая, дешёвая)"),
    ("openai/gpt-5.5",              "GPT-5.5"),
    ("anthropic/claude-sonnet-4.6", "Claude Sonnet 4.6"),
    ("anthropic/claude-fable-5.1",  "Claude Fable 5.1"),
    ("google/gemini-3.7-flash",     "Gemini 3.7 Flash"),
]

def load_key():
    try:
        return open(KEY_FILE, encoding="utf-8-sig").read().strip()
    except Exception:
        return ""

class ChatApp:
    def __init__(self, root):
        self.root = root
        self.key = load_key()
        self.history = [{"role": "system", "content": SYSTEM}]
        self.busy = False
        root.title("Чат с Astra · APIMIRA")
        root.geometry("680x640")
        root.configure(bg="#1e1e1e")

        base = tkfont.Font(family="Segoe UI", size=11)

        # ── верхняя панель: выбор модели + очистка ──
        top = tk.Frame(root, bg="#1e1e1e")
        top.pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(top, text="Модель:", bg="#1e1e1e", fg="#bbbbbb", font=base).pack(side="left")
        self.model_var = tk.StringVar(value=MODELS[0][1])
        self.cb = ttk.Combobox(top, textvariable=self.model_var, state="readonly",
                               values=[m[1] for m in MODELS], width=34, font=base)
        self.cb.pack(side="left", padx=8)
        tk.Button(top, text="Очистить", command=self.clear, bg="#333333", fg="#dddddd",
                  relief="flat", font=base, activebackground="#444").pack(side="right")

        # ── ввод (пакуем ДО ленты, снизу — иначе лента вытесняет поле из окна) ──
        bot = tk.Frame(root, bg="#1e1e1e")
        bot.pack(side="bottom", fill="x", padx=10, pady=(4, 10))

        # ── лента диалога ──
        mid = tk.Frame(root, bg="#1e1e1e")
        mid.pack(fill="both", expand=True, padx=10, pady=4)
        self.txt = tk.Text(mid, wrap="word", bg="#141414", fg="#e8e8e8", font=base,
                           bd=0, padx=12, pady=12, state="disabled", spacing1=2, spacing3=6)
        sb = tk.Scrollbar(mid, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y"); self.txt.pack(side="left", fill="both", expand=True)
        self.txt.tag_config("user", foreground="#4fc3f7", font=(base.actual("family"), 11, "bold"))
        self.txt.tag_config("bot",  foreground="#a5d6a7", font=(base.actual("family"), 11, "bold"))
        self.txt.tag_config("body", foreground="#e8e8e8")
        self.txt.tag_config("dim",  foreground="#888888")

        self.entry = tk.Text(bot, height=3, wrap="word", bg="#262626", fg="#ffffff",
                             insertbackground="#ffffff", font=base, bd=0, padx=10, pady=8)
        self.entry.bind("<Return>", self.on_enter)
        self.entry.bind("<Shift-Return>", lambda e: None)  # Shift+Enter = перенос строки
        # кнопка: ➤ отправить / ■ остановить ответ. Пакуем ДО поля, иначе поле её вытесняет
        self.send_btn = tk.Button(bot, text="➤", command=self.on_button, bg="#2e7d32", fg="white",
                                  relief="flat", font=("Segoe UI", 14, "bold"), width=4,
                                  activebackground="#388e3c")
        self.send_btn.pack(side="right", fill="y", padx=(8, 0))
        self.entry.pack(side="left", fill="both", expand=True)
        self.cancel = threading.Event()
        self.log_path = None

        # ── копирование: клик по ленте даёт фокус, Ctrl+A/C/V/X в любой раскладке, меню по ПКМ ──
        self.txt.bind("<Button-1>", lambda e: self.txt.focus_set())
        for w in (self.txt, self.entry):
            w.bind("<Control-KeyPress>", self._ctrl_key)
            w.bind("<Button-3>", self._menu)

        if not self.key:
            self._line("Система", "Не найден ключ в apimira_key.txt — вставьте ключ am-... в этот файл.", "dim", "dim")
        else:
            self._line("Система", "Готово. Пишите сообщение и жмите Enter (Shift+Enter — новая строка).", "dim", "dim")
        self.entry.focus_set()

    # Windows-коды клавиш не зависят от раскладки (в русской Tk не видит Ctrl+C/V)
    _KEYS = {65: "<<SelectAll>>", 67: "<<Copy>>", 86: "<<Paste>>", 88: "<<Cut>>"}

    def _ctrl_key(self, e):
        ev = self._KEYS.get(e.keycode)
        if not ev:
            return
        if ev == "<<SelectAll>>":
            e.widget.tag_add("sel", "1.0", "end-1c")
        elif ev == "<<Copy>>" and e.widget.tag_ranges("sel"):
            self.root.clipboard_clear()
            self.root.clipboard_append(e.widget.get("sel.first", "sel.last"))
        elif e.widget is self.entry:
            e.widget.event_generate(ev)
        return "break"

    def _menu(self, e):
        m = tk.Menu(self.root, tearoff=0)
        w = e.widget
        if w.tag_ranges("sel"):
            m.add_command(label="Копировать", command=lambda: self._copy(w.get("sel.first", "sel.last")))
        if w is self.entry:
            m.add_command(label="Вставить", command=lambda: w.event_generate("<<Paste>>"))
        else:
            m.add_command(label="Копировать весь диалог", command=lambda: self._copy(w.get("1.0", "end-1c")))
            m.add_command(label="Открыть папку с диалогами", command=lambda: os.startfile(self._log_dir()))
        m.tk_popup(e.x_root, e.y_root)

    def _copy(self, text):
        self.root.clipboard_clear(); self.root.clipboard_append(text)

    # ── каждый диалог сохраняется в astra_chats\<дата_время>.md ──
    def _log_dir(self):
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "astra_chats")
        os.makedirs(d, exist_ok=True)
        return d

    def _log(self, who, text):
        try:
            if not self.log_path:
                self.log_path = os.path.join(self._log_dir(), time.strftime("%Y-%m-%d_%H%M%S") + ".md")
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"## {who}\n\n{text}\n\n")
        except Exception:
            pass

    def model_id(self):
        label = self.model_var.get()
        for mid, lbl in MODELS:
            if lbl == label:
                return mid
        return MODELS[0][0]

    def _line(self, who, text, who_tag, body_tag="body"):
        self.txt.configure(state="normal")
        self.txt.insert("end", f"{who}: ", who_tag)
        self.txt.insert("end", text + "\n\n", body_tag)
        self.txt.configure(state="disabled")
        self.txt.see("end")

    def _append(self, text, tag="body"):
        self.txt.configure(state="normal")
        self.txt.insert("end", text, tag)
        self.txt.configure(state="disabled")
        self.txt.see("end")

    def clear(self):
        if self.busy: return
        self.history = [{"role": "system", "content": SYSTEM}]
        self.log_path = None   # новый диалог — новый файл
        self.txt.configure(state="normal"); self.txt.delete("1.0", "end"); self.txt.configure(state="disabled")
        self._line("Система", "Диалог очищен.", "dim", "dim")

    def on_enter(self, event):
        if event.state & 0x0001:   # Shift зажат -> перенос строки
            return
        self.send()
        return "break"

    def on_button(self):
        if self.busy:
            self.stop()
        else:
            self.send()

    def stop(self):
        # прервать текущий ответ: недописанное сохраняется в истории
        self.cancel.set()

    def send(self):
        if self.busy:
            # ответ ещё идёт — прерываем его и отправляем новое сообщение
            self.stop()
            self.root.after(150, self.send)
            return
        msg = self.entry.get("1.0", "end").strip()
        if not msg: return
        if not self.key:
            self._line("Система", "Нет ключа — вставьте его в apimira_key.txt.", "dim", "dim"); return
        self.entry.delete("1.0", "end")
        self._line("Вы", msg, "user")
        self.history.append({"role": "user", "content": msg})
        self._log("Вы", msg)
        self.busy = True
        self.cancel.clear()
        self.send_btn.configure(text="■", bg="#c62828", activebackground="#d32f2f")
        model = self.model_id()
        # шапка ответа + индикатор ожидания
        self.txt.configure(state="normal")
        self.txt.insert("end", "Astra: ", "bot")
        self._wait_mark = self.txt.index("end-1c")
        self.txt.insert("end", "думает…", "dim")
        self.txt.configure(state="disabled"); self.txt.see("end")
        threading.Thread(target=self._stream, args=(model,), daemon=True).start()

    def _clear_wait(self):
        # убрать "думает…" перед первым токеном
        try:
            self.txt.configure(state="normal")
            self.txt.delete(self._wait_mark, "end-1c")
            self.txt.configure(state="disabled")
        except Exception:
            pass

    def _stream(self, model):
        body = {"model": model, "messages": self.history, "temperature": 0.6,
                "max_tokens": 4000, "stream": True}
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + self.key}
        acc = []
        first = [True]
        t0 = time.time()
        try:
            req = urllib.request.Request(BASE_URL + "/chat/completions",
                                         data=json.dumps(body).encode("utf-8"), headers=headers)
            # timeout действует и на каждое чтение: если поток замолчал на 45с — обрываем
            resp = urllib.request.urlopen(req, timeout=45)
            stopped = False
            for raw in resp:
                if self.cancel.is_set():
                    stopped = True
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                    delta = chunk["choices"][0].get("delta", {}).get("content")
                except Exception:
                    delta = None
                if delta:
                    if first[0]:
                        first[0] = False
                        self.root.after(0, self._clear_wait)
                    acc.append(delta)
                    self.root.after(0, self._append, delta)
            try: resp.close()
            except Exception: pass
            dt = time.time() - t0
            if stopped:
                self.root.after(0, self._append, " [остановлено]", "dim")
            if first[0]:   # ничего не пришло
                self.root.after(0, self._clear_wait)
                self.root.after(0, self._append, "(пустой ответ)", "dim")
            self.root.after(0, self._finish, "".join(acc), dt)
        except urllib.error.HTTPError as e:
            txt = e.read().decode("utf-8", "replace")[:300]
            self.root.after(0, self._clear_wait)
            self.root.after(0, self._append, f"[ошибка HTTP {e.code}] {txt}", "dim")
            self.root.after(0, self._finish, "", 0)
        except Exception as e:
            if first[0]:
                self.root.after(0, self._clear_wait)
            msg = "ответ оборвался (сервер замолчал)" if isinstance(e, TimeoutError) or "timed out" in str(e) \
                  else f"{type(e).__name__}: {e}"
            self.root.after(0, self._append, f" [ошибка] {msg}", "dim")
            # недописанное всё равно в историю — можно сказать «продолжи»
            self.root.after(0, self._finish, "".join(acc), time.time() - t0)

    def _finish(self, full, dt):
        if full:
            self.history.append({"role": "assistant", "content": full})
            self._log(f"Astra ({self.model_id()})", full)
            self._append(f"\n\n⏱ {dt:.1f}с\n\n", "dim")
        else:
            self._append("\n\n", "dim")
        self.busy = False
        self.send_btn.configure(text="➤", bg="#2e7d32", activebackground="#388e3c")
        self.entry.focus_set()

if __name__ == "__main__":
    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)   # чёткий текст на HiDPI
    except Exception:
        pass
    ChatApp(root)
    root.mainloop()
