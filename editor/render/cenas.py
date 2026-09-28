"""CENAS: o quadro inteiro muda de arranjo por alguns segundos.

Um gráfico entra POR CIMA do vídeo; uma cena mexe no PRÓPRIO vídeo:

- ``moldura``: o vídeo encolhe, a partir da imagem cheia, para um cartão de
  canto largo de um lado da tela (a pessoa à direita, por exemplo), e o outro
  lado fica livre para o motion graphic explicar. O fundo vira o próprio vídeo
  desfocado e escurecido — ou a cor da marca. No fim, o cartão volta a ocupar
  a tela. É a "moldura do lado direito, explicação do lado esquerdo".
- ``vidro3d``: as CAMADAS DE VIDRO. Com o recorte da pessoa, o quadro vira três
  placas — o fundo (com o buraco da pessoa preenchido), os logos e a pessoa —
  que giram de lado e se separam em profundidade, como vidro empilhado; giram
  mais um pouco e se juntam de novo. "Separa tudo em camadas de vidro, o
  fundo, as logos e eu, para o pessoal entender... agora gira tudo e junta."

COMO CABE NO ENCODE ÚNICO. A cena é um ramo do filtergraph do trecho que só
existe nos quadros dela: o trecho é partido em [antes | cena | depois] com
``trim`` por NÚMERO DE QUADRO (o mesmo desenho das transições), a cena é
composta no meio e o ``concat`` devolve a mesma contagem de quadros. Fora da
janela o quadro passa intacto e não paga nada.

O TEMPO dentro do ramo é local (o ``trim`` zera o relógio); as expressões são
escritas com o deslocamento do começo do ramo, então uma cena que atravessa a
emenda de dois trechos continua na mesma fase dos dois lados.

A animação é por EXPRESSÃO avaliada a cada quadro (``scale``/``overlay``/
``perspective`` com ``eval=frame``): nada de um ramo por quadro. Medido: o
``perspective`` estica a borda da imagem para fora da placa; uma moldura de 4
px transparente antes dele faz o lado de fora sair transparente de verdade.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

TIPOS = ("moldura", "vidro3d")
LADOS = ("direita", "esquerda", "cima", "baixo")
FUNDOS = ("desfoque", "marca", "claro", "escuro")
PAD = 4


def _v(c, k, padrao=None):
    if isinstance(c, dict):
        return c.get(k, padrao)
    return getattr(c, k, padrao)


def no_trecho(cenas, t0: float, dur: float) -> list:
    out = []
    for c in cenas or []:
        if not _v(c, "enabled", True) or _v(c, "tipo") not in TIPOS:
            continue
        a, b = float(_v(c, "out_start", 0.0)), float(_v(c, "out_end", 0.0))
        if b - a < 0.5 or b <= t0 or a >= t0 + dur:
            continue
        out.append(c)
    return sorted(out, key=lambda c: float(_v(c, "out_start", 0.0)))


def janelas_de_recorte(cenas, t0: float, dur: float) -> list[tuple[float, float]]:
    """Onde as cenas precisam da máscara da pessoa (segundos do trecho)."""
    out = []
    for c in no_trecho(cenas, t0, dur):
        if _v(c, "tipo") == "vidro3d":
            a = max(0.0, float(_v(c, "out_start", 0.0)) - t0)
            b = min(dur + 0.05, float(_v(c, "out_end", 0.0)) - t0)
            if b > a:
                out.append((round(a, 3), round(b, 3)))
    return out


# ------------------------------------------------------------- utilidades
def _clip(x: str) -> str:
    return f"clip({x},0,1)"


def _ss(x: str) -> str:
    """smoothstep: começa e termina devagar."""
    return f"(({x})*({x})*(3-2*({x})))"


def _progresso(var: str, a: float, b: float, ed: float, sd: float) -> str:
    """0 fora da cena, sobe na entrada, 1 no meio, desce na saída."""
    pin = _ss(_clip(f"({var}-({a:.4f}))/{max(ed, 1e-3):.4f}"))
    pout = _ss(_clip(f"(({b:.4f})-{var})/{max(sd, 1e-3):.4f}"))
    return f"min({pin},{pout})"


def _hex(cor: str) -> str:
    c = str(cor or "").strip().lstrip("#")
    return f"0x{c.upper()}" if len(c) == 6 else "0x0D0D0F"


def _claro(cor: str) -> bool:
    c = str(cor or "").strip().lstrip("#")
    if len(c) != 6:
        return False
    r, g, b = (int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.6


def cor_de_fundo(cena, kit: dict | None, padrao: str) -> str:
    """"desfoque" ou uma cor #RRGGBB."""
    f = str(_v(cena, "fundo", "") or "").strip() or padrao
    if f.startswith("#") and len(f) == 7:
        return f
    cores = (kit or {}).get("cores") or {}
    if f == "marca":
        return cores.get("marca") or "#E11D48"
    if f == "claro":
        return cores.get("linha") or "#F0F0F0"
    if f == "escuro":
        return "#0D0D0F"
    return "desfoque"


