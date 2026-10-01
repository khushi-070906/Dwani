; DwaniLive installer (Inno Setup 6) -- built by `python build_exe.py`.
; Per-user install: no admin prompt, installs to %LOCALAPPDATA%\Programs\DwaniLive,
; Start-menu + desktop shortcuts, launches DwaniLive at the end.
; Models (~1.1 GB) live in %LOCALAPPDATA%\DwaniLive and survive upgrades.

#ifndef AppVersion
  #define AppVersion "1.1.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\DwaniLive"
#endif
#ifndef OutputDir
  #define OutputDir "..\release"
#endif

[Setup]
AppId={{6B2F3C1E-8D4A-4E0B-9A57-D7A1C0F2E9B4}
AppName=DwaniLive
AppVersion={#AppVersion}
AppVerName=DwaniLive {#AppVersion}
AppPublisher=DwaniLive
AppPublisherURL=https://dhwani-elit.onrender.com
DefaultDirName={localappdata}\Programs\DwaniLive
DefaultGroupName=DwaniLive
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=DwaniLive-Setup
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
; Close a running DwaniLive before overwriting its files (same mutex launcher.py holds).
AppMutex=Local\DwaniLiveSingleInstance
CloseApplications=yes
UninstallDisplayIcon={app}\DwaniLive.exe
#ifexist "..\assets\dwanilive.ico"
SetupIconFile=..\assets\dwanilive.ico
#endif

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[InstallDelete]
; Old version's files must not linger next to the new ones (stale .pyd/.dll mixes = startup crashes).
Type: filesandordirs; Name: "{app}\*"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\DwaniLive"; Filename: "{app}\DwaniLive.exe"; WorkingDir: "{app}"
Name: "{group}\DwaniLive (error report)"; Filename: "{cmd}"; Parameters: "/k ""{app}\DwaniLive.exe"" --diagnose"; WorkingDir: "{app}"
Name: "{group}\Uninstall DwaniLive"; Filename: "{uninstallexe}"
Name: "{userdesktop}\DwaniLive"; Filename: "{app}\DwaniLive.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\DwaniLive.exe"; Description: "Start DwaniLive now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    if MsgBox('Also delete downloaded models and logs (~1.1 GB in ' + ExpandConstant('{localappdata}\DwaniLive') + ')?',
              mbConfirmation, MB_YESNO) = IDYES then
      DelTree(ExpandConstant('{localappdata}\DwaniLive'), True, True, True);
end;
