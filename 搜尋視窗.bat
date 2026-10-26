@echo off
rem ====================================================================
rem  Launcher for the findpile search window.
rem
rem  Two deliberate choices here:
rem
rem  1) Everything is plain ASCII. cmd.exe reads .bat files in the OEM
rem     codepage (cp950 on this machine), so a Chinese filename written
rem     as UTF-8 comes out as garbage and the launcher silently fails.
rem
rem  2) pythonw.exe is addressed by its real path under the versioned
rem     WindowsApps folder, not by the bare "pythonw" alias -- the Store
rem     Python alias stub does not start a process. pythonw means no
rem     console window flashes up behind the GUI.
rem ====================================================================
cd /d "%~dp0"

set PW=%LOCALAPPDATA%\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\pythonw.exe

if exist "%PW%" (
    start "" "%PW%" gui.py
) else (
    rem Fallback for a different machine or a reinstalled interpreter.
    start "" /min cmd /c "python gui.py"
)
exit /b
