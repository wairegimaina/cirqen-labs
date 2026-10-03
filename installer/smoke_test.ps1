# Install Cirqen-Setup-*.exe silently on this (clean) Windows, start Cirqen and
# check that it opens its window and that its background-task worker answers.
# Run by .github/workflows/windows-build.yml; exits non-zero on failure.
$ErrorActionPreference = "Stop"
$setup = Get-ChildItem dist\Cirqen-Setup-*.exe | Where-Object { $_.Name -notmatch "-CH" } | Select-Object -First 1
Write-Host "Installing $($setup.Name)"
Start-Process $setup.FullName -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER" -Wait

$app = Join-Path $env:LOCALAPPDATA "Programs\Cirqen\Cirqen.exe"
$data = Join-Path $env:APPDATA "cirqen"
if (-not (Test-Path $app)) { throw "Cirqen.exe not installed at $app" }
$shortcut = Join-Path ([Environment]::GetFolderPath("Programs")) "Cirqen.lnk"
Write-Host "Start menu shortcut: $(Test-Path $shortcut)"

# The installer's elevated step: the CirqenPostgreSQL service (system_pg.py).
$programData = Join-Path $env:ProgramData "Cirqen"
$service = Get-Service CirqenPostgreSQL -ErrorAction SilentlyContinue
$dbReady = $service -and $service.Status -eq "Running" -and (Test-Path (Join-Path $programData "local_db.json"))
if ($service) { Write-Host "database service: $($service.Status) ($($service.StartType))" } else { Write-Host "database service: MISSING" }
# This runner has the VC++ redistributable, so a missing DLL wouldn't stop the
# service here: check the ones PostgreSQL ships with (build.py bundle_vc_runtime).
$pgBin = Join-Path $env:ProgramFiles "Cirqen\PostgreSQL\bin"
$vcMissing = @("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll") | Where-Object { -not (Test-Path (Join-Path $pgBin $_)) }
if ($vcMissing) { Write-Host "VC++ RUNTIME MISSING next to postgres.exe: $($vcMissing -join ', ')"; $dbReady = $false } else { Write-Host "VC++ runtime: next to postgres.exe" }

$env:CIRQEN_UNATTENDED = "1"          # no one is here to click OK
$env:CIRQEN_LOG_LEVEL = "INFO"         # the full story in cirqen_app.log if it fails
$proc = Start-Process $app -WorkingDirectory (Split-Path $app) -PassThru
$started = $false
for ($i = 0; $i -lt 180; $i++) {
    Start-Sleep -Seconds 2
    if (Test-Path (Join-Path $data "full_update_ok")) { $started = $true; break }
    if ($proc.HasExited) { Write-Host "Cirqen exited with code $($proc.ExitCode)"; break }
}

$pong = $false
if ($started) {
    Write-Host "STARTED: Cirqen $(Get-Content (Join-Path $data 'full_update_ok')) opened its window"
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

if (-not ($started -and $pong -and $dbReady)) {
    foreach ($log in (Join-Path $programData "setup.log"), (Get-ChildItem (Join-Path $programData "postgres\log") -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1 -ExpandProperty FullName)) {
        if ($log -and (Test-Path $log)) { Write-Host "--- $log"; Get-Content $log -Tail 400 }
    }
    foreach ($log in "launcher.log", "cirqen_app.log", "postgres_setup.log", "django.log", "postgres.log", "postgres_init.log", "redis.log", "celery.log") {
        $path = Join-Path $data "logs\$log"
        if (Test-Path $path) { Write-Host "--- $log"; Get-Content $path -Tail 40 }
    }
    Get-ChildItem $data -ErrorAction SilentlyContinue | Format-Table Name, Length
    New-Item -ItemType Directory -Force dist\app-logs | Out-Null
    Copy-Item (Join-Path $data "logs\*") dist\app-logs -ErrorAction SilentlyContinue
}
Get-Process Cirqen -ErrorAction SilentlyContinue | Stop-Process -Force
if (-not ($started -and $pong -and $dbReady)) { exit 1 }
