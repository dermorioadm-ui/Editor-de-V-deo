@echo off
title Liberar espaco - Editor de Video
cd /d "%~dp0"
echo.
echo  ============================================================
echo   Liberar espaco em disco
echo  ============================================================
echo.
echo  Cada copia ANTIGA do editor guarda um ambiente Python de ~3 GB
echo  dentro dela (a pasta .venv). Baixar o ZIP varias vezes enche o
echo  disco sem ninguem perceber. Isto apaga esses ambientes.
echo.
echo  O QUE NAO E APAGADO:
echo    - os seus projetos e videos (ficam em Videos\Editor de Video)
echo    - a sua chave do Gemini
echo    - o ambiente NOVO, compartilhado, em AppData\Local
echo.

python --version 2>nul | findstr /B "Python" >nul
if errorlevel 1 (
    echo  [X] Python nao encontrado. Nao consigo medir os tamanhos.
    pause
    exit /b 1
)

python "%~dp0limpar.py"
echo.
pause
