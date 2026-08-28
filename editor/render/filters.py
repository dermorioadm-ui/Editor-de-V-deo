"""Construção dos filtergraphs.

Tudo aqui devolve string de filtro. Nada roda ffmpeg — quem roda é o renderer,
e roda UMA vez por trecho.
"""
from __future__ import annotations

from pathlib import Path

from ..ffmpeg_utils import escape_filter_path

# Conversão só de transferência e primárias. Num teste de ida e volta
# (SDR -> HLG/BT.2020 -> de volta) esta reconstrói o original com erro médio de
# 0,22 em 255. É o padrão.
TONEMAP_TRANSFER = "zscale=t=bt709:p=bt709:m=bt709:r=tv,format=yuv420p"
TONEMAP = TONEMAP_TRANSFER


def tonemap_chain(mode: str = "transferencia", npl: float = 100.0,
                  operator: str = "hable", desat: float = 0.0) -> str:
    """HDR (HLG/BT.2020) -> SDR (BT.709).

    ``transferencia`` faz só a conversão de curva e primárias — no teste de ida
    e volta ela devolve o original quase exato.

    ``operador`` acrescenta um operador de tonemap (hable por padrão). Serve
    para material cujos altos passam mesmo do alcance SDR; no mesmo teste
    controlado ele escurece demais, então não é o padrão. Em gravação de
    celular de verdade o resultado depende do pico da fonte — por isso a
    comparação lado a lado existe na interface.
    """
    if mode in ("", "transferencia", "transfer"):
        return TONEMAP_TRANSFER
    op = operator if operator in (
        "none", "linear", "gamma", "clip", "reinhard", "hable", "mobius") else "hable"
    return (
        f"zscale=t=linear:npl={npl:g},format=gbrpf32le,zscale=p=bt709,"
        f"tonemap=tonemap={op}:desat={desat:g},"
        f"zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
    )


def fit_chain(width: int, height: int, blur_sigma: float = 28.0) -> str:
    """Encaixa qualquer proporção sobre um fundo desfocado dele mesmo.

    Nada de tarja preta, nada de cortar conteúdo (Parte 7.1).
    """
    return (
        f"split=2[__bg][__fg];"
        f"[__bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},gblur=sigma={blur_sigma}[__bgb];"
        f"[__fg]scale={width}:{height}:force_original_aspect_ratio=decrease[__fgs];"
        f"[__bgb][__fgs]overlay=(W-w)/2:(H-h)/2:format=auto"
    )


def color_chain(fit: dict | None) -> str:
    if not fit:
        return ""
    b = float(fit.get("brightness", 0.0) or 0.0)
    s = float(fit.get("saturation", 1.0) or 1.0)
    c = float(fit.get("contrast", 1.0) or 1.0)
    if abs(b) < 1e-6 and abs(s - 1.0) < 1e-6 and abs(c - 1.0) < 1e-6:
        return ""
    return f"eq=brightness={b:.4f}:saturation={s:.4f}:contrast={c:.4f}"


def _piecewise(keyframes: list[dict], key: str, t0: float, default: float) -> str:
    """Expressão linear por partes em ``t`` (relativo ao início do bloco)."""
    pts = sorted(keyframes, key=lambda k: float(k.get("t", 0.0)))
    if not pts:
        return f"{default}"
    if len(pts) == 1:
        return f"{float(pts[0].get(key, default)):.6f}"
    expr = f"{float(pts[-1].get(key, default)):.6f}"
    for a, b in reversed(list(zip(pts, pts[1:]))):
        ta = float(a.get("t", 0.0)) - t0
        tb = float(b.get("t", 0.0)) - t0
        va = float(a.get(key, default))
        vb = float(b.get(key, default))
        span = max(tb - ta, 1e-6)
        seg = f"({va:.6f}+({vb - va:.6f})*(t-{ta:.6f})/{span:.6f})"
        expr = f"if(lt(t,{tb:.6f}),{seg},{expr})"
    first_t = float(pts[0].get("t", 0.0)) - t0
    first_v = float(pts[0].get(key, default))
    return f"if(lt(t,{first_t:.6f}),{first_v:.6f},{expr})"


def _obscure(mode: str, strength: int, w_px: int, h_px: int) -> str:
    """Como a região é escondida. Para rosto e documento, pixel é mais seguro."""
    if mode == "pixel":
        n = max(2, int(strength))
        return (f"scale=iw/{n}:ih/{n}:flags=neighbor,"
                f"scale={w_px}:{h_px}:flags=neighbor")
    return f"gblur=sigma={max(2, int(strength))}:steps=3"


