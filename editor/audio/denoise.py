"""Limpeza de ruído — DESLIGADA POR PADRÃO (Parte 9.2).

Nunca aplicar sem pedido explícito e sem prévia A/B. Quando ligada, o
tratamento é DIRECIONADO: analisa o espectro do ruído e ataca o que está lá,
não um denoise genérico.
"""
from __future__ import annotations

import numpy as np

from .envelope import Envelope

FFT_SIZE = 4096
SIBILANCE_BAND = (5500.0, 9000.0)
VOICE_BAND = (300.0, 3000.0)


def _spectrum(samples: np.ndarray, sr: int, size: int = FFT_SIZE) -> tuple[np.ndarray, np.ndarray]:
    n = len(samples) // size
    if n == 0:
        return np.array([]), np.array([])
    frames = samples[: n * size].reshape(n, size) * np.hanning(size)
    mag = np.abs(np.fft.rfft(frames, axis=1))
    freqs = np.fft.rfftfreq(size, 1.0 / sr)
    return freqs, mag


def noise_profile(samples: np.ndarray, sr: int, env: Envelope) -> dict:
    """Espectro médio das regiões mais silenciosas — o ruído de fundo."""
    quiet = env.all_silence_runs(0.20)
    chunks = []
    for r in quiet[:60]:
        a, b = int(r.start * sr), int(min(r.end, r.start + 1.5) * sr)
        if b - a >= FFT_SIZE:
            chunks.append(samples[a:b])
    if not chunks:
        return {"available": False,
                "reason": "não há trecho silencioso longo o bastante para "
                          "medir o ruído"}
    noise = np.concatenate(chunks)
    freqs, mag = _spectrum(noise, sr)
    if not freqs.size:
        return {"available": False, "reason": "trecho de ruído curto demais"}
    avg = mag.mean(axis=0)
    db = 20 * np.log10(np.maximum(avg, 1e-12))

    # picos: bandas que sobem sobre a mediana do espectro
    med = float(np.median(db[(freqs > 40) & (freqs < 12000)]))
    peaks = []
    band = (freqs > 40) & (freqs < 12000)
    idx = np.flatnonzero(band)
    for j in idx[1:-1]:
        if db[j] > db[j - 1] and db[j] >= db[j + 1] and db[j] - med > 8:
            peaks.append({"freq": float(freqs[j]), "db": float(db[j]),
                          "over_median": float(db[j] - med)})
    peaks.sort(key=lambda p: -p["over_median"])

    # agrupa picos vizinhos em ressonâncias
    resonances: list[dict] = []
    for p in peaks[:80]:
        merged = False
        for r in resonances:
            if abs(np.log2(p["freq"] / r["center"])) < 0.12:  # ~1/8 de oitava
                r["low"] = min(r["low"], p["freq"])
                r["high"] = max(r["high"], p["freq"])
                r["center"] = (r["low"] + r["high"]) / 2.0
                r["db"] = max(r["db"], p["db"])
                r["over_median"] = max(r["over_median"], p["over_median"])
                merged = True
                break
        if not merged:
            resonances.append({"center": p["freq"], "low": p["freq"],
                               "high": p["freq"], "db": p["db"],
                               "over_median": p["over_median"]})
    resonances = sorted(resonances, key=lambda r: -r["over_median"])[:6]

    hum = _detect_hum(freqs, db, med)
    return {
        "available": True,
        "median_db": round(med, 2),
        "resonances": [{k: (round(v, 2) if isinstance(v, float) else v)
                        for k, v in r.items()} for r in resonances],
        "hum": hum,
        "noise_seconds": round(len(noise) / sr, 2),
        "spectrum": _thumb(freqs, db),
    }


def _detect_hum(freqs: np.ndarray, db: np.ndarray, med: float) -> dict | None:
    """Hum de rede: 50 ou 60 Hz (às vezes deslocado) e seus harmônicos."""
    best = None
    for base in np.arange(48.0, 63.01, 0.5):
        score = 0.0
        for k in (1, 2, 3):
            f = base * k
            sel = (freqs > f - 2.0) & (freqs < f + 2.0)
            if sel.any():
                score += max(0.0, float(db[sel].max()) - med)
        if best is None or score > best[1]:
            best = (float(base), score)
    if best and best[1] > 14:
        return {"base_hz": round(best[0], 1), "score_db": round(best[1], 1)}
    return None


