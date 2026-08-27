@echo off
title Instalar o Editor de Video
cd /d "%~dp0"
echo.
echo  ============================================================
echo   Instalando o Editor de Video
echo   Isso baixa cerca de 400 MB e demora alguns minutos.
echo  ============================================================
echo.

REM "where python" acha o ALIAS da Microsoft Store mesmo sem Python instalado;
REM o unico teste confiavel e rodar --version e conferir a saida
python --version 2>nul | findstr /B "Python" >nul
if errorlevel 1 (
    echo  [X] Python nao encontrado ^(ou e so o atalho da Microsoft Store^).
    echo.
    echo      Instale em https://www.python.org/downloads/windows/
    echo      e MARQUE a caixinha "Add python.exe to PATH".
    echo      Depois FECHE esta janela e rode o instalador de novo.
    echo.
    pause
    exit /b 1
)
echo  [OK] Python encontrado:
python --version

where ffmpeg >nul 2>&1
if errorlevel 1 (
    if exist "C:\ffmpeg\bin\ffmpeg.exe" (
        echo  [OK] ffmpeg encontrado em C:\ffmpeg\bin
    ) else (
        echo.
        echo  [!] ffmpeg nao encontrado.
        echo      Tentando instalar com o winget...
        winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
        echo.
        echo      Se o winget nao funcionou, veja a secao 2.2 do README.
        echo      Depois FECHE esta janela e rode o instalador de novo.
        echo.
    )
) else (
    echo  [OK] ffmpeg encontrado:
    ffmpeg -version 2>&1 | findstr /B "ffmpeg version"
)

echo.
echo  Criando o ambiente e instalando as bibliotecas...
python -m venv .venv
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\pip.exe" install -r requirements.txt
if errorlevel 1 (
    echo.
    echo  [X] A instalacao das bibliotecas falhou. A mensagem acima diz o motivo.
    pause
    exit /b 1
)

echo.
echo  Conferindo a instalacao...
".venv\Scripts\python.exe" -m editor --check
echo.
echo  Rodando o autoteste (corta e exporta um video de teste)...
".venv\Scripts\python.exe" -m editor --test
echo.
echo  ============================================================
echo   Pronto. Para abrir o editor, use o iniciar.bat
echo  ============================================================
echo.
pause
