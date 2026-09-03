"""Resumir a copy para o vídeo CABER numa duração pedida.

O usuário diz "quero 60 segundos" e o vídeo dele tem 3 minutos. Encurtar
acelerando a fala destrói o anúncio; encurtar cortando o silêncio não chega
perto. O que resolve é escolher O QUE SAI — e isso é julgamento de copy, que
é justamente o que a IA faz bem.

Este módulo é a chamada dedicada a isso. Ele não repete o trabalho de
``cortes.py``: as faixas que voltam passam pelas MESMAS travas (vale acústico
nas duas bordas, gancho protegido, nada de meia frase), só que com o teto de
copy aberto até o que o alvo exige — porque aqui encurtar É o pedido, não um
efeito colateral.

Se a IA não estiver disponível, quem resume é o programa, pelo mesmo
princípio, com a régua que ele tem: as etapas narrativas menos essenciais
saem primeiro, do fim para o começo, e gancho, oferta e CTA são as últimas a
serem tocadas.
"""
from __future__ import annotations

from . import gemini

MAX_PALAVRAS = 2600
# quanto o resumo pode tirar por julgamento de copy, no máximo. Acima disto
# não é resumo, é outro vídeo.
TETO_RESUMO = 0.80

INSTRUCAO = """Você é editor de anúncios em vídeo (VSL e criativo) em \
português do Brasil. O usuário gravou um vídeo mais longo do que ele pode \
publicar e quer o MESMO anúncio, mais curto.

Você recebe a transcrição PALAVRA POR PALAVRA, numerada, a duração atual e a \
duração ALVO. Sua tarefa é dizer QUAIS FAIXAS DE PALAVRAS saem para o vídeo \
caber no alvo — e o que fica tem que continuar sendo um anúncio que funciona \
sozinho, não um pedaço do original.

O que sai primeiro, nesta ordem:
1. REDUNDÂNCIA — ele já disse aquilo com outras palavras. Fica a versão mais \
curta e concreta.
2. PREÂMBULO e AUTO-COMENTÁRIO — "deixa eu te falar", "voltando aqui", \
"não sei se ficou claro".
3. DIVAGAÇÃO — assunto que abre, não volta e ninguém sentiria falta.
4. EXEMPLO REPETIDO — três exemplos da mesma coisa viram um, o melhor.
5. FINAL DUPLO — ele fecha duas ou três vezes; fica o fechamento mais forte.
6. DETALHE DE APOIO — explicação de segundo nível que sustenta um ponto que \
já está claro.

O que NUNCA sai, mesmo que o alvo aperte:
- o GANCHO (a abertura que segura a pessoa);
- a PROMESSA central do anúncio;
- número, preço, prazo, garantia, prova ou nome de produto;
- a CHAMADA PARA AÇÃO;
- meia frase: marque da primeira à última palavra do pensamento inteiro.

Regras de ouro:
- Marque o SUFICIENTE para chegar perto do alvo. Ficar 10% acima é melhor do \
que arrancar a oferta para bater o número exato.
- Cada faixa é uma IDEIA INTEIRA, da primeira à última palavra dela.
- Em "motivo", diga em poucas palavras por que aquilo era o mais dispensável.

Responda somente o JSON do esquema."""

ESQUEMA = {
    "type": "OBJECT",
    "properties": {
        "leitura": {"type": "STRING",
                    "description": "uma frase: o que o anúncio vende e o que "
                                   "você preservou"},
        "remover": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {
                "de": {"type": "INTEGER"},
                "ate": {"type": "INTEGER"},
                "tipo": {"type": "STRING", "enum": ["copy"]},
                "motivo": {"type": "STRING"},
            },
            "required": ["de", "ate", "tipo", "motivo"],
            "propertyOrdering": ["de", "ate", "tipo", "motivo"],
        }},
    },
    "required": ["leitura", "remover"],
    "propertyOrdering": ["leitura", "remover"],
}


def montar_pedido(words: list[dict], duracao_atual: float, alvo: float) -> str:
    """A transcrição numerada, a duração de agora e a que ele quer."""
    linhas = [
        f"Duração atual do vídeo montado: {duracao_atual:.0f} segundos.",
        f"Duração ALVO: {alvo:.0f} segundos.",
        f"Precisa sair, aproximadamente: {max(0.0, duracao_atual - alvo):.0f} "
        f"segundos de fala.",
        "",
        "TRANSCRIÇÃO (índice: palavra):",
    ]
    pedaco: list[str] = []
    for w in words[:MAX_PALAVRAS]:
        pedaco.append(f"{w['i']}:{str(w.get('text', '')).strip()}")
        if len(pedaco) >= 18:
            linhas.append(" ".join(pedaco))
            pedaco = []
    if pedaco:
        linhas.append(" ".join(pedaco))
    linhas += ["", "Diga as faixas que saem para caber no alvo. Responda o "
                   "JSON do esquema."]
    return "\n".join(linhas)


def pedir(chave: str, modelo: str, words: list[dict], duracao_atual: float,
          alvo: float) -> dict:
    """Uma chamada. Devolve o JSON já validado contra o esquema."""
    if not words:
        raise gemini.ErroDaIA("sem transcrição não há o que resumir")
    escolhido = gemini.escolher_modelo(chave, modelo)
    resposta = gemini.gerar_json(
        chave, escolhido["id"], INSTRUCAO,
        montar_pedido(words, duracao_atual, alvo),
        ESQUEMA, temperatura=0.1,
        maximo=min(escolhido.get("saida") or 8192, 8192))
    resposta["_modelo"] = escolhido["id"]
    return resposta


# ---------------------------------------------------------------- sem a IA
# A ordem em que as etapas narrativas são sacrificadas quando quem resume é o
# programa. É a mesma leitura de sempre: o que segura a pessoa e o que a faz
# comprar ficam; o que explica e contextualiza sai primeiro.
PRIORIDADE = ("explicacao", "dor", "mecanismo", "prova", "revelacao",
              "monetizacao", "garantia", "oferta", "gancho", "cta")


def pelo_programa(clips: list, alvo: float) -> list:
    """Quais BLOCOS o programa desligaria para caber no alvo.

    Devolve os blocos na ordem em que devem sair. Nunca toca no primeiro
    bloco (o gancho) nem no último (o fecho), e para assim que couber.
    """
    total = sum(float(c.out_duration) for c in clips)
    if total <= alvo or len(clips) < 3:
        return []
    candidatos = [c for c in clips[1:-1]]
    # menos essencial primeiro; entre iguais, o mais longo sai antes (tira
    # mais tempo com menos cortes)
    def peso(c) -> tuple:
        etapa = str(getattr(c, "section", "") or "")
        try:
            ordem = PRIORIDADE.index(etapa)
        except ValueError:
            ordem = 0           # sem etapa: é o primeiro a sair
        return (ordem, -float(c.out_duration))

    fora: list = []
    restante = total
    for c in sorted(candidatos, key=peso):
        if restante <= alvo:
            break
        fora.append(c)
        restante -= float(c.out_duration)
    return fora
