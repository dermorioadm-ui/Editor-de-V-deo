"""Autoteste: prova que a máquina consegue analisar, cortar e exportar.

Roda sem internet e sem o modelo de transcrição — gera um vídeo sintético com
fala simulada, palma e pausas, monta o plano, exporta e confere o resultado.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np

from .audio.clap import build_discarded_takes, detect_claps
from .audio.envelope import compute_envelope
from .config import FFMPEG, CutParams, SpeedParams
from .edit.audit import audit_edges
from .edit.plan_builder import build_auto_plan
from .edit.timeline import Timeline  # noqa: F401  (usado pelo cues)
from .ffmpeg_utils import (extract_wav, probe, read_wav_mono, write_wav)
from .models import EditPlan
from .render.export import export_project
from .subtitles.linebreak import build_cues
from .subtitles.remap import remap_words

SR = 16000
FRASES = [
    ("Presta atenção nisso aqui porque muda tudo", 1.1),
    ("O problema é que você perde cliente todo dia", 1.2),
    ("Então eu montei um jeito de cortar sozinho", 1.0),
    ("São trezentos e quarenta e sete clientes", 1.1),
    ("E você tem garantia de trinta dias", 0.8),
]


def _burst(n: int, f0: float = 155.0) -> np.ndarray:
    t = np.arange(n) / SR
    sig = np.zeros(n, dtype=np.float32)
    for harm, amp in ((1, 1.0), (2, 0.55), (3, 0.35), (5, 0.18)):
        sig += amp * np.sin(2 * np.pi * f0 * harm * t).astype(np.float32)
    syll = 0.5 + 0.5 * np.sin(2 * np.pi * 4.2 * t - np.pi / 2)
    ramp = np.minimum(np.linspace(0, 1, n) * 14, 1.0)
    ramp *= np.minimum(np.linspace(1, 0, n) * 14, 1.0)
    return (sig * syll * ramp / 2.4).astype(np.float32)


def _build_source(tmp: Path, width: int = 1080, height: int = 1920,
                  fps: int = 30) -> tuple[Path, list[dict], float]:
    rng = np.random.default_rng(4)
    parts: list[np.ndarray] = [np.zeros(int(0.5 * SR), dtype=np.float32)]
    words: list[dict] = []
    cursor = 0.5
    for frase, pausa in FRASES:
        tokens = frase.split()
        for tok in tokens:
            dur = 0.13 + len(tok) * 0.048
            n = int(dur * SR)
            parts.append(_burst(n) * 0.34)
            words.append({"i": len(words), "start": round(cursor, 3),
                          "end": round(cursor + dur, 3), "text": tok, "prob": 0.95})
            cursor += dur
            gap = int(0.045 * SR)
            parts.append(np.zeros(gap, dtype=np.float32))
            cursor += 0.045
        parts.append(np.zeros(int(pausa * SR), dtype=np.float32))
        cursor += pausa
    # uma palma no fim, para exercitar a detecção
    clap_at = cursor + 0.2
    parts.append(np.zeros(int(0.2 * SR), dtype=np.float32))
    n = int(0.11 * SR)
    env_decay = np.exp(-np.arange(n) / SR * 42)
    parts.append((rng.normal(0, 1, n).astype(np.float32) * env_decay * 0.9))
    cursor = clap_at + 0.11
    parts.append(np.zeros(int(0.8 * SR), dtype=np.float32))
    cursor += 0.8

    track = np.concatenate(parts)
    track += rng.normal(0, 0.0012, len(track)).astype(np.float32)
    track = np.clip(track, -1.0, 1.0)
    wav = tmp / "autoteste.wav"
    write_wav(wav, track, SR)
    dest = tmp / "autoteste.mp4"
    subprocess.run([
        FFMPEG, "-y", "-v", "error",
        "-f", "lavfi", "-i",
        f"testsrc2=size={width}x{height}:rate={fps}:duration={cursor:.3f}",
        "-i", str(wav),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", str(dest),
    ], check=True)
    return dest, words, cursor


def run(verbose: bool = True) -> bool:
    def say(msg: str) -> None:
        if verbose:
            print(msg)

    tmp = Path(tempfile.mkdtemp(prefix="editor-autoteste-"))
    problemas: list[str] = []
    try:
        say(" 1/6  gerando um vídeo de teste 1080x1920 …")
        t0 = time.time()
        src, words, duracao = _build_source(tmp)
        info = probe(src)
        say(f"      {info.width}x{info.height} @ {info.fps:.0f} fps · "
            f"{info.duration:.1f} s · {(info.v_bitrate or info.bitrate)/1e6:.1f} Mbps")

        say(" 2/6  extraindo o áudio e medindo o envelope de energia …")
        extract_wav(src, tmp / "a.wav", 16000, 1)
        samples, sr = read_wav_mono(tmp / "a.wav")
        env = compute_envelope(samples, sr)
        say(f"      piso de ruído {env.noise_floor:.1f} dB · "
            f"limiar de silêncio {env.silence_threshold:.1f} dB")

        say(" 3/6  procurando palmas …")
        claps = detect_claps(samples, sr, env)
        takes = [t.to_dict() for t in build_discarded_takes(env, claps, words)]
        say(f"      {len(claps)} palma(s), {len(takes)} take(s) descartado(s)")
        if not claps:
            problemas.append("nenhuma palma detectada no vídeo de teste")

        say(" 4/6  propondo cortes com encaixe no vale de energia …")
        plano = build_auto_plan(words, env, CutParams(), SpeedParams(), takes)
        clips = plano["clips"]
        removidas = set(plano["removed_word_ids"])
        alertas = audit_edges(clips, env, words, removidas)
        partidas = [w["text"] for w in words if w["i"] not in removidas
                    and any(c.src_start < w["end"] - 0.02 and c.src_end > w["start"] + 0.02
                            for c in clips)
                    and not any(c.src_start - 1e-6 <= w["start"]
                                and w["end"] <= c.src_end + 1e-6 for c in clips)]
        say(f"      {len(clips)} blocos · {len(alertas)} alerta(s) de borda · "
            f"{len(partidas)} palavra(s) partida(s)")
        if alertas:
            problemas.append(f"{len(alertas)} borda(s) de corte caíram em cima de fala")
        if partidas:
            problemas.append(f"palavras partidas pelo corte: {partidas[:5]}")

        say(" 5/6  exportando (uma geração de encode) …")
        plan = EditPlan(clips=clips, removed=plano["removed"])
        sources = {"main": {"path": str(src), "info": info}}

        def cues(timeline: Timeline) -> list[dict]:
            vivas = [w for w in words if w["i"] not in removidas]
            return build_cues(remap_words(vivas, timeline), plan.style,
                              limit=timeline.duration)

        dest = tmp / "saida.mp4"
        resultado = export_project(plan, sources, dest, tmp / "work", cues)
        say(f"      {resultado.duration:.2f} s · "
            f"{resultado.bitrate/1e6:.1f} Mbps · {len(resultado.segments)} trechos")

        say(" 6/6  conferindo o resultado …")
        final = probe(dest)
        deriva_ms = abs(resultado.drift) * 1000
        say(f"      deriva entre vídeo e áudio: {deriva_ms:.1f} ms")
        say(f"      resolução preservada: {final.width}x{final.height} "
            f"(fonte {info.width}x{info.height})")
        say(f"      bitrate: {resultado.bitrate/1e6:.1f} Mbps "
            f"(fonte {resultado.source_bitrate/1e6:.1f} Mbps)")
        if deriva_ms > 60:
            problemas.append(f"deriva de {deriva_ms:.0f} ms entre vídeo e áudio")
        if (final.width, final.height) != (info.width, info.height):
            problemas.append("a resolução mudou na exportação")
        if resultado.source_bitrate and resultado.bitrate < resultado.source_bitrate * 0.6:
            problemas.append("o bitrate caiu mais de 40% em relação à fonte")

        say(f"\n      tempo total: {time.time() - t0:.1f} s")
        if problemas:
            print("\n  RESULTADO: encontrei problemas\n")
            for p in problemas:
                print(f"    - {p}")
            return False
        print("\n  RESULTADO: tudo funcionando. "
              "Sua máquina consegue analisar, cortar e exportar.\n")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"\n  RESULTADO: o autoteste falhou\n\n    {type(exc).__name__}: {exc}\n")
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
