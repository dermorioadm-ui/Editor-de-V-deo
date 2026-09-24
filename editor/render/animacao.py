"""Animação por keyframes, máscara de forma e efeitos das sobreposições.

Tudo aqui devolve EXPRESSÃO ou string de filtro. Nada roda ffmpeg — quem roda é
o renderer, e roda UMA vez por trecho. Nenhuma coisa daqui toca o vídeo de base:
os filtros entram na cadeia da PRÓPRIA sobreposição (que é uma entrada separada
do ffmpeg) ou nas expressões de posição do ``overlay``. A regra do encode único
segue intacta.

OS PRIMITIVOS FORAM MEDIDOS, NÃO DEDUZIDOS. No ffmpeg 6.1:

- posição:    ``overlay=x='EXPR(t)':eval=frame``   (já era usado assim)
- escala:     ``scale=w='EXPR(t)':h=-2:eval=frame``  — sem ``eval=frame`` a
              expressão é avaliada UMA vez e a escala fica parada.
- opacidade:  ``geq=...:a='alpha(X,Y)*EXPR(T)'`` — ``colorchannelmixer=aa`` NÃO
              aceita expressão (a coluna de flags mostra ``T`` de comando em
              tempo de execução, não de expressão).
- rotação:    ``rotate=a='EXPR(t)':c=none:ow='hypot(iw,ih)':oh='hypot(iw,ih)'``
              — ``ow``/``oh`` são avaliados UMA vez na inicialização e NÃO
              conhecem ``t``; com ``ow=rotw(...)`` o ffmpeg aborta o trecho
              inteiro com "non-positive or indefinite value nan". A caixa de
              saída tem que ser fixa, e é ``c=none``, não ``c=none@0``.
- máscara:    o mesmo ``geq`` da opacidade, multiplicando outro fator no alfa.
              Máscara e opacidade compartilham UM geq de propósito: geq avalia
              a expressão por pixel e é o filtro caro da cadeia.

TEMPO. Os ``t`` dos keyframes são absolutos na linha do tempo de SAÍDA, como já
eram os do desfoque. É isso que faz ``ops.remap_output_items`` reancorá-los de
graça quando um corte encurta o vídeo: o instante vira fonte pela linha antiga e
volta pela nova. Quem chama daqui informa o ``t0`` que desloca a expressão para
o relógio do filtro em questão — e os dois relógios são diferentes:

- ``overlay=x/y`` roda sobre o quadro PRINCIPAL: ``t`` é o tempo do trecho, logo
  ``t0`` = início do trecho.
- ``scale``/``geq``/``rotate`` rodam dentro da cadeia da sobreposição, ANTES do
  ``setpts`` que a posiciona: ali ``t`` conta do primeiro quadro dela, logo
  ``t0`` = o instante de saída em que a janela começa a aparecer neste trecho.
"""
from __future__ import annotations

# As propriedades que aceitam keyframe, com o valor de repouso de cada uma.
PROPRIEDADES = {
    "x": 0.5,
    "y": 0.25,
    "scale": 1.0,
    "opacity": 1.0,
    "rotation": 0.0,
}

# Curvas entre dois marcos. `p` é o progresso 0..1 dentro do trecho.
#   linear  — constante, o que o desfoque sempre usou
#   suave   — smoothstep: sai devagar, chega devagar (movimento de câmera)
#   entra   — acelera do repouso
#   sai     — freia até o repouso
CURVAS = ("linear", "suave", "entra", "sai")


def _progresso(p: str, curva: str) -> str:
    if curva == "suave":
        return f"(({p})*({p})*(3-2*({p})))"
    if curva == "entra":
        return f"(({p})*({p}))"
    if curva == "sai":
        return f"(({p})*(2-({p})))"
    return f"({p})"


