param([switch]$Send)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if ($Send) {
    & "$PSScriptRoot\.venv\Scripts\python.exe" scripts/check_langfuse.py --send
} else {
    & "$PSScriptRoot\.venv\Scripts\python.exe" scripts/check_langfuse.py
}
exit $LASTEXITCODE
