@echo off
setlocal
cd /d "%~dp0..\"
set "PYTHONUTF8=1"
set "PYTHONNOUSERSITE=1"

where python >nul 2>&1
if errorlevel 1 (
    echo Python is required on PATH to bootstrap the standalone WebGPU runtime.
    echo Install Python 3.10+ or run: py -3 tools\bootstrap_webgpu_runtime.py %*
    pause
    exit /b 1
)

python -B "%~dp0bootstrap_webgpu_runtime.py" %*
if errorlevel 1 pause
