"""Servidor MCP do Sharkcut — as ferramentas que o Claude usa na máquina.

O editor já é um servidor HTTP em 127.0.0.1 com mais de cem rotas. Este pacote
não reimplementa nada disso: ele traduz GESTO em chamada HTTP e resposta em
TEXTO CURTO que diz o próximo passo.

POR QUE HTTP E NÃO IMPORTAR editor.projects DIRETO: o editor guarda tudo num
SQLite e tem uma fila de jobs com um worker. Dois processos escrevendo no mesmo
banco e disputando a mesma fila é corrupção esperando acontecer. O MCP é um
cliente do editor, como o navegador é.
"""
