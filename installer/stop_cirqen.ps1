# Stop everything a Cirqen install runs: the app, its web server, workers,
# Redis and its database. Used by the installer (before it replaces files) and
# the uninstaller (before it deletes them), for this version and older ones.
#
# Processes are matched by where their program lives (under -AppDir), never by
# name, so another PostgreSQL or Redis on the PC is left alone. The database
# is stopped cleanly with pg_ctl first; anything left is then ended.
# What it did is in %TEMP%\cirqen_stop.log.
param([Parameter(Mandatory = $true)][string]$AppDir, [switch]$Elevated)
$ErrorActionPreference = "Continue"
$log = Join-Path $env:TEMP "cirqen_stop.log"
function Say([string]$text) { Add-Content -Path $log -Value "$(Get-Date -Format 'HH:mm:ss') $text" }

# Installers may pass a short (8.3) path; processes report long ones.
Add-Type -Namespace Cirqen -Name Paths -MemberDefinition @'
[DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
public static extern uint GetLongPathName(string shortPath, System.Text.StringBuilder longPath, uint size);
'@
function Long([string]$path) {
    $buffer = New-Object System.Text.StringBuilder 1024
    if ([Cirqen.Paths]::GetLongPathName($path, $buffer, 1024) -gt 0) { return $buffer.ToString() }
    return $path
}
$prefixes = @($AppDir, (Long $AppDir)) | ForEach-Object { $_.TrimEnd('\') + '\' } | Select-Object -Unique
Say "AppDir $AppDir -> $($prefixes -join ' | ')"

# Never this script or the uninstaller (its unins000.exe is in the app folder).
function Get-Ours([bool]$IncludePostgres) {
    Get-Process | Where-Object {
        $path = $_.Path
        $path -and $_.Id -ne $PID -and $_.ProcessName -notlike "unins*" -and
        ($prefixes | Where-Object { $path.StartsWith($_, [StringComparison]::OrdinalIgnoreCase) }) -and
        ($IncludePostgres -or $_.ProcessName -notlike "postgres*")
    }
}

# A Cirqen started with "Run as administrator" is hidden from a script without
# those rights: Windows gives no path for it and won't let it be ended.
function Get-Hidden { Get-Process Cirqen -ErrorAction SilentlyContinue | Where-Object { -not $_.Path } }
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
Say "running as administrator: $isAdmin"

function End-All($procs) {
    foreach ($p in $procs) {
        try {
            Stop-Process -Id $p.Id -Force -ErrorAction Stop
            Say "ended $($p.ProcessName) $($p.Id)"
        } catch {
            Say "Stop-Process $($p.ProcessName) $($p.Id): $($_.Exception.Message); trying taskkill"
            $out = & taskkill.exe /F /T /PID $p.Id 2>&1
            Say "taskkill: $out"
        }
    }
}

# 1. The app first, so nothing restarts or still uses the database.
End-All (Get-Ours $false)
Start-Sleep -Milliseconds 500

# 2. The database, cleanly (this version keeps it in %LOCALAPPDATA%\Cirqen\db\pgNN).
$pgCtl = Join-Path $AppDir "runtime\postgresql\bin\pg_ctl.exe"
$dbRoot = Join-Path $env:LOCALAPPDATA "Cirqen\db"
if ((Test-Path $pgCtl) -and (Test-Path $dbRoot)) {
    Get-ChildItem $dbRoot -Directory -Filter "pg*" | ForEach-Object {
        if (Test-Path (Join-Path $_.FullName "postmaster.pid")) {
            $out = & $pgCtl stop -D $_.FullName -m fast -w -t 30 2>&1
            Say "pg_ctl stop $($_.FullName): $out"
        }
    }
}

# 3. Anything still running from the app folder (an older version's database
# lived in %APPDATA%\cirqen\postgres and is ended here).
End-All (Get-Ours $true)
for ($i = 0; $i -lt 20 -and (Get-Ours $true); $i++) { Start-Sleep -Milliseconds 500 }
# 4. A Cirqen this script can't see: do it all again with administrator rights
# (one Windows prompt, only in this case).
if ((Get-Hidden) -and -not $isAdmin -and -not $Elevated) {
    Say "Cirqen runs with administrator rights; asking for them to stop it"
    try {
        Start-Process powershell.exe -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList @(
            "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"",
            "-AppDir", "`"$AppDir`"", "-Elevated")
    } catch { Say "not given administrator rights: $($_.Exception.Message)" }
}
$left = @(Get-Ours $true) + @(Get-Hidden)
if ($left) { Say "STILL RUNNING: $(($left | ForEach-Object { "$($_.ProcessName) $($_.Id)" }) -join ', ')" }
else { Say "nothing of Cirqen running" }
exit 0
