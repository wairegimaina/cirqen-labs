; Cirqen for Windows: Cirqen-Setup-<version>.exe (Inno Setup 6).
;
; Built by .github/workflows/windows-build.yml after build.py has made
; dist\Cirqen. Installs for the current user, without admin rights, into
; %LOCALAPPDATA%\Programs\Cirqen: that folder belongs to the user, so
; Cirqen's own updates can replace files in it.
;
; The database is embedded (bulider_tools/embedded_pg.py): PostgreSQL ships
; inside the app and Cirqen runs it itself, so installing needs no service, no
; administrator and no database step. What Cirqen keeps on the PC:
;   %LOCALAPPDATA%\Programs\Cirqen   the app
;   %APPDATA%\cirqen                 settings, logs, media
;   %LOCALAPPDATA%\Cirqen\db         the database
; Uninstalling stops Cirqen and deletes all three.
;
; Cirqen 1.6.x set up a CirqenPostgreSQL service with admin rights. When one
; is found, installing and uninstalling offer to remove it (a UAC prompt).
;
; A hospital's installer: set CIRQEN_PROVISIONING to its installer file from
; the admin panel; it is installed next to Cirqen.exe and read on first start.

#define AppVersion GetEnv("CIRQEN_VERSION")
#if AppVersion == ""
  #define AppVersion "0.0.0"
#endif
#define Provisioning GetEnv("CIRQEN_PROVISIONING")
#define Hospital GetEnv("CIRQEN_HOSPITAL")

[Setup]
AppId={{6F3B2C1E-9D4A-4E57-8C21-CB8E0A5D7F31}
AppName=Cirqen
AppVersion={#AppVersion}
AppVerName=Cirqen {#AppVersion}
AppPublisher=Cirqen Labs
AppPublisherURL=https://cirqenlabs.com
AppSupportURL=https://cirqenlabs.com
DefaultDirName={localappdata}\Programs\Cirqen
DisableDirPage=yes
DefaultGroupName=Cirqen
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
#if Hospital != ""
OutputBaseFilename=Cirqen-Setup-{#AppVersion}-{#Hospital}
#else
OutputBaseFilename=Cirqen-Setup-{#AppVersion}
#endif
SetupIconFile=cirqen.ico
UninstallDisplayIcon={app}\Cirqen.exe
UninstallDisplayName=Cirqen
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Cirqen is stopped by stop_cirqen.ps1 (PrepareToInstall), database included.
CloseApplications=no
RestartApplications=no

[Messages]
ConfirmUninstall=Remove Cirqen and everything it keeps on this PC: the app, its settings, logs and its database?%n%nRecords that this PC has not yet sent to HQ will be lost.

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
; Run before any file is replaced (PrepareToInstall), so not copied there.
Source: "stop_cirqen.ps1"; Flags: dontcopy
Source: "remove_legacy_service.ps1"; Flags: dontcopy
Source: "..\dist\Cirqen\*"; DestDir: "{app}"; Excludes: "provisioning.json"; Flags: ignoreversion recursesubdirs createallsubdirs
; The uninstaller's copies.
Source: "stop_cirqen.ps1"; DestDir: "{app}\uninstall"; Flags: ignoreversion
Source: "remove_legacy_service.ps1"; DestDir: "{app}\uninstall"; Flags: ignoreversion
#if Provisioning != ""
; The hospital's installer file; kept if this PC already has one (an upgrade).
Source: "{#Provisioning}"; DestDir: "{app}"; DestName: "provisioning.json"; Flags: onlyifdoesntexist
#endif

[Icons]
Name: "{autoprograms}\Cirqen"; Filename: "{app}\Cirqen.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Cirqen"; Filename: "{app}\Cirqen.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Cirqen.exe"; Description: "Start Cirqen"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Everything Cirqen made after installing: code updates, settings, logs,
; media, the database, Qt's web cache.
Type: filesandordirs; Name: "{app}"
Type: filesandordirs; Name: "{userappdata}\cirqen"
Type: filesandordirs; Name: "{localappdata}\Cirqen"
Type: filesandordirs; Name: "{localappdata}\B12 Technologies\Cirqen"
Type: dirifempty; Name: "{localappdata}\B12 Technologies"

[Code]
const
  LegacyServiceKey = 'SYSTEM\CurrentControlSet\Services\CirqenPostgreSQL';

function PowerShell(Script, Params: String; Elevated: Boolean): Boolean;
var
  ResultCode: Integer;
  Verb: String;
begin
  if Elevated then Verb := 'runas' else Verb := '';
  // {sysnative}: the 64-bit PowerShell. This installer is 32-bit, and a plain
  // powershell.exe would be the 32-bit one.
  Result := ShellExec(Verb, ExpandConstant('{sysnative}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + Script + '" ' + Params,
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
  if not Result then
    Log('PowerShell ' + Script + ' failed: ' + IntToStr(ResultCode));
end;

procedure StopCirqen(Script: String);
begin
  if DirExists(ExpandConstant('{app}')) then
    PowerShell(Script, '-AppDir "' + ExpandConstant('{app}') + '"', False);
end;

function HasLegacyService(): Boolean;
begin
  Result := RegKeyExists(HKLM, LegacyServiceKey);
end;

procedure RemoveLegacyService(Script: String; Ask: Boolean; DefaultAnswer: Integer);
begin
  if not HasLegacyService() then
    Exit;
  if Ask and (SuppressibleMsgBox(
      'This PC still has the database service an older Cirqen set up (CirqenPostgreSQL).' + #13#10#13#10 +
      'Cirqen no longer uses it: it now keeps its own database and fills it from HQ.' + #13#10 +
      'Remove the old service and its files now? Windows will ask for an administrator.',
      mbConfirmation, MB_YESNO, DefaultAnswer) <> IDYES) then
    Exit;
  PowerShell(Script, '', True);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  ExtractTemporaryFile('stop_cirqen.ps1');
  ExtractTemporaryFile('remove_legacy_service.ps1');
  WizardForm.PreparingLabel.Caption := 'Closing Cirqen...';
  StopCirqen(ExpandConstant('{tmp}\stop_cirqen.ps1'));
  // A silent install (IT tools) leaves the old service alone.
  RemoveLegacyService(ExpandConstant('{tmp}\remove_legacy_service.ps1'), True, IDNO);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    // Before any file goes: Cirqen and its database must not be running.
    StopCirqen(ExpandConstant('{app}\uninstall\stop_cirqen.ps1'));
    RemoveLegacyService(ExpandConstant('{app}\uninstall\remove_legacy_service.ps1'), False, IDYES);
  end;
end;
