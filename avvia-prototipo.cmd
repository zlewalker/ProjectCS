@echo off
cd /d "%~dp0"
if exist "..\.runtime\python.exe" (
  "..\.runtime\python.exe" -m streamlit run streamlit_app.py --server.address 127.0.0.1
) else (
  python -m streamlit run streamlit_app.py --server.address 127.0.0.1
)
pause
