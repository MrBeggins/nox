; ============================================================================
;  Nox — установщик (Inno Setup 6). Собирается скриптом ..\build.py:
;  он готовит ..\payload и вызывает ISCC с /DAppVersion=<версия>.
; ============================================================================
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{8F3C2A51-6D0B-4E7A-9C41-2B7E5D1A9F60}
AppName=Nox
AppVersion={#AppVersion}
AppVerName=Nox {#AppVersion}
AppPublisher=MrBeggins
AppPublisherURL=https://github.com/MrBeggins/nox
AppSupportURL=https://github.com/MrBeggins/nox/issues
AppUpdatesURL=https://github.com/MrBeggins/nox/releases
VersionInfoDescription=Nox — голосовой помощник трейдера
; пути зашиты в программу (Rust и Python-службы), поэтому выбор папки отключён
DefaultDirName=C:\jarvis-lab
DisableDirPage=yes
DefaultGroupName=Nox
DisableProgramGroupPage=yes
; ставим без прав администратора: Python, Ollama и настройки — в профиль текущего пользователя
PrivilegesRequired=lowest
UsedUserAreasWarning=no
CloseApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=..\dist
OutputBaseFilename=Nox-Setup-{#AppVersion}
SetupIconFile=..\payload\jarvis-lab\nox.ico
UninstallDisplayIcon=C:\jarvis-lab\nox.ico
UninstallDisplayName=Nox
LicenseFile=license-ru.txt
InfoBeforeFile=before-install.txt
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4
; .venv (~1,5 ГБ) и Ollama с моделью (~3 ГБ) появятся после распаковки
ExtraDiskSpaceRequired=4500000000

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Ярлык Nox на рабочем столе"
Name: "ollama"; Description: "Локальный бесплатный мозг: Ollama + модель qwen2.5:3b (скачается ~3 ГБ)"
Name: "yandex"; Description: "Яндекс.Браузер для окна Т-Терминала (не найден на этом компьютере)"; Check: not YandexInstalled
Name: "voices"; Description: "Голос Джарвиса — клон по образцу (только видеокарта NVIDIA; общий torch для видеокарты ~3 ГБ):"; Flags: unchecked; Check: HasNvidia
Name: "voices\f5"; Description: "F5-TTS — модель ~1,4 ГБ"; Flags: unchecked; Check: HasNvidia
Name: "voices\xtts"; Description: "XTTS v2 — модель ~1,9 ГБ + своё окружение ~1 ГБ"; Flags: unchecked; Check: HasNvidia

[Files]
Source: "stop-nox.ps1"; Flags: dontcopy
Source: "..\payload\jarvis-lab\*"; DestDir: "C:\jarvis-lab"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\payload\jarvis-voice\*"; DestDir: "C:\jarvis-voice"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
Name: "C:\jarvis-voice\installer"

[Icons]
Name: "{autodesktop}\Nox"; Filename: "{sys}\wscript.exe"; Parameters: """C:\jarvis-lab\Jarvis.vbs"""; WorkingDir: "C:\jarvis-lab"; IconFilename: "C:\jarvis-lab\nox.ico"; Comment: "Запустить Nox"; Tasks: desktopicon
Name: "{autoprograms}\Nox\Nox"; Filename: "{sys}\wscript.exe"; Parameters: """C:\jarvis-lab\Jarvis.vbs"""; WorkingDir: "C:\jarvis-lab"; IconFilename: "C:\jarvis-lab\nox.ico"; Comment: "Запустить Nox"
Name: "{autoprograms}\Nox\Nox — окно Т-Терминала (Яндекс)"; Filename: "C:\jarvis-lab\Терминал-Яндекс.cmd"; WorkingDir: "C:\jarvis-lab"; IconFilename: "C:\jarvis-lab\nox.ico"
Name: "{autoprograms}\Nox\Nox — починить установку"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""C:\jarvis-voice\installer\setup-nox.ps1"""; WorkingDir: "C:\jarvis-voice"; IconFilename: "C:\jarvis-lab\nox.ico"
Name: "{autoprograms}\Nox\Nox — остановить"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""C:\jarvis-voice\installer\stop-nox.ps1"""; IconFilename: "C:\jarvis-lab\nox.ico"
Name: "{autoprograms}\Nox\Удалить Nox"; Filename: "{uninstallexe}"

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""C:\jarvis-voice\installer\setup-nox.ps1"" {code:SetupArgs}"; WorkingDir: "C:\jarvis-voice"; StatusMsg: "Ставлю Python, библиотеки и модели (10–30 минут, зависит от интернета)..."; Flags: waituntilterminated
Filename: "{sys}\wscript.exe"; Parameters: """C:\jarvis-lab\Jarvis.vbs"""; Description: "Запустить Nox"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""C:\jarvis-voice\installer\stop-nox.ps1"" -Uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "StopNox"

[UninstallDelete]
; то, что появилось после установки. Личные файлы (наблюдатели, заметки, профиль браузера,
; журнал сделок) и настройки в %APPDATA%\com.priler.jarvis остаются — удалите вручную, если не нужны.
Type: filesandordirs; Name: "C:\jarvis-voice\.venv"
Type: filesandordirs; Name: "C:\jarvis-voice\.venv-xtts"
Type: filesandordirs; Name: "C:\jarvis-voice\.xtts-gpu"
Type: filesandordirs; Name: "C:\jarvis-voice\downloads"
Type: filesandordirs; Name: "{localappdata}\tts\tts_models--multilingual--multi-dataset--xtts_v2"
Type: dirifempty; Name: "{localappdata}\tts"
Type: files; Name: "C:\jarvis-voice\xtts_ready.flag"
Type: files; Name: "C:\jarvis-voice\xtts_test*.wav"
Type: files; Name: "C:\jarvis-voice\installer\options.txt"
Type: filesandordirs; Name: "C:\jarvis-voice\models"
Type: filesandordirs; Name: "C:\jarvis-voice\__pycache__"
Type: files; Name: "C:\jarvis-voice\*.log"
Type: files; Name: "C:\jarvis-voice\nox_stopped.flag"
Type: dirifempty; Name: "C:\jarvis-voice"
Type: dirifempty; Name: "C:\jarvis-lab"

[Code]
function YandexInstalled: Boolean;
begin
  Result := FileExists(ExpandConstant('{commonpf64}\Yandex\YandexBrowser\Application\browser.exe')) or
            FileExists(ExpandConstant('{commonpf32}\Yandex\YandexBrowser\Application\browser.exe')) or
            FileExists(ExpandConstant('{localappdata}\Yandex\YandexBrowser\Application\browser.exe'));
end;

function HasNvidia: Boolean;
begin
  { драйвер NVIDIA кладёт nvidia-smi.exe в System32 }
  Result := FileExists(ExpandConstant('{sys}\nvidia-smi.exe'));
end;

function SetupArgs(Param: String): String;
begin
  Result := '';
  if WizardIsTaskSelected('ollama') then Result := Result + ' -Ollama';
  if WizardIsTaskSelected('yandex') then Result := Result + ' -Yandex';
  if WizardIsTaskSelected('voices\f5') then Result := Result + ' -F5';
  if WizardIsTaskSelected('voices\xtts') then Result := Result + ' -Xtts';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  rc: Integer;
begin
  { перед обновлением гасим запущенный Nox, иначе exe и службы заняты }
  ExtractTemporaryFile('stop-nox.ps1');
  Exec('powershell.exe', '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' +
       ExpandConstant('{tmp}\stop-nox.ps1') + '"', '', SW_HIDE, ewWaitUntilTerminated, rc);
  Result := '';
end;