def _thumb(freqs: np.ndarray, db: np.ndarray, points: int = 160) -> list:
    band = (freqs >= 30) & (freqs <= 16000)
    f, d = freqs[band], db[band]
    if not f.size:
        return []
    edges = np.geomspace(f[0], f[-1], points + 1)
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        sel = (f >= a) & (f < b)
        if sel.any():
            out.append([round(float((a + b) / 2), 1), round(float(d[sel].max()), 1)])
    return out


def propose_chain(profile: dict, strength: float = 1.0) -> dict:
    """Tratamento direcionado ao que foi medido — não um denoise genérico."""
    if not profile.get("available"):
        return {"chain": "", "steps": [], "note": profile.get("reason", "")}
    steps: list[dict] = []
    parts: list[str] = []

    hum = profile.get("hum")
    if hum:
        base = hum["base_hz"]
        for k in (1, 2, 3):
            f = round(base * k, 1)
            if f > 400:
                break
            parts.append(f"equalizer=f={f}:t=q:w=12:g={-18 * strength:.1f}")
            steps.append({"type": "notch", "freq": f,
                          "detail": f"hum de rede em {f} Hz (base {base} Hz)"})

    for r in profile.get("resonances", [])[:3]:
        center = r["center"]
        if center < 60 or center > 9000:
            continue
        width = max(1.0, (r["high"] - r["low"]) / max(center, 1.0) * 8)
        gain = -min(12.0, max(3.0, r["over_median"] * 0.6)) * strength
        parts.append(f"equalizer=f={center:.0f}:t=q:w={width:.2f}:g={gain:.1f}")
        steps.append({"type": "bell", "freq": round(center, 1),
                      "gain_db": round(gain, 1),
                      "detail": f"ressonância em {r['low']:.0f}–{r['high']:.0f} Hz, "
                                f"{r['over_median']:.1f} dB acima da mediana"})

    # um afftdn suave por cima, só para o piso largo
    nr = 6 + 6 * strength
    parts.append(f"afftdn=nr={nr:.0f}:nf=-45:tn=1")
    steps.append({"type": "afftdn", "detail": f"redução de piso larga, nr={nr:.0f} dB"})
    return {"chain": ",".join(parts), "steps": steps, "note": ""}


def snr(samples: np.ndarray, env: Envelope) -> float:
    """Relação sinal-ruído: RMS dos quadros de fala sobre o dos mais quietos.

    Trabalha com máscaras de quadro em vez de "regiões de fala" porque um
    ruído forte levanta o piso e faria o limiar de fala nunca ser atingido —
    justamente o caso em que a medida importa.
    """
    db = env.db
    if not db.size:
        return 0.0
    hop = max(1, int(round(env.sample_rate * env.hop)))
    n = min(len(db), len(samples) // hop)
    if n < 10:
        return 0.0
    frames = samples[: n * hop].reshape(n, hop).astype(np.float64)
    level = db[:n]

    speech_mask = level >= env.noise_floor + 15.0
    if speech_mask.sum() < max(5, int(0.03 * n)):
        speech_mask = level >= np.percentile(level, 75)
    noise_mask = level < env.silence_threshold
    if noise_mask.sum() < max(5, int(0.02 * n)):
        noise_mask = level <= np.percentile(level, 10)
    if not speech_mask.any() or not noise_mask.any():
        return 0.0

    s_rms = np.sqrt(np.mean(frames[speech_mask] ** 2)) + 1e-12
    q_rms = np.sqrt(np.mean(frames[noise_mask] ** 2)) + 1e-12
    return float(20 * np.log10(s_rms / q_rms))


def sibilance(samples: np.ndarray, sr: int) -> float:
    """Razão 5,5–9 kHz sobre 300–3.000 Hz, percentil 99.

    Realce que deixa os "s" agressivos é pior que realce nenhum. Esta é a
    medida que diz se o de-esser precisa entrar.
    """
    freqs, mag = _spectrum(samples.astype(np.float32), sr, 2048)
    if not freqs.size:
        return 0.0
    hi = (freqs >= SIBILANCE_BAND[0]) & (freqs <= SIBILANCE_BAND[1])
    lo = (freqs >= VOICE_BAND[0]) & (freqs <= VOICE_BAND[1])
    if not hi.any() or not lo.any():
        return 0.0
    e_hi = (mag[:, hi] ** 2).sum(axis=1)
    e_lo = (mag[:, lo] ** 2).sum(axis=1) + 1e-12
    ratio = e_hi / e_lo
    return float(np.percentile(ratio, 99))
