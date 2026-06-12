#!/usr/bin/env python3
"""
Cirqen Installer Creator
Creates professional installers for Windows (NSIS/Inno Setup) and Linux (DEB/RPM)
"""

import sys
import os
import platform
import subprocess
import shutil
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"

PROJECT_ROOT = Path(__file__).parent.resolve()
DIST_DIR = PROJECT_ROOT / "dist"
INSTALLERS_DIR = PROJECT_ROOT / "installers"
CIRQEN_DIR = DIST_DIR / "Cirqen"

VERSION = "1.0.0"
APP_NAME = "Cirqen"
PUBLISHER = "Your Company"
WEBSITE = "https://yourcompany.com"


class Colors:
    if IS_WINDOWS:
        GREEN = ""
        RED = ""
        YELLOW = ""
        BLUE = ""
        NC = ""
    else:
        GREEN = '\033[0;32m'
        RED = '\033[0;31m'
        YELLOW = '\033[1;33m'
        BLUE = '\033[0;34m'
        NC = '\033[0m'


def print_header():
    print(f"\n{Colors.BLUE}")
    print("=" * 60)
    print("  CIRQEN INSTALLER CREATOR")
    print("=" * 60)
    print(f"{Colors.NC}\n")


def print_step(step, total, message):
    print(f"{Colors.GREEN}[{step}/{total}]{Colors.NC} {message}")


def print_error(message):
    print(f"{Colors.RED}ERROR: {message}{Colors.NC}")


def print_success(message):
    print(f"{Colors.GREEN}✓ {message}{Colors.NC}")


def print_warning(message):
    print(f"{Colors.YELLOW}⚠ {message}{Colors.NC}")


def check_cirqen_build():
    """Check if Cirqen has been built"""
    print_step(1, 6, "Checking Cirqen build...")

    if not CIRQEN_DIR.exists():
        print_error(f"Cirqen not found at {CIRQEN_DIR}")
        print("Run 'python build_cirqen.py' first!")
        return False

    if IS_WINDOWS:
        exe = CIRQEN_DIR / "Cirqen.exe"
    else:
        exe = CIRQEN_DIR / "Cirqen"

    if not exe.exists():
        print_error(f"Cirqen executable not found: {exe}")
        return False

    print_success(f"Found Cirqen at {CIRQEN_DIR}")
    return True


