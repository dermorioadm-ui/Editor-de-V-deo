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

from ..models import SECTIONS
from . import gemini

SECOES_VALIDAS = tuple(SECTIONS)

# quanto cada nível da trilha vale em dB, RELATIVO ao volume que o usuário
# escolheu na primeira tela. "fora" é silêncio de verdade, não quase-silêncio.
NIVEL_MUSICA = {"alto": 3.0, "normal": 0.0, "baixo": -7.0, "fora": -60.0}

MAX_PALAVRAS = 2600      # ~15 min de fala; acima disso o pedido é truncado
MAX_REMOCAO = 0.85       # a IA nunca remove mais que isto do vídeo
FOLGA = 0.04             # respiro em volta da palavra, antes do snap

# --- travas só do corte de COPY -----------------------------------------
# Cortar copy é diferente de cortar tentativa refeita: a fala estava CERTA, e
# quem julga é a IA. As três travas abaixo saíram de medição, não de opinião.
MAX_COPY = 0.25          # no máximo um quarto do vídeo sai por julgamento
VALE_MIN = 0.12          # o corte precisa de 120 ms de vale onde se esconder
VALE_FRACO = 0.06        # ...mas a MULETA aceita 60 ms de um lado, se o outro tem vale
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
- [APROVADO] = a pessoa DISSE "próximo" (ou "seguinte"): tudo o que veio \
antes está APROVADO POR ELA. É uma trava, não uma sugestão: nunca remova \
nada que termina num [APROVADO], nem por refeitura nem por julgamento de copy.
- [PALMA] = mesma coisa que [CORTA], marcada com uma palma. A pessoa \
normalmente conta "1, 2, 3" depois e refaz a frase — a contagem sai junto.
- [ASSOBIO] = mesma coisa que [APROVADO], marcada com assobio.
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
- um trecho que termina em [APROVADO] ou [ASSOBIO];
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

=== tipo "vicio" — a muleta que não faz falta ===
Marcadas na transcrição com [VÍCIO?] estão as palavras que costumam ser
muleta: "então", "né", "tipo", "assim", "sabe", "na verdade", "ou seja",
"enfim", "simplesmente". Elas NÃO são todas descartáveis, e é você quem sabe
a diferença:

- SAI: quando é enchimento e a frase fica igual sem ela. "Então, hoje eu vou
  te mostrar" -> "Hoje eu vou te mostrar". "Isso é, tipo, muito rápido" ->
  "Isso é muito rápido". "Você sabe, né, que isso funciona" -> "Você sabe que
  isso funciona".
