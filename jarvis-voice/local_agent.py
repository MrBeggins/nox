# -*- coding: utf-8 -*-
"""
Локальный агент Astra: модель APIMIRA работает с файлами и папками на этом ПК.
Основа — схема, которую предложила сама Astra (local_agent.py), переделанная под APIMIRA:
chat/completions + tools, ответ потоком (без потока шлюз APIMIRA рвёт долгие ответы 504).

Что умеет: смотреть папки, читать файлы, искать текст, создавать/править файлы,
выполнять команды. Доступ — ТОЛЬКО внутри выбранной рабочей папки.
Запись файлов и команды — только после вашего подтверждения.

Запуск:  python local_agent.py [папка]      (без папки — откроется окно выбора)
Команды в чате: /папка  /очистить  /модель <id>  /выход
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BASE_URL = "https://apimira.com/v1"
KEY_FILE = Path(__file__).with_name("apimira_key.txt")
LOG_DIR = Path(__file__).with_name("astra_chats")
# Через APIMIRA инструменты корректно вызывает только Astra 6 (gpt-5.4-mini выдумывает результат)
MODEL = os.getenv("AGENT_MODEL", "openai/gpt-6-astra")

MAX_FILE_SIZE = 2 * 1024 * 1024
MAX_OUTPUT_SIZE = 30_000
MAX_READ_LINES = 2_000
COMMAND_TIMEOUT = 120
MAX_STEPS = 40              # сколько действий подряд агент может сделать за один ваш запрос
STREAM_TIMEOUT = 90         # если поток молчит дольше — обрываем

IGNORED_DIRECTORIES = {
    ".git", ".idea", ".vscode", "__pycache__", "node_modules",
    "dist", "build", ".next", ".venv", "venv",
}

# ── консоль ──────────────────────────────────────────────────────────────

if sys.platform == "win32":
    os.system("")  # включить ANSI-цвета в консоли Windows
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

CYAN, GREEN, YELLOW, RED, DIM, BOLD, RESET = (
    "\033[96m", "\033[92m", "\033[93m", "\033[91m", "\033[90m", "\033[1m", "\033[0m")


def print_error(message: str) -> None:
    print(f"\n{RED}[Ошибка] {message}{RESET}\n")


def print_info(message: str) -> None:
    print(f"{DIM}{message}{RESET}")


class Confirm:
    """Подтверждение действий. Ответ «в» (всё) — больше не спрашивать до конца сессии."""
    allow_all = False

    @classmethod
    def ask(cls, message: str) -> bool:
        if cls.allow_all:
            return True
        while True:
            answer = input(f"{YELLOW}{message} [д/н/в=да на всё]: {RESET}").strip().lower()
            if answer in {"y", "yes", "д", "да"}:
                return True
            if answer in {"", "n", "no", "н", "нет"}:
                return False
            if answer in {"в", "все", "всё", "a", "all"}:
                cls.allow_all = True
                print_info("Подтверждения отключены до конца сессии.")
                return True
            print("Введите д (да), н (нет) или в (да на всё).")


# ── рабочая папка и инструменты ──────────────────────────────────────────

class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def safe_path(self, relative_path: str) -> Path:
        candidate = (self.root / (relative_path or ".")).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as error:
            raise ValueError("Доступ за пределы рабочей папки запрещён.") from error
        return candidate

    def display_path(self, path: Path) -> str:
        try:
            rel = str(path.relative_to(self.root))
            return "." if rel == "." else rel
        except ValueError:
            return str(path)

    @staticmethod
    def _ignored(path: Path) -> bool:
        return any(part in IGNORED_DIRECTORIES for part in path.parts)

    def list_directory(self, path: str = ".", recursive: bool = False) -> dict[str, Any]:
        target = self.safe_path(path)
        if not target.is_dir():
            return {"ok": False, "error": f"Папка не найдена: {path}"}
        entries: list[str] = []
        iterator = target.rglob("*") if recursive else target.iterdir()
        truncated = False
        for entry in iterator:
            if self._ignored(entry.relative_to(self.root)):
                continue
            if len(entries) >= 2_000:
                truncated = True
                break
            if entry.is_dir():
                entries.append(self.display_path(entry) + "/")
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = -1
                entries.append(f"{self.display_path(entry)}  ({size} б)")
        entries.sort()
        return {"ok": True, "path": self.display_path(target), "entries": entries, "truncated": truncated}

    def read_file(self, path: str, start_line: int = 1, max_lines: int = MAX_READ_LINES) -> dict[str, Any]:
        target = self.safe_path(path)
        if not target.is_file():
            return {"ok": False, "error": f"Файл не найден: {path}"}
        size = target.stat().st_size
        if size > MAX_FILE_SIZE:
            return {"ok": False, "error": f"Файл слишком большой: {size} байт (максимум {MAX_FILE_SIZE})."}
        text = None
        for enc in ("utf-8-sig", "cp1251"):
            try:
                text = target.read_text(encoding=enc)
                break
            except UnicodeDecodeError:
                continue
            except OSError as error:
                return {"ok": False, "error": f"Не удалось прочитать файл: {error}"}
        if text is None or "\x00" in text:
            return {"ok": False, "error": "Это бинарный файл, а не текст."}
        lines = text.splitlines()
        start = max(1, start_line)
        count = max(1, min(max_lines, MAX_READ_LINES))
        chunk = lines[start - 1:start - 1 + count]
        numbered = "\n".join(f"{i:>5}| {line}" for i, line in enumerate(chunk, start=start))
        return {
            "ok": True, "path": self.display_path(target), "total_lines": len(lines),
            "shown": f"{start}-{start + len(chunk) - 1}", "content": numbered[:MAX_OUTPUT_SIZE * 3],
        }

    def write_file(self, path: str, content: str, overwrite: bool = False) -> dict[str, Any]:
        target = self.safe_path(path)
        exists = target.exists()
        if exists and not overwrite:
            return {"ok": False, "error": "Файл уже существует. Передайте overwrite=true или используйте edit_file."}
        what = "Перезаписать" if exists else "Создать"
        print(f"\n{BOLD}{what} файл:{RESET} {self.display_path(target)}  "
              f"({len(content.splitlines())} строк, {len(content.encode('utf-8'))} байт)")
        preview = content[:600] + ("\n…" if len(content) > 600 else "")
        print(f"{DIM}{preview}{RESET}")
        if not Confirm.ask(f"{what} файл?"):
            return {"ok": False, "cancelled": True, "error": "Пользователь отказал в записи файла."}
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as error:
            return {"ok": False, "error": f"Не удалось записать файл: {error}"}
        return {"ok": True, "path": self.display_path(target), "size": len(content.encode("utf-8"))}

    def edit_file(self, path: str, old_text: str, new_text: str) -> dict[str, Any]:
        """Точечная правка: заменить фрагмент. Для больших файлов — вместо полной перезаписи."""
        target = self.safe_path(path)
        if not target.is_file():
            return {"ok": False, "error": f"Файл не найден: {path}"}
        try:
            text = target.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as error:
            return {"ok": False, "error": f"Не удалось прочитать файл: {error}"}
        count = text.count(old_text) if old_text else 0
        if count == 0:
            return {"ok": False, "error": "Фрагмент old_text не найден в файле (нужно точное совпадение, с отступами)."}
        if count > 1:
            return {"ok": False, "error": f"Фрагмент встречается {count} раз — расширьте old_text, чтобы он был уникальным."}
        print(f"\n{BOLD}Правка файла:{RESET} {self.display_path(target)}")
        for line in old_text.splitlines()[:15]:
            print(f"{RED}- {line}{RESET}")
        for line in new_text.splitlines()[:15]:
            print(f"{GREEN}+ {line}{RESET}")
        if not Confirm.ask("Применить правку?"):
            return {"ok": False, "cancelled": True, "error": "Пользователь отказал в правке."}
        try:
            target.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        except OSError as error:
            return {"ok": False, "error": f"Не удалось записать файл: {error}"}
        return {"ok": True, "path": self.display_path(target)}

    def search_text(self, query: str, path: str = ".", case_sensitive: bool = False) -> dict[str, Any]:
        if not query:
            return {"ok": False, "error": "Поисковый запрос не может быть пустым."}
        base = self.safe_path(path)
        if not base.exists():
            return {"ok": False, "error": f"Путь не найден: {path}"}
        needle = query if case_sensitive else query.lower()
        results: list[dict[str, Any]] = []
        files = [base] if base.is_file() else base.rglob("*")
        for file in files:
            if not file.is_file() or self._ignored(file.relative_to(self.root)):
                continue
            try:
                if file.stat().st_size > MAX_FILE_SIZE:
                    continue
                text = file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if needle in (line if case_sensitive else line.lower()):
                    results.append({"path": self.display_path(file), "line": number, "text": line[:300]})
                    if len(results) >= 300:
                        return {"ok": True, "results": results, "truncated": True}
        return {"ok": True, "results": results, "truncated": False}

    def run_command(self, command: str, timeout_seconds: int = 60) -> dict[str, Any]:
        if not command.strip():
            return {"ok": False, "error": "Команда не может быть пустой."}
        timeout_seconds = max(1, min(int(timeout_seconds or 60), COMMAND_TIMEOUT))
        print(f"\n{BOLD}Агент хочет выполнить команду (PowerShell):{RESET}")
        print(f"{YELLOW}  {command}{RESET}")
        print_info(f"  папка: {self.root}   тайм-аут: {timeout_seconds} с")
        if not Confirm.ask("Выполнить команду?"):
            return {"ok": False, "cancelled": True, "error": "Пользователь отказал в выполнении команды."}
        # PowerShell с UTF-8 выводом — иначе русские буквы приходят кракозябрами
        script = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ErrorActionPreference='Continue'; " + command
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                cwd=str(self.root), capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout_seconds, stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as error:
            out = error.stdout.decode("utf-8", "replace") if isinstance(error.stdout, bytes) else (error.stdout or "")
            return {"ok": False, "timeout": True, "stdout": out[-MAX_OUTPUT_SIZE:],
                    "error": f"Команда не уложилась в {timeout_seconds} с."}
        except OSError as error:
            return {"ok": False, "error": f"Не удалось выполнить команду: {error}"}
        out = proc.stdout or ""
        if out.strip():
            shown = out.strip().splitlines()
            print(f"{DIM}" + "\n".join(shown[:20]) + (f"\n… ещё {len(shown) - 20} строк" if len(shown) > 20 else "") + RESET)
        return {"ok": proc.returncode == 0, "return_code": proc.returncode,
                "stdout": out[-MAX_OUTPUT_SIZE:], "stderr": (proc.stderr or "")[-MAX_OUTPUT_SIZE:]}


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }}


TOOLS = [
    _fn("list_directory", "Показать содержимое папки (пути относительно рабочей папки).",
        {"path": {"type": "string", "description": "Например: . или src"},
         "recursive": {"type": "boolean", "description": "Со всеми подпапками"}}, ["path"]),
    _fn("read_file", "Прочитать текстовый файл. Строки пронумерованы. Большие файлы читай частями.",
        {"path": {"type": "string"},
         "start_line": {"type": "integer", "description": "С какой строки (с 1)"},
         "max_lines": {"type": "integer", "description": f"Сколько строк, максимум {MAX_READ_LINES}"}}, ["path"]),
    _fn("write_file", "Создать файл или полностью заменить его содержимое. Нужно подтверждение пользователя.",
        {"path": {"type": "string"}, "content": {"type": "string", "description": "Полное содержимое файла"},
         "overwrite": {"type": "boolean", "description": "true — разрешить замену существующего файла"}},
        ["path", "content"]),
    _fn("edit_file", "Точечно заменить фрагмент в существующем файле (old_text должен встречаться ровно 1 раз). "
                     "Для правок больших файлов используй это, а не write_file.",
        {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
        ["path", "old_text", "new_text"]),
    _fn("search_text", "Найти текст во всех файлах папки (или в одном файле).",
        {"query": {"type": "string"}, "path": {"type": "string", "description": "Где искать, по умолчанию ."},
         "case_sensitive": {"type": "boolean"}}, ["query"]),
    _fn("run_command", "Выполнить команду PowerShell в рабочей папке (Windows). Нужно подтверждение пользователя.",
        {"command": {"type": "string"},
         "timeout_seconds": {"type": "integer", "description": f"До {COMMAND_TIMEOUT} с"}}, ["command"]),
]

TOOL_ICONS = {"list_directory": "📂", "read_file": "📄", "write_file": "💾",
              "edit_file": "✏️", "search_text": "🔎", "run_command": "⚙️"}


def system_prompt(ws: Workspace) -> str:
    return (
        "Ты — Astra, локальный помощник на компьютере пользователя (Windows). "
        f"Рабочая папка: {ws.root}. Все пути указывай относительно неё; выйти за её пределы нельзя. "
        "У тебя есть инструменты для просмотра папок, чтения, поиска, создания и правки файлов и запуска команд PowerShell. "
        "Сначала смотри, что есть в папке, потом действуй. Не выдумывай содержимое файлов — читай их. "
        "Большие файлы правь через edit_file, а не переписывай целиком. "
        "Если пользователь отказал в действии — не повторяй его, спроси, как лучше. "
        "Отвечай по-русски, коротко и по делу."
    )


# ── обращение к модели (поток) ───────────────────────────────────────────

class StreamBroken(Exception):
    pass


def chat_stream(key: str, model: str, messages: list[dict]) -> dict:
    """Один запрос к модели. Текст печатается по мере прихода. Возвращает сообщение ассистента."""
    body = {"model": model, "messages": messages, "tools": TOOLS, "stream": True, "max_tokens": 16000}
    req = urllib.request.Request(
        BASE_URL + "/chat/completions", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    text: list[str] = []
    calls: dict[int, dict] = {}
    finish = None
    printed_header = False
    spinner_shown = True
    print(f"{DIM}Astra думает…{RESET}", end="", flush=True)

    def header():
        nonlocal printed_header, spinner_shown
        if spinner_shown:
            print("\r" + " " * 20 + "\r", end="")
            spinner_shown = False
        if not printed_header:
            print(f"{GREEN}{BOLD}Astra:{RESET} ", end="", flush=True)
            printed_header = True

    try:
        resp = urllib.request.urlopen(req, timeout=STREAM_TIMEOUT)
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if chunk.get("error"):
                raise StreamBroken(str(chunk["error"])[:300])
            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            finish = choice.get("finish_reason") or finish
            delta = choice.get("delta") or {}
            if delta.get("content"):
                header()
                print(delta["content"], end="", flush=True)
                text.append(delta["content"])
            for tc in delta.get("tool_calls") or []:
                slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                slot["id"] = tc.get("id") or slot["id"]
                fn = tc.get("function") or {}
                slot["name"] += fn.get("name") or ""
                slot["arguments"] += fn.get("arguments") or ""
    except urllib.error.HTTPError as error:
        raise StreamBroken(f"HTTP {error.code}: {error.read().decode('utf-8', 'replace')[:300]}")
    except (TimeoutError, OSError) as error:
        raise StreamBroken(f"сервер замолчал ({error})")
    finally:
        if spinner_shown:
            print("\r" + " " * 20 + "\r", end="")
        if printed_header:
            print()

    if finish == "error":
        raise StreamBroken("APIMIRA оборвал ответ (слишком долгий поток)")

    message: dict[str, Any] = {"role": "assistant", "content": "".join(text) or None}
    if calls:
        message["tool_calls"] = [
            {"id": c["id"] or f"call_{i}", "type": "function",
             "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
            for i, c in sorted(calls.items())
        ]
    return message


# ── основной цикл ────────────────────────────────────────────────────────

def run_tool(ws: Workspace, name: str, raw_args: str) -> dict:
    try:
        args = json.loads(raw_args or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": "Аргументы инструмента — не валидный JSON (возможно, обрезаны)."}
    func = getattr(ws, name, None)
    if name not in TOOL_ICONS or func is None:
        return {"ok": False, "error": f"Неизвестный инструмент: {name}"}
    brief = args.get("path") or args.get("query") or args.get("command") or ""
    print(f"{CYAN}{TOOL_ICONS[name]} {name}{RESET} {DIM}{str(brief)[:120]}{RESET}")
    try:
        return func(**args)
    except TypeError as error:
        return {"ok": False, "error": f"Неверные аргументы: {error}"}
    except ValueError as error:
        return {"ok": False, "error": str(error)}


def pick_folder(initial: str | None) -> Path:
    if initial and Path(initial).is_dir():
        return Path(initial)
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
        chosen = filedialog.askdirectory(title="Astra: выберите рабочую папку")
        root.destroy()
        if chosen:
            return Path(chosen)
    except Exception:
        pass
    while True:
        typed = input("Путь к рабочей папке: ").strip().strip('"')
        if typed and Path(typed).is_dir():
            return Path(typed)
        print_error("Такой папки нет.")


class Log:
    def __init__(self):
        self.path = None

    def write(self, who: str, text: str) -> None:
        try:
            LOG_DIR.mkdir(exist_ok=True)
            if not self.path:
                self.path = LOG_DIR / ("agent_" + time.strftime("%Y-%m-%d_%H%M%S") + ".md")
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(f"## {who}\n\n{text}\n\n")
        except OSError:
            pass


def main() -> None:
    try:
        key = KEY_FILE.read_text(encoding="utf-8-sig").strip()
    except OSError:
        key = ""
    if not key:
        print_error(f"Нет ключа APIMIRA. Вставьте ключ am-... в файл {KEY_FILE}")
        input("Enter — выход"); return

    model = MODEL
    ws = Workspace(pick_folder(sys.argv[1] if len(sys.argv) > 1 else None))
    messages: list[dict] = [{"role": "system", "content": system_prompt(ws)}]
    log = Log()

    print(f"\n{BOLD}Агент Astra{RESET} · модель {model}")
    print(f"Рабочая папка: {CYAN}{ws.root}{RESET}")
    print_info("Пишите задачу и жмите Enter. Команды: /папка  /очистить  /модель <id>  /выход")
    print_info("Ctrl+C — прервать текущий ответ.\n")

    while True:
        try:
            user = input(f"{CYAN}{BOLD}Вы:{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); break
        if not user:
            continue
        low = user.lower()
        if low in {"/выход", "/exit", "/quit"}:
            break
        if low in {"/очистить", "/clear"}:
            messages = [{"role": "system", "content": system_prompt(ws)}]
            Confirm.allow_all = False
            print_info("Диалог очищен.\n"); continue
        if low in {"/папка", "/folder"}:
            ws = Workspace(pick_folder(None))
            messages = [{"role": "system", "content": system_prompt(ws)}]
            Confirm.allow_all = False
            print(f"Рабочая папка: {CYAN}{ws.root}{RESET} (диалог начат заново)\n"); continue
        if low.startswith(("/модель", "/model")):
            parts = user.split(maxsplit=1)
            if len(parts) == 2:
                model = parts[1].strip()
            print_info(f"Модель: {model}\n"); continue

        messages.append({"role": "user", "content": user})
        log.write("Вы", user)
        try:
            for _ in range(MAX_STEPS):
                try:
                    reply = chat_stream(key, model, messages)
                except StreamBroken as error:
                    print_error(f"{error}. Напишите «продолжи», чтобы попробовать ещё раз.")
                    break
                messages.append(reply)
                if reply.get("content"):
                    log.write("Astra", reply["content"])
                if not reply.get("tool_calls"):
                    break
                for call in reply["tool_calls"]:
                    result = run_tool(ws, call["function"]["name"], call["function"]["arguments"])
                    if not result.get("ok") and not result.get("cancelled"):
                        print_info(f"   ↳ {result.get('error', 'не получилось')}")
                    log.write(f"инструмент {call['function']['name']}",
                              call["function"]["arguments"][:500] + "\n→ " + json.dumps(result, ensure_ascii=False)[:500])
                    messages.append({"role": "tool", "tool_call_id": call["id"],
                                     "content": json.dumps(result, ensure_ascii=False)[:MAX_OUTPUT_SIZE * 3]})
            else:
                print_info(f"Агент сделал {MAX_STEPS} шагов подряд и остановился. Напишите «продолжи», если нужно.")
        except KeyboardInterrupt:
            print_info("\n[прервано]")
            close_pending_calls(messages)
        print()


def close_pending_calls(messages: list[dict]) -> None:
    """После Ctrl+C: каждому вызову инструмента нужен ответ, иначе API отклонит следующий запрос."""
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("tool_calls"):
            answered = {m.get("tool_call_id") for m in messages[i + 1:]}
            for call in messages[i]["tool_calls"]:
                if call["id"] not in answered:
                    messages.append({"role": "tool", "tool_call_id": call["id"],
                                     "content": '{"ok": false, "error": "Прервано пользователем."}'})
            return


if __name__ == "__main__":
    main()
