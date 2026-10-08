@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "FLOW_PYTHON=%~dp0dependencies\vocal2midi\python\python.exe"
if not exist "%FLOW_PYTHON%" goto missing
chcp 65001 >nul
"%FLOW_PYTHON%" -B -u "%~dp0check_dependencies.py" --full
set "FLOW_EXIT=%ERRORLEVEL%"
echo.
echo Report: "%~dp0"
pause
exit /b %FLOW_EXIT%
:missing
echo Python runtime not found: "%FLOW_PYTHON%"
pause
exit /b 1
