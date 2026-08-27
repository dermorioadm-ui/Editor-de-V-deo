"""Segmentação narrativa (Parte 2.5) e utilidades de pausa."""
from __future__ import annotations

from dataclasses import dataclass

from .envelope import Envelope, Region


@dataclass
class NarrativeSegment:
    index: int
    start: float
    end: float
    words: list

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def text(self) -> str:
        return " ".join(w["text"].strip() for w in self.words).strip()

    @property
    def wps(self) -> float:
        return len(self.words) / max(self.duration, 1e-6)


def split_narrative(words: list[dict], env: Envelope,
                    pause: float = 0.80) -> list[NarrativeSegment]:
    """Divide em blocos usando pausas longas como fronteira."""
    if not words:
        return []
    groups: list[list[dict]] = [[words[0]]]
    for prev, cur in zip(words, words[1:]):
        gap = cur["start"] - prev["end"]
        if gap >= pause:
            groups.append([cur])
        else:
            groups[-1].append(cur)
    segments = []
    for i, g in enumerate(groups):
        segments.append(NarrativeSegment(
            index=i, start=float(g[0]["start"]), end=float(g[-1]["end"]), words=g
        ))
    return segments


def pause_around(words: list[dict], index: int) -> tuple[float, float]:
    """Pausa (s) antes e depois da palavra ``index``."""
    before = words[index]["start"] - words[index - 1]["end"] if index > 0 else 999.0
    after = (words[index + 1]["start"] - words[index]["end"]
             if index < len(words) - 1 else 999.0)
    return float(before), float(after)


def long_pauses(words: list[dict], minimum: float) -> list[Region]:
    out = []
    for prev, cur in zip(words, words[1:]):
        gap = cur["start"] - prev["end"]
        if gap >= minimum:
            out.append(Region(float(prev["end"]), float(cur["start"])))
    return out
