# Nox — памятка по установке Windows 10/11

Nox — локальный голосовой ассистент на Rust/Tauri и Python. Торговые данные и терминал предназначены только для чтения; финальные действия выполняет пользователь.

## Требования

- Windows 10/11 x64
- NVIDIA GPU с CUDA 12.8 желательно для F5-TTS и Ollama (без GPU запуск возможен, но медленнее)
- микрофон и около 15 ГБ свободного места
- пути установки только ASCII: `C:\jarvis-lab` и `C:\jarvis-voice`

## Установить программы

1. Git: https://git-scm.com/download/win
2. Rustup/MSVC: https://rustup.rs/
3. Node.js LTS: https://nodejs.org/en/download
4. Python 3.11: https://www.python.org/downloads/release/python-3119/
5. Visual Studio Build Tools: https://visualstudio.microsoft.com/visual-cpp-build-tools/
   - выбрать workload **Desktop development with C++** и Windows SDK.
6. WebView2 Runtime: https://developer.microsoft.com/microsoft-edge/webview2/
7. Ollama: https://ollama.com/download/windows
8. FFmpeg: https://ffmpeg.org/download.html
9. Яндекс Браузер: https://browser.yandex.com/download/
10. NVIDIA driver: https://www.nvidia.com/Download/index.aspx

## Получение исходников

```powershell
git clone https://github.com/MrBeggins/nox-backup.git C:\nox
robocopy C:\nox\jarvis-lab C:\jarvis-lab /E
robocopy C:\nox\jarvis-voice C:\jarvis-voice /E
```

Не копируйте в публичный репозиторий `app.db`, ключи, cookies, браузерные профили и модели.

## Python-окружение и голос

```powershell
cd C:\jarvis-voice
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m playwright install chromium
```

Проверка CUDA:

```powershell
.\.venv\Scripts\python -c "import torch; print(torch.cuda.is_available())"
```

Модели F5-TTS: https://huggingface.co/Misha24-10/F5-TTS_RUSSIAN
Модели Vosk: https://alphacephei.com/vosk/models

## Ollama

```powershell
ollama pull qwen2.5:3b
```

Сайт моделей: https://ollama.com/library

## Сборка

```powershell
cd C:\jarvis-lab
cd frontend
npm install
npx vite build
cd ..
cargo build --release -p jarvis-gui -p jarvis-app
python post_build.py
```

Запуск: `C:\jarvis-lab\target\release\jarvis-gui.exe`.

## Настройки и приватные данные

Настройки GUI находятся в `%APPDATA%\com.priler.jarvis\app.db`. Файл должен быть JSON без BOM. API-ключи и токены вводите локально в приложении; не отправляйте их в GitHub.

Профили `browser_profile`, `mind_profile`, `yandex_jarvis` содержат cookies и сессии — храните их только локально.

## Возможные проблемы

- Ошибки Vosk из-за кириллицы в пути: используйте только `C:\jarvis-lab` и `C:\jarvis-voice`.
- Нет голоса: проверьте драйвер NVIDIA, CUDA/PyTorch и наличие модели F5-TTS.
- Нет распознавания: проверьте модель Vosk и микрофон Windows.
- Не запускается GUI: установите WebView2 Runtime и Visual C++ Build Tools.
- Порты и архитектура сервисов описаны в `README.md`.

## Безопасность

Не включайте автоматическое подтверждение финансовых операций. Nox не должен самостоятельно нажимать Buy/Sell или выполнять переводы.
