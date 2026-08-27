"""Gera um vídeo com fala real (espeak-ng) para testar o pipeline de ponta a ponta."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from editor.config import FFMPEG
from editor.ffmpeg_utils import decode_pcm, write_wav

SR = 16000
ESPEAK = shutil.which("espeak-ng") or shutil.which("espeak")


def say(text: str, speed: int = 155) -> np.ndarray:
    if not ESPEAK:
        raise RuntimeError("espeak-ng não instalado")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
        out = fh.name
    subprocess.run([ESPEAK, "-v", "pt-br", "-s", str(speed), "-w", out, text],
                   check=True, capture_output=True)
    pcm = decode_pcm(out, sample_rate=SR, channels=1)
    Path(out).unlink(missing_ok=True)
    return pcm.astype(np.float32) * 0.55


def clap(rng: np.random.Generator) -> np.ndarray:
    n = int(0.11 * SR)
    t = np.arange(n) / SR
    return (rng.normal(0, 1, n).astype(np.float32) * np.exp(-t * 42) * 0.95)


def build_track(sentences: list[tuple[str, float]], claps_after: set[int] = frozenset(),
                noise: float = 0.0012, seed: int = 5):
    """Devolve (samples, marcas) onde marcas descreve o que foi falado e quando."""
    rng = np.random.default_rng(seed)
    parts: list[np.ndarray] = []
    marks: list[dict] = []
    cursor = 0.0
    lead = np.zeros(int(0.5 * SR), dtype=np.float32)
    parts.append(lead)
    cursor += 0.5
    for i, (text, pause) in enumerate(sentences):
        pcm = say(text)
        parts.append(pcm)
        marks.append({"text": text, "start": cursor, "end": cursor + len(pcm) / SR})
        cursor += len(pcm) / SR
        if i in claps_after:
            gap = np.zeros(int(0.35 * SR), dtype=np.float32)
            parts.append(gap); cursor += 0.35
            c = clap(rng)
            parts.append(c)
            marks.append({"text": "[PALMA]", "start": cursor, "end": cursor + len(c) / SR,
                          "clap": True})
            cursor += len(c) / SR
        gap = np.zeros(int(pause * SR), dtype=np.float32)
        parts.append(gap)
        cursor += pause
    parts.append(np.zeros(int(0.6 * SR), dtype=np.float32))
    cursor += 0.6
    track = np.concatenate(parts)
    track = track + rng.normal(0, noise, len(track)).astype(np.float32)
    return np.clip(track, -1.0, 1.0), marks, cursor


def make_video(dest: Path, samples: np.ndarray, duration: float,
               width: int = 1080, height: int = 1920, fps: int = 30) -> Path:
    wav = dest.with_suffix(".wav")
    write_wav(wav, samples, SR)
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        FFMPEG, "-y", "-v", "error",
        "-f", "lavfi", "-i",
        f"testsrc2=size={width}x{height}:rate={fps}:duration={duration:.3f}",
        "-i", str(wav),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", str(dest),
    ], check=True)
    return dest
