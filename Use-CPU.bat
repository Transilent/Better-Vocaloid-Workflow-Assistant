@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONNOUSERSITE=1"
"%~dp0dependencies\vocal2midi\python\python.exe" -B "%~dp0tools\set_device.py" cpu
if errorlevel 1 pause
