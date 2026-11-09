
@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Please run setup_windows.bat first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" python\run_analysis.py

if errorlevel 1 (
    echo Analysis failed.
    pause
    exit /b 1
)

echo.
echo Analysis completed successfully!
pause
