@echo off
REM ===================================================================
REM  Grant Seeker - run the scout
REM  Usage:   run.bat              (print results)
REM           run.bat --json out.json   (also save JSON)
REM  Any arguments are passed straight through to scout.py.
REM ===================================================================
setlocal

REM --- Python: prefer the delftdashboard_dev env (has the anthropic SDK),
REM     fall back to whatever 'python' is on PATH. Override with PYTHON_EXE.
set "PYTHON_EXE=%PYTHON_EXE%"
if "%PYTHON_EXE%"=="" set "PYTHON_EXE=%USERPROFILE%\miniforge3\envs\delftdashboard_dev\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
REM --- Load your API key from a git-ignored secret.bat (copy secret.bat.example).
REM     secret.bat must contain:  set "ANTHROPIC_API_KEY=sk-ant-api03-..."
if exist "%~dp0secret.bat" call "%~dp0secret.bat"

REM --- API key check
if "%ANTHROPIC_API_KEY%"=="" (
    echo.
    echo ERROR: ANTHROPIC_API_KEY is not set.
    echo Set it for this window:   set ANTHROPIC_API_KEY=sk-ant-...
    echo Or permanently:           setx ANTHROPIC_API_KEY "sk-ant-..."   ^(then open a new terminal^)
    echo.
    exit /b 1
)

REM --- Run from this script's own folder so scout.py is found
cd /d "%~dp0"

"%PYTHON_EXE%" scout.py %*
set "RC=%ERRORLEVEL%"

REM --- Keep the window open if double-clicked from Explorer
echo.
if "%RC%"=="0" (echo Done.) else (echo Exited with code %RC%.)
if "%~1"=="" pause

exit /b %RC%
