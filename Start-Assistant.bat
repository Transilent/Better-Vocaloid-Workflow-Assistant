@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONNOUSERSITE=1"
set "FLOW_PYTHON=%~dp0dependencies\vocal2midi\python\python.exe"
if not exist "%FLOW_PYTHON%" goto missing
"%FLOW_PYTHON%" -B -u "%~dp0launch.py" %* > "%~dp0launcher.log" 2>&1
set "FLOW_EXIT=%ERRORLEVEL%"
if "%FLOW_EXIT%"=="0" exit /b 0
echo.
echo Better Vocaloid Workflow Assistant could not start or exited with an error.
echo Details: "%~dp0launcher.log"
type "%~dp0launcher.log"
pause
exit /b %FLOW_EXIT%
:missing
echo Python runtime not found: "%FLOW_PYTHON%"
pause
exit /b 1
