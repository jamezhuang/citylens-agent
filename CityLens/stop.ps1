$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$listener = Get-NetTCPConnection -State Listen -LocalPort 8090 -ErrorAction SilentlyContinue
foreach ($item in $listener) {
 $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($item.OwningProcess)"
 if ($proc.CommandLine -like '*uvicorn citylens.api:app*') { Stop-Process -Id $item.OwningProcess }
}
docker compose -p citylens-local -f dev/docker-compose.yml stop
