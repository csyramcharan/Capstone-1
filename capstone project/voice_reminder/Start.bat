@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
    python -B main.py
) else (
    py -3 -B main.py
)
set "result=%errorlevel%"
if not "%result%"=="0" (
    echo.
    echo Could not run Gentle Reminder. Install Python 3.10 or newer with Tcl/Tk.
    echo See README.md for setup instructions.
    pause
)
exit /b %result%