def blur_chain(blurs: list, clip_out_start: float, clip_out_end: float,
               width: int, height: int, tag_in: str, tag_out: str) -> tuple[str, bool]:
    """Desfoque retangular com região que acompanha keyframes (Parte 7.3)."""
    active = [b for b in blurs
              if b.enabled and b.out_end > clip_out_start and b.out_start < clip_out_end]
    if not active:
        return "", False
    parts: list[str] = []
    cur = tag_in
    for i, b in enumerate(active):
        kfs = b.keyframes or [{"t": b.out_start, "x": 0.35, "y": 0.35,
                               "w": 0.3, "h": 0.3}]
        w_px = max(16, int(round(max(float(k.get("w", 0.3)) for k in kfs) * width)))
        h_px = max(16, int(round(max(float(k.get("h", 0.3)) for k in kfs) * height)))
        w_px -= w_px % 2
        h_px -= h_px % 2
        x_expr = _piecewise(kfs, "x", clip_out_start, 0.35)
        y_expr = _piecewise(kfs, "y", clip_out_start, 0.35)
        x_px = f"clip(({x_expr})*{width},0,{width - w_px})"
        y_px = f"clip(({y_expr})*{height},0,{height - h_px})"
        start = max(0.0, b.out_start - clip_out_start)
        end = max(start, b.out_end - clip_out_start)
        a, c = f"__b{i}a", f"__b{i}c"
        nxt = f"__b{i}o"
        parts.append(f"[{cur}]split=2[{a}][{c}]")
        parts.append(
            # crop avalia x/y por quadro quando a expressão usa `t`;
            # a opção eval não existe mais no ffmpeg 6+.
            f"[{c}]crop=w={w_px}:h={h_px}:x='{x_px}':y='{y_px}',"
            f"{_obscure(getattr(b, 'shape', 'rect'), b.strength, w_px, h_px)}"
            f"[__b{i}bl]"
        )
        parts.append(
            f"[{a}][__b{i}bl]overlay=x='{x_px}':y='{y_px}':eval=frame:"
            f"enable='between(t,{start:.3f},{end:.3f})'[{nxt}]"
        )
        cur = nxt
    parts.append(f"[{cur}]null[{tag_out}]")
    return ";".join(parts), True


def overlay_inputs(overlays: list, clip_out_start: float,
                   clip_out_end: float) -> list:
    return [o for o in overlays
            if o.enabled and o.out_end > clip_out_start and o.out_start < clip_out_end]


def overlay_chain(overlays: list, media_paths: dict, clip_out_start: float,
                  width: int, height: int, first_input_index: int,
                  tag_in: str, tag_out: str) -> tuple[str, list[str]]:
    """PNGs com entrada configurável (Parte 8)."""
    if not overlays:
        return "", []
    parts: list[str] = []
    inputs: list[str] = []
    cur = tag_in
    for i, o in enumerate(overlays):
        path = media_paths.get(o.media_id)
        if not path:
            continue
        idx = first_input_index + len(inputs)
        inputs.append(str(path))
        start = max(0.0, o.out_start - clip_out_start)
        end = max(start + 0.05, o.out_end - clip_out_start)
        scaled = f"__ov{i}"
        scale_w = f"iw*{o.scale:.4f}"
        chain = [f"[{idx}:v]format=rgba,scale={scale_w}:-1"]
        if o.opacity < 0.999:
            chain.append(f"colorchannelmixer=aa={o.opacity:.3f}")
        if o.anim_in == "fade" and o.dur_in > 0:
            chain.append(f"fade=t=in:st=0:d={o.dur_in:.3f}:alpha=1")
        if o.anim_out == "fade" and o.dur_out > 0:
            chain.append(f"fade=t=out:st={max(0.0, (end-start)-o.dur_out):.3f}:"
                         f"d={o.dur_out:.3f}:alpha=1")
        chain.append(f"setpts=PTS-STARTPTS+{start:.3f}/TB")
        parts.append(",".join(chain) + f"[{scaled}]")

        cx = f"({o.x:.4f}*main_w-overlay_w/2)"
        cy = f"({o.y:.4f}*main_h-overlay_h/2)"
        x_expr, y_expr = cx, cy
        p = max(o.dur_in, 1e-3)
        rel = f"(t-{start:.3f})"
        prog = f"min(1,max(0,{rel}/{p:.3f}))"
        if o.anim_in == "slide_left":
            x_expr = f"({cx}-(1-{prog})*(main_w*0.6))"
        elif o.anim_in == "slide_right":
            x_expr = f"({cx}+(1-{prog})*(main_w*0.6))"
        elif o.anim_in == "pop":
            y_expr = f"({cy}+(1-{prog})*26)"
        nxt = f"__ovo{i}"
        parts.append(
            f"[{cur}][{scaled}]overlay=x='{x_expr}':y='{y_expr}':eval=frame:"
            f"enable='between(t,{start:.3f},{end:.3f})'[{nxt}]"
        )
        cur = nxt
    if not inputs:
        return "", []
    parts.append(f"[{cur}]null[{tag_out}]")
    return ";".join(parts), inputs


