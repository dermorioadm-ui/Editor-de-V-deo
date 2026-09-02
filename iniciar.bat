@echo off
REM ---------------------------------------------------------------------
REM  Onde vive o ambiente Python. UM SO, compartilhado por todas as copias
REM  do editor que existam no disco.
REM
REM  Antes ele era criado dentro da pasta do programa (.venv). Quem baixava
REM  o ZIP a cada atualizacao ficava com uma copia de ~3 GB por pasta: dez
REM  atualizacoes = 30 GB, e o disco enchia no meio da instalacao com
REM  "No space left on device". Agora todas as copias usam o mesmo.
REM
REM  As aspas em torno do SET nao sao enfeite: nome de usuario com & (por
REM  exemplo C:\Users\Renato&Cibele) quebra o batch sem elas.
REM ---------------------------------------------------------------------
set "VENV=%LOCALAPPDATA%\Editor de Video\venv"
set "PY=%VENV%\Scripts\python.exe"

REM instalacao antiga, feita dentro da propria pasta: continua valendo
if not exist "%PY%" (
    if exist "%~dp0.venv\Scripts\python.exe" (
        set "VENV=%~dp0.venv"
        set "PY=%~dp0.venv\Scripts\python.exe"
    )
)

title Bisturi
cd /d "%~dp0"

REM Para trocar o modelo de transcricao, tire o REM da linha abaixo:
REM set EDITOR_WHISPER_MODEL=turbo

REM A chave do Gemini colada na tela inicial fica guardada para sempre no
REM banco local. Se preferir nao ter chave em disco, use a variavel abaixo:
REM set EDITOR_GEMINI_KEY=sua-chave-aqui

if not exist "%PY%" (
    echo.
    echo  O editor ainda nao foi instalado.
    echo  Rode o instalar.bat primeiro.
    echo.
    pause
    exit /b 1
)

"%PY%" -m editor
if errorlevel 1 (
    echo.
    echo  O editor fechou com erro. A mensagem acima diz o motivo.
    echo  Se falar em ffmpeg, veja a secao 2.2 do README.
    echo.
    pause
)
