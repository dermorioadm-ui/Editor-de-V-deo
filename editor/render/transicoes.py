"""Transições nas emendas, dentro do encode de cada trecho.

O Sharkcut corta seco — é o que preserva a fala. Uma transição aqui NÃO é um
crossfade: dois trechos sobrepostos obrigariam o áudio a sobrepor também (ou
a picotar palavra), e a regra "corte não pode comer palavra" vem antes. O que
a transição faz é tratar a BORDA de cada lado da emenda: os últimos quadros
do trecho que sai e os primeiros do que entra recebem o mesmo efeito, que
cresce até a emenda e se desfaz depois dela. Zoom que mergulha e volta,
chicote, flash, glitch, desfoque, luz, giro — o que os editores fazem por
cima do corte seco.

UM FILTRO FIXO POR QUADRO. Os filtros que fazem esse trabalho (crop, scale,
gblur, rotate, eq) avaliam o tamanho UMA vez na configuração; animar por
expressão exigiria ``eval=frame`` e tamanhos variando no meio do grafo, que é
onde o ffmpeg se perde. Como a borda tem poucos quadros (meio segundo a 30
fps são 15), cada quadro ganha o SEU ramo com parâmetros fixos:
``split`` → ``trim`` de um quadro → efeito → ``concat``. O miolo do trecho
passa por um ramo sem efeito nenhum. A contagem de quadros não muda, e os
tempos são refeitos pela contagem no fim (o trecho já chegou em CFR).
"""
from __future__ import annotations

import math

TIPOS = ("zoom", "chicote", "flash", "glitch", "desfoque", "luz", "giro")
MAX_QUADROS_LADO = 20
DUR_PADRAO = 0.4
DUR_MAX = 1.2


def _par(v: int) -> int:
    v = int(v)
    return max(2, v - (v % 2))


def _zoom(z: float, W: int, H: int) -> str:
    """Recorte central de 1/z do quadro, esticado de volta ao tamanho."""
    if z <= 1.0005:
        return ""
    cw, ch = _par(W / z), _par(H / z)
    return (f"crop={cw}:{ch}:{(W - cw) // 2}:{(H - ch) // 2},"
            f"scale={W}:{H}:flags=bicubic")