def _marcos(keyframes: list | None, chave: str) -> list[tuple[float, float, str]]:
    """Só os marcos que falam desta propriedade, em ordem de tempo.

    Um marco que não traz a chave é de OUTRA propriedade (o usuário mexeu só na
    escala naquele instante) e não deve inventar valor para esta — senão mexer
    na escala arrastaria a posição junto.
    """
    fora: list[tuple[float, float, str]] = []
    for k in keyframes or []:
        if not isinstance(k, dict) or chave not in k or k.get(chave) is None:
            continue
        try:
            t = float(k.get("t", 0.0))
            v = float(k[chave])
        except (TypeError, ValueError):
            continue
        c = str(k.get("easing", k.get("curva", "linear")))
        fora.append((t, v, c if c in CURVAS else "linear"))
    fora.sort(key=lambda m: m[0])
    return fora


def tem_animacao(keyframes: list | None, chave: str) -> bool:
    """Dois marcos ou mais com valores diferentes. Um marco só é valor fixo."""
    m = _marcos(keyframes, chave)
    if len(m) < 2:
        return False
    return any(abs(b[1] - m[0][1]) > 1e-9 for b in m[1:])


def valor_em(keyframes: list | None, chave: str, t: float,
             repouso: float | None = None) -> float:
    """O mesmo número que a expressão do ffmpeg dá naquele instante.

    Existe para a prévia e para os testes: a prévia desenha com esta conta e o
    render desenha com a expressão, e um teste compara as duas. Se divergirem, a
    promessa de "a prévia é ao pixel o que vai baixar" está quebrada.
    """
    padrao = PROPRIEDADES.get(chave, 0.0) if repouso is None else repouso
    m = _marcos(keyframes, chave)
    if not m:
        return float(padrao)
    if t <= m[0][0]:
        return m[0][1]
    if t >= m[-1][0]:
        return m[-1][1]
    for (ta, va, _ca), (tb, vb, cb) in zip(m, m[1:]):
        if ta <= t <= tb:
            vao = tb - ta
            p = 0.0 if vao <= 1e-9 else (t - ta) / vao
            if cb == "suave":
                p = p * p * (3 - 2 * p)
            elif cb == "entra":
                p = p * p
            elif cb == "sai":
                p = p * (2 - p)
            return va + (vb - va) * p
    return m[-1][1]


def curva(keyframes: list | None, chave: str, t0: float,
          repouso: float | None = None, relogio: str = "t") -> str:
    """Expressão linear por partes (com curva) no relógio do filtro.

    Fora do primeiro e do último marco o valor SEGURA — não extrapola. Extrapolar
    manda uma janela para fora da tela quando o usuário põe dois marcos no meio
    do vídeo, e ele não pediu nada disso.

    ``relogio`` é ``t`` para quase tudo e ``T`` para o ``geq``, que é o único
    filtro cujo tempo se chama com maiúscula.
    """
    padrao = PROPRIEDADES.get(chave, 0.0) if repouso is None else repouso
    m = _marcos(keyframes, chave)
    if not m:
        return f"{float(padrao):.6f}"
    if len(m) == 1:
        return f"{m[0][1]:.6f}"
    tv = relogio
    # de trás para a frente: o valor do último marco é o fundo do if encadeado
    expr = f"{m[-1][1]:.6f}"
    for (ta, va, _ca), (tb, vb, cb) in reversed(list(zip(m, m[1:]))):
        a = ta - t0
        b = tb - t0
        vao = max(b - a, 1e-6)
        p = f"clip(({tv}-{a:.6f})/{vao:.6f},0,1)"
        trecho = f"({va:.6f}+({vb - va:.6f})*{_progresso(p, cb)})"
        expr = f"if(lt({tv},{b:.6f}),{trecho},{expr})"
    primeiro = m[0][0] - t0
    return f"if(lt({tv},{primeiro:.6f}),{m[0][1]:.6f},{expr})"


# --------------------------------------------------------------- máscara
# A máscara recorta a sobreposição por forma, com borda suave. Fica ANTES da
# rotação na cadeia, de propósito: mascarar depois de girar recortaria a caixa
# diagonal da rotação, e o usuário desenhou a máscara na imagem em pé.
FORMAS = ("retangulo", "elipse", "arredondado")


