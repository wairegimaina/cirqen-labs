# Install Cirqen-Setup-*.exe silently on this (clean) Windows, start Cirqen,
# check that it opens its window, that its embedded database and background
# worker run, then uninstall it and check that nothing of Cirqen is left.
# Run by .github/workflows/windows-build.yml; exits non-zero on failure.
$ErrorActionPreference = "Stop"
$setup = Get-ChildItem dist\Cirqen-Setup-*.exe | Where-Object { $_.Name -notmatch "-CH" } | Select-Object -First 1
Write-Host "Installing $($setup.Name)"
Start-Process $setup.FullName -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER" -Wait

$appDir = Join-Path $env:LOCALAPPDATA "Programs\Cirqen"
$app = Join-Path $appDir "Cirqen.exe"
$data = Join-Path $env:APPDATA "cirqen"
$dbRoot = Join-Path $env:LOCALAPPDATA "Cirqen\db"
if (-not (Test-Path $app)) { throw "Cirqen.exe not installed at $app" }
$shortcut = Join-Path ([Environment]::GetFolderPath("Programs")) "Cirqen.lnk"
Write-Host "Start menu shortcut: $(Test-Path $shortcut)"

# This runner has the VC++ redistributable, so a missing DLL wouldn't show
# here: check the ones PostgreSQL ships with (build.py bundle_vc_runtime).
$pgBin = Join-Path $appDir "runtime\postgresql\bin"
$vcMissing = @("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll") | Where-Object { -not (Test-Path (Join-Path $pgBin $_)) }
$vcOk = -not $vcMissing
if ($vcOk) { Write-Host "VC++ runtime: next to postgres.exe" } else { Write-Host "VC++ RUNTIME MISSING next to postgres.exe: $($vcMissing -join ', ')" }

$env:CIRQEN_UNATTENDED = "1"          # no one is here to click OK
$env:CIRQEN_LOG_LEVEL = "INFO"         # the full story in cirqen_app.log if it fails
$proc = Start-Process $app -WorkingDirectory $appDir -PassThru
$started = $false
for ($i = 0; $i -lt 180; $i++) {
    Start-Sleep -Seconds 2
    if (Test-Path (Join-Path $data "full_update_ok")) { $started = $true; break }
    if ($proc.HasExited) { Write-Host "Cirqen exited with code $($proc.ExitCode)"; break }
}

$pong = $false
$dbOk = $false
if ($started) {
    Write-Host "STARTED: Cirqen $(Get-Content (Join-Path $data 'full_update_ok')) opened its window"
    $pg = Get-Process postgres -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$appDir\*" }
    $cluster = Get-ChildItem $dbRoot -Directory -Filter "pg*" -ErrorAction SilentlyContinue | Select-Object -First 1
    $dbOk = $pg -and $cluster -and (Test-Path (Join-Path $dbRoot "ready"))
    if ($dbOk) { Write-Host "database: embedded, $($pg.Count) processes from the app, cluster $($cluster.FullName)" }
    else { Write-Host "DATABASE NOT EMBEDDED AS EXPECTED (processes: $($pg.Count), cluster: $cluster)" }
    if (Get-Service CirqenPostgreSQL -ErrorAction SilentlyContinue) { Write-Host "A DATABASE SERVICE EXISTS"; $dbOk = $false }

    $env:CELERY_BROKER_URL = "redis://127.0.0.1:7788/2"
    $env:CELERY_RESULT_BACKEND = "redis://127.0.0.1:7788/2"
    for ($i = 0; $i -lt 20 -and -not $pong; $i++) {
        $out = & $app celery -A Equiper.celery:app inspect ping -t 10 2>&1 | Out-String
        if ($out -match "pong") { $pong = $true } else { Start-Sleep -Seconds 3 }
    }
    if ($pong) { Write-Host "background tasks: ok (the worker answered a ping)" }
    else { Write-Host "BACKGROUND TASKS DID NOT ANSWER"; Write-Host $out }
} else {
    Write-Host "DID NOT START within 360s"
}

$ok = $started -and $pong -and $dbOk -and $vcOk
if (-not $ok) {
    foreach ($log in "launcher.log", "cirqen_app.log", "pg_ctl.log", "postgres.log", "migrations.log", "django.log", "redis.log", "celery.log") {
        $path = Join-Path $data "logs\$log"
        if (Test-Path $path) { Write-Host "--- $log"; Get-Content $path -Tail 40 }
    }
    Get-ChildItem $data -ErrorAction SilentlyContinue | Format-Table Name, Length
    New-Item -ItemType Directory -Force dist\app-logs | Out-Null
    Copy-Item (Join-Path $data "logs\*") dist\app-logs -ErrorAction SilentlyContinue
}

# Uninstall while Cirqen is still running, as a user would: the uninstaller
# must stop it (database included) and leave nothing behind.
$uninstaller = Get-ChildItem $appDir -Filter "unins*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($uninstaller) {
    Write-Host "Uninstalling (Cirqen still running)"
    Start-Process $uninstaller.FullName -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait
    Start-Sleep -Seconds 5          # the uninstaller finishes from a temporary copy
} else { Write-Host "UNINSTALLER NOT FOUND" }
$left = @($appDir, $data, (Join-Path $env:LOCALAPPDATA "Cirqen"), $shortcut) | Where-Object { Test-Path $_ }
$running = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$appDir\*" }
if ($left) { Write-Host "LEFT AFTER UNINSTALL: $($left -join ', ')"; $ok = $false }
if ($running) { Write-Host "STILL RUNNING AFTER UNINSTALL: $(($running | ForEach-Object { $_.ProcessName }) -join ', ')"; $ok = $false }
if (-not $left -and -not $running) { Write-Host "uninstall: nothing left" }

Get-Process Cirqen, postgres, redis-server -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$appDir\*" } | Stop-Process -Force
if (-not ($ok -and $uninstaller)) { exit 1 }
