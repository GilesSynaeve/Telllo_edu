@echo off
rem Start the GUI with the project's own Python (.venv), where OpenCV, djitellopy etc. are installed.
rem Plain "python tello_gui.py" uses the system Python, which lacks them (ModuleNotFoundError: cv2).
rem   lancer_gui.bat            -> direct Wi-Fi of the drone (AP)
rem   lancer_gui.bat --sim      -> simulator, no drone
rem   lancer_gui.bat --connection sta --host 192.168.1.77
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Environnement .venv introuvable. Creer-le avec :
    echo   python -m venv .venv
    echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)
if "%~1"=="" (
    ".venv\Scripts\python.exe" -X utf8 tello_gui.py --connection ap
) else (
    ".venv\Scripts\python.exe" -X utf8 tello_gui.py %*
)
if errorlevel 1 pause
