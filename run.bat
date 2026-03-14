@echo off
set PYTHON_EXE=C:\Users\admin\miniconda3\envs\ana11\python.exe

"%PYTHON_EXE%" multi.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Application exited with error code %ERRORLEVEL%.
    pause
)

