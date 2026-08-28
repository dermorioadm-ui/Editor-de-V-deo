"""Proxy de baixa resolução para a prévia.

O usuário grava 1080x1920 a 60 fps. O navegador decodifica isso quadro a quadro
enquanto a interface desenha, e a prévia engasga. É o mesmo problema que todo
editor tem, e a solução é a mesma que o CapCut e o Premiere usam: uma cópia
leve do arquivo FONTE, feita uma vez, usada só para tocar.

Não confundir com a "prévia 480p" que já existe: aquela RENDERIZA a edição
inteira e muda toda vez que se corta algo. O proxy é a fonte inteira, sem
edição nenhuma, e vale para sempre — a linha do tempo dele é idêntica à do
original, então a conversão saída -> fonte continua valendo sem mudar nada.

A EXPORTAÇÃO nunca toca no proxy. Ela lê o arquivo original, em qualidade
cheia. O proxy existe só para os olhos, enquanto se edita.
"""
from __future__ import annotations

from pathlib import Path

from ..config import FFMPEG
from ..ffmpeg_utils import probe, run_with_progress

LADO_MAIOR = 854         # 480p na vertical: 480x854
FPS = 30
CRF = 30
GOP = 15                 # keyframe a cada 0,5 s: scrub rápido no navegador

# Abaixo disto o arquivo já é leve e o proxy só gastaria tempo e disco.
MIN_PIXELS = 640 * 1136
MIN_FPS = 31.0


def vale_a_pena(info) -> tuple[bool, str]:
    """Este arquivo precisa de proxy?"""
    try:
        w, h = info.display_size
    except Exception:  # noqa: BLE001
        return False, "não deu para ler a resolução"
    fps = float(getattr(info, "fps", 0) or 0)
    if w * h > MIN_PIXELS or fps >= MIN_FPS:
        return True, f"{w}x{h} a {fps:.0f} fps pesa para o navegador tocar"
    return False, f"{w}x{h} a {fps:.0f} fps já toca liso; proxy seria desperdício"


def build_proxy(source: str | Path, dest: Path, duration: float,
                on_progress=None, cancel=None) -> dict:
    """Gera o proxy. Devolve o que saiu, para a interface poder conferir."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".parcial.mp4")
    args = [
        FFMPEG, "-y", "-v", "error", "-nostdin", "-i", str(source),
        # a caixa de 854 preserva a proporção e serve tanto vertical quanto
        # horizontal; o segundo scale garante dimensão par para o yuv420p
        "-vf", (f"scale={LADO_MAIOR}:{LADO_MAIOR}:force_original_aspect_ratio=decrease,"
                f"scale=trunc(iw/2)*2:trunc(ih/2)*2"),
        "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(CRF),
        "-g", str(GOP), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-ac", "1",
        "-movflags", "+faststart", str(tmp),
    ]
    run_with_progress(args, duration, on_progress=on_progress, cancel=cancel)
    tmp.replace(dest)
    info = probe(dest)
    return {
        "path": str(dest),
        "size_bytes": dest.stat().st_size,
        "width": info.width, "height": info.height,
        "duration": round(info.duration, 3),
        "fps": round(info.fps or 0, 2),
    }
