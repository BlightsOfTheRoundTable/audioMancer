; Inno Setup script for DM Mixer.
;
; Build (from the repo root, after the PyInstaller build has produced dist\dm-mixer\):
;   iscc packaging\windows\installer.iss /DMyAppVersion=1.0.0
;
; MyAppVersion defaults to 1.0.0 below if not passed via /D, so a plain `iscc installer.iss`
; still works for local testing. CI passes the real version parsed from pyproject.toml so the
; two never drift.

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

#define MyAppName "DM Mixer"
#define MyAppPublisher "Blights of the Round Table"
#define MyAppExeName "dm-mixer.exe"
#define MyAppURL "https://github.com/BlightsOfTheRoundTable/audioMancer"

[Setup]
; Fixed once, generated for this app - never change this, it's what lets future versions
; upgrade in place instead of installing side-by-side.
AppId={{6CCDEB50-67F9-4101-8FCD-B1A845837E96}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
; Per-user install under %LOCALAPPDATA%, not Program Files - needs no UAC elevation, so an
; unsigned installer only shows SmartScreen's "unrecognized publisher" warning once instead of
; SmartScreen *and* a UAC elevation prompt back to back.
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
OutputDir=Output
OutputBaseFilename=DM-Mixer-Setup-{#MyAppVersion}
UninstallDisplayIcon={app}\{#MyAppExeName}
; No LicenseFile= - no LICENSE file exists in the repo yet for v1.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; dist\dm-mixer is PyInstaller's onedir COLLECT output - see packaging/pyinstaller.spec.
Source: "..\..\dist\dm-mixer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
