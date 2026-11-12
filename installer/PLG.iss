; Inno Setup script for Pluggin' (PLG). Build:  ISCC.exe installer\PLG.iss
; Output: dist\installer\PLG_Setup.exe
;
; Packs ONLY dist\PLG.exe. The exe is a self-contained PyInstaller onefile build
; (UI, starter sounds and the .env.release template are inside it). It seeds its
; own .env on first run (app_config.seed_env_from_bundle), so no .env, source,
; node_modules, .git, tests, old builds or _MEI* folders are shipped.
;
; Per-user install on purpose: PLG writes next to its exe (.env, plg_device.json,
; plg_session.log, PLG_Library, PLG_MIDI, Projects — everything under app_dir()).
; Program Files is not writable for a normal user, so an installed copy there
; would silently fail to save its config and material.

#define AppName "Pluggin'"
#define AppVersion "1.0.0"
#define AppPublisher "Pluggin'"
#define AppURL "https://example.com"
#define AppExe "PLG.exe"

[Setup]
AppId={{6C1F0B54-5B7E-4E43-9D0A-3F2A7E9C41D8}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=PLG_Setup
SetupIconFile=..\assets\logo.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; No LicenseFile: the repo's terms (legal\TERMS_*.md) are Markdown, not a
; ready EULA text, and the app already asks for acceptance at sign-up.

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
