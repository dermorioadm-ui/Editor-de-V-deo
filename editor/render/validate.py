"""Validação automática depois de exportar (Parte 10.5).

É ela que diz se dá para publicar sem assistir o vídeo inteiro.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from pathlib import Path
from typing import Callable

from ..audio.loudness import measure_file
from ..config import AudioParams
from ..ffmpeg_utils import extract_wav, probe, read_wav_mono

SUBTITLE_TOLERANCE = 0.26      # desvio médio aceitável


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^\w]", "", text)


def compare_words(expected: list[str], actual: list[str]) -> dict:
    """Compara palavra a palavra e lista o que faltou ou saiu truncado."""
    e = [_norm(w) for w in expected]
    a = [_norm(w) for w in actual]
    sm = difflib.SequenceMatcher(None, e, a, autojunk=False)
    missing: list[dict] = []
    truncated: list[dict] = []
    extra: list[dict] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag in ("delete", "replace"):
            for k in range(i1, i2):
                # truncada = o que saiu é prefixo/sufixo do esperado
                cand = a[j1:j2]
                hit = next((c for c in cand
                            if c and (e[k].startswith(c) or e[k].endswith(c))
                            and c != e[k]), None)
                if hit:
                    truncated.append({"expected": expected[k], "heard": hit,
                                      "index": k})
                else:
                    missing.append({"expected": expected[k], "index": k})
        if tag in ("insert",):
            for k in range(j1, j2):
                extra.append({"heard": actual[k], "index": k})
    ratio = sm.ratio()
    return {
        "expected_count": len(expected),
        "actual_count": len(actual),
        "match_ratio": round(ratio, 4),
        "missing": missing[:80],
        "truncated": truncated[:80],
        "extra": extra[:40],
        "missing_count": len(missing),
        "truncated_count": len(truncated),
        "ok": not missing and not truncated,
    }


def subtitle_sync(cues: list[dict], actual_words: list[dict],
                  probes: int = 24) -> dict:
    """Mede o desvio das legendas contra o áudio real, por palavras-sonda."""
    if not cues or not actual_words:
        return {"available": False, "reason": "sem legendas ou sem transcrição"}
    index: dict[str, list[float]] = {}
    for w in actual_words:
        index.setdefault(_norm(w["text"]), []).append(float(w["start"]))

    samples: list[dict] = []
    step = max(1, len(cues) // max(probes, 1))
    for cue in cues[::step]:
        first = cue["text"].replace("\n", " ").split()
        if not first:
            continue
        # escolhe uma palavra longa e única dentro da legenda
        cand = sorted(first, key=lambda w: -len(_norm(w)))
        for word in cand[:3]:
            key = _norm(word)
            times = index.get(key, [])
            if len(key) < 4 or not times:
                continue
            best = min(times, key=lambda t: abs(t - float(cue["start"])))
            samples.append({"word": word, "subtitle": round(float(cue["start"]), 3),
                            "audio": round(best, 3),
                            "delta": round(best - float(cue["start"]), 3)})
            break
    if not samples:
        return {"available": False,
                "reason": "nenhuma palavra-sonda pôde ser casada"}
    deltas = [abs(s["delta"]) for s in samples]
    mean = sum(deltas) / len(deltas)
    worst = max(samples, key=lambda s: abs(s["delta"]))
    return {
        "available": True,
        "probes": samples[:24],
        "mean_abs_deviation": round(mean, 3),
        "max_abs_deviation": round(abs(worst["delta"]), 3),
        "worst": worst,
        "tolerance": SUBTITLE_TOLERANCE,
        "ok": mean <= SUBTITLE_TOLERANCE,
    }


def validate_export(
    output: Path,
    expected_words: list[str],
    cues: list[dict],
    audio_params: AudioParams,
    source_bitrate: int,
    expected_duration: float,
    transcriber: Callable[[Path], list[dict]] | None = None,
    on_progress: Callable[[float, str], None] | None = None,
    work: Path | None = None,
) -> dict:
    output = Path(output)
    work = Path(work or output.parent)
    report: dict = {"output": str(output)}

    if on_progress:
        on_progress(0.05, "conferindo o arquivo")
    info = probe(output)
    out_bitrate = info.v_bitrate or info.bitrate
    drop = (1 - out_bitrate / source_bitrate) * 100 if source_bitrate else 0.0
    report["container"] = {
        "duration": round(info.duration, 3),
        "expected_duration": round(expected_duration, 3),
        "duration_delta": round(info.duration - expected_duration, 3),
        "width": info.width, "height": info.height,
        "fps": round(info.fps, 3),
        "video_codec": info.v_codec, "audio_codec": info.a_codec,
        "video_bitrate": out_bitrate,
        "source_video_bitrate": source_bitrate,
        "bitrate_drop_percent": round(drop, 1),
        "ok": abs(info.duration - expected_duration) < 0.5 and drop <= 40.0,
        "warn_bitrate": drop > 40.0,
    }

    if on_progress:
        on_progress(0.2, "medindo loudness, pico e clipping")
    loud = measure_file(output, ceiling_db=-1.0)
    report["audio"] = {
        **loud.to_dict(),
        "target_lufs": audio_params.target_lufs,
        "target_true_peak": audio_params.true_peak,
        "ok": (abs(loud.lufs - audio_params.target_lufs) <= 1.5
               and loud.true_peak_db <= audio_params.true_peak + 0.3
               and loud.samples_over_ceiling == 0),
    }

    if transcriber is not None:
        if on_progress:
            on_progress(0.35, "transcrevendo o resultado para conferir palavra a palavra")
        actual = transcriber(output)
        report["words"] = compare_words(expected_words,
                                        [w["text"] for w in actual])
        if on_progress:
            on_progress(0.85, "medindo a sincronia das legendas")
        report["subtitles"] = subtitle_sync(cues, actual)
    else:
        report["words"] = {"available": False,
                           "reason": "faster-whisper não disponível para revalidar"}
        report["subtitles"] = {"available": False,
                               "reason": "sem transcrição do resultado"}

    checks = [report["container"]["ok"], report["audio"]["ok"]]
    if report["words"].get("ok") is not None and "ok" in report["words"]:
        checks.append(bool(report["words"]["ok"]))
    if report["subtitles"].get("available"):
        checks.append(bool(report["subtitles"]["ok"]))
    report["ok"] = all(checks)
    if on_progress:
        on_progress(1.0, "validação concluída")
    return report
