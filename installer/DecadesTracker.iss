#define MyAppName "Decades Tracker"
#define MyAppVersion "4.2.8"
#define MyAppPublisher "SeveralUDO"
#define MyAppExeName "Decades Tracker.exe"

[Setup]
AppId={{9D9EFDB8-CE10-46C0-B42E-42237C7896E4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} 4.2.8
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\DecadesTracker
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\release
OutputBaseFilename=Decades-Tracker-4.2.8-Setup
SetupIconFile=..\assets\decades-app-icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
CloseApplications=yes
RestartApplications=no
VersionInfoVersion=4.2.8.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} installer
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion=4.2.8.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a Desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: checkedonce

[Files]
Source: "..\dist\Decades Tracker\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Open {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
function WebView2Installed(): Boolean;
var
  Version: String;
begin
  Result :=
    (RegQueryStringValue(HKLM32,
      'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
      'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKCU,
      'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
      'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Result := WebView2Installed();
  if not Result then
  begin
    if MsgBox(
      'Decades Tracker requires the Microsoft Edge WebView2 Runtime. ' +
      'Would you like to open Microsoft''s official download page now?',
      mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', 'https://developer.microsoft.com/microsoft-edge/webview2/', '', '', SW_SHOWNORMAL, ewNoWait, ResultCode);
    MsgBox('Install the WebView2 Runtime, then run this installer again.', mbInformation, MB_OK);
  end;
end;
