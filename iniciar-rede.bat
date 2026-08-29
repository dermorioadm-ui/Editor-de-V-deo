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

title Editor de Video (modo rede)
cd /d "%~dp0"

REM Abre o editor tambem para outros aparelhos da MESMA rede (celular,
REM notebook). O video continua no disco DESTE PC: o outro aparelho so ve a
REM tela. Nao use em rede publica.

if not exist "%PY%" (
    echo.
    echo  O editor ainda nao foi instalado.
    echo  Rode o instalar.bat primeiro.
    echo.
    pause
    exit /b 1
)

"%PY%" -m editor --rede
if errorlevel 1 (
    echo.
    echo  O editor fechou com erro. A mensagem acima diz o motivo.
    echo.
    pause
)
