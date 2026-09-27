"""O trecho em duas camadas: a pessoa na frente, o fundo atrás.

Com a máscara da pessoa (render/recorte.py), o quadro vira duas camadas e
cada uma recebe o seu tratamento, no MESMO encode do trecho:

- ``desfoque``: o fundo desfocado, a pessoa nítida — a lente aberta que o
  celular não tem.
- ``escurecer``: o fundo apagado e sem cor, a pessoa acesa — o holofote.
- ``parallax``: a pessoa salta para a frente do fundo (cresce alguns por
  cento em torno de onde ela está) enquanto o fundo recua desfocado. É o
  "3D" possível numa câmera só: a pessoa ampliada cobre a silhueta original,
  então não aparece fantasma nem buraco.
- ``recorte``: a pessoa recortada sobre uma cor lisa, com contorno branco —
  o adesivo das telas didáticas.

E os gráficos com ``camada="atras"`` são desenhados no FUNDO, antes de a
pessoa voltar por cima: o título passa atrás da cabeça.

Tudo com ``enable`` por janela de tempo: fora das janelas, nenhuma camada
existe e o quadro passa intacto.
"""
from __future__ import annotations

from pathlib import Path

from ..ffmpeg_utils import escape_filter_path

EFEITOS = ("desfoque", "escurecer", "parallax", "recorte")
COR_DO_RECORTE = "0x0B1220"
CONTORNO = 5                # passadas de dilatação, no tamanho da máscara


def _v(c, k, padrao=None):
    if isinstance(c, dict):
        return c.get(k, padrao)
    return getattr(c, k, padrao)


def no_trecho(camadas, t0: float, dur: float) -> list:
    out = []
    for c in camadas or []:
        if not _v(c, "enabled", True) or _v(c, "efeito") not in EFEITOS:
            continue
        a, b = float(_v(c, "out_start", 0.0)), float(_v(c, "out_end", 0.0))
        if b - a < 0.1 or b <= t0 or a >= t0 + dur:
            continue
        out.append(c)
    return out


def _rel(c, t0: float, dur: float) -> tuple[float, float]:
    a = max(0.0, float(_v(c, "out_start", 0.0)) - t0)
    b = min(dur + 0.05, float(_v(c, "out_end", 0.0)) - t0)
    return round(a, 3), round(b, 3)


def janelas(camadas, graficos_atras, t0: float, dur: float) -> list[tuple[float, float]]:
    """Onde a máscara é usada, em segundos do trecho (unidas e ordenadas)."""
    brutas = [_rel(c, t0, dur) for c in camadas] + [_rel(g, t0, dur) for g in graficos_atras]
    brutas = sorted((a, b) for a, b in brutas if b > a)
    unidas: list[list[float]] = []
    for a, b in brutas:
        if unidas and a <= unidas[-1][1] + 0.05:
            unidas[-1][1] = max(unidas[-1][1], b)
        else:
            unidas.append([a, b])
    return [(a, b) for a, b in unidas]


def _entre(jan: list[tuple[float, float]]) -> str:
    return "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in jan) or "0"


def _rampa(a: float, b: float) -> str:
    """0 fora de [a, b], sobe em 0,6 s, desce nos últimos 0,5 s (suave)."""
    sobe = f"clip((t-{a:.3f})/0.6,0,1)"
    desce = f"clip(({b:.3f}-t)/0.5,0,1)"
    p = f"({sobe}*{desce})"
    return f"({p}*{p}*(3-2*{p}))"