def _pgm(caminho: Path, a: np.ndarray) -> Path:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    h, w = a.shape
    with open(caminho, "wb") as f:
        f.write(f"P5 {w} {h} 255\n".encode())
        f.write(np.clip(a, 0, 255).astype(np.uint8).tobytes())
    return caminho


def _retangulo_redondo(w: int, h: int, r: float, m: int = 0) -> np.ndarray:
    """Máscara 0..255 de um retângulo w x h de canto r (anti-serrilhado),
    centrada numa folha (w+2m) x (h+2m)."""
    W, H = w + 2 * m, h + 2 * m
    ys = np.arange(H, dtype=np.float32)[:, None] + 0.5 - m
    xs = np.arange(W, dtype=np.float32)[None, :] + 0.5 - m
    r = max(0.0, min(r, w / 2, h / 2))
    dx = np.maximum(np.maximum(r - xs, xs - (w - r)), 0.0)
    dy = np.maximum(np.maximum(r - ys, ys - (h - r)), 0.0)
    fora = np.sqrt(dx * dx + dy * dy) - r
    dentro = (xs >= 0) & (xs <= w) & (ys >= 0) & (ys <= h)
    a = np.clip(0.5 - fora, 0.0, 1.0) * dentro
    return a * 255.0


def mascara(pasta: Path, w: int, h: int, r: float) -> Path:
    alvo = pasta / f"cena_masc_{w}x{h}_{int(r)}.pgm"
    if not alvo.exists():
        _pgm(alvo, _retangulo_redondo(w, h, r))
    return alvo


def sombra(pasta: Path, w: int, h: int, r: float, m: int, forca: float = 0.42) -> Path:
    """A sombra longa e quase invisível: o retângulo desfocado (gaussiana
    separável em numpy, uma vez por tamanho) numa folha com margem m."""
    alvo = pasta / f"cena_somb_{w}x{h}_{int(r)}_{m}.pgm"
    if alvo.exists():
        return alvo
    a = _retangulo_redondo(w, h, r, m) / 255.0
    sigma = max(1.0, m / 2.6)
    k = np.arange(-int(3 * sigma), int(3 * sigma) + 1, dtype=np.float32)
    g = np.exp(-(k * k) / (2 * sigma * sigma))
    g /= g.sum()
    a = np.apply_along_axis(lambda v: np.convolve(v, g, mode="same"), 1, a)
    a = np.apply_along_axis(lambda v: np.convolve(v, g, mode="same"), 0, a)
    return _pgm(alvo, a * 255.0 * forca)


