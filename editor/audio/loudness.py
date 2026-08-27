"""Estágio anti-estouro e medição (Parte 9.1).

Gravação de celular tem crest factor alto. Normalizar direto para −14 LUFS
joga os picos acima de 0 dBFS e distorce. A cadeia certa comprime antes de
normalizar. Meta: zero amostras acima de −1 dBFS.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np

from ..config import FFMPEG, AudioParams

_EPS = 1e-9


@dataclass
class LoudnessReport:
    lufs: float = 0.0
    lra: float = 0.0
    true_peak_db: float = 0.0
    sample_peak_db: float = 0.0
    rms_db: float = 0.0
    crest_factor_db: float = 0.0
    samples_over_ceiling: int = 0
    ceiling_db: float = -1.0
    total_samples: int = 0

    def to_dict(self) -> dict:
        d = {k: (round(v, 2) if isinstance(v, float) else v)
             for k, v in asdict(self).items()}
        d["clipping"] = self.samples_over_ceiling > 0
        return d


def _db(x: float) -> float:
    return 20.0 * np.log10(max(float(x), _EPS))


def measure_samples(samples: np.ndarray, ceiling_db: float = -1.0) -> LoudnessReport:
    s = np.asarray(samples, dtype=np.float32)
    if not s.size:
        return LoudnessReport(ceiling_db=ceiling_db)
    peak = float(np.abs(s).max())
    rms = float(np.sqrt(np.mean(s.astype(np.float64) ** 2)))
    limit = 10 ** (ceiling_db / 20.0)
    return LoudnessReport(
        sample_peak_db=_db(peak),
        rms_db=_db(rms),
        crest_factor_db=_db(peak) - _db(rms),
        samples_over_ceiling=int(np.count_nonzero(np.abs(s) > limit)),
        ceiling_db=ceiling_db,
        total_samples=int(s.size),
    )


_EBU_KEYS = {
    "I:": "lufs", "LRA:": "lra", "Peak:": "true_peak_db",
}


def measure_file(path: str | Path, ceiling_db: float = -1.0,
                 filters: str | None = None) -> LoudnessReport:
    """LUFS/LRA/true peak pelo ebur128 + pico e crest factor pelo PCM."""
    from ..ffmpeg_utils import decode_pcm

    af = "ebur128=peak=true" if not filters else f"{filters},ebur128=peak=true"
    proc = subprocess.run(
        [FFMPEG, "-v", "info", "-nostdin", "-i", str(path), "-af", af,
         "-f", "null", "-"],
        capture_output=True,
    )
    text = proc.stderr.decode("utf-8", "replace")
    report = measure_samples(decode_pcm(path, filters=filters), ceiling_db)
    summary = text.rsplit("Summary:", 1)[-1]
    for line in summary.splitlines():
        line = line.strip()
        m = re.match(r"^I:\s*(-?[\d.]+)\s*LUFS", line)
        if m:
            report.lufs = float(m.group(1))
        m = re.match(r"^LRA:\s*(-?[\d.]+)\s*LU", line)
        if m:
            report.lra = float(m.group(1))
        m = re.match(r"^Peak:\s*(-?[\d.]+)\s*dBFS", line)
        if m:
            report.true_peak_db = float(m.group(1))
    if report.true_peak_db == 0.0:
        report.true_peak_db = report.sample_peak_db
    return report


def build_chain(params: AudioParams, include_denoise: bool = True) -> str:
    """A cadeia da Parte 9.1, na ordem: highpass -> compressor -> loudnorm."""
    stages: list[str] = []
    if include_denoise and params.denoise_enabled and params.denoise_chain:
        stages.append(params.denoise_chain)
    stages.append(f"highpass=f={params.highpass}")
    stages.append(
        f"acompressor=threshold={params.comp_threshold}dB:"
        f"ratio={params.comp_ratio}:attack={params.comp_attack}:"
        f"release={params.comp_release}:makeup={params.comp_makeup}:"
        f"knee={params.comp_knee}"
    )
    if params.presence_gain:
        stages.append(f"equalizer=f=4000:t=q:w=1.2:g={params.presence_gain}")
    if params.deesser:
        stages.append(f"deesser=i={min(max(params.deesser, 0.0), 1.0)}")
    stages.append(
        f"loudnorm=I={params.target_lufs}:TP={params.true_peak}:LRA={params.lra}"
    )
    return ",".join(stages)


def compare(before: LoudnessReport, after: LoudnessReport,
            target: AudioParams) -> dict:
    checks = [
        {"label": "LUFS integrado", "before": round(before.lufs, 2),
         "after": round(after.lufs, 2), "target": target.target_lufs,
         "ok": abs(after.lufs - target.target_lufs) <= 1.0},
        {"label": "Pico real (dBTP)", "before": round(before.true_peak_db, 2),
         "after": round(after.true_peak_db, 2), "target": target.true_peak,
         "ok": after.true_peak_db <= target.true_peak + 0.3},
        {"label": "Crest factor (dB)", "before": round(before.crest_factor_db, 2),
         "after": round(after.crest_factor_db, 2), "target": None, "ok": True},
        {"label": "Amostras acima de −1 dBFS", "before": before.samples_over_ceiling,
         "after": after.samples_over_ceiling, "target": 0,
         "ok": after.samples_over_ceiling == 0},
    ]
    return {"checks": checks, "ok": all(c["ok"] for c in checks)}
