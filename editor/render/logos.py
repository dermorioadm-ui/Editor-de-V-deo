"""Logos na pós-edição: um PNG com transparência, animado, na frente ou atrás.

O gráfico ``tipo="logo"`` não é desenho do libass: é o arquivo da marca (ou
um PNG que o usuário pôs — o do Airbnb, o do Booking), que entra no MESMO
filtergraph do trecho como mais uma entrada. Ele é escalado para o tamanho
pedido, ganha a opacidade pedida ("eu gosto das coisas mais transparentes")
e entra e sai com a mesma gramática dos outros gráficos: pop, fade, slide,
subir. Com ``camada="atras"`` e o recorte da pessoa, ele é posto no FUNDO,
antes de a pessoa voltar por cima — o logo passa atrás dela.

POR QUE NÃO VIRA O SOBREPOR (b-roll). O sobrepor é mídia do projeto, com
máscara, keyframes e janela de vídeo; o logo é identidade: mora no kit da
marca, vale para qualquer projeto e anima igual a um título. Aqui ele reusa
só o que é igual: ``-loop 1`` na entrada (senão o PNG é um quadro só e o
fade some com ele) e o overlay com ``eval=frame``.

Tempos: todos relativos ao começo do TRECHO (o PNG em loop começa em 0 junto
com o trecho). Um logo que atravessa a emenda aparece nos dois trechos na
mesma posição; a entrada e a saída acontecem só onde elas caem.
"""
from __future__ import annotations

import struct
from pathlib import Path

LARGURA_BASE = 0.15          # símbolo quadrado: 15% do lado menor do quadro
LARGURA_BASE_LARGO = 0.36    # assinatura (larga): 36% do lado menor
MARGEM = 0.035


def _v(g, k, padrao=None):
    if isinstance(g, dict):
        return g.get(k, padrao)
    return getattr(g, k, padrao)


