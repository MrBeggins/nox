# Nox feature snapshot

Изолированный подраздел с резервной копией исходников функций:

- STOP на пульсе: прерывание TTS и отслеживаемых внешних команд;
- окно продолжения диалога 15 секунд после ответа;
- frontend-сборка выполняется из `jarvis-lab/frontend`.

Это не смешивается с основными файлами репозитория. Файлы размещены в `backup/jarvis-lab/` и предназначены для сравнения или восстановления.

## Восстановление

Скопируйте содержимое `backup/jarvis-lab/` поверх соответствующих файлов рабочего `C:\jarvis-lab`, затем пересоберите:

```powershell
cd C:\jarvis-lab\frontend
npm install
npx vite build
cd ..
cargo build --release -p jarvis-app -p jarvis-gui
python post_build.py
```

В подраздел намеренно не включены `target`, модели, токены, `app.db`, cookies, браузерные профили, логи и аудиофайлы.