def grafo(tag_in: str, tag_out: str, idx_mascara: str, W: int, H: int,
          fps: float, t0: float, dur: float, camadas: list,
          ass_atras: Path | None, graficos_atras: list,
          centro: tuple[float, float], fontsdir: Path | None = None,
          extra_fundo=None) -> str:
    """O pedaço do filtergraph que separa e recompõe as camadas.

    ``extra_fundo(tag_in, tag_out)``: o que mais vai no FUNDO, antes de a
    pessoa voltar por cima — os logos com ``camada="atras"``.
    """
    jan = janelas(camadas, graficos_atras, t0, dur)
    if not jan:
        return ""
    recortes = [c for c in camadas if _v(c, "efeito") == "recorte"]
    parallax = [c for c in camadas if _v(c, "efeito") == "parallax"]
    partes: list[str] = []
    # A máscara entra com os tempos do PRÓPRIO trecho. ``floor`` de propósito:
    # o quadro k da máscara tem de cair em k/fps ou um tiquinho ANTES — o
    # alphamerge pareia com o último quadro que já chegou, e um arredondamento
    # para cima casaria a pessoa do quadro k com a máscara do quadro k-1.
    base_m = (f"[{idx_mascara}:v]format=gray,settb=AVTB,"
              f"setpts='floor(N/({fps:.6f}*TB))'")
    if recortes:
        partes.append(base_m + ",split=2[__mr0][__mr1]")
        partes.append(f"[__mr0]scale={W}:{H}:flags=bilinear[__mt]")
        partes.append("[__mr1]" + ",".join(["dilation"] * CONTORNO)
                      + f",scale={W}:{H}:flags=bilinear[__mo]")
    else:
        partes.append(base_m + f",scale={W}:{H}:flags=bilinear[__mt]")
    partes.append(f"[{tag_in}]split=2[__pa][__pb]")

    fundo: list[str] = []
    for c in sorted(camadas, key=lambda c: float(_v(c, "out_start", 0.0))):
        a, b = _rel(c, t0, dur)
        if b <= a:
            continue
        en = f"between(t,{a:.3f},{b:.3f})"
        try:
            f = max(0.0, min(1.0, float(_v(c, "forca", 0.6))))
        except (TypeError, ValueError):
            f = 0.6
        efeito = _v(c, "efeito")
        if efeito == "desfoque":
            fundo.append(f"gblur=sigma={4 + 22 * f:.1f}:enable='{en}'")
        elif efeito == "escurecer":
            fundo.append(f"eq=brightness={-0.3 * f:.3f}:saturation={1 - 0.55 * f:.3f}"
                         f":enable='{en}'")
        elif efeito == "parallax":
            fundo.append(f"gblur=sigma={2 + 9 * f:.1f}:enable='{en}'")
        elif efeito == "recorte":
            fundo.append(f"drawbox=x=0:y=0:w=iw:h=ih:color={COR_DO_RECORTE}@1:t=fill"
                         f":enable='{en}'")
    if ass_atras:
        fundo.append(f"ass='{escape_filter_path(ass_atras)}'"
                     + (f":fontsdir='{escape_filter_path(fontsdir)}'" if fontsdir else ""))
    fundo_tag = "__pb"
    if fundo:
        partes.append("[__pb]" + ",".join(fundo) + "[__bg]")
        fundo_tag = "__bg"
    if extra_fundo:
        extra = extra_fundo(fundo_tag, "__bgl")
        if extra:
            partes.append(extra)
            fundo_tag = "__bgl"
    if recortes:
        jr = [_rel(c, t0, dur) for c in recortes]
        partes.append(f"color=c=white:s={W}x{H}:r={fps:.6f}:d={dur + 1:.3f},"
                      "format=yuv420p[__br]")
        partes.append("[__br][__mo]alphamerge[__ct]")
        partes.append(f"[{fundo_tag}][__ct]overlay=format=auto:shortest=1"
                      f":enable='{_entre(jr)}'[__bg2]")
        fundo_tag = "__bg2"

    partes.append("[__pa][__mt]alphamerge[__ps]")
    pessoa = "__ps"
    x, y = "0", "0"
    if parallax:
        termos = []
        for c in parallax:
            a, b = _rel(c, t0, dur)
            try:
                f = max(0.0, min(1.0, float(_v(c, "forca", 0.6))))
            except (TypeError, ValueError):
                f = 0.6
            termos.append(f"{0.04 + 0.06 * f:.4f}*{_rampa(a, b)}")
        z = "(1+" + "+".join(termos) + ")"
        partes.append(f"[__ps]scale=w='trunc({W}*{z}/2)*2':h=-2:eval=frame[__pz]")
        pessoa = "__pz"
        cx, cy = (max(0.0, min(1.0, float(v))) for v in centro)
        x, y = f"{cx:.4f}*(W-w)", f"{cy:.4f}*(H-h)"
    partes.append(f"[{fundo_tag}][{pessoa}]overlay=x='{x}':y='{y}':eval=frame"
                  f":format=auto:enable='{_entre(jan)}'[{tag_out}]")
    return ";".join(partes)


def normalizar(d: dict, duracao: float | None = None) -> dict:
    d = dict(d or {})
    fim_max = duracao if duracao and duracao > 0 else 1e9

    def num(v, padrao, lo, hi):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return padrao
        if f != f or f in (float("inf"), float("-inf")):
            return padrao
        return max(lo, min(hi, f))

    a = num(d.get("out_start"), 0.0, 0.0, fim_max)
    b = min(fim_max, num(d.get("out_end"), a + 3.0, 0.0, fim_max))
    if b - a < 0.3:
        b = min(fim_max, a + 3.0)
        if b - a < 0.3:
            a = max(0.0, b - 3.0)
    out = {"efeito": d.get("efeito") if d.get("efeito") in EFEITOS else "desfoque",
           "out_start": round(a, 3), "out_end": round(b, 3),
           "forca": num(d.get("forca"), 0.6, 0.0, 1.0),
           "enabled": bool(d.get("enabled", True)),
           "origem": str(d.get("origem") or "")[:20]}
    if d.get("id"):
        out["id"] = str(d["id"])[:40]
    return out
