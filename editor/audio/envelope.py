"""Envelope de energia — a base de tudo (Parte 2.2 e 2.3).

Nada neste projeto decide corte por timestamp do Whisper. Quem decide é este
envelope. O Whisper erra +-80 ms nas bordas e cortar por ele decepa o ataque
das consoantes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np

HOP_SECONDS = 0.010          # janelas de 10 ms
SILENCE_OVER_FLOOR = 8.0     # limiar de silêncio = piso + 8 dB
SPEECH_OVER_FLOOR = 20.0     # limiar de fala   = piso + 20 dB
AUDIT_OVER_FLOOR = 25.0      # acima disso, uma borda de corte está em cima de fala
_EPS = 1e-7


@dataclass
class Region:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start

    def as_tuple(self) -> tuple[float, float]:
        return (self.start, self.end)


class Envelope:
    """Envelope em dB, uma amostra a cada ``hop`` segundos (pico da janela)."""

    def __init__(self, db: np.ndarray, hop: float = HOP_SECONDS,
                 sample_rate: int = 16000):
        self.db = np.asarray(db, dtype=np.float32)
        self.hop = hop
        self.sample_rate = sample_rate
        self.noise_floor = float(np.percentile(self.db, 2)) if self.db.size else -60.0
        self.silence_threshold = self.noise_floor + SILENCE_OVER_FLOOR
        self.speech_threshold = self.noise_floor + SPEECH_OVER_FLOOR
        self.audit_threshold = self.noise_floor + AUDIT_OVER_FLOOR
        self._silence_mask = self.db < self.silence_threshold

    # ---------------------------------------------------------------- básicos
    @property
    def duration(self) -> float:
        return len(self.db) * self.hop

    def index(self, t: float) -> int:
        return int(np.clip(round(t / self.hop), 0, max(0, len(self.db) - 1)))

    def time(self, i: int) -> float:
        return i * self.hop

    def value_at(self, t: float) -> float:
        if not len(self.db):
            return -90.0
        return float(self.db[self.index(t)])

    def slice_indices(self, t0: float, t1: float) -> tuple[int, int]:
        i0 = int(np.clip(np.floor(t0 / self.hop), 0, len(self.db)))
        i1 = int(np.clip(np.ceil(t1 / self.hop), 0, len(self.db)))
        if i1 <= i0:
            i1 = min(len(self.db), i0 + 1)
        return i0, i1

    # ------------------------------------------------------------- silêncio
    def silence_runs(self, t0: float, t1: float, min_duration: float = 0.0,
                     threshold: float | None = None) -> list[Region]:
        """Trechos contíguos abaixo do limiar dentro de [t0, t1]."""
        i0, i1 = self.slice_indices(t0, t1)
        if threshold is None:
            mask = self._silence_mask[i0:i1]
        else:
            mask = self.db[i0:i1] < threshold
        return [
            Region(self.time(i0 + s), self.time(i0 + e))
            for s, e in _mask_runs(mask)
            if (e - s) * self.hop >= min_duration - 1e-9
        ]

    def all_silence_runs(self, min_duration: float = 0.0) -> list[Region]:
        return self.silence_runs(0.0, self.duration, min_duration)

    def speech_runs(self, min_duration: float = 0.0) -> list[Region]:
        mask = self.db >= self.speech_threshold
        return [
            Region(self.time(s), self.time(e))
            for s, e in _mask_runs(mask)
            if (e - s) * self.hop >= min_duration - 1e-9
        ]

    def argmin_time(self, t0: float, t1: float) -> float:
        """Ponto de energia mínima na janela (usado quando não há vale)."""
        i0, i1 = self.slice_indices(t0, t1)
        window = self.db[i0:i1]
        if not window.size:
            return float(np.clip(t0, 0.0, self.duration))
        return self.time(i0 + int(np.argmin(window)))

    def min_db(self, t0: float, t1: float) -> float:
        i0, i1 = self.slice_indices(t0, t1)
        window = self.db[i0:i1]
        return float(window.min()) if window.size else -90.0

    def max_db(self, t0: float, t1: float) -> float:
        i0, i1 = self.slice_indices(t0, t1)
        window = self.db[i0:i1]
        return float(window.max()) if window.size else -90.0

    def median_db(self, t0: float, t1: float) -> float:
        i0, i1 = self.slice_indices(t0, t1)
        window = self.db[i0:i1]
        return float(np.median(window)) if window.size else -90.0

    def is_silent(self, t: float) -> bool:
        return self.value_at(t) < self.silence_threshold

    # ------------------------------------------------------------ interface
    def downsample(self, points: int = 4000) -> list[float]:
        """Versão leve pro desenho da forma de onda no navegador."""
        if not len(self.db):
            return []
        if len(self.db) <= points:
            return [round(float(v), 2) for v in self.db]
        edges = np.linspace(0, len(self.db), points + 1).astype(int)
        out = []
        for a, b in zip(edges[:-1], edges[1:]):
            chunk = self.db[a:max(b, a + 1)]
            out.append(round(float(chunk.max()), 2))
        return out

    def to_dict(self, points: int = 4000) -> dict:
        return {
            "hop": self.hop,
            "duration": self.duration,
            "noise_floor": round(self.noise_floor, 2),
            "silence_threshold": round(self.silence_threshold, 2),
            "speech_threshold": round(self.speech_threshold, 2),
            "audit_threshold": round(self.audit_threshold, 2),
            "points": self.downsample(points),
        }


def _mask_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Índices [start, end) dos trechos contíguos True."""
    if not mask.size:
        return []
    padded = np.concatenate(([False], mask.astype(bool), [False]))
    diff = np.diff(padded.astype(np.int8))
    starts = np.flatnonzero(diff == 1)
    ends = np.flatnonzero(diff == -1)
    return list(zip(starts.tolist(), ends.tolist()))


def compute_envelope(samples: np.ndarray, sample_rate: int,
                     hop: float = HOP_SECONDS) -> Envelope:
    """Pico absoluto por janela de ``hop`` segundos, convertido para dBFS."""
    samples = np.asarray(samples, dtype=np.float32)
    hop_n = max(1, int(round(sample_rate * hop)))
    n_frames = len(samples) // hop_n
    if n_frames == 0:
        return Envelope(np.array([-90.0], dtype=np.float32), hop, sample_rate)
    frames = samples[: n_frames * hop_n].reshape(n_frames, hop_n)
    peak = np.abs(frames).max(axis=1)
    db = 20.0 * np.log10(np.maximum(peak, _EPS))
    return Envelope(db.astype(np.float32), hop, sample_rate)


def rms_envelope(samples: np.ndarray, sample_rate: int,
                 hop: float = HOP_SECONDS) -> np.ndarray:
    samples = np.asarray(samples, dtype=np.float32)
    hop_n = max(1, int(round(sample_rate * hop)))
    n = len(samples) // hop_n
    if n == 0:
        return np.array([-90.0], dtype=np.float32)
    frames = samples[: n * hop_n].reshape(n, hop_n)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    return (20.0 * np.log10(np.maximum(rms, _EPS))).astype(np.float32)


def merge_regions(regions: Iterable[Region], gap: float = 0.0) -> list[Region]:
    ordered = sorted(regions, key=lambda r: r.start)
    out: list[Region] = []
    for r in ordered:
        if out and r.start - out[-1].end <= gap:
            out[-1] = Region(out[-1].start, max(out[-1].end, r.end))
        else:
            out.append(Region(r.start, r.end))
    return out