def create_windows_nsis_installer():
    """Create Windows NSIS installer"""
    print_step(2, 6, "Creating NSIS installer script...")

    # Ensure installers directory exists
    INSTALLERS_DIR.mkdir(exist_ok=True)

    nsis_script = f"""
; Cirqen NSIS Installer Script
; Generated automatically

!define APP_NAME "{APP_NAME}"
!define APP_VERSION "{VERSION}"
!define PUBLISHER "{PUBLISHER}"
!define WEB_SITE "{WEBSITE}"
!define INSTALL_DIR "$PROGRAMFILES64\\${{APP_NAME}}"

; Includes
!include "MUI2.nsh"
!include "FileFunc.nsh"

; General
Name "${{APP_NAME}}"
OutFile "..\\installers\\Cirqen_Setup_v${{APP_VERSION}}.exe"
InstallDir "${{INSTALL_DIR}}"
InstallDirRegKey HKLM "Software\\${{APP_NAME}}" "Install_Dir"
RequestExecutionLevel admin

; Interface Settings
!define MUI_ABORTWARNING
!define MUI_ICON "..\\dist\\Cirqen\\resources\\icon.ico"
!define MUI_UNICON "..\\dist\\Cirqen\\resources\\icon.ico"
!define MUI_HEADERIMAGE
!define MUI_WELCOMEFINISHPAGE_BITMAP "..\\dist\\Cirqen\\resources\\icon.ico"

; Pages
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\\dist\\Cirqen\\LICENSE.txt"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

; Languages
!insertmacro MUI_LANGUAGE "English"

; Installer Sections
Section "Install"
    SetOutPath "$INSTDIR"

    ; Copy all files
    File /r "..\\dist\\Cirqen\\*.*"

    ; Create shortcuts
    CreateDirectory "$SMPROGRAMS\\${{APP_NAME}}"
    CreateShortcut "$SMPROGRAMS\\${{APP_NAME}}\\${{APP_NAME}}.lnk" "$INSTDIR\\Cirqen.exe"
    CreateShortcut "$SMPROGRAMS\\${{APP_NAME}}\\Uninstall.lnk" "$INSTDIR\\Uninstall.exe"
    CreateShortcut "$DESKTOP\\${{APP_NAME}}.lnk" "$INSTDIR\\Cirqen.exe"

    ; Write registry
    WriteRegStr HKLM "Software\\${{APP_NAME}}" "Install_Dir" "$INSTDIR"
    WriteRegStr HKLM "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${{APP_NAME}}" "DisplayName" "${{APP_NAME}}"
    WriteRegStr HKLM "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${{APP_NAME}}" "UninstallString" '"$INSTDIR\\Uninstall.exe"'
    WriteRegStr HKLM "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${{APP_NAME}}" "DisplayIcon" "$INSTDIR\\Cirqen.exe"
    WriteRegStr HKLM "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${{APP_NAME}}" "Publisher" "${{PUBLISHER}}"
    WriteRegStr HKLM "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${{APP_NAME}}" "DisplayVersion" "${{APP_VERSION}}"
    WriteRegDWORD HKLM "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${{APP_NAME}}" "NoModify" 1
    WriteRegDWORD HKLM "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${{APP_NAME}}" "NoRepair" 1

    ; Create uninstaller
    WriteUninstaller "$INSTDIR\\Uninstall.exe"
SectionEnd

; Uninstaller Section
Section "Uninstall"
    ; Remove files
    RMDir /r "$INSTDIR"

    ; Remove shortcuts
    RMDir /r "$SMPROGRAMS\\${{APP_NAME}}"
    Delete "$DESKTOP\\${{APP_NAME}}.lnk"

    ; Remove registry
    DeleteRegKey HKLM "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${{APP_NAME}}"
    DeleteRegKey HKLM "Software\\${{APP_NAME}}"

    ; Note: User data in AppData is preserved
    MessageBox MB_YESNO "Remove user data? (This will delete all databases and settings)" IDNO SkipData
        RMDir /r "$APPDATA\\cirqen"
    SkipData:
SectionEnd
"""

    nsis_file = PROJECT_ROOT / "cirqen_installer.nsi"
    nsis_file.write_text(nsis_script)
    print_success(f"Created NSIS script: {nsis_file}")

    # Try to build with NSIS
    nsis_compiled = False

    if IS_WINDOWS:
        # Check if NSIS is installed (Windows)
        nsis_path = Path(r"C:\Program Files (x86)\NSIS\makensis.exe")
        if not nsis_path.exists():
            nsis_path = Path(r"C:\Program Files\NSIS\makensis.exe")

        if nsis_path.exists():
            print_step(3, 6, "Building NSIS installer...")
            try:
                result = subprocess.run(
                    [str(nsis_path), str(nsis_file)],
                    capture_output=True,
                    text=True,
                    check=True
                )
                print_success("NSIS installer created!")
                nsis_compiled = True
            except subprocess.CalledProcessError as e:
                print_error(f"NSIS build failed: {e.stderr}")
    else:
        # Try makensis on Linux (from nsis package)
        try:
            result = subprocess.run(
                ["makensis", str(nsis_file)],
                capture_output=True,
                text=True,
                check=True
            )
            print_success("NSIS installer created with Linux makensis!")
            nsis_compiled = True
        except FileNotFoundError:
            print_warning("makensis not found on Linux")
            print("Install with: sudo apt-get install nsis  (Debian/Ubuntu)")
            print("           or: sudo dnf install nsis     (Fedora/RedHat)")
        except subprocess.CalledProcessError as e:
            print_error(f"NSIS build failed: {e.stderr}")

    if not nsis_compiled:
        print(f"\n{Colors.BLUE}NSIS script ready:{Colors.NC}")
        print(f"  Linux:   makensis {nsis_file}")
        print(f"  Windows: \"C:\\Program Files\\NSIS\\makensis.exe\" {nsis_file}")
        print(f"\nInstall NSIS:")
        print(f"  Linux:   sudo apt install nsis")
        print(f"  Windows: https://nsis.sourceforge.io/")

    return True  # Script created successfully