# -------------------------------------------------------------- moldura
def geometria_moldura(cena, W: int, H: int, centro: tuple[float, float]) -> dict:
    """Onde o cartão termina e que pedaço do vídeo vai nele."""
    retrato = H > W
    lado = str(_v(cena, "lado", "") or "")
    if lado not in LADOS:
        lado = "baixo" if retrato else "direita"
    if retrato and lado in ("direita", "esquerda"):
        lado = "baixo" if lado == "direita" else "cima"
    if not retrato and lado in ("cima", "baixo"):
        lado = "direita" if lado == "baixo" else "esquerda"
    try:
        f = max(0.0, min(1.0, float(_v(cena, "forca", 0.5))))
    except (TypeError, ValueError):
        f = 0.5
    k = 0.88 + 0.24 * f                     # forca: o tamanho do cartão
    if retrato:
        fw, fh = W * 0.9, H * 0.46 * k
        x1 = (W - fw) / 2
        y1 = H - fh - H * 0.035 if lado == "baixo" else H * 0.035
    else:
        fw, fh = W * 0.4 * k, H * 0.86
        x1 = W - fw - W * 0.04 if lado == "direita" else W * 0.04
        y1 = (H - fh) / 2
    fw, fh = int(fw) // 2 * 2, int(fh) // 2 * 2
    ar = fw / fh
    if ar < W / H:
        ch, cw = H, int(round(H * ar)) // 2 * 2
    else:
        cw, ch = W, int(round(W / ar)) // 2 * 2
    cxp, cyp = (max(0.0, min(1.0, float(v))) for v in centro)
    cx0 = int(min(max(cxp * W - cw / 2, 0), W - cw))
    cy0 = int(min(max(cyp * H - ch / 2, 0), H - ch))
    s1 = fw / cw
    r = min(fw, fh) * 0.075
    return {"lado": lado, "fw": fw, "fh": fh, "x1": round(x1, 1), "y1": round(y1, 1),
            "cw": cw, "ch": ch, "cx0": cx0, "cy0": cy0, "s1": s1, "r": r}


def _alpha_no_tempo(tag_in: str, tag_out: str, W: int, H: int, fps: float,
                    a: float, d_in: float, b: float, d_out: float, p: str) -> str:
    """A transparência de uma camada pela HORA DA CENA, e não pela do trecho.

    O ``fade`` do ffmpeg só começa em t >= 0: numa cena que atravessa uma
    emenda, o trecho seguinte começa com ``a`` negativo, o fade recomeçava do
    zero ali e o fundo da moldura sumia e voltava num piscar — "a edição
    ficou piscando na hora da moldura". Aqui o alpha é uma expressão de t
    (sobe de ``a`` a ``a + d_in``, desce até ``b``), calculada numa fonte 2x2
    e esticada — custo desprezível — e vale igual dos dois lados da emenda.
    """
    al = (f"min(clip((T-({a:.4f}))/{max(d_in, 1e-3):.4f},0,1),"
          f"clip((({b:.4f})-T)/{max(d_out, 1e-3):.4f},0,1))")
    return (f"color=c=white:s=2x2:r={fps:.6f},format=gray,geq=lum='255*{al}',"
            f"scale={W}:{H}[{p}al];[{tag_in}][{p}al]alphamerge=shortest=1[{tag_out}]")


def _logo_no_tempo(a: float, d_in: float, b: float, d_out: float) -> str:
    """O mesmo, para um PNG que já tem transparência: multiplica o alpha dele."""
    al = (f"min(clip((T-({a:.4f}))/{max(d_in, 1e-3):.4f},0,1),"
          f"clip((({b:.4f})-T)/{max(d_out, 1e-3):.4f},0,1))")
    return f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*{al}'"


