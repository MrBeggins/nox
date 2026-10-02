# Nox — офлайн голосовой ассистент + торговый компаньон

Nox (бывш. Jarvis) — локальный голосовой ассистент для Windows с русским голосом на GPU,
чтением торгового терминала, новостей и макростатистики (РФ + США), автокликом по сценариям
для CScalp. Форк [Priler/jarvis](https://github.com/Priler/jarvis) (Rust + Tauri + Svelte),
расширенный Python-сервисами.

> ⚠️ **ГРАНИЦА БЕЗОПАСНОСТИ (неизменна).** Nox только **читает и готовит**: портфель, котировки,
> стакан, новости, заявки, алерты, открытие нужного стакана по сценарию. **Сделки/переводы
> финальным кликом «Купить/Продать» делает ТОЛЬКО пользователь.** Автоклик по сценарию лишь
> *открывает/переключает* заранее заданный стакан по координате — он никогда не жмёт Buy/Sell.

---

## 1. Архитектура и порты

| Компонент | Технологии | Порт | Назначение |
|---|---|---|---|
| `jarvis-app.exe` | Rust | IPC ws://127.0.0.1:**9712** | Голосовой движок: Vosk STT → интент/команды → мозг → голос |
| `jarvis-gui.exe` | Tauri 2 + Svelte 4 | — | Интерфейс и настройки |
| Ollama | — | **11434** | Локальный мозг `qwen2.5:3b` (OpenAI-совместимый) |
| `tts_server.py` | F5-TTS + RUAccent | **8123** | Русский голос на GPU |
| `invest_reader.py` | T-Invest API | **8124** | Портфель/котировки/стакан/заявки (чтение) |
| `terminal_reader.py` | CDP (Playwright) | **8126** | Чтение T-Terminal (новости/календарь MindStocks) |
| `magellan_reader.py` | HTTP JSON | **8127** | Order flow MOEX (russianmagellan.pro) |
| `bls_reader.py` | api.bls.gov | **8128** | Макро США: нонфарм, CPI, PPI, безработица, JOLTS |
| `bea_reader.py` | apps.bea.gov | **8129** | США: ВВП и PCE (нужен UserID) |
| `nox_watchdog.py` | — | **8130** | Сторож: поднимает упавшие сервисы |
| `nox_scenarios.py` | Win32 + LLM | — | Авто-клик по сценариям CScalp (F2-захват координат) |
| Яндекс.Браузер | CDP | **9222** | Выделенный профиль для чтения T-Terminal |

Голосовой конвейер: **Vosk STT → команды/интент → мозг Qwen (Ollama) → ответ → голос F5-TTS**.
Всё офлайн, без облака (кроме опциональных API новостей/статистики).

---

## 2. Требования к железу

- **Windows 10/11 x64.**
- **NVIDIA GPU с CUDA** (разрабатывалось на RTX 5050 8 ГБ, драйвер с поддержкой CUDA 12.8).
  F5-TTS и Ollama используют GPU. На встроенной графике будет очень медленно.
- ~15 ГБ свободного места (модели + venv + сборка).
- Микрофон.

---

## 3. Шаг 1 — установить ПО

Проверенные версии (можно новее). Через `winget` в PowerShell:

```powershell
winget install --id Git.Git -e
winget install --id Rustlang.Rustup -e            # Rust (stable MSVC)
winget install --id OpenJS.NodeJS.LTS -e          # Node.js (для сборки фронта)
winget install --id Python.Python.3.11 -e         # Python 3.11.x (важно 3.11!)
winget install --id Ollama.Ollama -e              # локальный мозг
winget install --id Gyan.FFmpeg -e                # нужен whisper/torchcodec
winget install --id Yandex.Browser -e             # для чтения T-Terminal по CDP
```

Плюс **Visual Studio 2022 Build Tools** с компонентом «Desktop development with C++»
(MSVC v143 / VCTools) — нужен для сборки Rust/Tauri:
```powershell
winget install --id Microsoft.VisualStudio.2022.BuildTools -e --override "--quiet --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
```
После установки Rustup выполните `rustup default stable-msvc`.
Tauri также требует **WebView2 Runtime** (на Win11 уже есть; иначе поставьте Evergreen Runtime).

---

## 4. Шаг 2 — получить код и разложить по ASCII-путям

> ⚠️ **КРИТИЧНО:** пути в коде захардкожены на `C:\jarvis-lab` и `C:\jarvis-voice`, и **Vosk STT
> ломается на пути с кириллицей**. Папки ОБЯЗАТЕЛЬНО должны лежать именно здесь.

```powershell
git clone <URL-вашего-приватного-репозитория> C:\nox-src
# Перенести две части на свои рабочие места:
robocopy C:\nox-src\jarvis-lab   C:\jarvis-lab   /E
robocopy C:\nox-src\jarvis-voice C:\jarvis-voice /E
```

---

## 5. Шаг 3 — Python-окружение

```powershell
cd C:\jarvis-voice
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
# PyTorch с CUDA 12.8 (ставить ОТДЕЛЬНО, из спец-индекса):
.\.venv\Scripts\python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
# Остальные зависимости:
.\.venv\Scripts\python -m pip install -r requirements.txt
# Браузеры Playwright (для terminal_reader):
.\.venv\Scripts\python -m playwright install chromium
```
Проверка GPU: `.\.venv\Scripts\python -c "import torch; print(torch.cuda.is_available())"` → `True`.

> `requirements.txt` — это полный `pip freeze` рабочей машины (включая `torch==2.11.0+cu128`).
> Если индекс cu128 ругается на версию — ставьте `torch`/`torchaudio` без пина, лишь бы сборка `cu128`.

---

## 6. Шаг 4 — скачать модели (в репозиторий НЕ входят, слишком большие)

Разложить строго по путям:

### 6.1 Vosk (распознавание речи) → `C:\jarvis-lab\resources\vosk\`
С https://alphacephei.com/vosk/models :
- `vosk-model-small-ru-0.22` (русский, обязательно)
- `vosk-model-en-us-0.22-lgraph`, `vosk-model-small-uk-v3-nano` (опц., англ./укр.)

### 6.2 Эмбеддинги (интент-классификатор) → `C:\jarvis-lab\resources\models\`
С HuggingFace (ONNX-версии):
- `all-MiniLM-L6-v2`
- `paraphrase-multilingual-MiniLM-L12-v2-onnx-Q`

### 6.3 F5-TTS русская модель → `C:\jarvis-voice\models\ru\`
С HuggingFace **`Misha24-10/F5-TTS_RUSSIAN`**:
- `F5TTS_v1_Base_v2\model_last_inference.safetensors`
- `F5TTS_v1_Base\vocab.txt`

Пути задаются в `tts_server.py` (`MODEL_CKPT`, `VOCAB`). Референс-голос `ref_jarvis_og.wav`
уже в репозитории (в `C:\jarvis-voice\`). RUAccent подтянет свои модели сам при первом запуске.

### 6.4 GLiNER (извлечение параметров, NER) → `C:\jarvis-lab\resources\models\gliner_multi-v2.1\`
С HuggingFace (ONNX int8 вариант `gliner_multi-v2.1`):
- `onnx\model.onnx` — **int8-файл сохранить именно под именем `model.onnx`** (загрузчик ищет это имя)
- `tokenizer.json`, `gliner_config.json`
- создать `model.toml`:
```toml
[model]
id = "gliner_multi-v2.1"
name = "GLiNER Multi v2.1 (int8)"
tasks = ["slots"]
description = "Многоязычная NER-модель для извлечения параметров из команд (русский)."
```
`post_build.py` скопирует `resources\` в `target\release\resources\` при сборке.

---

## 7. Шаг 5 — Ollama (мозг)

```powershell
ollama pull qwen2.5:3b          # основной (быстрый); при желании ещё qwen2.5:7b (умнее, тяжелее)
setx OLLAMA_KEEP_ALIVE -1       # держать модель в VRAM (ответы без задержки прогрузки)
```
Перезапустить Ollama после `setx`. Добавить Ollama в автозапуск.

---

## 8. Шаг 6 — сборка

```powershell
# Фронтенд (встраивается в jarvis-gui.exe):
cd C:\jarvis-lab\frontend
npm install
node node_modules/esbuild/install.js   # иначе vite может не собраться (postinstall esbuild блокируется)
npx vite build

# Rust (релиз):
cd C:\jarvis-lab
cargo build --release -p jarvis-gui
cargo build --release -p jarvis-app
py -3.11 post_build.py                  # копирует libvosk.dll, pv_recorder и resources в target\release
```
> При любых правках в `jarvis-core` (tts/brain/terminal/slots/settings) **пересобирайте `jarvis-app`**,
> а не только GUI — иначе бинарь читает старую логику. При правке фронта: `vite build` → сборка GUI.

---

## 9. Шаг 7 — конфигурация

Настройки живут в `app.db` (JSON-файл, **без BOM!**) по пути
`%APPDATA%\com.priler.jarvis\app.db`. Удобнее всего задать их через GUI (вкладки):

1. **Мозг / ИИ-модели:** `brain_backend = openai`, `openai_url = http://127.0.0.1:11434/v1`,
   `openai_model = qwen2.5:3b`.
2. **Имя (wake word):** по умолчанию «джарвис» (смена требует перезапуска — грамматика Vosk строится на старте).
3. **Т-Терминал:** вставить токен **T-Invest API** (только чтение) → поле `invest_token`.
4. **Нейросети:** «Извлечение параметров = GLiNER (NER)», модель `gliner_multi-v2.1`
   (переключатель записывает `slots_backend`; применяется после перезапуска `jarvis-app`).

Ключи и сценарии (файлы в `C:\jarvis-voice\`, шаблоны — в `jarvis-voice\config-examples\`):
- `nox_scenarios.json` — мозг сценариев (`ollama`/`claude`/`openai`) и ключи, список сценариев CScalp.
- `bea_key.txt` — **UserID BEA** (36 симв., https://apps.bea.gov/api/signup/) — для ВВП/PCE.
- `bls_key.txt` — ключ BLS (опционально, поднимает лимит с 25 до 500 запросов/сутки).

> ⚠️ Правьте `app.db` только редактором/скриптом в **UTF-8 без BOM**. PowerShell `Set-Content -Encoding UTF8`
> добавляет BOM → `jarvis-app` не парсит JSON и сбрасывает ВСЕ настройки в дефолт.

---

## 10. Шаг 8 — запуск

```powershell
powershell -ExecutionPolicy Bypass -File C:\jarvis-lab\start-jarvis.ps1
```
Поднимает голосовой сервер + ридеры + GUI. Либо запускайте сторожа — он сам поднимет весь стек
и будет перезапускать упавшее:
```powershell
C:\jarvis-voice\.venv\Scripts\python.exe C:\jarvis-voice\nox_watchdog.py
```

**Т-Terminal по CDP:** для `terminal_reader` нужен Яндекс.Браузер, запущенный с
`--remote-debugging-port=9222 --user-data-dir=C:\jarvis-voice\yandex_jarvis` и открытой страницей
терминала (логин в Т-Банк выполняется один раз вручную в этом профиле).

Проверка, что всё слушает:
```powershell
foreach($p in 8123,8124,8126,8127,8128,8129,8130,9712,11434){ "$p : $([bool](Get-NetTCPConnection -LocalPort $p -State Listen -EA SilentlyContinue))" }
```

---

## 11. Голосовые команды (примеры)

- **Портфель/котировки:** «мой портфель», «курс сбера», «стакан газпрома», «статус торгов».
- **Новости РФ/календарь:** «новости», «дивиденды», «календарь событий».
- **Order flow:** «магеллан», «поток по сберу».
- **США (BLS):** «когда нонфарм», «последние данные США», «инфляция в США» (CPI).
- **США (BEA):** «ВВП США», «PCE», «базовый PCE». «календарь США» показывает BLS и BEA вместе.
- **Сценарии CScalp:** «настрой сценарий … стакан 1/2/3», координаты ставятся наведением мыши + **F2**.
- **Управление:** «переключись на gpt/клод/авто», «не слушай» / «слушай меня».

---

## 12. Траблшутинг (грабли, уже пройденные)

- **Все настройки слетели в дефолт** → в `app.db` появился BOM. Перезаписать в UTF-8 без BOM.
- **Vosk не грузится / STT молчит** → путь с кириллицей. Папки должны быть в `C:\jarvis-lab` / `C:\jarvis-voice`.
- **Сервис под pythonw висит/падает** → запускать `python.exe` (не `pythonw.exe`); сервисы биндят порты только так.
- **F5 молчит, подключается голос Павла** → проверьте `tts_server` на 8123, ffmpeg в PATH, референс-голос на месте.
- **«Slot extraction disabled»** → `slots_backend` ≠ модель GLiNER, либо BOM в `app.db`, либо файл не `model.onnx`.
- **GLiNER int8 не грузится** → int8-файл должен называться `onnx\model.onnx`, рядом `tokenizer.json`.
- **Правки ядра не применились** → пересоберите `jarvis-app` (не только GUI).
- **Из РФ 403 на api.anthropic.com** (если используете Claude-мозг) → нужен VPN/`claude login`. Для Ollama-мозга не требуется.

---

## 13. Что НЕ входит в репозиторий (и почему)

Исключено `.gitignore`: `.venv`, `target`, `node_modules`, `frontend/dist`, крупные модели
(`resources/models`, `resources/vosk`, F5/GLiNER), профили браузеров (логин-сессии — приватность),
логи, сгенерированные `.wav`, рантайм-состояние (`*_state.json`, флаги), **ключи и `app.db`**.
Всё это либо восстанавливается по инструкции выше, либо задаётся заново.

Подробности о модулях — в `C:\jarvis-lab\HANDOFF.md`, `README-AI-BRAIN.md` и в `ВЫГРУЗКА_ЧАТА.md`.
