@echo off
REM ---------------------------------------------------------------------
REM  O servidor MCP do Sharkcut, para o Claude instalado nesta maquina.
REM
REM  QUEM RODA ISTO NAO E VOCE: e o Claude Code (ou o Claude Desktop), que
REM  abre este .bat e conversa com ele por stdin/stdout. Por isso NADA pode
REM  ser impresso na saida padrao aqui dentro: uma linha de echo no lugar
REM  errado quebra a conversa, e do outro lado o sintoma e "o servidor nao
REM  responde", sem dizer por que. Todo recado para humano vai com 1>&2.
REM
REM  O editor tem que estar aberto (iniciar.bat) — este processo so fala
REM  HTTP com ele em 127.0.0.1. O arquivo de video nao passa por aqui: o
REM  que trafega e o CAMINHO dele no disco.
REM
REM  As aspas em torno do SET inteiro nao sao enfeite: nome de usuario com
REM  & (por exemplo C:\Users\Renato&Cibele) quebra o batch sem elas.
REM ---------------------------------------------------------------------
set "VENV=%LOCALAPPDATA%\Editor de Video\venv"
set "PY=%VENV%\Scripts\python.exe"

REM instalacao antiga, feita dentro da propria pasta: continua valendo
if not exist "%PY%" (
    if exist "%~dp0.venv\Scripts\python.exe" (
        set "PY=%~dp0.venv\Scripts\python.exe"
    )
)

cd /d "%~dp0"

if not exist "%PY%" (
    echo O editor ainda nao foi instalado. Rode o instalar.bat primeiro. 1>&2
    exit /b 1
)

REM -u desliga o buffer: com buffer, a resposta fica presa esperando enche-lo
REM e o cliente desiste por tempo esgotado antes de receber a primeira linha.
"%PY%" -u -m editor.mcp