def mascara_fator(mask: dict | None) -> str:
    """Fator 0..1 do alfa, em ``X``/``Y``/``W``/``H`` do geq. "" = sem máscara.

    ``feather`` é a largura da borda suave em fração do raio. Zero dá borda
    dura — que serrilha em diagonal; o padrão tem um fio de suavização.
    """
    if not isinstance(mask, dict):
        return ""
    forma = str(mask.get("shape", mask.get("forma", "retangulo")))
    if forma not in FORMAS:
        return ""
    # a janela da máscara dentro da sobreposição, em fração (0..1)
    cx = _num(mask.get("cx", 0.5), 0.5)
    cy = _num(mask.get("cy", 0.5), 0.5)
    rx = max(1e-4, _num(mask.get("rx", 0.5), 0.5))
    ry = max(1e-4, _num(mask.get("ry", 0.5), 0.5))
    suave = max(1e-4, min(1.0, _num(mask.get("feather", mask.get("suavizar", 0.04)), 0.04)))
    # coordenadas normalizadas dentro da janela: 0 no centro, 1 na borda
    u = f"((X-{cx:.6f}*W)/({rx:.6f}*W))"
    v = f"((Y-{cy:.6f}*H)/({ry:.6f}*H))"
    if forma == "elipse":
        d = f"hypot({u},{v})"
        return f"clip((1-{d})/{suave:.6f},0,1)"
    if forma == "arredondado":
        # canto arredondado: distância de Chebyshev amaciada pelo raio. `raio` é
        # a fração do lado que o canto come.
        r = max(1e-4, min(0.999, _num(mask.get("radius", mask.get("raio", 0.18)), 0.18)))
        # dentro do miolo a distância é o máximo dos eixos; perto do canto vira
        # a distância euclidiana ao centro do arco
        # fora do miolo (|u| ou |v| passou de 1-r) a distância vira a euclidiana
        # até o centro do arco; dentro do miolo continua sendo a de Chebyshev
        qx = f"max(abs({u})-{1 - r:.6f},0)"
        qy = f"max(abs({v})-{1 - r:.6f},0)"
        arco = f"hypot({qx},{qy})"
        d = (f"if(gt({arco},0),{1 - r:.6f}+{arco},"
             f"max(abs({u}),abs({v})))")
        return f"clip((1-{d})/{suave:.6f},0,1)"
    d = f"max(abs({u}),abs({v}))"
    return f"clip((1-{d})/{suave:.6f},0,1)"


