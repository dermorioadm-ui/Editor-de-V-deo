"""Gera áudio/vídeo sintético com fala, pausas e palma — para testar de verdade."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

SR = 16000


def speech_burst(t: np.ndarray, f0: float = 150.0) -> np.ndarray:
    """Algo com formantes e envelope de sílaba — o suficiente pro envelope."""
    n = len(t)
    sig = np.zeros(n, dtype=np.float32)
    for harm, amp in ((1, 1.0), (2, 0.55), (3, 0.35), (5, 0.18), (8, 0.08)):
        sig += amp * np.sin(2 * np.pi * f0 * harm * t).astype(np.float32)
    syll = 0.5 + 0.5 * np.sin(2 * np.pi * 4.5 * t - np.pi / 2)
    ramp = np.minimum(np.linspace(0, 1, n) * 12, 1.0)
    ramp *= np.minimum(np.linspace(1, 0, n) * 12, 1.0)
    return (sig * syll * ramp / 2.4).astype(np.float32)


def build(words: list[tuple[float, float]], duration: float,
          claps: list[float] = (), noise: float = 0.0012,
          seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(duration * SR)
    out = rng.normal(0, noise, n).astype(np.float32)
    for a, b in words:
        i0, i1 = int(a * SR), int(b * SR)
        t = np.arange(i1 - i0) / SR
        out[i0:i1] += 0.32 * speech_burst(t)
    for c in claps:
        i0 = int(c * SR)
        length = int(0.12 * SR)
        t = np.arange(length) / SR
        env = np.exp(-t * 45)
        burst = rng.normal(0, 1, length).astype(np.float32) * env
        out[i0:i0 + length] += 0.95 * burst
    return np.clip(out, -1.0, 1.0)


def write_video(path: Path, samples: np.ndarray, duration: float,
                width: int = 1080, height: int = 1920, fps: int = 30) -> Path:
    from editor.ffmpeg_utils import write_wav
    from editor.config import FFMPEG

    wav = path.with_suffix(".wav")
    write_wav(wav, samples, SR)
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        FFMPEG, "-y", "-v", "error",
        "-f", "lavfi", "-i",
        f"testsrc2=size={width}x{height}:rate={fps}:duration={duration}",
        "-i", str(wav),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-shortest", str(path),
    ], check=True)
    return path
