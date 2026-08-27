"""Geração de ASS e SRT, e calibração de fontsize por largura (Parte 5.3)."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import numpy as np

from ..config import FFMPEG, SubtitleStyle

_ALIGN_NAMES = {1: "inferior esquerda", 2: "inferior centro", 3: "inferior direita",
                4: "meio esquerda", 5: "meio centro", 6: "meio direita",
                7: "topo esquerda", 8: "topo centro", 9: "topo direita"}


def hex_to_ass(color: str, alpha: int = 0) -> str:
    c = color.strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6:
        c = "FFFFFF"
    r, g, b = c[0:2], c[2:4], c[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ts_ass(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def ts_srt(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:
        ms, s = 0, s + 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_ass(cues: list[dict], style: SubtitleStyle, width: int, height: int,
              time_offset: float = 0.0, window: tuple[float, float] | None = None) -> str:
    """ASS completo. ``window`` recorta e reancoragem para queimar por bloco."""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{style.font},{style.fontsize},{hex_to_ass(style.primary)},{hex_to_ass(style.primary)},{hex_to_ass(style.outline_color)},{hex_to_ass(style.back_color, 128)},{-1 if style.bold else 0},{-1 if style.italic else 0},0,0,100,100,0,0,1,{style.outline},{style.shadow},{style.align},{style.margin_l},{style.margin_r},{style.margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for cue in cues:
        start, end = float(cue["start"]), float(cue["end"])
        if window is not None:
            w0, w1 = window
            if end <= w0 or start >= w1:
                continue
            start = max(start, w0)
            end = min(end, w1)
        start += time_offset
        end += time_offset
        if end <= start:
            continue
        text = str(cue["text"])
        if style.uppercase:
            text = text.upper()
        text = (text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
                .replace("\n", "\\N"))
        lines.append(f"Dialogue: 0,{ts_ass(start)},{ts_ass(end)},Main,,0,0,0,,{text}\n")
    return "".join(lines)


def build_srt(cues: list[dict], uppercase: bool = False) -> str:
    out = []
    for i, cue in enumerate(cues, 1):
        text = str(cue["text"])
        if uppercase:
            text = text.upper()
        out.append(f"{i}\n{ts_srt(float(cue['start']))} --> "
                   f"{ts_srt(float(cue['end']))}\n{text}\n")
    return "\n".join(out)


def write_ass(path: Path, cues: list[dict], style: SubtitleStyle,
              width: int, height: int, **kw) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_ass(cues, style, width, height, **kw), encoding="utf-8")
    return path


# --------------------------------------------------------------- calibração
def measure_text_width(text: str, style: SubtitleStyle, width: int, height: int,
                       fontsize: int | None = None) -> dict:
    """Renderiza um quadro e mede a caixa do texto em PIXELS.

    O fontsize do ASS não corresponde a pixels de forma direta — a única
    resposta confiável é medir o que o renderizador produz.
    """
    st = SubtitleStyle(**{**style.__dict__})
    if fontsize:
        st.fontsize = int(fontsize)
    st.align = 5
    st.margin_l = st.margin_r = st.margin_v = 10
    st.outline = style.outline
    cues = [{"start": 0.0, "end": 1.0, "text": text}]
    with tempfile.TemporaryDirectory() as tmp:
        ass_path = Path(tmp) / "probe.ass"
        ass_path.write_text(build_ass(cues, st, width, height), encoding="utf-8")
        escaped = str(ass_path).replace("\\", "/").replace(":", r"\:")
        proc = subprocess.run(
            [FFMPEG, "-v", "error", "-nostdin",
             "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:d=0.1",
             "-vf", f"ass='{escaped}'", "-frames:v", "1",
             "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"],
            capture_output=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-500:])
        frame = np.frombuffer(proc.stdout, dtype=np.uint8)
        if frame.size < width * height:
            raise RuntimeError("quadro de calibração incompleto")
        frame = frame[: width * height].reshape(height, width)
    mask = frame > 40
    cols = np.flatnonzero(mask.any(axis=0))
    rows = np.flatnonzero(mask.any(axis=1))
    if not cols.size:
        return {"fontsize": st.fontsize, "width": 0, "height": 0,
                "left": 0, "top": 0}
    return {
        "fontsize": st.fontsize,
        "width": int(cols[-1] - cols[0] + 1),
        "height": int(rows[-1] - rows[0] + 1),
        "left": int(cols[0]), "top": int(rows[0]),
    }


def calibrate_fontsize(target_px: int, sample: str, style: SubtitleStyle,
                       width: int, height: int, iterations: int = 8) -> dict:
    """Acha o fontsize que produz ``target_px`` de largura para ``sample``."""
    probe = measure_text_width(sample, style, width, height, fontsize=100)
    if probe["width"] <= 0:
        raise RuntimeError(
            f"a fonte '{style.font}' não foi encontrada pelo renderizador. "
            "Escolha outra fonte na aba de legendas."
        )
    guess = max(8, min(400, int(round(100 * target_px / probe["width"]))))
    history = [probe]
    best = probe
    lo, hi = 8, 400
    seen: set[int] = set()
    for _ in range(iterations):
        if guess in seen:
            break
        seen.add(guess)
        m = measure_text_width(sample, style, width, height, fontsize=guess)
        history.append(m)
        if abs(m["width"] - target_px) < abs(best["width"] - target_px):
            best = m
        if m["width"] == target_px:
            break
        if m["width"] < target_px:
            lo = guess
        else:
            hi = guess
        nxt = int(round(guess * target_px / max(m["width"], 1)))
        nxt = max(lo + 1, min(hi - 1, nxt)) if hi - lo > 1 else guess
        if nxt == guess:
            nxt = guess + (1 if m["width"] < target_px else -1)
        if not (8 <= nxt <= 400):
            break
        guess = nxt
    return {
        "fontsize": best["fontsize"],
        "measured_width": best["width"],
        "measured_height": best["height"],
        "target": target_px,
        "error_px": best["width"] - target_px,
        "sample": sample,
        "history": [{"fontsize": h["fontsize"], "width": h["width"]} for h in history],
    }


def align_label(align: int) -> str:
    return _ALIGN_NAMES.get(align, "inferior centro")
