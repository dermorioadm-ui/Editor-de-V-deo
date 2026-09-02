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
        best = None
        best_d = None
        # o silêncio mais próximo do alvo, em qualquer lugar razoável do
        # intervalo — cortar exatamente em `want` cairia no meio de uma
        # palavra, que sairia truncada nas DUAS transcrições
        for r in silences:
            mid = (r.start + r.end) / 2.0
            if not (cursor + target * 0.3 < mid < cursor + target * 1.8):
                continue
            d = abs(mid - want)
            if best_d is None or d < best_d:
                best, best_d = mid, d
        cursor = best if best is not None else want
        bounds.append(cursor)
    bounds.append(duration)
    return list(zip(bounds[:-1], bounds[1:]))


# O SILÊNCIO NÃO PRECISA SER TRANSCRITO. Numa gravação de anúncio o take bruto
# é metade pausa: ele erra, para, respira, refaz. O corte de silêncio é
# decidido pelo ENVELOPE, não pela transcrição, então mandar o buraco para o
# Whisper é pagar caro por nada.
#
# Mas NÃO se resolve fatiando em blocos pequenos. O Whisper processa em
# janelas de 30 s: trinta blocos de 3 s custam trinta janelas, ou seja, PIOR
# que um bloco corrido. O jeito certo é COMPACTAR — tirar o miolo de cada
# pausa longa, colar a fala num áudio contínuo e guardar o mapa de tempo para
# devolver cada palavra ao instante real dela.
#
# De cada pausa longa fica um toco de PAUSA_MANTIDA: a fronteira de frase é
# informação para o modelo, e emendar fala em fala faria ele juntar duas
# frases numa. O que some é só o vazio no meio.
PULO_MIN = 0.9           # pausa menor que isto fica inteira
FOLGA_FALA = 0.25        # respiro preservado em volta da fala
# 0,6 s e não 0,3: abaixo disso o modelo passa a emendar duas frases numa só,
# porque a pausa deixa de ler como fim de frase. O que se ganha encurtando
# mais o toco é pouco; o que se perde é a segmentação.
PAUSA_MANTIDA = 0.60     # o toco de silêncio que fica na emenda


def compactar_fala(samples: "np.ndarray", duration: float,
                   silence: Iterable | None = None,
                   sr: int = 16000) -> tuple["np.ndarray", list[tuple[float, float]]]:
    """Áudio só com a fala + o mapa (início_compacto, início_real).

    Devolve o áudio compactado e uma lista de trechos; para converter um tempo
    do compacto para o real, ache o trecho e some a diferença.
    """
    buracos = [r for r in (silence or [])
               if (float(r.end) - FOLGA_FALA) - (float(r.start) + FOLGA_FALA)
               >= PULO_MIN + PAUSA_MANTIDA]
    if not buracos:
        return samples, [(0.0, 0.0)]
    pedacos: list["np.ndarray"] = []
    mapa: list[tuple[float, float]] = []
    silencio = np.zeros(int(PAUSA_MANTIDA * sr), dtype=np.float32)
    cursor = 0.0          # onde estamos no áudio REAL
    saida = 0.0           # onde estamos no áudio COMPACTO
    for r in sorted(buracos, key=lambda x: float(x.start)):
        fim = max(cursor, float(r.start) + FOLGA_FALA)
        if fim - cursor > 0.02:
            mapa.append((saida, cursor))
            trecho = samples[int(cursor * sr):int(fim * sr)]
            pedacos.append(trecho)
            saida += len(trecho) / sr
        pedacos.append(silencio)
        saida += PAUSA_MANTIDA
        cursor = max(cursor, float(r.end) - FOLGA_FALA)
    if duration - cursor > 0.02:
        mapa.append((saida, cursor))
        pedacos.append(samples[int(cursor * sr):])
    if not pedacos or not mapa:
        # sobrou só silêncio (ou nada mapeável): manda o áudio como está, em
        # vez de mandar uma colcha de tocos que o mapa não sabe desfazer
        return samples, [(0.0, 0.0)]
    return np.concatenate(pedacos), mapa


def tempo_real(t: float, mapa: list[tuple[float, float]]) -> float:
    """Converte um instante do áudio compactado para o instante real."""
    real = t
    for inicio_compacto, inicio_real in mapa:
        if t + 1e-9 >= inicio_compacto:
            real = inicio_real + (t - inicio_compacto)
        else:
            break
    return real


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

    silencios = list(silence or [])
    samples, mapa = compactar_fala(samples, duration, silencios)
    compacta = len(samples) / 16000.0
    if on_progress and duration > 0 and compacta < duration * 0.97:
        on_progress(0.02, f"tirei {duration - compacta:.0f} s de silêncio antes "
                          f"de transcrever: {compacta/60:.1f} min em vez de "
                          f"{duration/60:.1f} min")
    # As fronteiras de bloco precisam cair em SILÊNCIO — cortar em tempo fixo
    # parte uma palavra ao meio e ela sai truncada nas duas transcrições. Os
    # silêncios conhecidos estão no tempo REAL; o áudio agora é o compactado.
    # Em vez de inverter o mapa, recalcula-se o envelope sobre o áudio
    # compactado (numpy, centésimos de segundo): os tocos de PAUSA_MANTIDA e
    # as pausas curtas que ficaram inteiras aparecem como silêncio ali.
    if compacta > CHUNK_TARGET * 1.25:
        from .audio.envelope import compute_envelope
        silencios_compactos = compute_envelope(samples, 16000).all_silence_runs(0.3)
    else:
        silencios_compactos = []
    blocks = chunk_bounds(compacta, silencios_compactos)
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
        # Com entrada ndarray o faster-whisper devolve tempos RELATIVOS ao
        # bloco, sempre. Um timestamp alucinado além do fim do bloco é erro do
        # modelo, não sinal de tempo absoluto — é clampado, nunca muda o
        # offset (zerar o offset jogaria o bloco inteiro no começo do vídeo).
        # relativo ao bloco -> absoluto no COMPACTO -> absoluto no vídeo REAL
        for w in local_words:
            w["start"] = tempo_real(
                min(max(0.0, w["start"]), chunk_len) + start, mapa)
            w["end"] = tempo_real(
                min(max(0.0, w["end"]), chunk_len + 0.5) + start, mapa)
            if w["end"] < w["start"]:
                # a palavra caiu em cima de uma emenda: o fim voltou para antes
                # do começo porque os dois lados vieram de trechos diferentes
                w["end"] = w["start"] + 0.08
        for seg_item in local_segments:
            seg_item["start"] = tempo_real(
                min(max(0.0, seg_item["start"]), chunk_len) + start, mapa)
            seg_item["end"] = tempo_real(
                min(max(0.0, seg_item["end"]), chunk_len + 0.5) + start, mapa)
            if seg_item["end"] < seg_item["start"]:
                seg_item["end"] = seg_item["start"] + 0.1
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
