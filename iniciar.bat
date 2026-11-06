@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3.10+ nao encontrado. Instale em python.org e marque "Add to PATH". & pause & exit /b 1)
if not exist .venv\Scripts\pythonw.exe (
  echo Primeira execucao: instalando dependencias, aguarde...
  python -m venv .venv || (pause & exit /b 1)
  .venv\Scripts\python -m pip install -q --disable-pip-version-check -r requirements.txt || (pause & exit /b 1)
  .venv\Scripts\python -m playwright install chromium
)
start "" .venv\Scripts\pythonw.exe main.py
