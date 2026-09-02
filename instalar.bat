@echo off
title Instalar o Sharkcut
cd /d "%~dp0"

REM A instalacao tem janela agora: o instalar.ps1 desenha uma tela de verdade,
REM com barra de progresso e as caixinhas dos atalhos. PowerShell ja vem em
REM qualquer Windows desde o 7, entao nao entrou dependencia nova.
REM
REM Se o PowerShell nao existir, ou se a janela nao subir (o script sai com
REM codigo 2 quando o WinForms nao carrega, e o proprio powershell devolve
REM erro se a politica de execucao barrar), cai para o instalador de texto,
REM que continua inteiro em instalar-console.bat.
REM
REM SEM "goto" E SEM BLOCOS DE PARENTESES DE PROPOSITO: sao justamente as
REM construcoes que o cmd.exe erra quando o .bat chega com quebra de linha
REM do Unix - e o ZIP baixado do GitHub chega assim. Linha a linha, funciona
REM dos dois jeitos.
set SHARKCUT_TELA=0
where powershell >nul 2>&1
if errorlevel 1 set SHARKCUT_TELA=1
if "%SHARKCUT_TELA%"=="0" echo  Abrindo a janela de instalacao...
if "%SHARKCUT_TELA%"=="0" powershell -NoProfile -ExecutionPolicy Bypass -STA -File "%~dp0instalar.ps1"
if errorlevel 1 set SHARKCUT_TELA=1
if "%SHARKCUT_TELA%"=="1" echo  Abrindo o instalador em modo texto...
if "%SHARKCUT_TELA%"=="1" call "%~dp0instalar-console.bat"