def _moldura(tag_in: str, tag_out: str, cena, W: int, H: int, fps: float,
             a: float, b: float, kit: dict | None, centro, img, pasta: Path,
             p: str) -> str:
    g = geometria_moldura(cena, W, H, centro)
    total = b - a
    ed, sd = min(0.7, total * 0.3), min(0.6, total * 0.25)
    P = _progresso("t", a, b, ed, sd)
    cw, ch, s1 = g["cw"], g["ch"], g["s1"]
    esc = f"(1+({s1 - 1:.5f})*{P})"
    x = f"{g['cx0']}+({g['x1'] - g['cx0']:.2f})*{P}"
    y = f"{g['cy0']}+({g['y1'] - g['cy0']:.2f})*{P}"
    masc = mascara(pasta, cw, ch, g["r"] / s1)
    m = max(8, int(min(cw, ch) * 0.06))
    somb = sombra(pasta, cw, ch, g["r"] / s1, m)
    fundo = cor_de_fundo(cena, kit, "desfoque")
    partes = [f"[{tag_in}]split=3[{p}a][{p}b][{p}c]"]
    # o fundo: aparece por cima do vídeo cheio junto com o encolher
    if fundo == "desfoque":
        partes.append(f"[{p}b]format=yuva420p,gblur=sigma={min(W, H) * 0.03:.1f},"
                      f"eq=brightness=-0.13:saturation=0.8[{p}bx]")
    else:
        partes.append(f"[{p}b]format=yuva420p,drawbox=x=0:y=0:w=iw:h=ih:"
                      f"color={_hex(fundo)}@1:t=fill[{p}bx]")
    partes.append(_alpha_no_tempo(f"{p}bx", f"{p}bg", W, H, fps, a, ed * 0.8,
                                  b - sd * 0.1, sd * 0.9, p))
    partes.append(f"[{p}a][{p}bg]overlay=format=auto[{p}1]")
    # a sombra do cartão, que cresce e anda junto com ele
    partes.append(f"color=c=black:s={cw + 2 * m}x{ch + 2 * m}:r={fps:.6f},format=yuva420p[{p}sc]")
    partes.append(f"[{img(somb)}]format=gray,scale={cw + 2 * m}:{ch + 2 * m}[{p}sm]")
    partes.append(f"[{p}sc][{p}sm]alphamerge,"
                  f"scale=w='max(2,trunc({cw + 2 * m}*{esc}/2)*2)'"
                  f":h='max(2,trunc({ch + 2 * m}*{esc}/2)*2)':eval=frame[{p}ss]")
    dy = min(W, H) * 0.02
    partes.append(f"[{p}1][{p}ss]overlay=x='{x}-{m}*{esc}':y='{y}-{m}*{esc}+{dy:.1f}*{P}'"
                  f":eval=frame:format=auto:shortest=1[{p}2]")
    # o cartão: o pedaço do vídeo em volta da pessoa, de canto largo
    partes.append(f"[{p}c]crop={cw}:{ch}:{g['cx0']}:{g['cy0']},format=yuva420p[{p}cc]")
    partes.append(f"[{img(masc)}]format=gray,scale={cw}:{ch}[{p}mm]")
    partes.append(f"[{p}cc][{p}mm]alphamerge,"
                  f"scale=w='max(2,trunc({cw}*{esc}/2)*2)'"
                  f":h='max(2,trunc({ch}*{esc}/2)*2)':eval=frame[{p}cs]")
    partes.append(f"[{p}2][{p}cs]overlay=x='{x}':y='{y}':eval=frame:format=auto"
                  f":shortest=1[{tag_out}]")
    return ";".join(partes)


# -------------------------------------------------------------- vidro 3D
def _projecao(W: int, H: int, zc: float, f: float, a: float, b: float, e: float,
              s: float, meio: float, th1: float, fps: float) -> list[str]:
    """As expressões dos 4 cantos (x0,y0 … x3,y3) de uma placa W x H.

    A placa está na profundidade ``zc * Q`` (Q = o quanto as placas estão
    abertas), girada em torno do eixo vertical, encolhida e projetada com
    distância focal ``f``. Cada expressão calcula Q, o ângulo e a escala uma
    vez só, em variáveis (``st``/``ld``) — escrito por extenso, o grafo das
    três placas passava de 40 mil caracteres, e no Windows a linha de
    comando para em 32 767. Os cantos são os da placa COM a moldura
    transparente de PAD px.
    """
    T = f"(on/{fps:.4f})"
    cabeca = (f"st(5,clip(({T}-{a:.3f})/{max(e, 1e-3):.3f},0,1));"
              f"st(6,clip(({b:.3f}-{T})/{max(s, 1e-3):.3f},0,1));"
              "st(0,min(ld(5)*ld(5)*(3-2*ld(5)),ld(6)*ld(6)*(3-2*ld(6))));"
              f"st(7,clip(({T}-{a + e:.3f})/{max(meio, 1e-3):.3f},0,1));"
              f"st(1,ld(0)*({th1:.4f}+{-1.7 * th1:.4f}*ld(7)*ld(7)*(3-2*ld(7))));"
              "st(2,1-0.34*ld(0));"
              f"st(3,{zc:.1f}*ld(0));")
    out = []
    for X, Y in ((-W / 2 - PAD, -H / 2 - PAD), (W / 2 + PAD, -H / 2 - PAD),
                 (-W / 2 - PAD, H / 2 + PAD), (W / 2 + PAD, H / 2 + PAD)):
        z = f"st(4,ld(2)*(({-X:.1f})*sin(ld(1))+ld(3)*cos(ld(1))));"
        k = f"{f:.1f}/({f:.1f}+ld(4))"
        out.append(cabeca + z + f"{W / 2 + PAD:.1f}+ld(2)*(({X:.1f})*cos(ld(1))"
                                f"+ld(3)*sin(ld(1)))*{k}")
        out.append(cabeca + z + f"{H / 2 + PAD:.1f}+ld(2)*({Y:.1f})*{k}")
    return out


