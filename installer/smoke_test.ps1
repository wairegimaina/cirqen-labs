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

if (-not ($started -and $pong)) {
    foreach ($log in "launcher.log", "cirqen_app.log", "postgres_setup.log", "django.log", "postgres.log", "postgres_init.log", "redis.log", "celery.log") {
        $path = Join-Path $data "logs\$log"
        if (Test-Path $path) { Write-Host "--- $log"; Get-Content $path -Tail 40 }
    }
    Get-ChildItem $data -ErrorAction SilentlyContinue | Format-Table Name, Length
    New-Item -ItemType Directory -Force dist\app-logs | Out-Null
    Copy-Item (Join-Path $data "logs\*") dist\app-logs -ErrorAction SilentlyContinue
}
Get-Process Cirqen -ErrorAction SilentlyContinue | Stop-Process -Force
if (-not ($started -and $pong)) { exit 1 }
