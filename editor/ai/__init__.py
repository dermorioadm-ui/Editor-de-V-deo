"""Camada de IA — opcional, desligada por padrão, e sempre opinião.

A regra que organiza este pacote inteiro: A IA OPINA, O CÓDIGO EXECUTA.

O modelo nunca escreve num tempo de corte, num src_start, num zoom ou num
plano. Ele devolve INTENÇÃO — que trecho é o gancho, onde a ênfase pede um
plano mais fechado, em que momento um anexo ajuda — e a maquinaria
determinística que já existe traduz isso, com todas as invariantes de sempre:

    editor/edit/zoom.py     troca só em corte, cena mínima, passo mínimo,
                            teto pela resolução da fonte, âncora alcançável
    editor/anexos.py        janela que a mídia realmente cobre, tipo certo,
                            sem sobreposição, dentro do vídeo
    editor/edit/snap.py     a borda cai no vale, nunca no meio da palavra
    editor/edit/audit.py    o que ficou sujo é acertado ou o corte não acontece

Se a IA sugerir algo que quebre qualquer uma delas, a sugestão é recusada com
o motivo escrito — não aplicada "quase".

O QUE SAI DA MÁQUINA. O vídeo NÃO sai: nem o arquivo, nem o caminho dele, nem
um segundo dele. O que sai é o TEXTO já transcrito (que é o que o modelo
precisa para entender a fala) e, quando o usuário pede ajuda com anexos, os
QUADROS JPEG dos anexos dele em 360 px. Nada disso acontece sem a chave que o
usuário coloca e sem ele ligar a IA naquele projeto.
"""