def create_windows_inno_installer():
    """Create Windows Inno Setup installer"""
    print_step(2, 6, "Creating Inno Setup installer script...")

    # Ensure installers directory exists
    INSTALLERS_DIR.mkdir(exist_ok=True)

    inno_script = f"""
; Cirqen Inno Setup Installer Script

#define MyAppName "{APP_NAME}"
#define MyAppVersion "{VERSION}"
#define MyAppPublisher "{PUBLISHER}"
#define MyAppURL "{WEBSITE}"
#define MyAppExeName "Cirqen.exe"

[Setup]
AppId={{{{12345678-1234-1234-1234-123456789ABC}}}}
AppName={{#MyAppName}}
AppVersion={{#MyAppVersion}}
AppPublisher={{#MyAppPublisher}}
AppPublisherURL={{#MyAppURL}}
DefaultDirName={{autopf}}\\{{#MyAppName}}
DefaultGroupName={{#MyAppName}}
AllowNoIcons=yes
LicenseFile=..\\dist\\Cirqen\\LICENSE.txt
OutputDir=..\\installers
OutputBaseFilename=Cirqen_Setup_v{VERSION}
SetupIconFile=..\\dist\\Cirqen\\resources\\icon.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{{cm:CreateDesktopIcon}}"; GroupDescription: "{{cm:AdditionalIcons}}"

[Files]
Source: "..\\dist\\Cirqen\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{{group}}\\{{#MyAppName}}"; Filename: "{{app}}\\{{#MyAppExeName}}"
Name: "{{group}}\\{{cm:UninstallProgram,{{#MyAppName}}}}"; Filename: "{{uninstallexe}}"
Name: "{{autodesktop}}\\{{#MyAppName}}"; Filename: "{{app}}\\{{#MyAppExeName}}"; Tasks: desktopicon

[Run]
Filename: "{{app}}\\{{#MyAppExeName}}"; Description: "{{cm:LaunchProgram,{{#StringChange(MyAppName, '&', '&&')}}}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{{app}}\\cleanup_cirqen.py"; Parameters: ""; Flags: runhidden

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{{userappdata}}\\cirqen');
    if DirExists(DataDir) then
    begin
      if MsgBox('Do you want to remove all user data (databases, logs, uploaded files)?', mbConfirmation, MB_YESNO) = IDYES then
      begin
        DelTree(DataDir, True, True, True);
      end;
    end;
  end;
end;
"""

    inno_file = PROJECT_ROOT / "cirqen_installer.iss"
    inno_file.write_text(inno_script)
    print_success(f"Created Inno Setup script: {inno_file}")

    # Try to build with Inno Setup
    inno_compiled = False

    if IS_WINDOWS:
        # Check if Inno Setup is installed (Windows)
        inno_path = Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe")
        if not inno_path.exists():
            inno_path = Path(r"C:\Program Files\Inno Setup 6\ISCC.exe")

        if inno_path.exists():
            print_step(3, 6, "Building Inno Setup installer...")
            try:
                result = subprocess.run(
                    [str(inno_path), str(inno_file)],
                    capture_output=True,
                    text=True,
                    check=True
                )
                print_success("Inno Setup installer created!")
                inno_compiled = True
            except subprocess.CalledProcessError as e:
                print_error(f"Inno Setup build failed: {e.stderr}")
    else:
        # Try with Wine on Linux
        try:
            # Check if Wine is available
            subprocess.run(["wine", "--version"], capture_output=True, check=True)

            # Try to find Inno Setup under Wine
            wine_inno_paths = [
                Path.home() / ".wine/drive_c/Program Files (x86)/Inno Setup 6/ISCC.exe",
                Path.home() / ".wine/drive_c/Program Files/Inno Setup 6/ISCC.exe"
            ]

            wine_inno = None
            for path in wine_inno_paths:
                if path.exists():
                    wine_inno = path
                    break

            if wine_inno:
                print_step(3, 6, "Building Inno Setup installer with Wine...")
                try:
                    result = subprocess.run(
                        ["wine", str(wine_inno), str(inno_file)],
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    print_success("Inno Setup installer created with Wine!")
                    inno_compiled = True
                except subprocess.CalledProcessError as e:
                    print_error(f"Wine Inno Setup build failed: {e.stderr}")
            else:
                print_warning("Inno Setup not found under Wine")

        except (FileNotFoundError, subprocess.CalledProcessError):
            print_warning("Wine not available on Linux")
            print("To compile Inno Setup scripts on Linux:")
            print("  1. Install Wine: sudo apt install wine64")
            print("  2. Download Inno Setup: https://jrsoftware.org/isinfo.php")
            print("  3. Install with Wine: wine innosetup-6.x.x.exe")

    if not inno_compiled:
        print(f"\n{Colors.BLUE}Inno Setup script ready:{Colors.NC}")
        print(f"  Linux:   wine ~/.wine/drive_c/Program\\ Files\\ \\(x86\\)/Inno\\ Setup\\ 6/ISCC.exe {inno_file}")
        print(f"  Windows: \"C:\\Program Files (x86)\\Inno Setup 6\\ISCC.exe\" {inno_file}")
        print(f"\nInstall Inno Setup:")
        print(f"  Windows: https://jrsoftware.org/isinfo.php")
        print(f"  Linux:   Install Wine first, then Inno Setup via Wine")

    return True  # Script created successfully


def create_linux_deb_package():
    """Create Debian/Ubuntu DEB package"""
    print_step(2, 6, "Creating DEB package...")

    # Create package structure
    pkg_name = f"cirqen_{VERSION}_amd64"
    pkg_dir = INSTALLERS_DIR / pkg_name

    # Clean old package
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)

    # Create directories
    (pkg_dir / "DEBIAN").mkdir(parents=True, exist_ok=True)
    (pkg_dir / "opt" / "cirqen").mkdir(parents=True, exist_ok=True)
    (pkg_dir / "usr" / "share" / "applications").mkdir(parents=True, exist_ok=True)
    (pkg_dir / "usr" / "share" / "pixmaps").mkdir(parents=True, exist_ok=True)
    (pkg_dir / "usr" / "bin").mkdir(parents=True, exist_ok=True)

    # Copy application files
    print("Copying application files...")
    shutil.copytree(CIRQEN_DIR, pkg_dir / "opt" / "cirqen", dirs_exist_ok=True)

    # Create control file
    control_content = f"""Package: cirqen
Version: {VERSION}
Section: office
Priority: optional
Architecture: amd64
Maintainer: {PUBLISHER} <support@yourcompany.com>
Description: Cirqen - Calibration & Maintenance Management System
 A comprehensive CMMS (Computerized Maintenance Management System)
 for managing calibrations, maintenance, inventory, and assets.
 .
 Features:
  - Embedded PostgreSQL database
  - Offline-first operation
  - Bidirectional sync with HQ
  - Background task processing
  - Modern desktop interface
Depends: python3 (>= 3.8), python3-pip, libxcb-xinerama0
"""

    (pkg_dir / "DEBIAN" / "control").write_text(control_content)

    # Create postinst script
    postinst_content = """#!/bin/bash
set -e

# Create symlink
ln -sf /opt/cirqen/Cirqen /usr/bin/cirqen

# Set permissions
chmod +x /opt/cirqen/Cirqen
chmod +x /opt/cirqen/launch_cirqen.py
chmod +x /opt/cirqen/cleanup_cirqen.py

# Install Python dependencies
echo "Installing Python dependencies..."
python3 -m pip install --upgrade pip > /dev/null 2>&1 || true

echo "Cirqen installed successfully!"
echo "Run 'cirqen' to start the application"
"""

    postinst_file = pkg_dir / "DEBIAN" / "postinst"
    postinst_file.write_text(postinst_content)
    postinst_file.chmod(0o755)

    # Create prerm script
    prerm_content = """#!/bin/bash
set -e

# Stop any running instances
python3 /opt/cirqen/cleanup_cirqen.py || true

# Remove symlink
rm -f /usr/bin/cirqen
"""

    prerm_file = pkg_dir / "DEBIAN" / "prerm"
    prerm_file.write_text(prerm_content)
    prerm_file.chmod(0o755)

    # Create postrm script
    postrm_content = """#!/bin/bash
set -e

if [ "$1" = "purge" ]; then
    # Ask user if they want to remove data
    echo "User data is located in ~/.local/share/cirqen"
    echo "To remove it manually, run: rm -rf ~/.local/share/cirqen"
fi
"""

    postrm_file = pkg_dir / "DEBIAN" / "postrm"
    postrm_file.write_text(postrm_content)
    postrm_file.chmod(0o755)

    # Create desktop file
    desktop_content = f"""[Desktop Entry]
Version=1.0
Type=Application
Name=Cirqen
Comment=Calibration & Maintenance Management System
Exec=/usr/bin/cirqen
Icon=cirqen
Terminal=false
Categories=Office;Database;
StartupNotify=true
"""

    (pkg_dir / "usr" / "share" / "applications" / "cirqen.desktop").write_text(desktop_content)

    # Copy icon
    icon_src = CIRQEN_DIR / "resources" / "icon.png"
    if icon_src.exists():
        shutil.copy(icon_src, pkg_dir / "usr" / "share" / "pixmaps" / "cirqen.png")

    print_step(3, 6, "Building DEB package...")

    # Build package
    try:
        result = subprocess.run(
            ["dpkg-deb", "--build", pkg_name],
            cwd=INSTALLERS_DIR,
            capture_output=True,
            text=True,
            check=True
        )
        print_success(f"DEB package created: {pkg_name}.deb")

        # Show installation command
        print(f"\n{Colors.BLUE}To install:{Colors.NC}")
        print(f"  sudo dpkg -i installers/{pkg_name}.deb")
        print(f"  sudo apt-get install -f  # Fix dependencies if needed")

        return True
    except subprocess.CalledProcessError as e:
        print_error(f"DEB package creation failed: {e.stderr}")
        return False
    except FileNotFoundError:
        print_error("dpkg-deb not found. Install with: sudo apt-get install dpkg")
        return False


