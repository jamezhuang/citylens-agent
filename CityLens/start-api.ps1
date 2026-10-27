$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
Get-Content .env | ForEach-Object {
 if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
  [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim().Trim('"').Trim("'"), 'Process')
 }
}
& "$PSScriptRoot\.venv\Scripts\python.exe" -m uvicorn citylens.api:app --host 127.0.0.1 --port 8090
