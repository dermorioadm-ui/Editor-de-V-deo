@echo off
title Instalar o Editor de Video
cd /d "%~dp0"
echo.
echo  ============================================================
echo   Instalando o Editor de Video
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

REM ------------------------------------------------------------------ disco
REM O ambiente pesa ~3 GB e o modelo de transcricao ~1,5 GB. Conferir ANTES
REM evita o "No space left on device" no meio do download, que deixa a
REM instalacao pela metade e ninguem entende o que aconteceu.
REM A leitura e feita pelo Python (que ja sabemos que existe) porque ele le
REM a variavel do ambiente direto, sem passar pelas regras de aspas do batch
REM - importante quando o nome do usuario tem & no meio.
set LIVRE=999
for /f %%G in ('python -c "import shutil,os;print(shutil.disk_usage(os.environ.get('LOCALAPPDATA') or 'C:\\').free//(1024**3))"') do set LIVRE=%%G
echo  [i] Espaco livre: %LIVRE% GB

if %LIVRE% LSS 6 (
    echo.
    echo  [X] Espaco insuficiente: %LIVRE% GB livres, e sao precisos uns 6 GB.
    echo.
    echo      Rode o limpar.bat desta pasta: ele apaga os ambientes das
    echo      copias ANTIGAS do editor, que costumam ser o que encheu o
    echo      disco ^(cada copia antiga guarda ~3 GB^).
    echo.
    pause
    exit /b 1
)

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

REM ----------------------------------------------------------------- ambiente
REM UM ambiente so, fora da pasta do programa, compartilhado por todas as
REM copias. Atualizar o editor passa a ser: extrair o ZIP novo e rodar isto -
REM que so confere as bibliotecas em vez de baixar 3 GB de novo.
set "VENV=%LOCALAPPDATA%\Editor de Video\venv"
set "PY=%VENV%\Scripts\python.exe"

echo.
if exist "%PY%" (
    echo  Ambiente ja existe em:
    echo    "%VENV%"
    echo  Conferindo as bibliotecas ^(rapido^)...
) else (
    echo  Criando o ambiente em:
    echo    "%VENV%"
    echo  Isso baixa cerca de 400 MB e demora alguns minutos.
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo.
        echo  [X] Nao consegui criar o ambiente. A mensagem acima diz o motivo.
        pause
        exit /b 1
    )
)

"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo  [X] A instalacao das bibliotecas falhou. A mensagem acima diz o motivo.
    echo      Se falou em "No space left on device", rode o limpar.bat.
    echo.
    pause
    exit /b 1
)

echo.
echo  Conferindo a instalacao...
"%PY%" -m editor --check
echo.
echo  Rodando o autoteste ^(corta e exporta um video de teste^)...
"%PY%" -m editor --test
echo.
echo  ============================================================
echo   Pronto. Para abrir o editor, use o iniciar.bat
echo  ============================================================
echo.
pause