def ken_burns_chain(width: int, height: int, duration: float,
                    intensity: float, direction: str = "in",
                    fps: float = 30.0) -> tuple[str, str]:
    """Push-in lento (Parte 7.2).

    Usa ``zoompan``, não ``crop`` com expressão: o crop avalia largura e altura
    uma única vez, na inicialização, então um zoom feito com ele fica parado.

    Devolve (cadeia, expressão do zoom em ``t``) — a expressão é reaproveitada
    pelas anotações, para que elas acompanhem o movimento.
    """
    k = max(0.0, min(0.6, intensity))
    dur = max(duration, 0.1)
    frames = max(1, int(round(dur * fps)))
    if direction == "out":
        z_expr = f"(1+{k:.4f}-{k:.4f}*on/{frames})"
        z_time = f"(1+{k:.4f}-{k:.4f}*t/{dur:.4f})"
    else:
        z_expr = f"(1+{k:.4f}*on/{frames})"
        z_time = f"(1+{k:.4f}*t/{dur:.4f})"
    chain = (
        f"zoompan=z='{z_expr}':d=1:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={width}x{height}:fps={fps}"
    )
    return chain, z_time


_MARK_GLYPH = {"x": "✕", "circle": "◯", "arrow": "➜", "dot": "●"}


def annotation_chain(annotations: list, width: int, height: int,
                     zoom_expr: str, font: str = "") -> str:
    """Marcadores sobre a foto, com tempo de entrada próprio.

    As coordenadas passam pela mesma expressão do zoom, então a anotação
    acompanha o movimento.
    """
    parts: list[str] = []
    for a in annotations or []:
        px = float(a.get("x", 0.5))
        py = float(a.get("y", 0.5))
        z = f"({zoom_expr})"
        x_expr = f"(({px:.5f}*{z})-(({z})-1)/2)*{width}"
        y_expr = f"(({py:.5f}*{z})-(({z})-1)/2)*{height}"
        start = float(a.get("start", 0.0))
        glyph = _MARK_GLYPH.get(str(a.get("kind", "x")), "✕")
        color = str(a.get("color", "#FF2D2D")).lstrip("#")
        size = int(a.get("size", max(48, width // 14)))
        common = (f":fontcolor=0x{color}:borderw=3:bordercolor=black@0.75"
                  f":enable='gte(t,{start:.3f})'")
        fontfile = f":fontfile='{escape_filter_path(font)}'" if font else ""
        parts.append(
            f"drawtext=text='{glyph}':fontsize={size}{fontfile}"
            f":x='({x_expr})-text_w/2':y='({y_expr})-text_h/2'{common}"
        )
        label = str(a.get("label", "")).strip()
        if label:
            safe = (label.replace("\\", "\\\\").replace(":", r"\:")
                    .replace("'", r"’").replace("%", r"\%"))
            parts.append(
                f"drawtext=text='{safe}':fontsize={max(24, size//2)}{fontfile}"
                f":fontcolor=white:box=1:boxcolor=0x{color}@0.85:boxborderw=12"
                f":x='({x_expr})-text_w/2':y='({y_expr})+{size*0.7:.0f}'"
                f":enable='gte(t,{start:.3f})'"
            )
    return ",".join(parts)


def subtitle_chain(ass_path: str | Path) -> str:
    return f"ass='{escape_filter_path(ass_path)}'"


def music_chain(gain_db: float, ducking: bool, duck_amount: float,
                fade_in: float, fade_out: float, total: float,
                out_start: float = 0.0, out_end: float | None = None) -> str:
    """Trilha com ducking por sidechain (Parte 9.3).

    ``out_start``/``out_end`` posicionam a trilha na linha do tempo — é o que
    permite arrastar o item de música no trilho em vez de ela cobrir o vídeo
    inteiro à força.
    """
    fim = total if out_end is None else min(float(out_end), total)
    inicio = max(0.0, float(out_start))
    dur = max(0.1, fim - inicio)
    music = [f"volume={gain_db}dB"]
    if fade_in > 0:
        music.append(f"afade=t=in:st=0:d={min(fade_in, dur / 2):.2f}")
    if fade_out > 0:
        music.append(f"afade=t=out:st={max(0.0, dur - fade_out):.2f}:"
                     f"d={min(fade_out, dur / 2):.2f}")
    music.append(f"atrim=0:{dur:.3f}")
    if inicio > 0.001:
        # adelay põe a trilha no lugar certo; apad garante que ela não encurta
        # a mixagem quando termina antes do vídeo
        music.append(f"adelay={int(inicio * 1000)}|{int(inicio * 1000)}")
    music.append(f"apad=whole_dur={total:.3f}")
    music_chain_str = ",".join(music)
    if ducking:
        ratio = max(2.0, duck_amount / 2.0)
        return (
            f"[1:a]{music_chain_str}[mus];"
            f"[0:a]asplit=2[voz][key];"
            f"[mus][key]sidechaincompress=threshold=0.06:ratio={ratio:.1f}:"
            f"attack=20:release=350:makeup=1[duck];"
            f"[voz][duck]amix=inputs=2:duration=first:dropout_transition=0:"
            f"normalize=0[aout]"
        )
    return (f"[1:a]{music_chain_str}[mus];"
            f"[0:a][mus]amix=inputs=2:duration=first:normalize=0[aout]")
