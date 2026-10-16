@echo off
REM Levanta tegchess: crea el entorno si falta, instala dependencias y abre la interfaz.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creando entorno virtual...
    py -3.13 -m venv .venv 2>nul || py -3 -m venv .venv || python -m venv .venv
    if errorlevel 1 (
        echo No se pudo crear el entorno virtual. Instala Python 3.11 o superior.
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
)

".venv\Scripts\python.exe" -m pip install -q -e ".[dev]"
if errorlevel 1 exit /b 1

if not exist "config.yaml" copy config.example.yaml config.yaml >nul

".venv\Scripts\python.exe" -m tegchess serve %*
