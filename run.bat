@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
    echo Error: uv is not installed or is not in PATH.
    exit /b 1
)
uv run multi.py
