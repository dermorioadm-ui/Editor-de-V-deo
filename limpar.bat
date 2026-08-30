@echo off
title Liberar espaco - Editor de Video
cd /d "%~dp0"
echo.
echo  ============================================================
echo   Liberar espaco em disco
echo  ============================================================
echo.
echo  Apagar a pasta do editor NAO limpa tudo. Sobra lixo em quatro
echo  lugares, e este programa acha os quatro:
echo.
echo    1. o ambiente Python de cada copia antiga  (~3 GB cada)
echo    2. um ambiente compartilhado que ficou orfao
echo    3. o cache do pip                          (1 a 5 GB)
echo    4. os projetos de teste que voce ja editou
echo    5. modelos de transcricao que nenhuma versao usa mais (ate 10 GB)
echo.
echo  Cada grupo e confirmado SEPARADO. Nada e apagado sem voce dizer
echo  sim, e voce ve o tamanho antes de decidir.
echo.
echo  O QUE NUNCA E APAGADO:
echo    - os seus VIDEOS originais (nunca sairam da pasta onde estao)
echo    - a sua chave do Gemini
echo    - o modelo de transcricao EM USO (o app usa um so)
echo    - modelos de qualquer OUTRA ferramenta de IA que voce tenha
echo    - o ambiente de qualquer OUTRO projeto Python seu
echo.

python --version 2>nul | findstr /B "Python" >nul
if errorlevel 1 (
    echo  [X] Python nao encontrado. Nao consigo medir os tamanhos.
    echo      Instale em https://www.python.org/downloads/windows/
    pause
    exit /b 1
)

python "%~dp0limpar.py"
echo.
pause
