@echo off
REM ===================================================================
REM  Grant Seeker - send (or preview) the digest email
REM  Usage:  email --opps out.json --dry-run    (preview, no sending)
REM          email --opps out.json              (send; needs SMTP_* env vars)
REM  All arguments pass straight through to email_digest.py.
REM ===================================================================
setlocal

set "PYTHON_EXE=%PYTHON_EXE%"
if "%PYTHON_EXE%"=="" set "PYTHON_EXE=%USERPROFILE%\miniforge3\envs\delftdashboard_dev\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

REM --- Load your API key (and any SMTP_* vars) from git-ignored secret.bat
if exist "%~dp0secret.bat" call "%~dp0secret.bat"

if "%ANTHROPIC_API_KEY%"=="" (
    echo.
    echo ERROR: ANTHROPIC_API_KEY is not set ^(see secret.bat.example^).
    echo.
    exit /b 1
)

cd /d "%~dp0"
"%PYTHON_EXE%" email_digest.py %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (echo Done.) else (echo Exited with code %RC%.)
if "%~1"=="" pause
exit /b %RC%
