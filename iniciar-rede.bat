@echo off
title Editor de Video (modo rede)
cd /d "%~dp0"

REM Abre o editor tambem para outros aparelhos da MESMA rede (celular,
REM notebook). O video continua no disco DESTE PC: o outro aparelho so ve a
REM tela. Nao use em rede publica.

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo  O editor ainda nao foi instalado nesta pasta.
    echo  Rode o instalar.bat primeiro.
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m editor --rede
if errorlevel 1 (
    echo.
    echo  O editor fechou com erro. A mensagem acima diz o motivo.
    echo.
    pause
)