def create_linux_rpm_package():
    """Create Fedora/RedHat RPM package"""
    print_step(4, 6, "Creating RPM package...")

    # Create RPM build structure
    rpm_root = INSTALLERS_DIR / "rpm_build"

    dirs = ["BUILD", "RPMS", "SOURCES", "SPECS", "SRPMS"]
    for d in dirs:
        (rpm_root / d).mkdir(parents=True, exist_ok=True)

    # Create source tarball
    tarball_name = f"cirqen-{VERSION}.tar.gz"
    tarball_path = rpm_root / "SOURCES" / tarball_name

    print("Creating source tarball...")
    shutil.make_archive(
        str(rpm_root / "SOURCES" / f"cirqen-{VERSION}"),
        'gztar',
        DIST_DIR,
        'Cirqen'
    )

    # Create RPM spec file
    spec_content = f"""
Name:           cirqen
Version:        {VERSION}
Release:        1%{{?dist}}
Summary:        Calibration & Maintenance Management System

License:        Proprietary
URL:            {WEBSITE}
Source0:        %{{name}}-%{{version}}.tar.gz

BuildArch:      x86_64
Requires:       python3 >= 3.8

%description
Cirqen is a comprehensive CMMS (Computerized Maintenance Management System)
for managing calibrations, maintenance, inventory, and assets.

Features:
- Embedded PostgreSQL database
- Offline-first operation
- Bidirectional sync with HQ
- Background task processing
- Modern desktop interface

%prep
%setup -q -n Cirqen

%install
rm -rf $RPM_BUILD_ROOT
mkdir -p $RPM_BUILD_ROOT/opt/cirqen
mkdir -p $RPM_BUILD_ROOT/usr/bin
mkdir -p $RPM_BUILD_ROOT/usr/share/applications
mkdir -p $RPM_BUILD_ROOT/usr/share/pixmaps

cp -r * $RPM_BUILD_ROOT/opt/cirqen/
ln -s /opt/cirqen/Cirqen $RPM_BUILD_ROOT/usr/bin/cirqen

# Desktop file
cat > $RPM_BUILD_ROOT/usr/share/applications/cirqen.desktop << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Cirqen
Comment=Calibration & Maintenance Management System
Exec=/usr/bin/cirqen
Icon=cirqen
Terminal=false
Categories=Office;Database;
EOF

# Icon
cp resources/icon.png $RPM_BUILD_ROOT/usr/share/pixmaps/cirqen.png || true

%post
chmod +x /opt/cirqen/Cirqen
chmod +x /opt/cirqen/launch_cirqen.py
chmod +x /opt/cirqen/cleanup_cirqen.py
echo "Cirqen installed. Run 'cirqen' to start."

%preun
python3 /opt/cirqen/cleanup_cirqen.py || true

%postun
if [ $1 -eq 0 ]; then
    echo "User data is in ~/.local/share/cirqen"
    echo "To remove: rm -rf ~/.local/share/cirqen"
fi

%files
/opt/cirqen/*
/usr/bin/cirqen
/usr/share/applications/cirqen.desktop
/usr/share/pixmaps/cirqen.png

%changelog
* {__import__('datetime').datetime.now().strftime('%a %b %d %Y')} {PUBLISHER}
- Initial release {VERSION}
"""

    spec_file = rpm_root / "SPECS" / "cirqen.spec"
    spec_file.write_text(spec_content)
    print_success("Created RPM spec file")

    # Build RPM
    try:
        result = subprocess.run(
            ["rpmbuild", "-ba", str(spec_file), "--define", f"_topdir {rpm_root}"],
            capture_output=True,
            text=True,
            check=True
        )

        # Find and copy RPM to installers dir
        rpm_file = list((rpm_root / "RPMS" / "x86_64").glob("*.rpm"))[0]
        shutil.copy(rpm_file, INSTALLERS_DIR)

        print_success(f"RPM package created: {rpm_file.name}")

        print(f"\n{Colors.BLUE}To install:{Colors.NC}")
        print(f"  sudo dnf install installers/{rpm_file.name}")

        return True
    except subprocess.CalledProcessError as e:
        print_error(f"RPM build failed: {e.stderr}")
        return False
    except (FileNotFoundError, IndexError):
        print_warning("rpmbuild not found. Install with: sudo dnf install rpm-build")
        print(f"Spec file created at: {spec_file}")
        return False


