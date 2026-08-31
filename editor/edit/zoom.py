"""Zoom automático entre cenas — simulação de multicâmera num take único.

O vídeo é um take só, câmera fixa. O zoom é recorte digital: recorta-se uma
área menor do quadro e reescala de volta para a resolução de saída. Trocar
essa área periodicamente cria a impressão de troca de plano.

A regra que sustenta tudo: **a troca de enquadramento só pode acontecer em
cima de um corte.** Durante fala contínua o olho lê como salto e fica
horrível; exatamente no corte, lê como câmera nova.

Dois erros documentados, porque custaram caro:

1. Trocar o enquadramento a cada bloco. Como os blocos saem do corte de
   silêncio, alguns têm 0,13 s — o enquadramento piscava duas ou três vezes
   por segundo. O critério certo é TEMPO DE TELA ACUMULADO: só troca quando
   passa de ``seconds_per_scene``.

2. Somar deslocamento aleatório em X e Y a cada recorte, "para dar
   variedade". O rosto mudava de posição na tela a cada corte e o olho
   cansava perseguindo. Todo recorte é CONCÊNTRICO no mesmo ponto — o centro
   do rosto. Só a escala muda, que é o que uma segunda câmera faria.
"""
from __future__ import annotations

from ..config import ZoomParams
from ..models import SECTIONS, Clip

CONTIGUOUS_EPS = 0.002
MIN_STEP = 0.05         # diferença abaixo disso não lê como troca de plano
MIN_SCENE = 2.0         # enquadramento mais curto que isso é suspeito


def par(n: float) -> int:
    """Largura e altura precisam ser pares para yuv420p."""
    i = int(round(n))
    return i - (i % 2)


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def zoom_maximo(largura_fonte: int, largura_saida: int,
                teto: float = 1.25) -> float:
    """Teto de zoom que a resolução da FONTE aguenta.

    Zoom digital é recorte seguido de reescala. Recortar 1,20x de um vídeo de
    1080 de largura é pegar 900 px e esticar de volta para 1080 — perde
    nitidez. Nunca esticamos mais que 15% acima do que a fonte entrega.
    """
    if largura_fonte <= 0 or largura_saida <= 0:
        return 1.0
    return round(max(1.0, min(teto, (largura_fonte / largura_saida) * 1.15)), 4)


def escada_efetiva(params: ZoomParams, teto: float) -> list[float]:
    """A escada com a amplitude do preset, a intensidade global e o teto.

    Quando o teto da fonte é mais baixo que o que a escada pede, a escada é
    reduzida PROPORCIONALMENTE em vez de simplesmente cortada no teto — cortar
    faria vários degraus virarem o mesmo valor e a troca sumiria.
    """
    base = list(params.ladder or (1.0,))
    if not base:
        base = [1.0]
    amp = float(params.amplitude) * float(params.intensity)
    # a escada nominal tem amplitude ~0,17; normaliza para a do preset
    nominal = max((abs(z - 1.0) for z in base), default=0.0) or 1.0
    escala = amp / nominal
    return [round(max(1.0, 1.0 + (z - 1.0) * escala), 4) for z in base]


def fator_teto(escada: list[float], bases: list[float], teto: float) -> float:
    """Quanto reduzir escada E base para o mais fechado caber no teto.

    Cortar no teto não serve: com base 1,10 e teto 1,15, metade dos degraus
    vira 1,15 e a troca de plano some — foram 12 trocas imperceptíveis num
    vídeo. A redução PROPORCIONAL preserva a amplitude de cada etapa e a
    distância entre elas.
    """
    pico = max(bases or [1.0]) * max(escada or [1.0])
    if pico <= teto or pico <= 1.0:
        return 1.0
    return (teto - 1.0) / (pico - 1.0)


