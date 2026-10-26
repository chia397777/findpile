@echo off
rem ====================================================================
rem  Launcher for the NEW findpile search window (PySide6 / Qt).
rem
rem  Same two rules as the old launcher:
rem    1) plain ASCII only -- cmd.exe reads .bat in the OEM codepage
rem    2) address pythonw.exe by its real versioned path, not the alias
rem
rem  Extra rule here: if PySide6 is missing or broken (bad GPU driver,
rem  missing DLL, interpreter reinstalled), fall back to the old Tkinter
rem  window instead of failing silently. pythonw is used for the check
rem  too, so no console flashes up.
rem ====================================================================
cd /d "%~dp0"

set PW=%LOCALAPPDATA%\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\pythonw.exe

if not exist "%PW%" (
    start "" /min cmd /c "python app.py || python gui.py"
    exit /b
)

"%PW%" -c "import PySide6"
if errorlevel 1 (
    rem No Qt on this machine -- the old window still works.
    start "" "%PW%" gui.py
) else (
    start "" "%PW%" app.py
)
exit /b
