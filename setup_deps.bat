@echo off
REM Instalon te gjitha librarite Python per sistemin e plagjiatures
cd /d "%~dp0"

echo ============================================
echo  Instalimi i librarive Python
echo ============================================

if not exist ".venv\Scripts\python.exe" (
  echo Duke krijuar virtual environment (.venv)...
  python -m venv .venv
)

set PY=%CD%\.venv\Scripts\python.exe

echo Duke perditesuar pip...
"%PY%" -m pip install --upgrade pip -q

echo Duke instaluar PyTorch (CPU)...
"%PY%" -m pip install torch --index-url https://download.pytorch.org/whl/cpu -q

echo Duke instaluar librarite kryesore...
"%PY%" -m pip install -r requirements.txt -q

echo.
echo Instalimi perfundoi. Perdor .venv\Scripts\python.exe ose start_all.bat
echo.
pause
