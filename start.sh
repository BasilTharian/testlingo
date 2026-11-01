#!/usr/bin/env bash
# Testlingo - macOS / Linux start script
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi
source .venv/bin/activate
echo "Checking packages..."
pip install -q -r requirements.txt        # also installs anything new after an update
python -m playwright install chromium
( sleep 2; (command -v open >/dev/null && open http://127.0.0.1:5000) || (command -v xdg-open >/dev/null && xdg-open http://127.0.0.1:5000) ) &
python app.py
