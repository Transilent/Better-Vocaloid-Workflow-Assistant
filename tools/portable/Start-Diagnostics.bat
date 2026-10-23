@echo off
setlocal
cd /d "%~dp0.."
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONNOUSERSITE=1"
set "VWA_ROOT=%CD%"
set "VWA_PYTHON=%VWA_ROOT%\dependencies\vocal2midi\python\python.exe"
set "VWA_DIAGNOSTICS=%VWA_ROOT%\diagnostics.log"
if not exist "%VWA_PYTHON%" goto install
if exist "%VWA_ROOT%\cache\runtime-install-in-progress" goto install
:run
"%VWA_PYTHON%" -B -u "%VWA_ROOT%\launch.py" %* > "%VWA_DIAGNOSTICS%" 2>&1
set "VWA_EXIT=%ERRORLEVEL%"
if "%VWA_EXIT%"=="0" exit /b 0
type "%VWA_DIAGNOSTICS%"
echo Startup failed. See diagnostics.log for details.
pause
exit /b %VWA_EXIT%
:install
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%VWA_ROOT%\tools\Install-Runtime.ps1"
if errorlevel 1 goto failed
if not exist "%VWA_PYTHON%" goto failed
goto run
:failed
echo Runtime installation failed. Download and extract the complete portable package.
pause
exit /b 1
