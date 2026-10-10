@echo off
setlocal
cd /d "%~dp0.."
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONNOUSERSITE=1"
set "BVWA_ROOT=%CD%"
set "BVWA_PYTHON=%BVWA_ROOT%\dependencies\vocal2midi\python\python.exe"
if not exist "%BVWA_PYTHON%" goto install
if exist "%BVWA_ROOT%\cache\runtime-install-in-progress" goto install
:run
"%BVWA_PYTHON%" -B -u "%BVWA_ROOT%\launch.py" %* > "%BVWA_ROOT%\launcher.log" 2>&1
set "BVWA_EXIT=%ERRORLEVEL%"
if "%BVWA_EXIT%"=="0" exit /b 0
type "%BVWA_ROOT%\launcher.log"
echo Startup failed. See launcher.log for details.
pause
exit /b %BVWA_EXIT%
:install
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%BVWA_ROOT%\tools\Install-Runtime.ps1"
if errorlevel 1 goto failed
if not exist "%BVWA_PYTHON%" goto failed
goto run
:failed
echo Runtime installation failed. Download and extract the complete portable package.
pause
exit /b 1
