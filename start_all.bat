@echo off
title Plagiarism Checker - Full Stack
cd /d "%~dp0"

echo ============================================
echo  Sistemi i Plagjiatures - Nisja e Stack-ut
echo ============================================
echo.

REM SQLite eshte default (.env). XAMPP/MySQL niset vetem nese .env perdor mysql.
findstr /I /C:"sqlite:" ".env" >nul 2>&1
if not errorlevel 1 (
  echo Duke perdorur SQLite — XAMPP/MySQL nuk nevojitet.
) else (
  if exist "C:\xampp2\mysql_start.bat" (
    echo Duke nisur MySQL...
    call "C:\xampp2\mysql_start.bat" >nul 2>&1
    timeout /t 3 /nobreak >nul
  )
)

REM Kill existing API on port 8000
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000" ^| findstr "LISTENING"') do (
  echo Duke ndalur API e vjeter ne portin 8000 (PID %%a)...
  taskkill /F /PID %%a >nul 2>&1
)

REM Kill existing agent on port 5000
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":5000" ^| findstr "LISTENING"') do (
  echo Duke ndalur procesin e vjeter ne portin 5000 (PID %%a)...
  taskkill /F /PID %%a >nul 2>&1
)

echo [1/3] Duke nisur FastAPI API (port 8000)...
set PYTHONPATH=%CD%
set OPENBLAS_NUM_THREADS=1
set OMP_NUM_THREADS=1
set MKL_NUM_THREADS=1
REM Set DISABLE_ML_MODELS=1 below only if analysis crashes with memory errors
if exist ".venv\Scripts\python.exe" (
  set PY=%CD%\.venv\Scripts\python.exe
) else (
  set PY=python
)
start "Plagiarism API" /MIN cmd /c ""%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

timeout /t 3 /nobreak >nul

echo [2/3] Duke nisur Python Agent (port 5000)...
set PYTHONPATH=%CD%
start "Plagiarism Agent" /MIN cmd /c ""%PY%" agent\main.py"

timeout /t 2 /nobreak >nul

echo [3/3] Duke verifikuar API...
curl -s http://127.0.0.1:8000/health >nul 2>&1
if errorlevel 1 (
  echo Kujdes: API nuk u pergjigj menjehere. Prisni disa sekonda dhe rifreskoni faqen.
) else (
  echo API eshte gati.
)

echo.
echo  Hape aplikacionin:
echo    http://127.0.0.1:8000/ballina.html
echo.
echo  Admin: perdoruesi "admin" (fjalekalimi vendoset me ADMIN_PASSWORD ne .env)
echo.
pause