def create_license_file():
    """Create a LICENSE.txt file"""
    license_file = CIRQEN_DIR / "LICENSE.txt"

    if not license_file.exists():
        license_content = f"""
CIRQEN SOFTWARE LICENSE AGREEMENT

Copyright (c) 2024 {PUBLISHER}. All rights reserved.

This software and associated documentation files (the "Software") are
licensed, not sold. By installing or using the Software, you agree to
be bound by the terms of this license agreement.

GRANT OF LICENSE:
{PUBLISHER} grants you a non-exclusive, non-transferable license to use
the Software on a single computer or workstation.

RESTRICTIONS:
You may not:
- Reverse engineer, decompile, or disassemble the Software
- Rent, lease, or lend the Software
- Use the Software for any illegal purpose

DISCLAIMER OF WARRANTY:
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.

LIMITATION OF LIABILITY:
IN NO EVENT SHALL {PUBLISHER} BE LIABLE FOR ANY DAMAGES ARISING FROM
THE USE OF THIS SOFTWARE.

For support, contact: support@yourcompany.com
"""
        license_file.write_text(license_content)


def main():
    """Main installer creator"""
    print_header()

    # Create installers directory
    INSTALLERS_DIR.mkdir(exist_ok=True)

    # Step 1: Check build exists
    if not check_cirqen_build():
        return 1

    # Create license file
    create_license_file()

    # Show platform info
    print(f"{Colors.BLUE}Platform detected:{Colors.NC} {platform.system()}")
    print(f"{Colors.BLUE}Python version:{Colors.NC} {sys.version.split()[0]}")
    print()

    success = False

    # Show all options regardless of platform
    print(f"{Colors.BLUE}Available Installer Options:{Colors.NC}")
    print("1. NSIS Installer (Windows)")
    print("2. Inno Setup Installer (Windows)")
    print("3. DEB Package (Linux/Ubuntu/Debian)")
    print("4. RPM Package (Linux/Fedora/RedHat)")
    print("5. All Windows Installers")
    print("6. All Linux Packages")
    print("7. Everything (All platforms)")

    choice = input("\nSelect option (1-7): ").strip()

    if choice == '1':
        success = create_windows_nsis_installer()
    elif choice == '2':
        success = create_windows_inno_installer()
    elif choice == '3':
        success = create_linux_deb_package()
    elif choice == '4':
        success = create_linux_rpm_package()
    elif choice == '5':
        success = create_windows_nsis_installer() or success
        success = create_windows_inno_installer() or success
    elif choice == '6':
        success = create_linux_deb_package() or success
        success = create_linux_rpm_package() or success
    elif choice == '7':
        success = create_windows_nsis_installer() or success
        success = create_windows_inno_installer() or success
        success = create_linux_deb_package() or success
        success = create_linux_rpm_package() or success
    else:
        print_error("Invalid choice")
        return 1

    # Summary
    print(f"\n{Colors.BLUE}")
    print("=" * 60)
    print("  INSTALLER CREATION COMPLETE")
    print("=" * 60)
    print(f"{Colors.NC}\n")

    if success:
        print_success("Installers created successfully!")
        print(f"\n{Colors.BLUE}Output directory:{Colors.NC} {INSTALLERS_DIR}")

        # List created installers
        installers = list(INSTALLERS_DIR.glob("*"))
        if installers:
            print(f"\n{Colors.BLUE}Created installers:{Colors.NC}")
            for installer in installers:
                if installer.is_file():
                    size = installer.stat().st_size / (1024 * 1024)
                    print(f"  - {installer.name} ({size:.1f} MB)")
    else:
        print_warning("Some installers could not be created")
        print("Check the messages above for required tools")

    print(f"\n{Colors.BLUE}Installation Notes:{Colors.NC}")
    if IS_LINUX:
        print("- NSIS: sudo apt install nsis (Debian/Ubuntu)")
        print("        sudo dnf install nsis (Fedora/RedHat)")
        print("- Inno Setup: Requires Wine")
        print("  1. sudo apt install wine64")
        print("  2. Download Inno from https://jrsoftware.org/isinfo.php")
        print("  3. wine innosetup-6.x.x.exe")
        print("- DEB: Requires dpkg-deb (usually pre-installed)")
        print("- RPM: sudo dnf install rpm-build")
    else:
        print("- NSIS: Download from https://nsis.sourceforge.io/")
        print("- Inno Setup: Download from https://jrsoftware.org/isinfo.php")
        print("- DEB: Requires Linux")
        print("- RPM: Requires Linux")

    return 0 if success else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}Installer creation cancelled{Colors.NC}")
        sys.exit(1)
    except Exception as e:
        print(f"\n{Colors.RED}Error: {e}{Colors.NC}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
