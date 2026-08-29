"""A IA decide os CORTES — automática, assim que o vídeo é analisado.

O usuário mediu: a regra determinística acerta uns 75% dos takes. O motivo é
estrutural, não de ajuste — palma e assobio dizem ONDE algo aconteceu, mas não
O QUE deve sair. Quem sabe o que é tentativa errada, muleta e repetição é quem
LÊ a fala inteira. Então a leitura vai para o modelo, e a decisão volta em
FAIXAS DE PALAVRAS, nunca em tempos:

    {"remover": [{"de": 12, "ate": 31, "motivo": "tentativa refeita"}]}

Índice de palavra é a moeda por três motivos:
  1. é inambíguo — tempo com arredondamento parte palavra no meio; índice não;
  2. a borda real continua sendo do snap: quem encosta o corte no vale de
     energia é o código de sempre, e a regra 3 fica onde sempre esteve;
  3. dá para AUDITAR — cada faixa vira um take descartado visível na lista
     "Saiu sozinho", com o texto riscado e o botão de voltar.

A regra continua a mesma da casa: a IA opina, o código executa. Se a resposta
vier com bobagem (faixa que não existe, remover o vídeo quase inteiro), a
bobagem é recusada com o motivo e o que sobrou de bom é aplicado. Se a rede
cair, a regra determinística — com as barreiras de assobio — assume sozinha.
"""
from __future__ import annotations

import uuid

from . import gemini

MAX_PALAVRAS = 2600      # ~15 min de fala; acima disso o pedido é truncado
MAX_REMOCAO = 0.85       # a IA nunca remove mais que isto do vídeo
FOLGA = 0.04             # respiro em volta da palavra, antes do snap

INSTRUCAO = """Você é editor de vídeos verticais de anúncio em português do \
Brasil (VSL e criativo): uma pessoa falando para a câmera, gravado num take \
só, cheio de tentativas refeitas.

Você recebe a transcrição PALAVRA POR PALAVRA, numerada, com os marcadores \
que a própria pessoa fez durante a gravação:

- [CORTA] = a pessoa DISSE "corta" (ou "apaga", "errei"): a tentativa em \
andamento deve ser descartada, e ela refaz em seguida. A própria palavra de \
comando já é removida pelo programa.
- [OK] = a pessoa DISSE "ok" (ou "boa", "fechou"): tudo o que veio antes está \
APROVADO. Nunca remova nada que termina num [OK].
- [PALMA] = mesma coisa que [CORTA], marcada com uma palma. A pessoa \
normalmente conta "1, 2, 3" depois e refaz a frase — a contagem sai junto.
- [ASSOBIO] = mesma coisa que [OK], marcada com assobio.
- [PAUSA Xs] = silêncio longo. O silêncio já é cortado pelo programa; você \
não precisa se ocupar dele.

Sua tarefa: dizer QUAIS FAIXAS DE PALAVRAS saem do vídeo final. Remova:
1. Toda tentativa que foi refeita — fica sempre a ÚLTIMA versão completa.
2. Falsos começos ("então... então hoje eu vou") — sai o tropeço, fica a frase.
3. Contagens e vinhetas de gravação ("1, 2, 3", "gravando", "de novo").
4. Muletas ISOLADAS que não carregam sentido ("éé", "tipo assim" solto) — só
   quando removê-las não deixa a frase manca.

NUNCA remova:
- conteúdo que só aparece uma vez, mesmo que a dicção não esteja perfeita;
- um trecho que termina em [ASSOBIO];
- uma palavra do meio de uma frase que vai ficar.

Na dúvida entre tirar e deixar: DEIXE. Errar deixando custa um clique do
usuário; errar tirando apaga fala que não volta.

Responda somente o JSON do esquema. Cada faixa: "de" e "ate" são índices de
palavra INCLUSIVOS, e "motivo" tem no máximo 12 palavras."""

ESQUEMA = {
    "type": "OBJECT",
    "properties": {
        "leitura": {"type": "STRING",
                    "description": "uma frase: o que o vídeo vende e como"},
        "remover": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {
                "de": {"type": "INTEGER"},
                "ate": {"type": "INTEGER"},
                "motivo": {"type": "STRING"},
            },
            "required": ["de", "ate", "motivo"],
            "propertyOrdering": ["de", "ate", "motivo"],
        }},
    },
    "required": ["leitura", "remover"],
    "propertyOrdering": ["leitura", "remover"],
}


