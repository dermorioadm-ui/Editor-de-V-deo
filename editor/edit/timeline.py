"""Mapeamento entre a linha do tempo da FONTE e a de SAÍDA.

Quando as durações medidas do render existem, elas mandam. É isso que evita a
deriva de 0,9 s em 36 blocos que a conta ``duração ÷ velocidade`` acumula.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass

from ..models import Clip


@dataclass
class Placed:
    clip: Clip
    index: int
    out_start: float
    out_end: float

    @property
    def out_duration(self) -> float:
        return self.out_end - self.out_start

    @property
    def scale(self) -> float:
        """Fator real de compressão do tempo dentro deste clipe."""
        src = self.clip.src_duration
        return (self.out_duration / src) if src > 1e-9 else 0.0


class Timeline:
    """Linha do tempo de saída.

    Com ``fps``, a duração prevista de cada bloco é quantizada em quadros, que
    é o que o encoder produz de fato. Sem isso a previsão fica curta uns 14 ms
    por bloco e, em 200 blocos, a conta erra quase 3 s.
    """

    def __init__(self, clips: list[Clip], fps: float | None = None):
        self.placed: list[Placed] = []
        self.fps = fps if (fps and fps > 0) else None
        cursor = 0.0
        for i, clip in enumerate(clips):
            if not clip.enabled or clip.src_duration <= 1e-4:
                continue
            dur = clip.out_duration
            if clip.measured_duration is None and self.fps:
                frames = math.ceil(dur * self.fps - 1e-6)
                dur = max(1, frames) / self.fps
            self.placed.append(Placed(clip, i, cursor, cursor + dur))
            cursor += dur
        self.duration = cursor
        self._starts = [p.out_start for p in self.placed]

    def __len__(self) -> int:
        return len(self.placed)

    def __iter__(self):
        return iter(self.placed)

    # ------------------------------------------------------- fonte -> saída
    def to_output(self, src_time: float, source: str = "main") -> float | None:
        for p in self.placed:
            if p.clip.source != source:
                continue
            if p.clip.src_start - 1e-6 <= src_time <= p.clip.src_end + 1e-6:
                offset = (src_time - p.clip.src_start) * p.scale
                return p.out_start + max(0.0, min(offset, p.out_duration))
        return None

    def to_output_clamped(self, src_time: float, source: str = "main") -> float:
        """Como ``to_output``, mas empurra para a borda do clipe mais próximo.

        Serve para palavras cuja borda caiu dentro de um trecho removido.
        """
        exact = self.to_output(src_time, source)
        if exact is not None:
            return exact
        best, best_d = 0.0, float("inf")
        for p in self.placed:
            if p.clip.source != source:
                continue
            if src_time < p.clip.src_start:
                d, val = p.clip.src_start - src_time, p.out_start
            else:
                d, val = src_time - p.clip.src_end, p.out_end
            if d < best_d:
                best, best_d = val, d
        return best

    def covers(self, src_time: float, source: str = "main") -> bool:
        return self.to_output(src_time, source) is not None

    # ------------------------------------------------------- saída -> fonte
    def at(self, out_time: float) -> Placed | None:
        if not self.placed:
            return None
        i = bisect.bisect_right(self._starts, out_time) - 1
        i = max(0, min(i, len(self.placed) - 1))
        return self.placed[i]

    def to_source(self, out_time: float) -> tuple[str, float] | None:
        p = self.at(out_time)
        if p is None:
            return None
        scale = p.scale or 1.0
        src = p.clip.src_start + (out_time - p.out_start) / scale
        return p.clip.source, src

    # ------------------------------------------------------------- bordas
    def real_cut_edges(self) -> list[dict]:
        """Bordas com descontinuidade real (o que a auditoria 3.5 olha).

        Um ponto de subdivisão — onde o áudio continua e só muda a velocidade —
        não é corte e não deve alarmar.
        """
        edges: list[dict] = []
        for i, p in enumerate(self.placed):
            prev = self.placed[i - 1] if i else None
            nxt = self.placed[i + 1] if i + 1 < len(self.placed) else None
            contiguous_before = bool(
                prev and prev.clip.source == p.clip.source
                and abs(prev.clip.src_end - p.clip.src_start) < 0.002
            )
            contiguous_after = bool(
                nxt and nxt.clip.source == p.clip.source
                and abs(p.clip.src_end - nxt.clip.src_start) < 0.002
            )
            if not contiguous_before:
                edges.append({"clip_id": p.clip.id, "side": "in",
                              "src_time": p.clip.src_start,
                              "out_time": p.out_start,
                              "source": p.clip.source,
                              "is_cut": bool(prev is None or not contiguous_before)})
            if not contiguous_after:
                edges.append({"clip_id": p.clip.id, "side": "out",
                              "src_time": p.clip.src_end,
                              "out_time": p.out_end,
                              "source": p.clip.source,
                              "is_cut": True})
        return edges