def dims_png(caminho: str | Path) -> tuple[int, int] | None:
    """Largura e altura lidas do cabeçalho do PNG (sem abrir a imagem)."""
    try:
        with open(caminho, "rb") as f:
            cab = f.read(24)
    except OSError:
        return None
    if len(cab) < 24 or cab[:8] != b"\x89PNG\r\n\x1a\n" or cab[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", cab[16:24])
    return (int(w), int(h)) if w > 0 and h > 0 else None


def no_trecho(graficos, t0: float, dur: float, camadas: tuple = ("frente",)) -> list:
    out = []
    for g in graficos or []:
        if _v(g, "tipo") != "logo" or not _v(g, "enabled", True):
            continue
        a, b = float(_v(g, "out_start", 0.0)), float(_v(g, "out_end", 0.0))
        if b - a < 0.1 or b <= t0 or a >= t0 + dur:
            continue
        if str(_v(g, "camada", "frente") or "frente") not in camadas:
            continue
        out.append(g)
    return out


def tamanho(g, caminho: str, W: int, H: int) -> tuple[int, int]:
    """O logo no quadro, em pixels pares (o yuv420 não aceita ímpar)."""
    d = dims_png(caminho) or (512, 512)
    aspecto = d[0] / max(1, d[1])
    u = min(W, H)
    try:
        s = max(0.3, min(4.0, float(_v(g, "tamanho", 1.0) or 1.0)))
    except (TypeError, ValueError):
        s = 1.0
    base = LARGURA_BASE_LARGO if aspecto > 2.2 else LARGURA_BASE
    pw = max(8, int(round(u * base * s * (1.0 if aspecto >= 1 else aspecto))))
    pw = min(pw, int(W * 0.9))
    ph = max(8, int(round(pw / aspecto)))
    if ph > H * 0.9:
        ph = int(H * 0.9)
        pw = int(round(ph * aspecto))
    return pw + pw % 2, ph + ph % 2


def centro(g, pw: int, ph: int, W: int, H: int) -> tuple[float, float]:
    """O centro pedido, puxado para dentro do quadro (com margem)."""
    m = min(W, H) * MARGEM
    try:
        cx = float(_v(g, "x", 0.9)) * W
        cy = float(_v(g, "y", 0.16)) * H
    except (TypeError, ValueError):
        cx, cy = 0.9 * W, 0.16 * H
    cx = min(max(cx, pw / 2 + m), W - pw / 2 - m) if pw < W - 2 * m else W / 2
    cy = min(max(cy, ph / 2 + m), H - ph / 2 - m) if ph < H - 2 * m else H / 2
    return cx, cy


def _tempos(g, t0: float) -> tuple[float, float, float, float]:
    a = float(_v(g, "out_start", 0.0)) - t0
    b = float(_v(g, "out_end", 0.0)) - t0
    total = max(0.1, b - a)
    ed = min(0.5, total * 0.4)
    sd = min(0.35, total * 0.3)
    return a, b, ed, sd


def cadeia(g, entrada: str, saida_tag: str, W: int, H: int, t0: float,
           caminho: str) -> tuple[str, dict]:
    """A cadeia que prepara o PNG (tamanho, opacidade, fade, pop) e os
    parâmetros do overlay dele. ``entrada`` é o rótulo da entrada do PNG."""
    pw, ph = tamanho(g, caminho, W, H)
    cx, cy = centro(g, pw, ph, W, H)
    a, b, ed, sd = _tempos(g, t0)
    ent = str(_v(g, "entrada", "pop") or "pop")
    sai = str(_v(g, "saida", "fade") or "fade")
    try:
        op = max(0.1, min(1.0, float(_v(g, "opacidade", 1.0) or 1.0)))
    except (TypeError, ValueError):
        op = 1.0
    filtros = ["format=rgba"]
    # o tamanho: fixo, ou animado (pop) — escala por quadro só quando precisa
    s_ent = ent in ("pop", "3d")
    s_sai = sai == "pop"
    if s_ent or s_sai:
        termos = ["1"]
        if s_ent:
            e1 = ed * 0.62
            termos = [f"if(lt(t,{a:.3f}),0.02,if(lt(t,{a + e1:.3f}),"
                      f"0.02+1.1*sin(PI/2*(t-{a:.3f})/{e1:.3f}),"
                      f"if(lt(t,{a + ed:.3f}),1.12-0.12*(t-{a + e1:.3f})/{ed - e1:.3f},1)))"]
        if s_sai:
            termos.append(f"if(gt(t,{b - sd:.3f}),max(0.02,1-(t-{b - sd:.3f})/{sd:.3f}),1)")
        S = "*".join(f"({x})" for x in termos)
        filtros.append(f"scale=w='max(2,trunc({pw}*{S}/2)*2)':h=-2:eval=frame:flags=bicubic")
    else:
        filtros.append(f"scale={pw}:{ph}:flags=lanczos")
    if op < 0.999:
        filtros.append(f"colorchannelmixer=aa={op:.3f}")
    # o fade só onde ele cai dentro do trecho (fade não aceita início negativo)
    if ent != "nenhuma" and a >= 0:
        filtros.append(f"fade=t=in:st={a:.3f}:d={max(0.05, ed * (0.35 if s_ent else 0.7)):.3f}:alpha=1")
    if sai != "corte" and b - sd >= 0:
        filtros.append(f"fade=t=out:st={b - sd:.3f}:d={sd:.3f}:alpha=1")
    pre = f"[{entrada}]" + ",".join(filtros) + f"[{saida_tag}]"
    # a posição: o centro, e o deslize de entrada/saída
    lado = -1 if cx <= W / 2 else 1
    x = f"{cx:.1f}-w/2"
    y = f"{cy:.1f}-h/2"
    if ent == "slide":
        x += f"+{0.22 * W * lado:.1f}*pow(1-clip((t-{a:.3f})/{ed:.3f},0,1),2)"
    elif ent == "subir":
        y += f"+{0.05 * H:.1f}*pow(1-clip((t-{a:.3f})/{ed:.3f},0,1),2)"
    if sai == "slide":
        x += f"+{0.22 * W * lado:.1f}*pow(clip((t-{b - sd:.3f})/{sd:.3f},0,1),2)"
    return pre, {"x": x, "y": y, "a": a, "b": b}


def grafo(tag_in: str, tag_out: str, itens: list[tuple], W: int, H: int,
          t0: float, prefixo: str = "__lg") -> str:
    """Os logos por cima de ``tag_in``. ``itens``: [(g, rótulo da entrada, caminho)]."""
    if not itens:
        return ""
    partes = []
    atual = tag_in
    for k, (g, entrada, caminho) in enumerate(itens):
        pre, ov = cadeia(g, entrada, f"{prefixo}p{k}", W, H, t0, caminho)
        partes.append(pre)
        saida = tag_out if k == len(itens) - 1 else f"{prefixo}{k}"
        partes.append(f"[{atual}][{prefixo}p{k}]overlay=x='{ov['x']}':y='{ov['y']}'"
                      f":eval=frame:format=auto"
                      f":enable='between(t,{max(0.0, ov['a']):.3f},{ov['b']:.3f})'[{saida}]")
        atual = saida
    return ";".join(partes)