- FICA: quando carrega sentido. "Então" ligando causa e consequência ("juntei
  tudo, ENTÃO deu certo"). "Na verdade" corrigindo o que veio antes. "Ou seja"
  abrindo uma explicação que vem em seguida.

Leia a frase SEM a palavra. Se ela continua dizendo a mesma coisa, marque
tipo "vicio". Se muda o sentido ou fica truncada, deixe.

Uma por vez, nunca em faixa: cada muleta é um "de" e "ate" na mesma palavra
(ou nas duas palavras de "tipo assim").

=== "secoes" — o RITMO do vídeo ===
Divida o vídeo INTEIRO (as palavras que FICAM) nas etapas abaixo, em ordem,
sem buraco e sem sobreposição. É esta divisão que decide a velocidade de cada
trecho e o quanto a câmera fecha — quem escolhe os números é o programa, você
escolhe o que cada pedaço É:

- gancho       — a abertura que segura a pessoa nos primeiros segundos
- dor          — o problema, o contexto, o que dói hoje
- mecanismo    — a virada, o método, "funciona assim"
- explicacao   — desenvolvimento, detalhe, o miolo (é o padrão)
- revelacao    — o clímax, o segredo, a sacada
- prova        — números, casos, resultados, depoimento
- monetizacao  — quanto se ganha, faturamento, retorno
- oferta       — preço, parcela, o que está sendo vendido
- garantia     — risco zero, devolução, prazo de arrependimento
- cta          — a chamada para agir

Use a etapa que descreve o que o trecho FAZ no anúncio, não o assunto. Um
número dentro do gancho ainda é gancho. Na dúvida, "explicacao".

=== "camera" — o JOGO DE CÂMERA ===
Marque só os trechos que merecem um enquadramento diferente do resto:

- "fechado" — a câmera chega mais perto. Para o que a pessoa precisa OUVIR
  com atenção: o número, o preço, a promessa, a frase que decide a venda.
- "aberto"  — a câmera abre. Para respiro: transição, contexto, o momento
  depois de uma informação pesada.

Seja econômico: se tudo é ênfase, nada é. Num vídeo de 2 minutos, algo entre
3 e 8 marcações. O programa escolhe os valores exatos dentro do que a lente
e a resolução permitem — você diz onde aperta e onde solta.

=== "musica" — onde a trilha sobe e onde ela some ===
Só preencha se o vídeo tiver trilha. O programa já abaixa a música sozinho
quando alguém fala (isso é reflexo, não intenção). O que você decide é a
INTENÇÃO:

- "alto"   — gancho e fechamento: a música empurra.
- "normal" — o padrão. Não precisa marcar, é o que vale onde você não falar.
- "baixo"  — embaixo de explicação densa, número, passo a passo: a trilha não
  pode disputar atenção com a informação.
- "fora"   — em cima do preço, da garantia e do CTA. Silêncio de trilha faz a
  frase pesar.

Poucas faixas, largas. Três a cinco num vídeo inteiro.

Responda somente o JSON do esquema. Em TODAS as listas, "de" e "ate" são
índices de palavra INCLUSIVOS. Em "remover", "tipo" é "refeito", "copy" ou "vicio" e
"motivo" tem no máximo 12 palavras e explica para o usuário, não para você."""

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
                "tipo": {"type": "STRING", "enum": ["refeito", "copy", "vicio"]},
                "motivo": {"type": "STRING"},
            },
            "required": ["de", "ate", "tipo", "motivo"],
            "propertyOrdering": ["de", "ate", "tipo", "motivo"],
        }},
        "secoes": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {
                "de": {"type": "INTEGER"},
                "ate": {"type": "INTEGER"},
                "secao": {"type": "STRING", "enum": list(SECOES_VALIDAS)},
            },
            "required": ["de", "ate", "secao"],
            "propertyOrdering": ["de", "ate", "secao"],
        }},
        "musica": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {
                "de": {"type": "INTEGER"},
                "ate": {"type": "INTEGER"},
                "nivel": {"type": "STRING",
                          "enum": ["alto", "normal", "baixo", "fora"]},
            },
            "required": ["de", "ate", "nivel"],
            "propertyOrdering": ["de", "ate", "nivel"],
        }},
        "camera": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {
                "de": {"type": "INTEGER"},
                "ate": {"type": "INTEGER"},
                "enfase": {"type": "STRING", "enum": ["fechado", "aberto"]},
            },
            "required": ["de", "ate", "enfase"],
            "propertyOrdering": ["de", "ate", "enfase"],
        }},
    },
    "required": ["leitura", "remover", "secoes"],
    "propertyOrdering": ["leitura", "remover", "secoes", "camera", "musica"],
}


def montar_pedido(words: list[dict], claps: list[dict],
                  whistles: list[dict], vicios: list[dict] | None = None) -> str:
    """A transcrição numerada com os marcadores no lugar onde aconteceram.

    ``vicios`` são os candidatos a muleta que o programa já achou por
    dicionário. Eles vão MARCADOS, não decididos: quem sabe se "então" está
    ligando duas ideias ou só enchendo linguiça é quem lê a frase.
    """
    eventos: list[tuple[float, str]] = []
    for c in claps:
        if c.get("enabled", True):
            dito = "disse" in str(c.get("reason", ""))
            eventos.append((float(c["time"]), "[CORTA]" if dito else "[PALMA]"))
    for a in whistles:
        if a.get("enabled", True):
            dito = "disse" in str(a.get("reason", ""))
            eventos.append((float(a["time"]),
                            "[APROVADO]" if dito else "[ASSOBIO]"))
    eventos.sort()

    candidatos = {i for v in (vicios or [])
                  for i in (v.get("word_ids") or [])}
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
        marca = " [VÍCIO?]" if i in candidatos else ""
        linhas.append(
            f"{i} | {inicio:6.1f}s | {str(w.get('text', '')).strip()}{marca}")
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
        t = str(item.get("tipo", "")).strip()
        tipo = t if t in ("copy", "vicio") else "refeito"
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


def _faixas_de_tempo(itens: list, n: int, words: list[dict], campo: str,
                     validos: tuple[str, ...]) -> list[dict]:
    """Faixas de PALAVRA -> faixas de TEMPO, validadas e sem sobreposição.

    A IA fala em índice de palavra (inambíguo); o resto do programa raciocina
    em segundos. A conversão é aqui, uma vez, e é aqui que a bobagem morre:
    faixa que não existe, rótulo que não está na lista, faixa invertida.

    Sobreposição resolve pela PRIMEIRA: duas etapas reivindicando a mesma
    palavra é a IA se contradizendo, e escolher a de baixo esconderia o
    problema no meio do vídeo em vez de na borda.
    """
    if not words:
        return []
    fora: list[dict] = []
    for item in (itens or []):
        try:
            de, ate = int(item.get("de", -1)), int(item.get("ate", -1))
        except (TypeError, ValueError):
            continue
        rotulo = str(item.get(campo, "")).strip()
        if rotulo not in validos:
            continue
        if de > ate:
            de, ate = ate, de
        de, ate = max(0, de), min(n - 1, ate)
        if de > ate:
            continue
        fora.append({"de": de, "ate": ate, campo: rotulo})
    fora.sort(key=lambda f: (f["de"], f["ate"]))
    limpas: list[dict] = []
    for f in fora:
        if limpas and f["de"] <= limpas[-1]["ate"]:
            f = {**f, "de": limpas[-1]["ate"] + 1}
            if f["de"] > f["ate"]:
                continue
        limpas.append(f)
    return [{campo: f[campo],
             "inicio": round(float(words[f["de"]]["start"]), 3),
             "fim": round(float(words[f["ate"]]["end"]), 3),
             "de": f["de"], "ate": f["ate"]}
            for f in limpas]


def _tem_vale(env, t: float) -> float:
    """Existe respiro em volta deste instante? Devolve a duração do maior."""
    if env is None:
        return 9.9              # sem envelope, não há como vetar
    runs = env.silence_runs(max(0.0, t - JANELA_VALE),
                            min(env.duration, t + JANELA_VALE),
                            min_duration=0.02)
    return max((r.duration for r in runs), default=0.0)


def aplicar(words: list[dict], resposta: dict, env=None,
            duracao: float = 0.0, teto_copy: float | None = None) -> dict:
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
    # o teto de copy é 25% na edição normal; num RESUMO para caber em X
    # segundos o usuário pediu explicitamente para encurtar, e o teto é o
    # que sobra para chegar no alvo
    max_copy = MAX_COPY if teto_copy is None else max(0.0, min(0.95, teto_copy))
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
        # o corte foi recusado ("ok" continua False, e nenhum take entra),
        # mas o RITMO e a CÂMERA não são o corte: a IA ter exagerado no que
        # tirar não invalida ela ter lido o vídeo. Quem consome secoes/camera
        # olha essas chaves, não o "ok".
        return {"takes": [], "ok": False,
                "secoes": _faixas_de_tempo(resposta.get("secoes"), n, words,
                                           "secao", SECOES_VALIDAS),
                "camera": _faixas_de_tempo(resposta.get("camera"), n, words,
                                           "enfase", ("fechado", "aberto")),
                "leitura": str(resposta.get("leitura", ""))[:300],
                "recusados": recusadas + [{
                    "o_que": "a resposta inteira",
                    "motivo": (f"a IA quis remover {removidas} de {n} palavras "
                               f"({removidas / n:.0%}). Isso não é edição, é "
                               f"apagar o vídeo — fiquei com a regra do "
                               f"programa.")}]}

    takes: list[dict] = []
    gasto_copy = 0
    palavras_removidas = 0
    for f in faixas:
        nota = ""
        bloco = words[f["de"]:f["ate"] + 1]
        texto = " ".join(str(w.get("text", "")).strip() for w in bloco)
        t0, t1 = float(bloco[0]["start"]), float(bloco[-1]["end"])
        copy = f.get("tipo") == "copy"
        vicio = f.get("tipo") == "vicio"

        if vicio:
            # A MULETA tem a mesma trava acústica do corte de copy — a emenda
            # precisa de respiro dos dois lados — mas nenhuma das outras. Não
            # entra no teto de 25% (tirar "então" não é reescrever o roteiro,
            # é limpar) e não respeita o gancho (é justamente no começo que a
            # muleta mais atrapalha). Quem julga se a palavra faz falta é a IA;
            # quem julga se a emenda cola é o envelope.
            n_palavras = f["ate"] - f["de"] + 1
            if n_palavras > 3:
                recusadas.append({
                    "o_que": texto[:40],
                    "motivo": "muleta é palavra, não frase — faixa grande "
                              "demais para tirar como vício"})
                continue
            # A muleta ("então", "né", "tipo") vive DENTRO da fala corrida: quase
            # nunca tem 120 ms de respiro dos dois lados, e com a mesma trava
            # do corte de copy ela nunca saía — o usuário via o "então" na tela
            # e a IA dizendo que tinha pedido para tirar. A muleta é curta e a
            # emenda dela é pequena: basta um lado com vale de verdade e o
            # outro com um respiro mínimo para o fade de 12 ms esconder.
            va, vb = _tem_vale(env, t0), _tem_vale(env, t1)
            if max(va, vb) < VALE_MIN or min(va, vb) < VALE_FRACO:
                recusadas.append({
                    "o_que": texto[:40],
                    "motivo": f"a muleta está colada na fala ({min(va, vb)*1000:.0f} ms "
                              f"de respiro; preciso de {VALE_MIN*1000:.0f} de um lado "
                              f"e {VALE_FRACO*1000:.0f} do outro). Tirar daqui "
                              f"picota a frase"})
                continue
            if min(va, vb) < VALE_MIN:
                nota = " (emenda apertada: pouco respiro de um lado)"

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
            if n and (gasto_copy + n_bloco) / n > max_copy:
                recusadas.append({
                    "o_que": texto[:40],
                    "motivo": f"já tirei {max_copy:.0%} do vídeo por julgamento "
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
            "reason": (f["motivo"] or ("a IA achou que atrapalha" if copy
                                       else "muleta que não faz falta" if vicio
                                       else "a IA marcou como refeito")) + nota,
            "source": "ia_copy" if copy else "ia_vicio" if vicio else "ia",
            "restored": False,
        })
        palavras_removidas += f["ate"] - f["de"] + 1
    # O RITMO e a CÂMERA voltam em FAIXAS DE TEMPO, não em números.
    #
    # A tentação era pedir a velocidade e o zoom prontos ao modelo. Isso
    # atropelaria tudo que já está medido: o teto de velocidade do preset, o
    # teto GEOMÉTRICO do zoom (que depende da largura da fonte contra a da
    # saída), a âncora concêntrica alcançável, o passo mínimo entre cenas
    # vizinhas e os dois multiplicadores da primeira tela. Devolvendo a ETAPA
    # NARRATIVA, a IA substitui exatamente o pedaço fraco — o classificador
    # por palavra-chave, que confundia "clica no link" com garantia — e todo
    # o resto do caminho continua o mesmo, com as travas no lugar.
    secoes = _faixas_de_tempo(resposta.get("secoes"), n, words,
                              "secao", SECOES_VALIDAS)
    camera = _faixas_de_tempo(resposta.get("camera"), n, words,
                              "enfase", ("fechado", "aberto"))
    musica = [{"inicio": f["inicio"], "fim": f["fim"],
               "nivel": f["nivel"], "db": NIVEL_MUSICA[f["nivel"]]}
              for f in _faixas_de_tempo(resposta.get("musica"), n, words,
                                        "nivel", tuple(NIVEL_MUSICA))
              if NIVEL_MUSICA[f["nivel"]] != 0.0]
    return {"takes": takes, "recusados": recusadas,
            "secoes": secoes, "camera": camera, "musica": musica,
            # O RELATÓRIO. Sem ele o usuário não tem como saber se a IA botou
            # a mão no vídeo — e não saber é o mesmo que ela não ter botado.
            # Tudo isto já existia calculado e morria dentro da função.
            "resumo": {
                "palavras": n,
                "refeito": sum(1 for t in takes if t["source"] == "ia"),
                "copy": sum(1 for t in takes if t["source"] == "ia_copy"),
                "vicio": sum(1 for t in takes if t["source"] == "ia_vicio"),
                # o que SAIU de verdade — antes era a soma do que a IA
                # propôs, e "0 de copy fora (56 palavras)" não fechava
                "palavras_fora": palavras_removidas,
                "palavras_propostas": sum(f["ate"] - f["de"] + 1 for f in faixas),
                "propostos": len(faixas),
                "recusados": len(recusadas),
                "secoes": len(secoes),
                "camera": len(camera),
                "musica": len(musica),
                "fechado": sum(1 for c in camera if c.get("enfase") == "fechado"),
                "aberto": sum(1 for c in camera if c.get("enfase") == "aberto"),
                "teto_copy": max_copy,
                "gancho_s": round(gancho, 1),
            },
            "leitura": str(resposta.get("leitura", ""))[:300], "ok": True}


def decidir(chave: str, modelo: str, words: list[dict], claps: list[dict],
            whistles: list[dict], env=None,
            vicios: list[dict] | None = None) -> dict:
    """Uma chamada. Transcrição vai, faixas voltam, travas aplicam."""
    if not words:
        raise gemini.ErroDaIA("sem transcrição não há o que decidir")
    escolhido = gemini.escolher_modelo(chave, modelo)
    resposta = gemini.gerar_json(
        chave, escolhido["id"], INSTRUCAO,
        montar_pedido(words, claps, whistles, vicios),
        ESQUEMA, temperatura=0.1,
        maximo=min(escolhido.get("saida") or 8192, 8192))
    saida = aplicar(words, resposta, env=env,
                    duracao=float(words[-1]["end"]) if words else 0.0)
    saida["modelo"] = escolhido["id"]
    # o modelo fixado sumiu da conta e outro entrou no lugar: isso não pode
    # acontecer em silêncio, senão o vídeo sai de um modelo que o usuário não
    # escolheu e ele descobre pela qualidade
    if escolhido.get("trocado_de"):
        saida["modelo_trocado_de"] = escolhido["trocado_de"]
    return saida