def efeito(tipo: str, p: float, W: int, H: int, i: int, lado: str) -> str:
    """O filtro de UM quadro. ``p`` vai de 0 (longe) a 1 (colado na emenda).

    ``lado`` é "sai" (antes da emenda) ou "entra" (depois); só o chicote e o
    giro olham para ele — o movimento continua na mesma direção através do
    corte, como numa câmera de verdade.
    """
    e = p * p                       # acelera até a emenda
    partes: list[str] = []
    if tipo == "zoom":
        partes.append(_zoom(1 + 0.32 * e, W, H))
        if e > 0.35:
            partes.append(f"gblur=sigma={2.5 * e:.2f}")
    elif tipo == "chicote":
        z = 1.12
        cw, ch = _par(W / z), _par(H / z)
        folga = (W - cw) / 2
        sentido = -1 if lado == "sai" else 1
        x = int(round((W - cw) / 2 + sentido * folga * e))
        x = max(0, min(W - cw, x))
        partes.append(f"crop={cw}:{ch}:{x}:{(H - ch) // 2},scale={W}:{H}:flags=bicubic")
        raio = int(round(W * 0.035 * e))
        if raio >= 1:
            partes.append(f"avgblur=sizeX={raio}:sizeY=1")
    elif tipo == "flash":
        partes.append(f"eq=brightness={0.92 * e:.3f}:contrast={1 - 0.45 * e:.3f}")
    elif tipo == "glitch":
        # desalinho de cor e um tranco de posição, pseudoaleatório mas FIXO
        # por quadro (mesmo plano, mesmo resultado, cache vale)
        desl = int(round(W * 0.018 * e)) * (1 if i % 2 else -1)
        partes.append(f"chromashift=cbh={desl}:crh={-desl}")
        if e > 0.2:
            z = 1 + 0.06 * e
            cw, ch = _par(W / z), _par(H / z)
            tranco = int(((i * 37) % 11 - 5) / 5 * (W - cw) / 2)
            x = max(0, min(W - cw, (W - cw) // 2 + tranco))
            partes.append(f"crop={cw}:{ch}:{x}:{(H - ch) // 2},scale={W}:{H}:flags=neighbor")
            partes.append(f"noise=alls={int(18 * e)}:allf=u")
    elif tipo == "desfoque":
        partes.append(_zoom(1 + 0.07 * e, W, H))
        partes.append(f"gblur=sigma={max(0.1, 26 * e):.2f}")
    elif tipo == "luz":
        partes.append(f"lutyuv=y='clip(val+{int(70 * e)},minval,maxval)':"
                      f"u='clip(val-{int(14 * e)},minval,maxval)':"
                      f"v='clip(val+{int(22 * e)},minval,maxval)'")
        partes.append(f"gblur=sigma={max(0.1, 6 * e):.2f}")
    elif tipo == "giro":
        sentido = 1 if lado == "sai" else -1
        a = sentido * 0.2 * e
        z = math.cos(abs(a)) + max(W / H, H / W) * math.sin(abs(a))
        partes.append(f"rotate={a:.4f}:fillcolor=black")
        partes.append(_zoom(z * (1 + 0.1 * e), W, H))
        if e > 0.3:
            partes.append(f"gblur=sigma={3 * e:.2f}")
    return ",".join(x for x in partes if x)


def quadros_do_lado(dur_lado: float, fps: float, n_total: int) -> int:
    k = int(round(max(0.0, dur_lado) * fps))
    return max(0, min(MAX_QUADROS_LADO, k, n_total // 2))


def grafo(tag_in: str, tag_out: str, W: int, H: int, fps: float,
          n_total: int, entra: dict | None, sai: dict | None,
          pix_fmt: str = "yuv420p") -> str:
    """O pedaço do filtergraph que aplica as bordas. "" = nada a fazer.

    ``entra``/``sai``: {"tipo", "dur"} — ``dur`` é a parte DESTE lado.
    ``n_total``: quantos quadros o trecho tem (da duração nominal); se o
    trecho real sair um quadro mais curto, o último ramo fica vazio e o
    concat segue sem ele.
    """
    ki = quadros_do_lado(entra["dur"], fps, n_total) if entra and entra.get("tipo") in TIPOS else 0
    ko = quadros_do_lado(sai["dur"], fps, n_total) if sai and sai.get("tipo") in TIPOS else 0
    if not ki and not ko:
        return ""
    ramos: list[tuple[int, int, str]] = []       # (do quadro, até, filtro)
    for i in range(ki):
        p = 1 - i / ki
        ramos.append((i, i + 1, efeito(entra["tipo"], p, W, H, i, "entra")))
    meio_ini, meio_fim = ki, n_total - ko
    if meio_fim > meio_ini:
        ramos.append((meio_ini, meio_fim, ""))
    for j in range(ko):
        p = (j + 1) / ko
        a = n_total - ko + j
        ramos.append((a, a + 1, efeito(sai["tipo"], p, W, H, j, "sai")))
    # o último ramo vai até o fim de verdade (se o trecho tiver um quadro a
    # mais do que a conta, ele não se perde)
    ultimo = ramos[-1]
    ramos[-1] = (ultimo[0], -1, ultimo[2])

    n = len(ramos)
    rotulos = [f"__tr{k}" for k in range(n)]
    partes = [f"[{tag_in}]format={pix_fmt},setsar=1,split={n}"
              + "".join(f"[{r}s]" for r in rotulos)]
    for (a, b, filtro), r in zip(ramos, rotulos):
        corte = f"trim=start_frame={a}" + (f":end_frame={b}" if b >= 0 else "")
        cadeia = [corte, "setpts=PTS-STARTPTS"]
        if filtro:
            cadeia += [filtro, f"format={pix_fmt}", "setsar=1"]
        partes.append(f"[{r}s]" + ",".join(cadeia) + f"[{r}]")
    partes.append("".join(f"[{r}]" for r in rotulos)
                  + f"concat=n={n}:v=1:a=0,setpts=N/({fps:.6f}*TB)[{tag_out}]")
    return ";".join(partes)


def resolver(transicoes, clips) -> dict[str, str]:
    """{id da transição: id do bloco que entra}, reencontrando pela âncora.

    O id do bloco é o que vale enquanto existir. Quando some (um corte partiu
    o bloco, "refazer edição" recriou todos), vale o bloco da MESMA gravação
    que começa perto do mesmo ponto da fonte — é a mesma emenda, com outro
    nome. Longe demais, a emenda deixou de existir e a transição fica parada
    (não aparece no vídeo) até alguém apagá-la ou recolocá-la.
    """
    ids = {c.id for c in clips}
    out: dict[str, str] = {}
    for x in transicoes or []:
        if x.clip_id in ids:
            out[x.id] = x.clip_id
            continue
        src_t = float(getattr(x, "src_t", -1.0) or -1.0)
        if src_t < 0:
            continue
        fonte = getattr(x, "fonte", "") or "main"
        perto = [c for c in clips if c.source == fonte
                 and abs(float(c.src_start) - src_t) <= 0.5]
        if perto:
            out[x.id] = min(perto, key=lambda c: abs(float(c.src_start) - src_t)).id
    return out


def normalizar(d: dict) -> dict:
    d = dict(d or {})
    tipo = d.get("tipo") if d.get("tipo") in TIPOS else "zoom"
    try:
        dur = float(d.get("duracao", DUR_PADRAO))
    except (TypeError, ValueError):
        dur = DUR_PADRAO
    if math.isnan(dur) or math.isinf(dur):
        dur = DUR_PADRAO
    try:
        src_t = float(d.get("src_t", -1.0))
    except (TypeError, ValueError):
        src_t = -1.0
    out = {"tipo": tipo, "duracao": round(max(0.1, min(DUR_MAX, dur)), 3),
           "clip_id": str(d.get("clip_id") or "")[:40],
           "fonte": str(d.get("fonte") or "")[:40],
           "src_t": src_t if src_t == src_t else -1.0,
           "enabled": bool(d.get("enabled", True)),
           "origem": str(d.get("origem") or "")[:20]}
    if d.get("id"):
        out["id"] = str(d["id"])[:40]
    return out
