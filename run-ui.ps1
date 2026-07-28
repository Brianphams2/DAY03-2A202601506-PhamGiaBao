$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Error "Khong tim thay .venv. Hay tao moi truong va cai requirements truoc."
    exit 1
}

$env:PYTHONUTF8 = "1"
& $venvPython (Join-Path $projectRoot "src\web.py") @args
exit $LASTEXITCODE
