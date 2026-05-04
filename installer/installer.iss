; =====================================================================
; Email Assistant - Inno Setup installer script
; ---------------------------------------------------------------------
; Wraps the PyInstaller `dist\EmailAssistant\` folder into a single
; double-clickable EmailAssistantSetup.exe with:
;   - Per-user install (no UAC, no admin)
;   - Start Menu shortcuts for the daemon, wizard, sign-in, and run-once
;   - "Add or Remove Programs" entry + clean uninstaller
;   - Optional Finish-page checkbox to launch the Setup Wizard
;   - Optional Finish-page checkbox to register the 5-minute Scheduled Task
;
; Build prerequisites:
;   1. Inno Setup 6+ installed (https://jrsoftware.org/isdl.php).
;   2. PyInstaller bundle present at ..\dist\EmailAssistant\
;
; Compile from a shell:
;     "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\installer.iss
; ...or run build_installer.ps1 which handles both PyInstaller and ISCC.
; =====================================================================

#define AppName        "Email Assistant"
#define AppPublisher   "Rocky"
#define AppVersion     "1.0.0"
#define AppExeName     "EmailAssistant.exe"
#define AppId          "{{8F2C7A91-3B4D-4E7F-9A2C-1D5E6F7A8B9C}}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppSupportURL=https://github.com/
AppUpdatesURL=https://github.com/
DefaultDirName={localappdata}\Programs\EmailAssistant
DefaultGroupName={#AppName}
DisableDirPage=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=EmailAssistantSetup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
UsePreviousAppDir=yes
; Set this to a path under installer\ once you have an icon:
; SetupIconFile=app.ico
LicenseFile=
InfoBeforeFile=
; Show a small banner explaining what we are
WizardSmallImageFile=
WizardImageFile=

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startmenuicon"; Description: "Create Start Menu shortcuts"; GroupDescription: "Shortcuts:"; Flags: checkedonce
Name: "desktopicon"; Description: "Create a Desktop shortcut for the Setup Wizard"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
; Pull in the entire PyInstaller one-folder bundle.
Source: "..\dist\EmailAssistant\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Helper PowerShell script for the Scheduled Task action on the Finish page.
Source: "..\scripts\install_task.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion

[Dirs]
; Per-user data folder. Inno marks it 'uninsneveruninstall' so an
; uninstall cleanly removes the program but preserves your .env,
; state.db, and OAuth tokens. A reinstall picks them up.
Name: "{localappdata}\EmailAssistant"; Flags: uninsneveruninstall

[Icons]
Name: "{group}\Email Assistant (Run continuously)"; \
    Filename: "{app}\{#AppExeName}"; \
    WorkingDir: "{app}"; \
    Comment: "Start the polling daemon (Ctrl+C to stop)"; \
    Tasks: startmenuicon

Name: "{group}\Email Assistant - Setup Wizard"; \
    Filename: "{app}\{#AppExeName}"; \
    Parameters: "--setup"; \
    WorkingDir: "{app}"; \
    Comment: "Build / edit your .env file (mailbox, LLM, notifications)"; \
    Tasks: startmenuicon

Name: "{group}\Email Assistant - Sign In"; \
    Filename: "{app}\{#AppExeName}"; \
    Parameters: "--auth"; \
    WorkingDir: "{app}"; \
    Comment: "One-time OAuth sign-in to your mailbox / calendar"; \
    Tasks: startmenuicon

Name: "{group}\Email Assistant - Run Once (smoke test)"; \
    Filename: "{app}\{#AppExeName}"; \
    Parameters: "--once"; \
    WorkingDir: "{app}"; \
    Comment: "Process current unread mail one time, then exit"; \
    Tasks: startmenuicon

Name: "{group}\Open Data Folder"; \
    Filename: "{localappdata}\EmailAssistant"; \
    Comment: "Open the folder containing .env, state.db, and tokens"; \
    Tasks: startmenuicon

Name: "{group}\Uninstall {#AppName}"; \
    Filename: "{uninstallexe}"; \
    Tasks: startmenuicon

Name: "{userdesktop}\Email Assistant - Setup"; \
    Filename: "{app}\{#AppExeName}"; \
    Parameters: "--setup"; \
    WorkingDir: "{app}"; \
    Comment: "Run the Email Assistant setup wizard"; \
    Tasks: desktopicon

[Run]
; Optional Finish-page actions. Both default to UNCHECKED so a fresh
; install is conservative; the user has to opt into running the wizard
; or scheduling the task.

Filename: "{app}\{#AppExeName}"; \
    Parameters: "--setup"; \
    WorkingDir: "{app}"; \
    Description: "Run the Setup Wizard now (build .env, validate credentials)"; \
    Flags: postinstall nowait skipifsilent unchecked

Filename: "powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\install_task.ps1"""; \
    WorkingDir: "{app}"; \
    Description: "Schedule the agent to run every 5 minutes (Task Scheduler)"; \
    Flags: postinstall nowait skipifsilent unchecked

[UninstallRun]
; If the Scheduled Task was created, remove it on uninstall (best-effort).
Filename: "powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\install_task.ps1"" -Uninstall"; \
    Flags: runhidden; \
    RunOnceId: "RemoveSchedTask"

[UninstallDelete]
; Belt-and-braces cleanup: remove anything we generated inside {app}.
Type: filesandordirs; Name: "{app}"

; Compile-time guard: fail the ISCC build with a clear message when the
; PyInstaller output isn't present (i.e. someone ran ISCC standalone
; without running PyInstaller first). build_installer.ps1 chains both,
; so this only fires for manual runs.
#if !FileExists(AddBackslash(SourcePath) + "..\dist\EmailAssistant\EmailAssistant.exe")
  #error "PyInstaller output not found at ..\dist\EmailAssistant\EmailAssistant.exe. Run build_installer.ps1 (or `pyinstaller EmailAssistant.spec --clean --noconfirm`) before compiling the installer."
#endif
