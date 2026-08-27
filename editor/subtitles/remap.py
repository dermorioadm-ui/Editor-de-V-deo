"""Remapeamento das legendas para a linha do tempo final (Parte 5.1).

NÃO calcula posição por ``duração ÷ velocidade``. O ffmpeg encoda cada bloco
alguns milissegundos mais longo que o teórico e isso ACUMULA — 0,9 s de deriva
em 36 blocos. Aqui a linha do tempo é a soma das durações REAIS medidas.
"""
from __future__ import annotations

from ..edit.timeline import Timeline


def remap_words(words: list[dict], timeline: Timeline,
                source: str = "main") -> list[dict]:
    """Devolve só as palavras que sobreviveram, já em tempo de saída."""
    out: list[dict] = []
    for w in words:
        start = timeline.to_output(float(w["start"]), source)
        end = timeline.to_output(float(w["end"]), source)
        if start is None and end is None:
            continue
        if start is None:
            start = timeline.to_output_clamped(float(w["start"]), source)
        if end is None:
            end = timeline.to_output_clamped(float(w["end"]), source)
        if end - start < 0.02:
            end = start + 0.02
        item = dict(w)
        item["src_start"] = float(w["start"])
        item["src_end"] = float(w["end"])
        item["start"] = round(start, 3)
        item["end"] = round(end, 3)
        out.append(item)
    out.sort(key=lambda x: x["start"])
    for a, b in zip(out, out[1:]):
        if b["start"] < a["end"]:
            b["start"] = a["end"]
            b["end"] = max(b["end"], b["start"] + 0.02)
    return out


def coverage(words: list[dict], timeline: Timeline, source: str = "main") -> dict:
    kept = [w for w in words if timeline.covers(float(w["start"]), source)
            or timeline.covers(float(w["end"]), source)]
    return {"total": len(words), "kept": len(kept),
            "removed": len(words) - len(kept)}
