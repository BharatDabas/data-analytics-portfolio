
@echo off
setlocal
cd /d "%~dp0"

echo ===============================================
echo JAD Ucompare Analytics - Setup
echo ===============================================

if not exist ".venv\Scripts\python.exe" (
    py -m venv .venv
)

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Virtual environment creation failed.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt

if errorlevel 1 (
    echo ERROR: Dependency installation failed.
    pause
    exit /b 1
)

echo.
echo SETUP SUCCESSFUL!
".venv\Scripts\python.exe" --version
pause
