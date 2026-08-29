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

# --- travas só do corte de COPY -----------------------------------------
# Cortar copy é diferente de cortar tentativa refeita: a fala estava CERTA, e
# quem julga é a IA. As três travas abaixo saíram de medição, não de opinião.
MAX_COPY = 0.25          # no máximo um quarto do vídeo sai por julgamento
VALE_MIN = 0.12          # o corte precisa de 120 ms de vale onde se esconder
JANELA_VALE = 0.25       # e ele é procurado nesta janela em volta da borda
GANCHO = 8.0             # os primeiros segundos são intocáveis...
GANCHO_FRACAO = 0.15     # ...mas nunca mais que isto do vídeo
PAUSA_FUNDE = 0.30       # acima disto, duas faixas vizinhas são cortes SEPARADOS

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

Sua tarefa: dizer QUAIS FAIXAS DE PALAVRAS saem do vídeo final, em duas
categorias diferentes.

=== tipo "refeito" — o que deu errado na gravação ===
1. Toda tentativa que foi refeita — fica sempre a ÚLTIMA versão completa.
2. Falsos começos ("então... então hoje eu vou") — sai o tropeço, fica a frase.
3. Contagens e vinhetas de gravação ("1, 2, 3", "gravando", "de novo").
4. Muletas ISOLADAS que não carregam sentido ("éé", "tipo assim" solto).

=== tipo "copy" — o que foi dito certo mas ATRAPALHA o anúncio ===
Aqui você é diretor de criação, não revisor. Um anúncio perde a pessoa em
segundos: tudo que não empurra a venda para frente está roubando tempo do que
empurra. Corte:

5. REDUNDÂNCIA: ele já disse isso, com outras palavras. Fica a versão mais
   forte — normalmente a mais curta e concreta.
6. PREÂMBULO: "então, olha, deixa eu te falar uma coisa", "antes de começar
   eu queria dizer". A frase de verdade começa depois disso.
7. AUTO-COMENTÁRIO: "não sei se ficou claro", "deixa eu explicar melhor",
   "voltando aqui", "como eu tinha falado".
8. DIVAGAÇÃO: um assunto que abre e não volta, e que ninguém sentiria falta.
9. FINAL DUPLO: ele fecha duas ou três vezes. Fica o fechamento mais forte.

NUNCA remova, em nenhuma das duas categorias:
- conteúdo que aparece UMA VEZ SÓ, mesmo com dicção imperfeita;
- número, preço, prazo, garantia, prova ou nome de produto;
- os primeiros 8 segundos (o gancho é o que segura a pessoa);
- a chamada para ação;
- um trecho que termina em [OK] ou [ASSOBIO];
- pedaço do MEIO de uma frase que vai ficar.

Sobre o tipo "copy", duas regras que valem mais que sua vontade de melhorar:
- a unidade é a IDEIA INTEIRA. Marque da primeira à última palavra do
  pensamento, nunca meia frase. Meia frase soa picotado e o programa vai
  recusar.
- seja PARCIMONIOSO. Um anúncio bom não é o mais curto: é o que não tem
  gordura. Se você não souber dizer em uma frase por que aquilo atrapalha,
  deixe.

Na dúvida entre tirar e deixar: DEIXE. Errar deixando custa um clique do
usuário; errar tirando apaga fala que não volta.