def assign_zoom(clips: list[Clip], params: ZoomParams,
                largura_fonte: int = 0, largura_saida: int = 0) -> dict:
    """Distribui os enquadramentos pelo tempo de tela acumulado.

    Duas fases, de propósito separadas:

    A. **onde cada cena começa** — pelo acumulador de tempo de tela, com a
       troca de etapa narrativa reiniciando a contagem, e sempre em cima de
       um corte.
    B. **que valor cada cena recebe** — andando na escada, com a garantia de
       que duas cenas vizinhas nunca ficam a menos de 0,05 uma da outra.

    Fazer as duas coisas num passe só produzia cenas vizinhas com o MESMO
    valor (a escada volta ao 1,00 com frequência), elas se fundiam, e a média
    ia de 4,5 s para 8,7 s — com uma cena de 53 s no meio do vídeo.
    """
    main = [c for c in clips if c.enabled and c.source == "main"]
    teto = (zoom_maximo(largura_fonte, largura_saida, params.max_zoom)
            if largura_fonte and largura_saida else params.max_zoom)
    if not params.enabled or not main:
        for c in clips:
            if not c.zoom_locked:
                c.zoom = 1.0
        return {"teto": teto, "fechados": 0, "cenas": 0, "trocas": 0,
                "absorvidas": 0, "escada": []}

    escada = escada_efetiva(params, teto)
    grupos = _particionar(main, params.seconds_per_scene)
    absorvidas = _fundir_curtas(grupos, min(MIN_SCENE,
                                            max(1.2, params.seconds_per_scene * 0.5)))
    usadas = {g[0].section for g in grupos}
    bases = [float(SECTIONS.get(sec, {}).get("zoom_base", 1.0)) for sec in usadas]
    fator = fator_teto(escada, bases, teto)
    _valorar(grupos, escada, teto, fator)

    zs = [c.zoom for c in main if c.zoom > 1.001]
    menor = min(zs) if zs else 1.0
    params.anchor_x, params.anchor_y = ancora_alcancavel(
        params.face_x, params.face_y, menor)

    for c in clips:
        if (c.source != "main" or not c.enabled) and not c.zoom_locked:
            c.zoom = 1.0

    trocas = sum(1 for a, b in zip(main, main[1:]) if abs(a.zoom - b.zoom) > 1e-6)
    return {"teto": teto,
            "fechados": sum(1 for c in main if abs(c.zoom - 1.0) > 1e-3),
            "cenas": len(grupos), "trocas": trocas,
            "absorvidas": absorvidas,
            "menor_zoom": round(menor, 4),
            "anchor": (round(params.anchor_x, 4), round(params.anchor_y, 4)),
            "escada": escada}


def _duracao(grupo: list[Clip]) -> float:
    return sum(c.out_duration for c in grupo)


def _particionar(main: list[Clip], alvo: float) -> list[list[Clip]]:
    """Fase A: onde cada cena começa.

    A cena só pode virar em cima de um CORTE. Bloco contíguo (onde só a
    velocidade muda) nunca abre cena nova, senão a imagem pula sem que nada
    tenha sido cortado.
    """
    grupos: list[list[Clip]] = []
    atual: list[Clip] = []
    acumulado = 0.0
    secao: str | None = None
    anterior: Clip | None = None
    for clip in main:
        contiguo = bool(anterior
                        and abs(anterior.src_end - clip.src_start) < CONTIGUOUS_EPS)
        virou_etapa = clip.section != secao
        # Fechar SÓ depois de estourar o alvo produz cenas longas demais:
        # com 4,2 s acumulados e um bloco de 12,8 s pela frente, 4,2 não
        # estoura 4,5 e o bloco entra — sai uma cena de 17 s num vídeo com
        # alvo de 4,5. Fecha-se no corte que chega MAIS PERTO do alvo.
        estourou = (acumulado >= alvo
                    or abs(acumulado - alvo) <= abs(acumulado + clip.out_duration - alvo))
        # EMENDA DE COPY: a IA tirou uma ideia inteira daqui. O som emenda
        # limpo (o corte só passa se houver vale), mas a imagem não: é jump
        # cut numa cabeça falante, e o pulo se vê. Abrir cena aqui obriga o
        # enquadramento a mudar, que é o disfarce que o cinema usa desde
        # sempre. Só vale onde REALMENTE houve corte — num trecho contíguo
        # não há emenda para disfarçar.
        emenda_copy = bool(getattr(clip, "copy_seam", False)) and not contiguo
        if atual and not contiguo and (virou_etapa or estourou or emenda_copy):
            grupos.append(atual)
            atual = []
            acumulado = 0.0
        secao = clip.section
        atual.append(clip)
        acumulado += clip.out_duration
        anterior = clip
    if atual:
        grupos.append(atual)
    return grupos


