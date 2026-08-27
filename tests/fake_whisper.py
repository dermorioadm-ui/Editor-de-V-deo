"""Modelo falso do Whisper para testar o pipeline sem baixar pesos.

Usa o envelope do áudio REAL para achar as regiões de fala e distribui o texto
conhecido dentro delas — as bordas ficam onde a fala está de verdade, que é o
que o resto do pipeline consome.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from editor.audio.envelope import compute_envelope


@dataclass
class FakeWord:
    start: float
    end: float
    word: str
    probability: float = 0.93


@dataclass
class FakeSegment:
    start: float
    end: float
    text: str
    words: list


class FakeModel:
    def __init__(self, sentences: list[str], jitter: float = 0.06):
        self.sentences = sentences
        self.jitter = jitter          # simula o erro de +-80 ms do Whisper

    def transcribe(self, audio, **kw):
        sr = 16000
        env = compute_envelope(np.asarray(audio, dtype=np.float32), sr)
        regions = [r for r in env.speech_runs(0.25)]
        # junta regiões separadas por menos de 0,45 s: são a mesma frase
        merged = []
        for r in regions:
            if merged and r.start - merged[-1].end < 0.45:
                merged[-1].end = r.end
            else:
                merged.append(r)
        rng = np.random.default_rng(11)
        segments = []
        for i, region in enumerate(merged):
            text = self.sentences[i] if i < len(self.sentences) else "trecho"
            tokens = text.split()
            total = sum(len(t) for t in tokens) or 1
            cursor = region.start
            words = []
            span = region.end - region.start
            for tok in tokens:
                dur = span * len(tok) / total
                a = cursor + rng.normal(0, self.jitter)
                b = cursor + dur + rng.normal(0, self.jitter)
                words.append(FakeWord(max(0.0, a), max(a + 0.03, b), " " + tok))
                cursor += dur
            segments.append(FakeSegment(region.start, region.end, text, words))
        return iter(segments), {"language": "pt"}


def install(sentences: list[str], jitter: float = 0.06) -> None:
    from editor import transcribe as T

    model = FakeModel(sentences, jitter)
    T.load_model = lambda *a, **k: model          # type: ignore[assignment]
