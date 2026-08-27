"""Transcrição com faster-whisper (Parte 2.1).

Arquivos longos são transcritos em blocos, com as fronteiras caindo em
silêncio, para não estourar memória e para dar progresso real.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

from .config import (WHISPER_COMPUTE, WHISPER_DEVICE, WHISPER_LANGUAGE,
                     WHISPER_MODEL, WHISPER_MODEL_CPU, WHISPER_MODEL_GPU)

_model_lock = threading.Lock()
_model_cache: dict[tuple, object] = {}

CHUNK_TARGET = 480.0     # ~8 min por bloco
CHUNK_SLACK = 90.0       # margem para achar silêncio perto do alvo


@dataclass
class DeviceInfo:
    device: str
    compute_type: str
    detail: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def detect_device(preferred: str = WHISPER_DEVICE,
                  compute: str = WHISPER_COMPUTE) -> DeviceInfo:
    """GPU quando houver, CPU como fallback."""
    device = preferred
    detail = ""
    if preferred in ("", "auto"):
        device = "cpu"
        try:
            import ctranslate2  # type: ignore

            count = ctranslate2.get_cuda_device_count()
            if count > 0:
                device = "cuda"
                detail = f"{count} GPU(s) CUDA visível(is) para o CTranslate2"
            else:
                detail = "nenhuma GPU CUDA visível; rodando na CPU"
        except Exception as exc:  # noqa: BLE001
            detail = f"CTranslate2 não reportou GPU ({exc}); rodando na CPU"
    if not compute:
        compute = "float16" if device == "cuda" else "int8"
    return DeviceInfo(device=device, compute_type=compute, detail=detail)


def resolve_model(model_size: str, device: str) -> str:
    """``auto`` escolhe pelo hardware: large-v3 na GPU, turbo na CPU."""
    if model_size not in ("", "auto"):
        return model_size
    return WHISPER_MODEL_GPU if device == "cuda" else WHISPER_MODEL_CPU


def load_model(model_size: str = WHISPER_MODEL, device_info: DeviceInfo | None = None):
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as exc:  # pragma: no cover - depende do ambiente
        raise RuntimeError(
            "faster-whisper não está instalado. Rode:\n"
            "    pip install faster-whisper\n"
            "(veja o README, seção 'Instalação')"
        ) from exc

    info = device_info or detect_device()
    model_size = resolve_model(model_size, info.device)
    key = (model_size, info.device, info.compute_type)
    with _model_lock:
        if key not in _model_cache:
            try:
                _model_cache[key] = WhisperModel(
                    model_size, device=info.device, compute_type=info.compute_type
                )
            except Exception:
                if info.device == "cuda":
                    fallback = DeviceInfo("cpu", "int8", "falha ao abrir a GPU; CPU")
                    return load_model(model_size, fallback)
                raise
        return _model_cache[key]


def chunk_bounds(duration: float, silence: Iterable | None = None,
                 target: float = CHUNK_TARGET) -> list[tuple[float, float]]:
    """Fronteiras de bloco, empurradas para o silêncio mais próximo do alvo."""
    if duration <= target * 1.25:
        return [(0.0, duration)]
    silences = list(silence or [])
    bounds: list[float] = [0.0]
    cursor = 0.0
    while duration - cursor > target * 1.25:
        want = cursor + target
        best = want
        best_d = CHUNK_SLACK + 1
        for r in silences:
            mid = (r.start + r.end) / 2.0
            d = abs(mid - want)
            if d < best_d and mid > cursor + target * 0.3:
                best, best_d = mid, d
        cursor = best if best_d <= CHUNK_SLACK else want
        bounds.append(cursor)
    bounds.append(duration)
    return list(zip(bounds[:-1], bounds[1:]))


def transcribe(
    audio: np.ndarray | str | Path,
    duration: float,
    language: str = WHISPER_LANGUAGE,
    model_size: str = WHISPER_MODEL,
    silence: Iterable | None = None,
    on_progress: Callable[[float, str], None] | None = None,
    device_info: DeviceInfo | None = None,
    initial_prompt: str | None = None,
) -> dict:
    """Devolve ``{"words": [...], "segments": [...], "device": {...}}``.

    Cada palavra: ``{"start", "end", "text", "prob"}`` em segundos absolutos
    do arquivo original.
    """
    info = device_info or detect_device()
    model_size = resolve_model(model_size, info.device)
    if on_progress:
        on_progress(0.0, f"carregando o modelo {model_size} — na primeira vez "
                         f"ele é baixado (1 a 3 GB) e isso demora alguns "
                         f"minutos; das próximas ele já está no disco")
    model = load_model(model_size, info)
    if on_progress:
        on_progress(0.01, f"modelo {model_size} pronto ({info.device})")

    if isinstance(audio, (str, Path)):
        from .ffmpeg_utils import read_wav_mono

        samples, _sr = read_wav_mono(audio)
    else:
        samples = np.asarray(audio, dtype=np.float32)

    blocks = chunk_bounds(duration, silence)
    words: list[dict] = []
    segments: list[dict] = []

    for i, (start, end) in enumerate(blocks):
        if on_progress:
            on_progress(i / len(blocks),
                        f"transcrevendo bloco {i+1}/{len(blocks)} "
                        f"({start/60:.1f}–{end/60:.1f} min)")
        chunk = samples[int(start * 16000):int(end * 16000)]
        if chunk.size == 0:
            continue
        seg_iter, _info = model.transcribe(
            chunk,
            language=language,
            word_timestamps=True,
            vad_filter=False,
            beam_size=5,
            condition_on_previous_text=False,
            initial_prompt=initial_prompt,
        )
        chunk_len = len(chunk) / 16000.0
        local_words: list[dict] = []
        local_segments: list[dict] = []
        for seg in seg_iter:
            local_segments.append({
                "start": float(seg.start), "end": float(seg.end),
                "text": seg.text,
            })
            for w in (seg.words or []):
                local_words.append({
                    "start": float(w.start), "end": float(w.end),
                    "text": w.word, "prob": float(getattr(w, "probability", 1.0)),
                })
        # Algumas versões devolvem tempo absoluto, outras relativo ao bloco.
        # Detecta pelo maior tempo visto e corrige só quando é relativo.
        offset = start
        if local_words:
            last = max(w["end"] for w in local_words)
            if last > chunk_len + 1.0:
                offset = 0.0
        for w in local_words:
            w["start"] += offset
            w["end"] += offset
        for s in local_segments:
            s["start"] += offset
            s["end"] += offset
        words.extend(local_words)
        segments.extend(local_segments)

    if on_progress:
        on_progress(1.0, f"{len(words)} palavras transcritas")

    words = normalize_words(words)
    return {"words": words, "segments": segments, "device": info.to_dict(),
            "model": model_size, "language": language}


def normalize_words(words: list[dict]) -> list[dict]:
    """Ordena, limpa e garante que os tempos não se cruzem."""
    out = []
    for w in sorted(words, key=lambda x: (x["start"], x["end"])):
        text = str(w.get("text", "")).strip()
        if not text:
            continue
        start = float(w["start"])
        end = max(float(w["end"]), start + 0.01)
        if out and start < out[-1]["end"]:
            start = out[-1]["end"]
            end = max(end, start + 0.01)
        out.append({
            "i": len(out),
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
            "prob": round(float(w.get("prob", 1.0)), 3),
        })
    return out
