@echo off
title Editor de Video
cd /d "%~dp0"

REM Para trocar o modelo de transcricao, tire o REM da linha abaixo:
REM set EDITOR_WHISPER_MODEL=turbo

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo  O editor ainda nao foi instalado nesta pasta.
    echo  Rode o instalar.bat primeiro.
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m editor
if errorlevel 1 (
    echo.
    echo  O editor fechou com erro. A mensagem acima diz o motivo.
    echo  Se falar em ffmpeg, veja a secao 2.2 do README.
    echo.
    pause
)
