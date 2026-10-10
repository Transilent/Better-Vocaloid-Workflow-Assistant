@echo off
setlocal
cd /d "%~dp0..\"
set "PYTHONUTF8=1"
set "PYTHONNOUSERSITE=1"
"%~dp0..\dependencies\vocal2midi\python\python.exe" -B "%~dp0..\tools\set_device.py" dml
if errorlevel 1 pause
