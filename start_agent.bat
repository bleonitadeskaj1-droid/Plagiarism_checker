@echo off
cd /d "%~dp0"
set PYTHONPATH=%CD%
echo Starting Python plagiarism agent on http://127.0.0.1:5000 ...
".venv\Scripts\python.exe" agent\main.py
pause
