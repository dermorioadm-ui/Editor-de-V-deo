"""Análises sobre o vídeo (zona segura de legenda queimada — Parte 8)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from .config import FFMPEG
from .ffmpeg_utils import MediaInfo, probe

SAMPLE_WIDTH = 240


def detect_subtitle_band(path: str | Path, info: MediaInfo | None = None,
                         frames: int = 40) -> dict:
    """Acha a faixa que a legenda queimada ocupa.

    Procura linhas da metade inferior com muitos pixels quase brancos. Serve
    para impedir que um elemento seja posicionado em cima dela.
    """
    info = info or probe(path)
    w, h = info.display_size
    if not w or not h:
        return {"available": False, "reason": "vídeo sem dimensões"}
    sw = SAMPLE_WIDTH
    sh = max(2, int(round(h * sw / w)))
    sh -= sh % 2
    step = max(1, int(info.duration * (info.fps or 30) / max(frames, 1)))
    proc = subprocess.run(
        [FFMPEG, "-v", "error", "-nostdin", "-i", str(path),
         "-vf", f"select='not(mod(n\\,{step}))',scale={sw}:{sh}",
         "-vsync", "vfr", "-frames:v", str(frames),
         "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"],
        capture_output=True,
    )
    data = np.frombuffer(proc.stdout, dtype=np.uint8)
    n = len(data) // (sw * sh)
    if n == 0:
        return {"available": False, "reason": "não foi possível ler quadros"}
    stack = data[: n * sw * sh].reshape(n, sh, sw)
    bright = (stack > 205).mean(axis=2)          # fração de pixels quase brancos
    profile = bright.mean(axis=0)
    half = sh // 2
    lower = profile.copy()
    lower[:half] = 0.0
    threshold = max(0.045, float(lower.max()) * 0.35)
    rows = np.flatnonzero(lower >= threshold)
    if not rows.size:
        return {"available": True, "found": False,
                "profile": [round(float(v), 4) for v in profile],
                "message": "nenhuma faixa de legenda queimada detectada"}
    top = int(rows.min())
    bottom = int(rows.max())
    return {
        "available": True, "found": True,
        "top": round(top / sh, 4), "bottom": round((bottom + 1) / sh, 4),
        "top_px": int(round(top / sh * h)),
        "bottom_px": int(round((bottom + 1) / sh * h)),
        "coverage": round(float(lower[rows].mean()), 4),
        "profile": [round(float(v), 4) for v in profile],
        "message": (f"legenda queimada detectada entre {top/sh*100:.0f}% e "
                    f"{(bottom+1)/sh*100:.0f}% da altura — essa faixa fica bloqueada"),
    }


def suggest_anchor(band: dict, info: MediaInfo) -> dict:
    """Uma âncora única no topo, reaproveitada por todos os elementos.

    Consistência vale mais que variedade.
    """
    w, h = info.display_size
    y = 0.14
    if band.get("found") and band.get("top", 1.0) < 0.35:
        y = max(0.06, band["top"] - 0.12)
    return {"x": 0.5, "y": round(y, 4),
            "x_px": int(w / 2), "y_px": int(round(y * h)),
            "reason": "âncora única no topo, fora da faixa de legenda"}


def frame_jpeg(path: str | Path, time: float, filters: str = "",
               width: int = 360) -> tuple[bytes, float]:
    """Um quadro em JPEG (para comparação lado a lado) e o brilho médio dele.

    Se o instante pedido não existir — imagem parada, ou tempo além do fim —
    cai para o começo do arquivo em vez de falhar.
    """
    chain = ",".join(x for x in (filters, f"scale={width}:-2") if x)

    def shot(seek: float | None, out_args: list[str]) -> bytes:
        cmd = [FFMPEG, "-v", "error", "-nostdin"]
        if seek is not None:
            cmd += ["-ss", f"{max(0.0, seek):.3f}"]
        cmd += ["-i", str(path), "-vf", chain, "-frames:v", "1", *out_args, "pipe:1"]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0 or not proc.stdout:
            detail = proc.stderr.decode("utf-8", "replace").strip()[-300:]
            raise RuntimeError(detail or "o ffmpeg não devolveu quadro nenhum")
        return proc.stdout

    jpeg_args = ["-f", "image2", "-vcodec", "mjpeg", "-q:v", "3"]
    gray_args = ["-pix_fmt", "gray", "-f", "rawvideo"]
    try:
        data = shot(time, jpeg_args)
        raw = shot(time, gray_args)
    except RuntimeError:
        data = shot(None, jpeg_args)
        raw = shot(None, gray_args)
    arr = np.frombuffer(raw, dtype=np.uint8)
    return data, (float(arr.mean()) if arr.size else 0.0)
