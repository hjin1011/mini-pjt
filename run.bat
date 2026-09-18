@echo off
rem Double-click launcher for Windows (Windows counterpart to run.sh).
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [1/2] First run - creating virtual environment and installing packages...
  python -m venv .venv
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

if not exist ".env" (
  echo WARNING: .env not found. Add AWS credentials to .env or worksheet generation will fail.
)

echo [2/2] Starting server. Closing this window will stop the server.
start "" cmd /c "ping -n 3 127.0.0.1 >nul && start http://127.0.0.1:8000/"
".venv\Scripts\python.exe" -m uvicorn src.api:app --host 127.0.0.1 --port 8000

pause