def _num(v, padrao: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return padrao


def geq_alfa(fatores: list[str]) -> str:
    """UM geq que multiplica todos os fatores no canal alfa.

    Os canais de cor passam intactos — ``r(X,Y)`` e companhia apenas copiam. É
    o jeito de mexer no alfa sem tocar na imagem.
    """
    limpos = [f for f in fatores if f]
    if not limpos:
        return ""
    produto = "*".join(f"({f})" for f in limpos)
    return (f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
            f"a='alpha(X,Y)*clip({produto},0,1)'")


# ------------------------------------------------------- opacidade animada
# POR QUE sendcmd E NÃO geq. ``colorchannelmixer=aa`` não aceita expressão de
# tempo — a coluna de flags do ffmpeg mostra ``T``, que é "aceita COMANDO em
# tempo de execução", não "aceita expressão". Fazer a opacidade variar por
# ``geq`` no canal alfa funciona e foi medido: numa janela em tela cheia
# (1080x1920, 60 quadros) custou 15,1 s contra 1,2 s da sobreposição crua.
# Com ``sendcmd`` alimentando o mesmo ``colorchannelmixer``, o resultado é
# IDÊNTICO no pixel (brilho 8 → 42 → 71 contra 7 → 42 → 72 do geq) e custa
# +0,3 s. É a mesma conta, feita em C uma vez por quadro em vez de uma vez por
# pixel.
#
# O texto vai para ARQUIVO, não embutido no grafo: o filtergraph viaja na linha
# de comando e no Windows ela para em 32767 caracteres. Uma janela de 60 s a
# 30 fps são 1800 comandos — sozinhos passariam de 60 KB.
def texto_dos_comandos(keyframes: list | None, chave: str, t0: float,
                       duracao: float, fps: float, filtro: str, opcao: str,
                       repouso: float | None = None) -> str:
    """Um comando por quadro, em tempo LOCAL da cadeia da sobreposição.

    Devolve "" quando não há animação — quem chama então usa o valor fixo.
    """
    if not tem_animacao(keyframes, chave):
        return ""
    passo = 1.0 / max(1.0, float(fps))
    fim = max(passo, float(duracao))
    linhas: list[str] = []
    t = 0.0
    while t <= fim + 1e-9:
        v = valor_em(keyframes, chave, t0 + t, repouso=repouso)
        linhas.append(f"{t:.4f} {filtro} {opcao} {v:.5f};")
        t += passo
    return "\n".join(linhas) + "\n"


# ---------------------------------------------------- entrada do lado de fora
# NADA QUE VEM DE FORA ENTRA NO PLANO SEM PASSAR POR AQUI. O plano é lido pelo
# render, que monta expressão de ffmpeg com esses números: um "t" que é texto,
# um NaN ou uma lista dentro de lista viram expressão inválida e o ffmpeg
# ABORTA O TRECHO INTEIRO. O sintoma não é um marco errado — é um buraco no
# vídeo exportado. A rota do desfoque já gravava keyframes crus, sem conferir
# nada; estas funções existem para isso não se repetir.
LIMITES = {
    "x": (-2.0, 3.0),
    "y": (-2.0, 3.0),
    "scale": (0.01, 20.0),
    "opacity": (0.0, 1.0),
    "rotation": (-3600.0, 3600.0),
    # os marcos do DESFOQUE falam de uma caixa, não das propriedades de uma
    # janela: x, y, largura e altura, todos em fração do quadro
    "w": (0.01, 1.0),
    "h": (0.01, 1.0),
}
# o desfoque interpola com filters._piecewise, que trata chave AUSENTE como o
# valor padrão em vez de ignorar o marco. Por isso um marco de desfoque só vale
# se trouxer as QUATRO chaves: perder uma faria a caixa saltar para o padrão no
# meio do movimento, em cima do rosto que ela existe para cobrir.
CHAVES_DO_DESFOQUE = ("x", "y", "w", "h")
MAX_MARCOS = 400          # 400 marcos são 13 s a 30 fps de movimento desenhado
                          # à mão; acima disso é cliente com defeito, não gesto


def _limitar(chave: str, v: float) -> float:
    lo, hi = LIMITES.get(chave, (-1e6, 1e6))
    return max(lo, min(hi, v))


def normalizar_marcos(bruto, chaves=None, exigir_todas: bool = False) -> list:
    """Lista de marcos confiável, ou [] — nunca levanta.

    Descarta o que não dá para usar em silêncio, de propósito: recusar a
    edição inteira porque um marco veio torto faria o usuário perder o gesto
    todo. O que sobrevive é garantidamente numérico e finito.

    ``chaves`` são as propriedades aceitas (por omissão as da sobreposição).
    ``exigir_todas`` derruba o marco que não trouxer todas elas — é o que o
    desfoque precisa, porque a interpolação dele completa chave ausente com o
    padrão em vez de ignorar o marco.
    """
    import math

    if not isinstance(bruto, list):
        return []
    aceitas = tuple(chaves) if chaves else tuple(PROPRIEDADES)
    fora: list[dict] = []
    for item in bruto[:MAX_MARCOS]:
        if not isinstance(item, dict):
            continue
        try:
            t = float(item.get("t"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(t):
            continue
        marco: dict = {"t": round(max(0.0, t), 4)}
        for chave in aceitas:
            if chave not in item or item[chave] is None:
                continue
            try:
                v = float(item[chave])
            except (TypeError, ValueError):
                continue
            if not math.isfinite(v):
                continue
            marco[chave] = round(_limitar(chave, v), 6)
        curva_pedida = str(item.get("easing", item.get("curva", "linear")))
        if curva_pedida in CURVAS and curva_pedida != "linear":
            marco["easing"] = curva_pedida
        # marco que não fala de nenhuma propriedade não é marco
        if exigir_todas:
            if all(k in marco for k in aceitas):
                fora.append(marco)
        elif any(k in marco for k in aceitas):
            fora.append(marco)
    fora.sort(key=lambda m: m["t"])
    return fora


def normalizar_mascara(bruto) -> dict | None:
    """Máscara confiável, ou None. ``{"shape": null}`` tira a máscara."""
    import math

    if not isinstance(bruto, dict):
        return None
    forma = str(bruto.get("shape", bruto.get("forma", "")) or "")
    if forma not in FORMAS:
        return None
    limpa: dict = {"shape": forma}
    faixas = {"cx": (0.0, 1.0, 0.5), "cy": (0.0, 1.0, 0.5),
              "rx": (0.02, 1.0, 0.5), "ry": (0.02, 1.0, 0.5),
              "radius": (0.01, 0.99, 0.18), "feather": (0.001, 1.0, 0.04)}
    for chave, (lo, hi, padrao) in faixas.items():
        try:
            v = float(bruto.get(chave, padrao))
        except (TypeError, ValueError):
            v = padrao
        if not math.isfinite(v):
            v = padrao
        limpa[chave] = round(max(lo, min(hi, v)), 5)
    return limpa


# --------------------------------------------------------------- efeitos
# Cada efeito é um dicionário {"kind": ..., parâmetros}. O vocabulário segue o
# precedente do ``fit`` que já existe no plano: chave em inglês, valor em
# português. Tudo roda DENTRO do mesmo passe de encode do trecho — nenhum
# efeito acrescenta geração de compressão.
#
# O que NÃO está aqui, de propósito: tremor do QUADRO INTEIRO. Na sobreposição
# o tremor é de graça, porque é só somar um deslocamento na expressão de
# posição que o overlay já avalia por quadro. No quadro inteiro ele exige
# recortar com margem e reescalar de volta, e isso entra no meio da cadeia que
# decide o enquadramento (o recorte concêntrico no rosto) — mexer ali para
# acrescentar um tremor é trocar o certo pelo bonito. Fica anotado como o que
# falta, não escondido atrás de um efeito que trema errado.
EFEITOS_DA_SOBREPOSICAO = ("desfoque", "cor", "chroma", "tremor")
EFEITOS_DO_CLIPE = ("desfoque", "cor", "vinheta", "flash")


def _f(d: dict, chave: str, padrao: float, lo: float, hi: float) -> float:
    import math

    try:
        v = float(d.get(chave, padrao))
    except (TypeError, ValueError):
        return padrao
    if not math.isfinite(v):
        return padrao
    return max(lo, min(hi, v))


def normalizar_efeitos(bruto, permitidos: tuple[str, ...]) -> list:
    """Lista de efeitos confiável, ou []. Descarta o que não reconhece."""
    if not isinstance(bruto, list):
        return []
    fora: list[dict] = []
    for item in bruto[:12]:
        if not isinstance(item, dict):
            continue
        tipo = str(item.get("kind", item.get("tipo", "")) or "")
        if tipo not in permitidos:
            continue
        e: dict = {"kind": tipo}
        if tipo == "desfoque":
            e["sigma"] = round(_f(item, "sigma", 8.0, 0.5, 60.0), 3)
        elif tipo == "cor":
            e["brightness"] = round(_f(item, "brightness", 0.0, -1.0, 1.0), 4)
            e["saturation"] = round(_f(item, "saturation", 1.0, 0.0, 3.0), 4)
            e["contrast"] = round(_f(item, "contrast", 1.0, 0.0, 3.0), 4)
        elif tipo == "chroma":
            cor = str(item.get("color", "0x00FF00"))
            limpa = "".join(c for c in cor if c in "0123456789abcdefABCDEFxX")[:8]
            e["color"] = limpa or "0x00FF00"
            e["similarity"] = round(_f(item, "similarity", 0.25, 0.01, 1.0), 4)
            e["blend"] = round(_f(item, "blend", 0.1, 0.0, 1.0), 4)
        elif tipo == "tremor":
            e["amplitude"] = round(_f(item, "amplitude", 0.01, 0.0, 0.2), 5)
            e["frequency"] = round(_f(item, "frequency", 6.0, 0.1, 30.0), 3)
        elif tipo == "vinheta":
            e["amount"] = round(_f(item, "amount", 0.5, 0.0, 1.0), 4)
        elif tipo == "flash":
            e["at"] = round(_f(item, "at", 0.0, 0.0, 36000.0), 4)
            e["duration"] = round(_f(item, "duration", 0.18, 0.02, 3.0), 4)
            e["amount"] = round(_f(item, "amount", 0.6, 0.0, 1.0), 4)
        fora.append(e)
    return fora


def filtros_da_sobreposicao(efeitos: list | None) -> list[str]:
    """Filtros que entram na cadeia da PRÓPRIA sobreposição, na ordem.

    O ``tremor`` não sai por aqui — ele é deslocamento de posição e sai por
    ``tremor_da_sobreposicao``, somado na expressão que o ``overlay`` já
    avalia por quadro. Assim ele não custa filtro nenhum.
    """
    saida: list[str] = []
    for e in efeitos or []:
        tipo = e.get("kind")
        if tipo == "chroma":
            # o chroma vem PRIMEIRO: furar o verde depois de desfocar espalha o
            # verde na borda do que sobrou, e aí a borda fica esverdeada
            saida.insert(0, f"chromakey={e['color']}:{e['similarity']:g}:"
                            f"{e['blend']:g}")
        elif tipo == "desfoque":
            # UMA passada. O desfoque de proteção do rosto usa steps=3, e eu
            # tentei copiar isso aqui: medido, sigma 12 num quadro de 240x180
            # dá gradiente 1,72 com uma passada e 1,69 com três — três passadas
            # de custo para nada visível. Para esconder um documento a
            # diferença importa; para um efeito, não.
            saida.append(f"gblur=sigma={e['sigma']:g}")
        elif tipo == "cor":
            saida.append(f"eq=brightness={e['brightness']:g}:"
                         f"saturation={e['saturation']:g}:"
                         f"contrast={e['contrast']:g}")
    return saida


def tremor_da_sobreposicao(efeitos: list | None) -> tuple[str, str]:
    """Deslocamento ("dx", "dy") em pixels do quadro, ou ("", "").

    Duas frequências levemente diferentes nos dois eixos de propósito: com a
    mesma frequência o movimento vira uma diagonal que parece defeito de
    monitor, não câmera na mão.
    """
    for e in efeitos or []:
        if e.get("kind") != "tremor":
            continue
        a = float(e.get("amplitude", 0.01))
        f = float(e.get("frequency", 6.0))
        if a <= 1e-6:
            continue
        w = f"({a:.5f}*main_w*sin({2 * 3.14159265358979 * f:.4f}*t))"
        h = f"({a:.5f}*main_h*cos({2 * 3.14159265358979 * f * 0.83:.4f}*t))"
        return w, h
    return "", ""


def filtros_do_clipe(efeitos: list | None) -> list[str]:
    """Filtros do QUADRO INTEIRO, dentro do mesmo passe de encode do trecho."""
    saida: list[str] = []
    for e in efeitos or []:
        tipo = e.get("kind")
        if tipo == "desfoque":
            saida.append(f"gblur=sigma={e['sigma']:g}")
        elif tipo == "cor":
            saida.append(f"eq=brightness={e['brightness']:g}:"
                         f"saturation={e['saturation']:g}:"
                         f"contrast={e['contrast']:g}")
        elif tipo == "vinheta":
            # o ângulo é o que controla o quanto a borda escurece; PI/5 é uma
            # vinheta discreta e PI/2.2 é forte
            ang = 0.628 + (1.428 - 0.628) * float(e.get("amount", 0.5))
            saida.append(f"vignette=angle={ang:.4f}")
        elif tipo == "flash":
            # um pulso de brilho que sobe e desce dentro da duração. eq aceita
            # expressão com eval=frame; fora da janela o termo é zero, então o
            # filtro é transparente no resto do trecho.
            t0 = float(e.get("at", 0.0))
            d = max(0.02, float(e.get("duration", 0.18)))
            amp = float(e.get("amount", 0.6))
            p = f"clip((t-{t0:.4f})/{d:.4f},0,1)"
            pulso = f"({amp:.4f}*sin(3.14159265*{p})*between(t,{t0:.4f},{t0 + d:.4f}))"
            saida.append(f"eq=brightness='{pulso}':eval=frame")
    return saida
