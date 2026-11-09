@echo off
cd /d "%~dp0"
if not exist .env (
  copy .env.example .env >nul
  echo .env file created. Fill in your keys in .env, save it, then run this file again.
  notepad .env
  pause
  exit /b
)
python -m pip install --quiet pykrx yfinance requests pandas
python collect.py
echo.
echo Done. Open index.html.
pause
