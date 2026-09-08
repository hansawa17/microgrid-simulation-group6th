@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_a.ps1" %*
if errorlevel 1 (
    echo.
    echo A failed to start. Review the message above.
    pause
)
endlocal
