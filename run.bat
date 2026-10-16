@echo off
REM Levanta tegchess: prepara el entorno si hace falta y abre la interfaz.
REM Es portable: si el repo se copio de otra PC (pendrive), el .venv viejo no sirve
REM (guarda rutas absolutas) y se recrea solo. Requiere Python 3.11+ e internet esa vez.
setlocal
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"

REM 1) Entorno virtual: si existe pero no arranca en esta PC, se borra y se recrea.
if exist "%PY%" (
    "%PY%" -c "import sys" >nul 2>&1
    if errorlevel 1 (
        echo El entorno .venv es de otra PC o esta roto: se va a recrear.
        rmdir /s /q .venv
    )
)
REM Separador: protege un run.bat que este corriendo mientras se actualiza el archivo.
::::::::::::::::::::::::::::::::::::::::
if not exist "%PY%" (
    echo Creando entorno virtual...
    py -3.13 -m venv .venv 2>nul || py -3 -m venv .venv 2>nul || python -m venv .venv
    if not exist "%PY%" (
        echo No se pudo crear el entorno virtual. Instala Python 3.11 o superior desde python.org.
        pause
        exit /b 1
    )
    "%PY%" -m pip install --upgrade pip
)

REM 2) Dependencias e instalacion del paquete (tambien corrige rutas si cambio la carpeta).
"%PY%" -m pip install -q -e ".[dev]"
if errorlevel 1 (
    echo Fallo la instalacion de dependencias. Revisa la conexion a internet.
    pause
    exit /b 1
)

REM 3) Config local: si no hay config.yaml se parte del ejemplo.
if not exist "config.yaml" copy config.example.yaml config.yaml >nul

REM 4) Diagnostico rapido (Stockfish, token, usuarios) y servidor.
"%PY%" -m tegchess doctor
"%PY%" -m tegchess serve %*