def _vidro3d(tag_in: str, tag_out: str, mascara_tag: str | None, cena, W: int, H: int,
             fps: float, a: float, b: float, kit: dict | None, img, logos: list[str],
             p: str, pix_fmt: str) -> str:
    total = b - a
    e, s = min(1.0, total * 0.28), min(0.9, total * 0.25)
    meio = max(0.1, total - e - s)
    try:
        forca = max(0.0, min(1.0, float(_v(cena, "forca", 0.6))))
    except (TypeError, ValueError):
        forca = 0.6
    lado = -1.0 if str(_v(cena, "lado", "") or "") == "esquerda" else 1.0
    th1 = math.radians(22 + 20 * forca) * lado
    D = W * (0.36 + 0.3 * forca)
    f = W * 1.25
    u = min(W, H)
    borda = max(2, int(u * 0.004))
    fundo = cor_de_fundo(cena, kit, "marca" if kit else "escuro")
    if fundo == "desfoque":
        fundo = "#0D0D0F"
    cor_borda = "black@0.28" if _claro(fundo) else "white@0.6"
    vis = f"between(t,{a + 0.12:.3f},{b - 0.12:.3f})"
    padf = f"pad=w=iw+{2 * PAD}:h=ih+{2 * PAD}:x={PAD}:y={PAD}:color=black@0"

    def persp(rot: str, zc: float) -> str:
        c = _projecao(W, H, zc, f, a, b, e, s, meio, th1, fps)
        nomes = ("x0", "y0", "x1", "y1", "x2", "y2", "x3", "y3")
        return (f"{padf},perspective=" + ":".join(f"{n}='{v}'" for n, v in zip(nomes, c))
                + f":sense=destination:eval=frame,crop={W}:{H}:{PAD}:{PAD}[{rot}]")

    partes = [f"[{tag_in}]split=3[{p}a][{p}b][{p}c]"]
    tem_pessoa = bool(mascara_tag)
    if tem_pessoa:
        partes.append(f"[{mascara_tag}]split=2[{p}m1][{p}m2]")
        # o fundo sem a pessoa: onde ela estava, o próprio fundo bem desfocado
        partes.append(f"[{p}b]split=2[{p}b1][{p}b2]")
        partes.append(f"[{p}b1]gblur=sigma={u * 0.06:.1f},format=yuva444p[{p}bb]")
        partes.append(f"[{p}m2]negate[{p}mn]")
        partes.append(f"[{p}b2]format=yuva444p[{p}b3];[{p}b3][{p}mn]alphamerge[{p}bh]")
        partes.append(f"[{p}bb][{p}bh]overlay=format=auto,format=yuva444p,"
                      f"drawbox=x=0:y=0:w=iw:h=ih:color={cor_borda}:t={borda}:enable='{vis}',"
                      + persp(f"{p}q0", D))
        # a pessoa, na placa da frente: vidro quase invisível por trás dela
        partes.append(f"[{p}c]format=yuva444p[{p}c1];[{p}c1][{p}m1]alphamerge[{p}ps]")
        partes.append(f"color=c=white@0.07:s={W}x{H}:r={fps:.6f},format=yuva444p,"
                      f"drawbox=x=0:y=0:w=iw:h=ih:color={cor_borda}:t={borda}:enable='{vis}'[{p}v2]")
        partes.append(f"[{p}v2][{p}ps]overlay=format=auto:shortest=1,format=yuva444p,"
                      + persp(f"{p}q2", -D))
    else:
        partes.append(f"[{p}b]format=yuva444p,"
                      f"drawbox=x=0:y=0:w=iw:h=ih:color={cor_borda}:t={borda}:enable='{vis}',"
                      + persp(f"{p}q0", D))
        partes.append(f"[{p}c]nullsink")
    # a placa do meio: os logos, em fila, que acendem enquanto as placas abrem
    partes.append(f"color=c=white@0.06:s={W}x{H}:r={fps:.6f},format=yuva444p,"
                  f"drawbox=x=0:y=0:w=iw:h=ih:color={cor_borda}:t={borda}:enable='{vis}'[{p}l0]")
    atual = f"{p}l0"
    n = len(logos)
    for k, caminho in enumerate(logos):
        from .logos import dims_png

        d = dims_png(caminho) or (512, 512)
        aspecto = d[0] / max(1, d[1])
        lh = u * (0.2 if n <= 2 else 0.15)
        lw = lh * aspecto
        if lw > W / max(1, n) * 0.8:
            lw = W / max(1, n) * 0.8
            lh = lw / aspecto
        lw, lh = int(lw) // 2 * 2, int(lh) // 2 * 2
        cx = W * (k + 1) / (n + 1)
        partes.append(f"[{img(caminho)}]format=rgba,scale={lw}:{lh}:flags=lanczos,"
                      f"{_logo_no_tempo(a + e * 0.3, e * 0.6, b - s * 0.3, s * 0.7)}[{p}lg{k}]")
        partes.append(f"[{atual}][{p}lg{k}]overlay=x={cx - lw / 2:.1f}:y={H / 2 - lh / 2:.1f}"
                      f":format=auto:shortest=1[{p}l{k + 1}]")
        atual = f"{p}l{k + 1}"
    partes.append(f"[{atual}]format=yuva444p," + persp(f"{p}q1", 0.0))
    # o palco: a cor, e as placas de trás para a frente
    partes.append(f"[{p}a]drawbox=x=0:y=0:w=iw:h=ih:color={_hex(fundo)}@1:t=fill[{p}st]")
    partes.append(f"[{p}st][{p}q0]overlay=format=auto:shortest=1[{p}s1]")
    partes.append(f"[{p}s1][{p}q1]overlay=format=auto:shortest=1[{p}s2]")
    if tem_pessoa:
        partes.append(f"[{p}s2][{p}q2]overlay=format=auto:shortest=1,format={pix_fmt}[{tag_out}]")
    else:
        partes.append(f"[{p}s2]format={pix_fmt}[{tag_out}]")
    return ";".join(partes)