def montar_pedido(words: list[dict], claps: list[dict],
                  whistles: list[dict]) -> str:
    """A transcrição numerada com os marcadores no lugar onde aconteceram."""
    eventos: list[tuple[float, str]] = []
    for c in claps:
        if c.get("enabled", True):
            dito = "disse" in str(c.get("reason", ""))
            eventos.append((float(c["time"]), "[CORTA]" if dito else "[PALMA]"))
    for a in whistles:
        if a.get("enabled", True):
            dito = "disse" in str(a.get("reason", ""))
            eventos.append((float(a["time"]), "[OK]" if dito else "[ASSOBIO]"))
    eventos.sort()

    linhas: list[str] = []
    ev = 0
    fim_anterior: float | None = None
    for i, w in enumerate(words[:MAX_PALAVRAS]):
        inicio = float(w["start"])
        while ev < len(eventos) and eventos[ev][0] <= inicio:
            linhas.append(f"    {eventos[ev][1]} aos {eventos[ev][0]:.1f}s")
            ev += 1
        if fim_anterior is not None and inicio - fim_anterior >= 1.0:
            linhas.append(f"    [PAUSA {inicio - fim_anterior:.1f}s]")
        linhas.append(f"{i} | {inicio:6.1f}s | {str(w.get('text', '')).strip()}")
        fim_anterior = float(w["end"])
    for t, rot in eventos[ev:]:
        linhas.append(f"    {rot} aos {t:.1f}s")
    if len(words) > MAX_PALAVRAS:
        linhas.append(f"(cortado em {MAX_PALAVRAS} palavras; o resto fica "
                      f"com a regra do programa)")
    return "\n".join(linhas)


def _faixas_validas(resposta: dict, n: int) -> tuple[list[dict], list[dict]]:
    """Valida e FUNDE as faixas. O que não presta volta com o motivo."""
    boas: list[tuple[int, int, str]] = []
    recusadas: list[dict] = []
    for item in (resposta.get("remover") or []):
        try:
            de, ate = int(item.get("de", -1)), int(item.get("ate", -1))
        except (TypeError, ValueError):
            continue
        motivo = str(item.get("motivo", ""))[:120]
        if de > ate:
            de, ate = ate, de
        if de < 0 or ate >= n:
            recusadas.append({"o_que": f"palavras {de}-{ate}",
                              "motivo": "essa faixa não existe na transcrição"})
            continue
        boas.append((de, ate, motivo))
    boas.sort()
    fundidas: list[list] = []
    for de, ate, motivo in boas:
        if fundidas and de <= fundidas[-1][1] + 1:
            fundidas[-1][1] = max(fundidas[-1][1], ate)
            if motivo and motivo not in fundidas[-1][2]:
                fundidas[-1][2] = f"{fundidas[-1][2]}; {motivo}"[:160]
        else:
            fundidas.append([de, ate, motivo])
    return ([{"de": f[0], "ate": f[1], "motivo": f[2]} for f in fundidas],
            recusadas)


def aplicar(words: list[dict], resposta: dict) -> dict:
    """Faixas de palavras -> takes descartados, com todas as travas.

    Cada faixa vira um take na lista "Saiu sozinho": restaurável com um
    clique, com o texto riscado e o motivo que a IA deu. A borda em tempo é
    provisória de propósito — quem a encosta no vale é o snap de sempre.
    """
    n = len(words)
    faixas, recusadas = _faixas_validas(resposta, n)

    removidas = sum(f["ate"] - f["de"] + 1 for f in faixas)
    if n and removidas / n > MAX_REMOCAO:
        return {"takes": [], "recusados": recusadas + [{
            "o_que": "a resposta inteira",
            "motivo": (f"a IA quis remover {removidas} de {n} palavras "
                       f"({removidas / n:.0%}). Isso não é edição, é apagar o "
                       f"vídeo — fiquei com a regra do programa.")}],
            "leitura": str(resposta.get("leitura", ""))[:300], "ok": False}

    takes: list[dict] = []
    for f in faixas:
        bloco = words[f["de"]:f["ate"] + 1]
        texto = " ".join(str(w.get("text", "")).strip() for w in bloco)
        takes.append({
            "id": f"ia_{uuid.uuid4().hex[:8]}",
            "start": round(max(0.0, float(bloco[0]["start"]) - FOLGA), 3),
            "end": round(float(bloco[-1]["end"]) + FOLGA, 3),
            "clap_id": None,
            "clap_time": None,
            "text": texto[:400],
            "reason": f["motivo"] or "a IA marcou como refeito",
            "source": "ia",
            "restored": False,
        })
    return {"takes": takes, "recusados": recusadas,
            "leitura": str(resposta.get("leitura", ""))[:300], "ok": True}


def decidir(chave: str, modelo: str, words: list[dict], claps: list[dict],
            whistles: list[dict]) -> dict:
    """Uma chamada. Transcrição vai, faixas voltam, travas aplicam."""
    if not words:
        raise gemini.ErroDaIA("sem transcrição não há o que decidir")
    escolhido = gemini.escolher_modelo(chave, modelo)
    resposta = gemini.gerar_json(
        chave, escolhido["id"], INSTRUCAO,
        montar_pedido(words, claps, whistles),
        ESQUEMA, temperatura=0.1,
        maximo=min(escolhido.get("saida") or 8192, 8192))
    saida = aplicar(words, resposta)
    saida["modelo"] = escolhido["id"]
    return saida
