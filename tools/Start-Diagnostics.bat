@echo off
setlocal
cd /d "%~dp0.."
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%CD%\tools\Prepare-Source.ps1"
if errorlevel 1 exit /b 1
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONNOUSERSITE=1"
set "VWA_ROOT=%CD%"
set "VWA_PYTHON=%VWA_ROOT%\dependencies\vocal2midi\python\python.exe"
if not exist "%VWA_PYTHON%" goto failed
if exist "%VWA_ROOT%\cache\runtime-install-in-progress" goto failed
"%VWA_PYTHON%" -B -u "%VWA_ROOT%\launch.py" %* > "%VWA_ROOT%\diagnostics.log" 2>&1
set "VWA_EXIT=%ERRORLEVEL%"
if "%VWA_EXIT%"=="0" exit /b 0
type "%VWA_ROOT%\diagnostics.log"
echo Startup failed. See diagnostics.log for details.
pause
exit /b %VWA_EXIT%
:failed
echo Python or the processing runtime is missing. Use a complete Qt Quick portable package.
pause
exit /b 1
