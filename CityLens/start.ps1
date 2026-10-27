$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$ErrorActionPreference = 'Continue'
docker info *> $null
if ($LASTEXITCODE -ne 0) {
 $dockerDesktop = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\Docker Desktop.exe'
 if (-not (Test-Path $dockerDesktop)) { $dockerDesktop = 'C:\Program Files\Docker\Docker\Docker Desktop.exe' }
 Start-Process -FilePath $dockerDesktop -WindowStyle Hidden
 for ($i=0; $i -lt 60; $i++) {
  Start-Sleep -Seconds 2
  docker info *> $null
  if ($LASTEXITCODE -eq 0) { break }
 }
 if ($LASTEXITCODE -ne 0) { throw 'Please check Docker Desktop.' }
}
$ErrorActionPreference = 'Stop'
docker compose -p citylens-local -f dev/docker-compose.yml up -d
if ($LASTEXITCODE -ne 0) { throw 'Docker services failed to start' }
if (-not (Get-NetTCPConnection -State Listen -LocalPort 8090 -ErrorAction SilentlyContinue)) {
 Start-Process powershell.exe -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File .\start-api.ps1' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput "$PSScriptRoot\api.stdout.log" -RedirectStandardError "$PSScriptRoot\api.stderr.log"
}
Write-Host 'CityLens: http://127.0.0.1:8090'
for ($i=0; $i -lt 90; $i++) {
 try {
  $result=Invoke-RestMethod 'http://127.0.0.1:8090/ready' -TimeoutSec 3
  if ($result.status -eq 'ready') { Start-Process 'http://127.0.0.1:8090'; exit 0 }
 } catch {}
 Start-Sleep -Seconds 2
}
throw 'CityLens is still preparing. Check service logs.'
