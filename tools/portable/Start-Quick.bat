@echo off
setlocal
cd /d "%~dp0"

set "VWA_ROOT=%CD%"
set "VWA_PYTHON=%VWA_ROOT%\dependencies\vocal2midi\python\python.exe"
set "VWA_QT6=%VWA_ROOT%\runtime\qt6"
set "VWA_QUICK_LOG=%VWA_ROOT%\quick-launch.log"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONNOUSERSITE=1"

if not exist "%VWA_PYTHON%" goto missing_python
if not exist "%VWA_ROOT%\launch.py" goto missing_launch
if not exist "%VWA_QT6%\PySide6\plugins\platforms\qwindows.dll" goto missing_qt

set "PATH=%VWA_QT6%\PySide6;%VWA_ROOT%\dependencies\vocal2midi\python;%PATH%"
set "PYTHONPATH=%VWA_QT6%"
set "QT_PLUGIN_PATH=%VWA_QT6%\PySide6\plugins"
set "QT_QPA_PLATFORM_PLUGIN_PATH=%VWA_QT6%\PySide6\plugins\platforms"
set "QML2_IMPORT_PATH=%VWA_QT6%\PySide6\qml"
set "QML_IMPORT_PATH=%VWA_QT6%\PySide6\qml"

"%VWA_PYTHON%" -B -u "%VWA_ROOT%\launch.py" %* > "%VWA_QUICK_LOG%" 2>&1
set "VWA_EXIT=%ERRORLEVEL%"
if "%VWA_EXIT%"=="0" exit /b 0

type "%VWA_QUICK_LOG%"
echo.
echo Quick component candidate failed. See quick-launch.log for details.
pause
exit /b %VWA_EXIT%

:missing_python
echo Embedded Python is missing: %VWA_PYTHON%
pause
exit /b 1

:missing_launch
echo Real launch.py is missing: %VWA_ROOT%\launch.py
pause
exit /b 1

:missing_qt
echo Qt 6 runtime is missing or incomplete: %VWA_QT6%
pause
exit /b 1
