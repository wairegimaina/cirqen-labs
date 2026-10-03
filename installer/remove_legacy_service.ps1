# Remove what Cirqen 1.6.x set up with administrator rights: the
# CirqenPostgreSQL Windows service, PostgreSQL in Program Files\Cirqen, and
# its data in %ProgramData%\Cirqen. Cirqen no longer uses any of it (its
# database is now embedded in the app). Run elevated by the installer and the
# uninstaller, only when that service exists.
$ErrorActionPreference = "SilentlyContinue"
$service = "CirqenPostgreSQL"
if (Get-Service $service) {
    Stop-Service $service -Force
    for ($i = 0; $i -lt 60 -and (Get-Service $service).Status -ne "Stopped"; $i++) { Start-Sleep 1 }
    sc.exe delete $service | Out-Null
}
# Its PostgreSQL processes, if the service manager left any.
$pgDir = Join-Path $env:ProgramFiles "Cirqen\PostgreSQL"
Get-Process postgres | Where-Object { $_.Path -and $_.Path.StartsWith($pgDir, [StringComparison]::OrdinalIgnoreCase) } |
    Stop-Process -Force
Start-Sleep 1
Remove-Item (Join-Path $env:ProgramFiles "Cirqen") -Recurse -Force
Remove-Item (Join-Path $env:ProgramData "Cirqen") -Recurse -Force
exit 0