def _fundir_curtas(grupos: list[list[Clip]], minimo: float) -> int:
    """Cena curta demais não existe: some na vizinha.

    Um bloco de 0,13 s logo depois de uma troca de etapa ganharia degrau novo
    e piscaria. A especificação manda AVISAR; avisar não resolve — o usuário
    teria que clicar em "fundir" toda vez.
    """
    fundidas = 0
    i = 0
    while len(grupos) > 1 and i < len(grupos):
        if _duracao(grupos[i]) >= minimo:
            i += 1
            continue
        if i == 0:
            grupos[1][:0] = grupos[0]
            grupos.pop(0)
        else:
            grupos[i - 1].extend(grupos[i])
            grupos.pop(i)
            i = max(0, i - 1)
        fundidas += 1
    return fundidas


def _valorar(grupos: list[list[Clip]], escada: list[float], teto: float,
             fator: float = 1.0) -> None:
    """Fase B: que valor cada cena recebe.

    Duas cenas vizinhas nunca podem ficar a menos de ``MIN_STEP`` uma da
    outra: diferença pequena não lê como troca de plano, lê como erro de
    render. Se a escada não oferecer nenhum degrau distante o bastante para a
    etapa atual, a cena mantém o valor da anterior — melhor não trocar do que
    trocar de um jeito que não se percebe.
    """
    indice = 0
    anterior: float | None = None
    for grupo in grupos:
        crua = float(SECTIONS.get(grupo[0].section, {}).get("zoom_base", 1.0))
        base = 1.0 + (crua - 1.0) * fator
        deg = [1.0 + (z - 1.0) * fator for z in escada]

        # ÊNFASE. Ela reordena qual degrau da escada é tentado primeiro, e só
        # isso. Não entra na base, não entra no fator, não entra no teto.
        #
        # A tentação era transformar ênfase num multiplicador — e ela custa
        # caro: basta um bloco pedir mais fechamento para fator_teto reduzir
        # PROPORCIONALMENTE a amplitude do vídeo INTEIRO (medido: 0,0638 para
        # 0,0573, uns 10% a menos de movimento em todas as cenas). Como
        # critério de escolha entre candidatos que a escada já produziu e já
        # clampou, ela não tem esse efeito colateral: o conjunto de valores
        # possíveis é exatamente o mesmo, muda só a ordem de preferência.
        enfase = (grupo[0].emphasis or "").strip()
        ordem = list(range(len(deg)))
        if enfase in ("fechado", "aberto"):
            ordem.sort(key=lambda k: deg[k], reverse=(enfase == "fechado"))
        else:
            ordem = [(indice + salto) % len(deg) for salto in range(len(deg))]

        escolhido: float | None = None
        for salto, k in enumerate(ordem):
            cand = clamp(base * deg[k], 1.0, teto)
            if anterior is None or abs(cand - anterior) >= MIN_STEP:
                escolhido = cand
                indice += salto + 1
                break
        if escolhido is None:
            # A faixa da etapa é estreita demais para um passo de 0,05 (a VSL
            # tem amplitude 0,08: cabem dois níveis, e um valor herdado de
            # outra etapa pode cair no meio deles). Vai para o extremo MAIS
            # DISTANTE: repetir o valor funde as duas cenas numa só, e foi
            # assim que uma cena de 53 s apareceu no meio de um vídeo com
            # alvo de 4,5 s.
            cands = [clamp(base * z, 1.0, teto) for z in deg]
            distintos = [v for v in cands
                         if anterior is None or abs(v - anterior) > 1e-6]
            # qualquer diferença é melhor que nenhuma: valor repetido funde as
            # duas cenas numa só, e é assim que uma cena de 17 s nasce num
            # vídeo com alvo de 4,5 s
            escolhido = (max(distintos, key=lambda v: abs(v - (anterior or 1.0)))
                         if distintos else (anterior if anterior is not None else 1.0))
            indice += 1
        for c in grupo:
            if not c.zoom_locked:
                c.zoom = round(escolhido, 4)
        # um bloco travado no meio da cena manda no valor efetivo dela
        anterior = grupo[0].zoom


def _cenas(main: list[Clip]) -> list[dict]:
    out: list[dict] = []
    for c in main:
        if out and abs(out[-1]["zoom"] - c.zoom) < 1e-6:
            out[-1]["duration"] += c.out_duration
            out[-1]["clip_ids"].append(c.id)
        else:
            out.append({"zoom": c.zoom, "duration": c.out_duration,
                        "clip_ids": [c.id]})
    return out


