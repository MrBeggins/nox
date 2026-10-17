; ============================================================================
;  Nox — установщик (Inno Setup 6). Собирается скриптом ..\build.py:
;  он готовит ..\payload и вызывает ISCC с /DAppVersion=<версия>.
; ============================================================================
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
; Корень установки. Пути C:\jarvis-lab и C:\jarvis-voice зашиты в программу; другой корень —
; только для проверочной сборки (/DTestBuild /DRoot=C:\noxtest): она ставится рядом и не трогает рабочий Nox.
#ifndef Root
  #define Root "C:"
#endif
#define LabDir Root + "\jarvis-lab"
#define VoiceDir Root + "\jarvis-voice"
#ifdef TestBuild
  #define AppTitle "Nox (проверочная сборка)"
  #define Group "Nox-test"
  ; в параметрах [Run]/[Icons] кавычки удваиваются, в Pascal-коде — нет
  #define ScriptRoot ' -Root ""' + Root + '"" -AppDataDir ""' + Root + '\appdata\com.priler.jarvis""'
  #define ScriptRootCode ' -Root "' + Root + '" -AppDataDir "' + Root + '\appdata\com.priler.jarvis"'
#else
  #define AppTitle "Nox"
  #define Group "Nox"
  #define ScriptRoot ""
  #define ScriptRootCode ""
#endif

[Setup]
#ifdef TestBuild
AppId={{3B7D9E14-2C55-4F0A-8A11-7E6C4D2B9F01}
#else
AppId={{8F3C2A51-6D0B-4E7A-9C41-2B7E5D1A9F60}
#endif
AppName={#AppTitle}
AppVersion={#AppVersion}
AppVerName={#AppTitle} {#AppVersion}
AppPublisher=MrBeggins
AppPublisherURL=https://github.com/MrBeggins/nox
AppSupportURL=https://github.com/MrBeggins/nox/issues
AppUpdatesURL=https://github.com/MrBeggins/nox/releases
VersionInfoDescription=Nox — голосовой помощник трейдера
; пути зашиты в программу (Rust и Python-службы), поэтому выбор папки отключён
DefaultDirName={#LabDir}
DisableDirPage=yes
DefaultGroupName={#Group}
DisableProgramGroupPage=yes
; ставим без прав администратора: Python, Ollama и настройки — в профиль текущего пользователя
PrivilegesRequired=lowest
UsedUserAreasWarning=no
CloseApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=..\dist
#ifdef TestBuild
OutputBaseFilename=Nox-Setup-TEST-{#AppVersion}
#else
OutputBaseFilename=Nox-Setup-{#AppVersion}
#endif
SetupIconFile=..\payload\jarvis-lab\nox.ico
UninstallDisplayIcon={#LabDir}\nox.ico
UninstallDisplayName={#AppTitle}
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
Source: "..\payload\jarvis-lab\*"; DestDir: "{#LabDir}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\payload\jarvis-voice\*"; DestDir: "{#VoiceDir}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
Name: "{#VoiceDir}\installer"

[Icons]
Name: "{autodesktop}\Nox"; Filename: "{sys}\wscript.exe"; Parameters: """{#LabDir}\Jarvis.vbs"""; WorkingDir: "{#LabDir}"; IconFilename: "{#LabDir}\nox.ico"; Comment: "Запустить Nox"; Tasks: desktopicon
Name: "{autoprograms}\{#Group}\Nox"; Filename: "{sys}\wscript.exe"; Parameters: """{#LabDir}\Jarvis.vbs"""; WorkingDir: "{#LabDir}"; IconFilename: "{#LabDir}\nox.ico"; Comment: "Запустить Nox"
Name: "{autoprograms}\{#Group}\Nox — окно Т-Терминала (Яндекс)"; Filename: "{#LabDir}\Терминал-Яндекс.cmd"; WorkingDir: "{#LabDir}"; IconFilename: "{#LabDir}\nox.ico"
Name: "{autoprograms}\{#Group}\Nox — починить установку"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{#VoiceDir}\installer\setup-nox.ps1""{#ScriptRoot}"; WorkingDir: "{#VoiceDir}"; IconFilename: "{#LabDir}\nox.ico"
Name: "{autoprograms}\{#Group}\Nox — остановить"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{#VoiceDir}\installer\stop-nox.ps1""{#ScriptRoot}"; IconFilename: "{#LabDir}\nox.ico"
Name: "{autoprograms}\{#Group}\Удалить Nox"; Filename: "{uninstallexe}"

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{#VoiceDir}\installer\setup-nox.ps1""{#ScriptRoot} {code:SetupArgs}"; WorkingDir: "{#VoiceDir}"; StatusMsg: "Ставлю Python, библиотеки и модели (10–30 минут, зависит от интернета)..."; Flags: waituntilterminated
Filename: "{sys}\wscript.exe"; Parameters: """{#LabDir}\Jarvis.vbs"""; Description: "Запустить Nox"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{#VoiceDir}\installer\stop-nox.ps1"" -Uninstall{#ScriptRoot}"; Flags: runhidden waituntilterminated; RunOnceId: "StopNox"

[UninstallDelete]
; то, что появилось после установки. Личные файлы (наблюдатели, заметки, профиль браузера,
; журнал сделок) и настройки в %APPDATA%\com.priler.jarvis остаются — удалите вручную, если не нужны.
Type: filesandordirs; Name: "{#VoiceDir}\.venv"
Type: filesandordirs; Name: "{#VoiceDir}\.venv-xtts"
Type: filesandordirs; Name: "{#VoiceDir}\.xtts-gpu"
Type: filesandordirs; Name: "{#VoiceDir}\downloads"
#ifndef TestBuild
Type: filesandordirs; Name: "{localappdata}\tts\tts_models--multilingual--multi-dataset--xtts_v2"
Type: dirifempty; Name: "{localappdata}\tts"
#endif
Type: files; Name: "{#VoiceDir}\xtts_ready.flag"
Type: files; Name: "{#VoiceDir}\xtts_test*.wav"
Type: files; Name: "{#VoiceDir}\installer\options.txt"
Type: filesandordirs; Name: "{#VoiceDir}\models"
Type: filesandordirs; Name: "{#VoiceDir}\__pycache__"
Type: files; Name: "{#VoiceDir}\*.log"
Type: files; Name: "{#VoiceDir}\nox_stopped.flag"
Type: dirifempty; Name: "{#VoiceDir}"
Type: dirifempty; Name: "{#LabDir}"

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
  if WizardSilent then Result := Result + ' -NoPause';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  rc: Integer;
begin
  { перед обновлением гасим запущенный Nox, иначе exe и службы заняты }
  ExtractTemporaryFile('stop-nox.ps1');
  Exec('powershell.exe', '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' +
       ExpandConstant('{tmp}\stop-nox.ps1') + '"{#ScriptRootCode}', '', SW_HIDE, ewWaitUntilTerminated, rc);
  Result := '';
end;