# ------------------------------------------------------------------ grafo
def grafo(tag_in: str, tag_out: str, cenas: list, W: int, H: int, fps: float,
          n_total: int, t0: float, pix_fmt: str, kit: dict | None,
          centro: tuple[float, float], mascara_in: str | None, img,
          pasta: Path, logos_de) -> str:
    """As cenas do trecho. "" = nenhuma.

    ``mascara_in``: o rótulo da máscara da pessoa já no tamanho W x H e com os
    tempos por número de quadro (ou None). ``img(caminho)``: registra uma
    imagem em loop como entrada e devolve o rótulo dela. ``logos_de(cena)``:
    os caminhos dos logos da placa do meio.
    """
    pedacos = []
    for c in cenas:
        a = float(_v(c, "out_start", 0.0)) - t0
        b = float(_v(c, "out_end", 0.0)) - t0
        f0 = max(0, int(round(a * fps)))
        f1 = min(n_total, int(round(b * fps)))
        if pedacos and f0 < pedacos[-1][1]:
            f0 = pedacos[-1][1]                  # cenas não se sobrepõem
        if f1 - f0 >= 2:
            pedacos.append((f0, f1, c, a, b))
    if not pedacos:
        return ""
    ramos: list[tuple[int, int, object]] = []
    cursor = 0
    for f0, f1, c, a, b in pedacos:
        if f0 > cursor:
            ramos.append((cursor, f0, None))
        ramos.append((f0, f1, (c, a, b)))
        cursor = f1
    ramos.append((cursor, -1, None))           # até o fim de verdade
    n = len(ramos)
    rot = [f"__cn{k}" for k in range(n)]
    partes = [f"[{tag_in}]format={pix_fmt},setsar=1,split={n}" + "".join(f"[{r}s]" for r in rot)]
    usa_mascara = [r for r in ramos if r[2] and _v(r[2][0], "tipo") == "vidro3d"]
    if mascara_in and usa_mascara:
        partes.append(f"[{mascara_in}]split={len(usa_mascara)}"
                      + "".join(f"[__cnm{k}]" for k in range(len(usa_mascara))))
    km = 0
    for k, ((f0, f1, alvo), r) in enumerate(zip(ramos, rot)):
        corte = f"trim=start_frame={f0}" + (f":end_frame={f1}" if f1 >= 0 else "")
        if alvo is None:
            partes.append(f"[{r}s]{corte},setpts=PTS-STARTPTS[{r}]")
            continue
        c, a, b = alvo
        o = f0 / fps                            # o relógio local começa aqui
        partes.append(f"[{r}s]{corte},setpts=PTS-STARTPTS[{r}i]")
        if _v(c, "tipo") == "moldura":
            partes.append(_moldura(f"{r}i", f"{r}x", c, W, H, fps, a - o, b - o, kit,
                                   centro, img, pasta, f"__c{k}"))
        else:
            mt = None
            if mascara_in:
                partes.append(f"[__cnm{km}]{corte},setpts=PTS-STARTPTS[{r}m]")
                mt = f"{r}m"
                km += 1
            partes.append(_vidro3d(f"{r}i", f"{r}x", mt, c, W, H, fps, a - o, b - o,
                                   kit, img, logos_de(c), f"__c{k}", pix_fmt))
        partes.append(f"[{r}x]format={pix_fmt},setsar=1[{r}]")
    partes.append("".join(f"[{r}]" for r in rot)
                  + f"concat=n={n}:v=1:a=0,setpts=N/({fps:.6f}*TB)[{tag_out}]")
    return ";".join(partes)