def cenas(clips: list[Clip]) -> list[dict]:
    """Blocos vizinhos com o mesmo enquadramento formam uma CENA.

    É a unidade que o usuário vê e edita na timeline — não o bloco.
    """
    main = [c for c in clips if c.enabled and c.source == "main"]
    out: list[dict] = []
    for c in main:
        if out and abs(out[-1]["zoom"] - c.zoom) < 1e-6:
            out[-1]["end"] = c.src_end
            out[-1]["out_end"] += c.out_duration
            out[-1]["clip_ids"].append(c.id)
            out[-1]["locked"] = out[-1]["locked"] or c.zoom_locked
        else:
            inicio = out[-1]["out_end"] if out else 0.0
            out.append({"zoom": round(c.zoom, 4), "start": c.src_start,
                        "end": c.src_end, "out_start": inicio,
                        "out_end": inicio + c.out_duration,
                        "clip_ids": [c.id], "locked": c.zoom_locked})
    for cena in out:
        cena["duration"] = round(cena["out_end"] - cena["out_start"], 3)
        cena["start"] = round(cena["start"], 3)
        cena["end"] = round(cena["end"], 3)
        cena["out_start"] = round(cena["out_start"], 3)
        cena["out_end"] = round(cena["out_end"], 3)
    return out


def auditar(clips: list[Clip], params: ZoomParams, teto: float) -> list[dict]:
    """O que a interface deve mostrar depois de atribuir os enquadramentos."""
    avisos: list[dict] = []
    lista = cenas(clips)
    for i, cena in enumerate(lista):
        if cena["duration"] < MIN_SCENE:
            avisos.append({
                "kind": "cena-curta", "severity": "media",
                "out_start": cena["out_start"], "zoom": cena["zoom"],
                "message": (f"enquadramento de {cena['duration']:.1f} s "
                            f"(mínimo confortável {MIN_SCENE:.1f} s)"),
                "suggestion": "fundir com o vizinho",
            })
        if i and abs(cena["zoom"] - lista[i - 1]["zoom"]) < MIN_STEP:
            # no teto: não há degrau mais distante para pular. Dizer "pule
            # para o próximo degrau" seria mentira — não existe próximo.
            no_teto = max(cena["zoom"], lista[i - 1]["zoom"]) > teto - 0.02
            avisos.append({
                "kind": "troca-fraca",
                "severity": "baixa" if no_teto else "media",
                "out_start": cena["out_start"], "zoom": cena["zoom"],
                "message": (f"troca de {lista[i-1]['zoom']:.2f}x para "
                            f"{cena['zoom']:.2f}x é sutil"),
                "suggestion": (f"a fonte só aguenta até {teto:.2f}x — para "
                               f"trocas mais fortes, grave em resolução maior"
                               if no_teto else
                               "pular para o próximo degrau da escada"),
            })
        if cena["zoom"] > teto + 1e-6:
            avisos.append({
                "kind": "acima-do-teto", "severity": "alta",
                "out_start": cena["out_start"], "zoom": cena["zoom"],
                "message": (f"{cena['zoom']:.2f}x acima do que a fonte aguenta "
                            f"({teto:.2f}x) — vai perder nitidez"),
                "suggestion": f"baixar para {teto:.2f}x",
            })

    # troca que não cai em corte: nunca deveria acontecer. Erro grave.
    main = [c for c in clips if c.enabled and c.source == "main"]
    for a, b in zip(main, main[1:]):
        if abs(a.zoom - b.zoom) < 1e-6:
            continue
        if abs(a.src_end - b.src_start) < CONTIGUOUS_EPS:
            avisos.append({
                "kind": "troca-sem-corte", "severity": "alta",
                "out_start": round(b.src_start, 3), "zoom": b.zoom,
                "message": (f"o enquadramento troca em {b.src_start:.2f} s sem "
                            f"corte nenhum — o olho lê como salto"),
                "suggestion": "igualar ao bloco anterior",
            })
    return avisos


