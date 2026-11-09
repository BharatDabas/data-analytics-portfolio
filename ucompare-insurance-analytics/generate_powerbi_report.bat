@echo off
cd /d "%~dp0"
py generate_powerbi_report.py
if errorlevel 1 pause
echo.
echo Power BI project source configuration completed.
pause
