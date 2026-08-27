"""Confere a sincronia das legendas SEM depender de transcrever de novo.

Compara o início de cada legenda com a subida de energia mais próxima no áudio
exportado. Se o remapeamento derivasse, este número cresceria ao longo do vídeo.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

from editor.audio.envelope import compute_envelope
from editor.ffmpeg_utils import extract_wav, read_wav_mono


def check(output: str | Path, cues: list[dict]) -> dict:
    tmp = Path(tempfile.mkdtemp())
    extract_wav(output, tmp / "o.wav", 16000, 1)
    samples, sr = read_wav_mono(tmp / "o.wav")
    env = compute_envelope(samples, sr)
    onsets = [r.start for r in env.speech_runs(0.05)]
    if not onsets or not cues:
        return {"ok": False, "reason": "sem onsets ou sem legendas"}
    deltas, first_half, second_half = [], [], []
    mid = cues[len(cues) // 2]["start"]
    for c in cues:
        nearest = min(onsets, key=lambda o: abs(o - c["start"]))
        d = nearest - c["start"]
        deltas.append(d)
        (first_half if c["start"] < mid else second_half).append(abs(d))
    a = np.array([abs(d) for d in deltas])
    return {
        "cues": len(cues),
        "median_ms": round(float(np.median(a)) * 1000, 1),
        "p90_ms": round(float(np.percentile(a, 90)) * 1000, 1),
        "max_ms": round(float(a.max()) * 1000, 1),
        # se houvesse deriva, a segunda metade seria muito pior que a primeira
        "first_half_ms": round(float(np.mean(first_half or [0])) * 1000, 1),
        "second_half_ms": round(float(np.mean(second_half or [0])) * 1000, 1),
        "ok": float(np.median(a)) <= 0.26,
    }
