$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Error "Không tìm thấy .venv. Hãy tạo môi trường và cài requirements trước."
    exit 1
}

$env:PYTHONUTF8 = "1"
& $venvPython (Join-Path $projectRoot "src\cli.py") @args
exit $LASTEXITCODE
