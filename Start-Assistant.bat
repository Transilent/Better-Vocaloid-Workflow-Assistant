@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONNOUSERSITE=1"
set "FLOW_PYTHON=%~dp0dependencies\vocal2midi\python\python.exe"
if not exist "%FLOW_PYTHON%" goto bootstrap
if exist "%~dp0cache\runtime-install-in-progress" goto bootstrap
:run
"%FLOW_PYTHON%" -B -u "%~dp0launch.py" %* > "%~dp0launcher.log" 2>&1
set "FLOW_EXIT=%ERRORLEVEL%"
if "%FLOW_EXIT%"=="0" exit /b 0
echo.
echo Better Vocaloid Workflow Assistant could not start or exited with an error.
echo Details: "%~dp0launcher.log"
type "%~dp0launcher.log"
pause
exit /b %FLOW_EXIT%
:bootstrap
echo This folder needs the portable runtime. Installing it before startup...
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\Install-Runtime.ps1"
if errorlevel 1 goto installfailed
if not exist "%FLOW_PYTHON%" goto missing
goto run
:installfailed
echo Runtime installation failed. See the messages above for download instructions.
pause
exit /b 1
:missing
echo Python runtime not found: "%FLOW_PYTHON%"
pause
exit /b 1
