# Stop everything a Cirqen install runs: the app, its web server, workers,
# Redis and its database. Used by the installer (before it replaces files) and
# the uninstaller (before it deletes them), for this version and older ones.
#
# Processes are matched by where their program lives (under -AppDir), never by
# name, so another PostgreSQL or Redis on the PC is left alone. The database
# is stopped cleanly with pg_ctl first; anything left is then ended.
param([Parameter(Mandatory = $true)][string]$AppDir)
$ErrorActionPreference = "SilentlyContinue"
$prefix = $AppDir.TrimEnd('\') + '\'

function Get-Ours([bool]$IncludePostgres) {
    Get-Process | Where-Object {
        $_.Path -and $_.Path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -and
        ($IncludePostgres -or $_.ProcessName -notlike "postgres*")
    }
}

# 1. The app first, so nothing restarts or still uses the database.
Get-Ours $false | Stop-Process -Force
Start-Sleep -Milliseconds 500

# 2. The database, cleanly (this version keeps it in %LOCALAPPDATA%\Cirqen\db\pgNN).
$pgCtl = Join-Path $AppDir "runtime\postgresql\bin\pg_ctl.exe"
$dbRoot = Join-Path $env:LOCALAPPDATA "Cirqen\db"
if ((Test-Path $pgCtl) -and (Test-Path $dbRoot)) {
    Get-ChildItem $dbRoot -Directory -Filter "pg*" | ForEach-Object {
        if (Test-Path (Join-Path $_.FullName "postmaster.pid")) {
            & $pgCtl stop -D $_.FullName -m fast -w -t 30 *> $null
        }
    }
}

# 3. Anything still running from the app folder (an older version's database
# lived in %APPDATA%\cirqen\postgres and is ended here).
Get-Ours $true | Stop-Process -Force
for ($i = 0; $i -lt 20 -and (Get-Ours $true); $i++) { Start-Sleep -Milliseconds 500 }
exit 0
