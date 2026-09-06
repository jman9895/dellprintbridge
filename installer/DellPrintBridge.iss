#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif

#define MyAppName "DellPrintBridge"
#define MyAppPublisher "Josh Nichols"
#define MyAppURL "https://github.com/jman9895/dellprintbridge"
#define MyAppExeName "DellPrintBridge.exe"

[Setup]
AppId={{5BB015C4-8B59-4AF1-8CB5-A9A924D0EC43}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\DellPrintBridge
DefaultGroupName=DellPrintBridge
DisableProgramGroupPage=yes
OutputBaseFilename=DellPrintBridge-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName=DellPrintBridge
SetupLogging=yes
CloseApplications=yes
RestartApplications=no

[Files]
Source: "..\build\dist\backend\DellPrintBridge\*"; DestDir: "{app}\backend"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\build\dist\tray\DellPrintBridgeTray\*"; DestDir: "{app}\tray"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "install-runtime.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "uninstall-runtime.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion

[Icons]
Name: "{group}\DellPrintBridge Web Console"; Filename: "http://localhost:8631/"

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\installer\install-runtime.ps1"" -InstallRoot ""{app}"""; StatusMsg: "Configuring DellPrintBridge..."; Flags: runhidden waituntilterminated
Filename: "http://localhost:8631/"; Description: "Open DellPrintBridge web console"; Flags: postinstall shellexec skipifsilent unchecked

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\installer\uninstall-runtime.ps1"" -InstallRoot ""{app}"""; Flags: runhidden waituntilterminated; RunOnceId: "DellPrintBridgeCleanup"
