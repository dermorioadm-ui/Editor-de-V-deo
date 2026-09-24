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
