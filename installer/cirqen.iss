; Cirqen for Windows: Cirqen-Setup-<version>.exe (Inno Setup 6).
;
; Built by .github/workflows/windows-build.yml after build.py has made
; dist\Cirqen. Installs for the current user, without admin rights, into
; %LOCALAPPDATA%\Programs\Cirqen: that folder belongs to the user, so
; Cirqen's own updates can replace files in it. Records and settings live in
; %APPDATA%\cirqen and are kept on uninstall.
;
; The database is a Windows service, CirqenPostgreSQL (NetworkService,
; automatic start), set up once with admin rights by `Cirqen.exe
; system-postgres` (a UAC prompt): PostgreSQL in Program Files\Cirqen, its
; data and connection settings in %ProgramData%\Cirqen. Both stay on
; uninstall, like the records. See bulider_tools/system_pg.py.
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
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\Cirqen\*"; DestDir: "{app}"; Excludes: "provisioning.json"; Flags: ignoreversion recursesubdirs createallsubdirs
#if Provisioning != ""
; The hospital's installer file; kept if this PC already has one (an upgrade).
Source: "{#Provisioning}"; DestDir: "{app}"; DestName: "provisioning.json"; Flags: onlyifdoesntexist
#endif

[Icons]
Name: "{autoprograms}\Cirqen"; Filename: "{app}\Cirqen.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Cirqen"; Filename: "{app}\Cirqen.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Cirqen.exe"; Parameters: "system-postgres"; Verb: "runas"; Flags: shellexec waituntilterminated runhidden; StatusMsg: "Setting up the Cirqen database service..."
Filename: "{app}\Cirqen.exe"; Description: "Start Cirqen"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
