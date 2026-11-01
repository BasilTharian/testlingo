@echo off
REM Testlingo - Windows start script
cd /d "%~dp0"
if not exist .venv (
  echo Creating virtual environment...
  python -m venv .venv
)
call .venv\Scripts\activate
echo Checking packages...
pip install -q -r requirements.txt
python -m playwright install chromium
start "" http://127.0.0.1:5000
python app.py