# -------------------------------------------------------------- validação
def normalizar(d: dict, duracao: float | None = None) -> dict:
    d = dict(d or {})
    fim_max = duracao if duracao and duracao > 0 else 1e9

    def num(v, padrao, lo, hi):
        try:
            x = float(v)
        except (TypeError, ValueError):
            return padrao
        if x != x or x in (float("inf"), float("-inf")):
            return padrao
        return max(lo, min(hi, x))

    tipo = d.get("tipo") if d.get("tipo") in TIPOS else "moldura"
    a = num(d.get("out_start"), 0.0, 0.0, fim_max)
    b = min(fim_max, num(d.get("out_end"), a + 4.0, 0.0, fim_max))
    minimo = 1.5
    if b - a < minimo:
        b = min(fim_max, a + 4.0)
        if b - a < minimo:
            a = max(0.0, b - 4.0)
    fundo = str(d.get("fundo") or "").strip()
    if not (fundo in FUNDOS or (fundo.startswith("#") and len(fundo) == 7)):
        fundo = ""
    logos = [str(x)[:60] for x in (d.get("logos") or []) if str(x).strip()][:4]
    out = {"tipo": tipo, "out_start": round(a, 3), "out_end": round(b, 3),
           "lado": d.get("lado") if d.get("lado") in LADOS else "",
           "fundo": fundo, "logos": logos,
           "forca": num(d.get("forca"), 0.6 if tipo == "vidro3d" else 0.5, 0.0, 1.0),
           "enabled": bool(d.get("enabled", True)),
           "origem": str(d.get("origem") or "")[:20]}
    if d.get("id"):
        out["id"] = str(d["id"])[:40]
    return out