def ancora_alcancavel(centro_x: float, centro_y: float,
                      menor_zoom: float) -> tuple[float, float]:
    """O ponto mais perto do rosto que TODO enquadramento consegue centrar.

    Uma janela de recorte não pode sair do quadro. Com zoom 1,06 a janela
    ocupa 94% da altura, então o centro dela só pode ficar entre 0,472 e
    0,528 — pedir 0,44 é impossível, e o clamp empurra a janela para a borda.
    O efeito colateral é o pior possível: o centro EFETIVO muda de um
    enquadramento para outro e o rosto anda na tela, que é exatamente o que o
    recorte concêntrico existe para evitar.

    A solução é escolher uma âncora única, alcançável já no MENOR zoom em uso.
    Aí todos os recortes ficam de fato concêntricos.

    Sobra um resíduo, e ele é irredutível: um ponto que está a D pixels da
    âncora anda ``D * (zoom_maior - zoom_menor)`` pixels entre o enquadramento
    mais aberto e o mais fechado — é assim que zoom funciona, inclusive numa
    câmera de verdade. Medido: rosto a 0,42 da altura, âncora forçada para
    0,472 pela geometria do zoom 1,06 — 100 px de distância, escada de 1,06 a
    1,15, resíduo de 9 px em 1920 (0,5% da altura). Como a âncora é o ponto
    alcançável MAIS PERTO do rosto, esse resíduo é o mínimo que existe.
    """
    z = max(1.0001, float(menor_zoom))
    meia = 1.0 / (2.0 * z)
    return (clamp(centro_x, meia, 1.0 - meia),
            clamp(centro_y, meia, 1.0 - meia))


def recorte(zoom: float, largura: int, altura: int,
            centro_x: float, centro_y: float,
            proporcao: float = 0.0) -> tuple[int, int, int, int]:
    """Recorte CONCÊNTRICO no rosto. Devolve (x, y, w, h), todos pares.

    ``proporcao`` (largura/altura) muda o FORMATO da janela — é o que permite
    tirar um 1:1 ou um 16:9 do mesmo take vertical. Zero mantém a proporção
    da fonte. A janela é sempre a MAIOR que cabe na fonte com aquela
    proporção, e só então dividida pelo zoom: assim o formato derivado usa
    todos os pixels que existem antes de qualquer reescala.
    """
    z = max(1.0, float(zoom))
    base_w, base_h = float(largura), float(altura)
    if proporcao and proporcao > 0:
        if proporcao > base_w / max(base_h, 1e-9):
            base_h = base_w / proporcao          # mais largo que a fonte: sobra altura
        else:
            base_w = base_h * proporcao          # mais alto: sobra largura
    w = par(base_w / z)
    h = par(base_h / z)
    x = par(clamp(centro_x * largura - w / 2, 0, largura - w))
    y = par(clamp(centro_y * altura - h / 2, 0, altura - h))
    return x, y, w, h


def zoom_chain(zoom: float, largura_fonte: int, altura_fonte: int,
               largura_saida: int, altura_saida: int,
               centro_x: float, centro_y: float,
               unsharp: float = 0.35) -> str:
    """crop concêntrico -> scale de volta -> unsharp leve.

    O unsharp compensa a suavização da reescala. Acima de 0,6 fica artificial.

    Quando a saída tem OUTRA PROPORÇÃO que a fonte (o 1:1 e o 16:9 tirados do
    mesmo take vertical), o recorte já sai no formato da saída — senão o
    ``scale`` esticaria a imagem para caber. É por isso que este caminho vale
    mesmo com zoom 1,00: não há zoom, mas há reenquadramento.
    """
    prop_fonte = largura_fonte / max(altura_fonte, 1e-9)
    prop_saida = largura_saida / max(altura_saida, 1e-9)
    reenquadra = abs(prop_fonte - prop_saida) > 0.01
    if (zoom is None or abs(float(zoom) - 1.0) <= 1e-3) and not reenquadra:
        return ""
    x, y, w, h = recorte(zoom, largura_fonte, altura_fonte, centro_x, centro_y,
                         prop_saida if reenquadra else 0.0)
    if w <= 0 or h <= 0:
        return ""
    partes = [f"crop=w={w}:h={h}:x={x}:y={y}",
              f"scale=w={largura_saida}:h={altura_saida}:flags=lanczos"]
    u = clamp(float(unsharp), 0.0, 0.6)
    if u > 0.01:
        partes.append(f"unsharp=5:5:{u:.2f}:5:5:0.0")
    return ",".join(partes)
