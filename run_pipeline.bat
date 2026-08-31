@echo off
setlocal EnableDelayedExpansion

:: Set title and working directory to the script's root folder
title Lebanon Market Tracker - Automated Data Pipeline
cd /d "%~dp0"

echo ================================================================
echo           LEBANON MARKET TRACKER - AUTOMATED ETL
echo ================================================================
echo Started at: %DATE% %TIME%
echo Working Directory: %CD%
echo.

:: 1. Detect Python environment
set "PYTHON_EXE=python"
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
    echo [INFO] Using virtual environment: .venv
) else if exist "venv\Scripts\python.exe" (
    set "PYTHON_EXE=venv\Scripts\python.exe"
    echo [INFO] Using virtual environment: venv
) else (
    echo [INFO] Using system Python
)

:: Verify Python is installed and accessible
%PYTHON_EXE% --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python is not installed or not in your system PATH!
    echo Please install Python 3.10+ from python.org and add it to PATH.
    echo.
    pause
    exit /b 1
)

:: 2. Check if .env exists
if not exist ".env" (
    echo [WARNING] .env file not found. Pipeline will fallback to default localhost settings.
    echo.
)

:: 3. Execute the complete Python ETL & SQL Pipeline
echo [INFO] Executing ETL Pipeline (Extract, Stage, and SQL Views Refresh)...
echo.
%PYTHON_EXE% src\extract_and_stage.py

set "EXIT_CODE=%ERRORLEVEL%"

echo.
if %EXIT_CODE% NEQ 0 (
    echo ================================================================
    echo [ERROR] Pipeline failed with error code: %EXIT_CODE%
    echo Check the latest log file at: logs\pipeline_latest.log
    echo ================================================================
    echo.
    pause
    exit /b %EXIT_CODE%
) else (
    echo ================================================================
    echo [SUCCESS] Pipeline completed successfully!
    echo - Staging tables updated
    echo - SQL cleaning views refreshed
    echo - Power BI dashboard is ready for refresh
    echo ================================================================
    echo.
    echo Logs recorded in: logs\
    echo.
    pause
)

exit /b 0
