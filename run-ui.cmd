@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "VENV_PYTHON=%CD%\.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
    echo [ERROR] Khong tim thay .venv.
    echo Tao moi truong va cai thu vien truoc khi chay.
    exit /b 1
)

"%VENV_PYTHON%" "%CD%\src\web.py" %*
exit /b %ERRORLEVEL%
