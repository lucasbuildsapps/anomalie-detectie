@echo off
REM Anomalie-detectie starter — dubbel-klik om de app te draaien.
REM Zet Python Scripts in PATH voor deze sessie en start streamlit.

set "PY_SCRIPTS=C:\Users\lucas\AppData\Local\Python\pythoncore-3.14-64\Scripts"

if not exist "%PY_SCRIPTS%\streamlit.exe" (
    echo Streamlit niet gevonden op %PY_SCRIPTS%
    echo Pas dit pad aan in start.bat naar de juiste Python-installatie.
    pause
    exit /b 1
)

cd /d "%~dp0"
set "PATH=%PY_SCRIPTS%;%PATH%"
echo Starting Anomalie-detectie...
streamlit run app.py
pause
