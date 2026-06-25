@echo off
REM ===================================================================
REM  Grant Seeker - rebuild the website data
REM  Usage:  site --merge out.json   (merge a fresh scout run, then rebuild)
REM          site                     (just rebuild from the existing database)
REM  Then open docs\index.html in your browser.
REM  Arguments pass straight through to build_site.py. No API key needed.
REM ===================================================================
setlocal

set "PYTHON_EXE=%PYTHON_EXE%"
if "%PYTHON_EXE%"=="" set "PYTHON_EXE=%USERPROFILE%\miniforge3\envs\delftdashboard_dev\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

cd /d "%~dp0"
"%PYTHON_EXE%" build_site.py %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (echo Done. Open docs\index.html in your browser.) else (echo Exited with code %RC%.)
if "%~1"=="" pause
exit /b %RC%