Responda somente o JSON do esquema. Cada faixa: "de" e "ate" são índices de
palavra INCLUSIVOS, "tipo" é "refeito" ou "copy", e "motivo" tem no máximo
12 palavras e explica para o usuário, não para você."""

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
                "tipo": {"type": "STRING", "enum": ["refeito", "copy"]},
                "motivo": {"type": "STRING"},
            },
            "required": ["de", "ate", "tipo", "motivo"],
            "propertyOrdering": ["de", "ate", "tipo", "motivo"],
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


def _faixas_validas(resposta: dict, n: int,
                    words: list[dict] | None = None
                    ) -> tuple[list[dict], list[dict]]:
    """Valida e FUNDE as faixas. O que não presta volta com o motivo.

    O TIPO viaja junto do começo ao fim. Ele se perdia aqui, e a consequência
    era grave e silenciosa: sem tipo, nenhum corte era tratado como copy, e
    então o gancho podia ser cortado, o veto acústico nunca rodava e o teto
    de 25% nunca contava. Só a regressão pegou.

    E faixas encostadas de tipos DIFERENTES não se fundem: um "refeito" é
    ordem do usuário e não passa por veto; um "copy" é julgamento e passa.
    Fundir os dois faria um herdar as regras do outro.

    Nem se funde ATRAVÉS DE UMA PAUSA. Duas ideias separadas por respiro são
    dois cortes, mesmo sendo vizinhas na numeração das palavras. Fundir fazia
    cinco ideias virarem um bloco só — que estourava o teto de copy e era
    recusado INTEIRO, em vez de o teto pegar o que coubesse.
    """
    def emendadas(i: int, j: int) -> bool:
        if not words or not (0 <= i < len(words) and 0 <= j < len(words)):
            return True
        return float(words[j]["start"]) - float(words[i]["end"]) < PAUSA_FUNDE
    boas: list[tuple[int, int, str, str]] = []
    recusadas: list[dict] = []
    for item in (resposta.get("remover") or []):
        try:
            de, ate = int(item.get("de", -1)), int(item.get("ate", -1))
        except (TypeError, ValueError):
            continue
        motivo = str(item.get("motivo", ""))[:120]
        tipo = "copy" if str(item.get("tipo", "")).strip() == "copy" else "refeito"
        if de > ate:
            de, ate = ate, de
        if de < 0 or ate >= n:
            recusadas.append({"o_que": f"palavras {de}-{ate}",
                              "motivo": "essa faixa não existe na transcrição"})
            continue
        boas.append((de, ate, motivo, tipo))
    boas.sort()
    fundidas: list[list] = []
    for de, ate, motivo, tipo in boas:
        if (fundidas and de <= fundidas[-1][1] + 1 and fundidas[-1][3] == tipo
                and emendadas(fundidas[-1][1], de)):
            fundidas[-1][1] = max(fundidas[-1][1], ate)
            if motivo and motivo not in fundidas[-1][2]:
                fundidas[-1][2] = f"{fundidas[-1][2]}; {motivo}"[:160]
        else:
            fundidas.append([de, ate, motivo, tipo])
    return ([{"de": f[0], "ate": f[1], "motivo": f[2], "tipo": f[3]}
             for f in fundidas], recusadas)


def _tem_vale(env, t: float) -> float:
    """Existe respiro em volta deste instante? Devolve a duração do maior."""
    if env is None:
        return 9.9              # sem envelope, não há como vetar
    runs = env.silence_runs(max(0.0, t - JANELA_VALE),
                            min(env.duration, t + JANELA_VALE),
                            min_duration=0.02)
    return max((r.duration for r in runs), default=0.0)


def aplicar(words: list[dict], resposta: dict, env=None,
            duracao: float = 0.0) -> dict:
    """Faixas de palavras -> takes descartados, com todas as travas.

    Cada faixa vira um take na lista "Saiu sozinho": restaurável com um
    clique, com o texto riscado e o motivo que a IA deu. A borda em tempo é
    provisória de propósito — quem a encosta no vale é o snap de sempre.

    O VETO ACÚSTICO do corte de copy mora aqui. Medido: tirar uma ideia no
    meio de fala corrida não tem onde esconder a emenda — em 4 de 5 pontos
    dentro de uma frase fluente não existe vale nenhum, e a costura salta até
    6 dB. Na fronteira de frase o vale tem 250 ms e o salto é 0,0 dB. Então a
    regra não é "palavra ou frase": é TEM VALE OU NÃO TEM. Uma palavra pode
    sair, se estiver cercada de micro-pausa; uma frase não pode, se o falante
    emendou na seguinte.
    """
    n = len(words)
    faixas, recusadas = _faixas_validas(resposta, n, words)
    # O gancho é medido em segundos, mas 8 s fixos comeriam 60% de um criativo
    # de 13 s — a proteção viraria uma mordaça. Vale o MENOR entre os 8 s e
    # 15% do vídeo: num criativo de 40 s protege 6 s, numa VSL de 5 min
    # protege os 8 s de sempre.
    if duracao <= 0:
        duracao = float(words[-1]["end"]) if words else 0.0
    gancho = min(GANCHO, duracao * GANCHO_FRACAO) if duracao > 0 else GANCHO

    removidas = sum(f["ate"] - f["de"] + 1 for f in faixas)
    if n and removidas / n > MAX_REMOCAO:
        return {"takes": [], "recusados": recusadas + [{
            "o_que": "a resposta inteira",
            "motivo": (f"a IA quis remover {removidas} de {n} palavras "
                       f"({removidas / n:.0%}). Isso não é edição, é apagar o "
                       f"vídeo — fiquei com a regra do programa.")}],
            "leitura": str(resposta.get("leitura", ""))[:300], "ok": False}

    takes: list[dict] = []
    gasto_copy = 0
    for f in faixas:
        bloco = words[f["de"]:f["ate"] + 1]
        texto = " ".join(str(w.get("text", "")).strip() for w in bloco)
        t0, t1 = float(bloco[0]["start"]), float(bloco[-1]["end"])
        copy = f.get("tipo") == "copy"

        if copy:
            # 1) o gancho não se toca
            if t0 < gancho:
                recusadas.append({
                    "o_que": texto[:40],
                    "motivo": f"está nos primeiros {gancho:.1f} s — o gancho é "
                              f"o que segura a pessoa, não corto"})
                continue
            # 2) as DUAS bordas precisam de vale
            va, vb = _tem_vale(env, t0), _tem_vale(env, t1)
            if min(va, vb) < VALE_MIN:
                recusadas.append({
                    "o_que": texto[:40],
                    "motivo": f"não tem respiro aqui ({min(va, vb)*1000:.0f} ms; "
                              f"preciso de {VALE_MIN*1000:.0f}). Cortar no meio "
                              f"da fala corrida sai picotado"})
                continue
            # 3) teto próprio, bem mais apertado que o do take
            n_bloco = f["ate"] - f["de"] + 1
            if n and (gasto_copy + n_bloco) / n > MAX_COPY:
                recusadas.append({
                    "o_que": texto[:40],
                    "motivo": f"já tirei {MAX_COPY:.0%} do vídeo por julgamento "
                              f"de copy; daqui em diante só o que você mandou"})
                continue
            gasto_copy += n_bloco

        takes.append({
            "id": f"ia_{uuid.uuid4().hex[:8]}",
            "start": round(max(0.0, float(bloco[0]["start"]) - FOLGA), 3),
            "end": round(float(bloco[-1]["end"]) + FOLGA, 3),
            "clap_id": None,
            "clap_time": None,
            "text": texto[:400],
            "reason": f["motivo"] or ("a IA achou que atrapalha" if copy
                                      else "a IA marcou como refeito"),
            "source": "ia_copy" if copy else "ia",
            "restored": False,
        })
    return {"takes": takes, "recusados": recusadas,
            "leitura": str(resposta.get("leitura", ""))[:300], "ok": True}


def decidir(chave: str, modelo: str, words: list[dict], claps: list[dict],
            whistles: list[dict], env=None) -> dict:
    """Uma chamada. Transcrição vai, faixas voltam, travas aplicam."""
    if not words:
        raise gemini.ErroDaIA("sem transcrição não há o que decidir")
    escolhido = gemini.escolher_modelo(chave, modelo)
    resposta = gemini.gerar_json(
        chave, escolhido["id"], INSTRUCAO,
        montar_pedido(words, claps, whistles),
        ESQUEMA, temperatura=0.1,
        maximo=min(escolhido.get("saida") or 8192, 8192))
    saida = aplicar(words, resposta, env=env,
                    duracao=float(words[-1]["end"]) if words else 0.0)
    saida["modelo"] = escolhido["id"]
    return saida
