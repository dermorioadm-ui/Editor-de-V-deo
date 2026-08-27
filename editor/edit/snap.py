"""Encaixe de borda no vale de energia (Parte 3.1).

A parte mais importante do projeto. Toda borda de corte é movida para o vale
de energia mais próximo, medido no envelope — nunca no timestamp do Whisper.

Caso real que motivou a janela assimétrica do ``snap_end``: o Whisper marcou o
fim de "R$ 360" em 597,90 s enquanto a pessoa ainda dizia "reais" até 598,55 s.
Cortar em 597,90 decepava a palavra inteira.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np

from ..audio.envelope import Envelope, Region, _mask_runs

# janela do snap_start
START_BACK = 0.70
START_FWD = 0.25
START_INSET = 0.03          # entra 30 ms no fim do silêncio
START_FALLBACK = 0.12       # janela de mínima quando é fala contínua

# janela do snap_end — procura MUITO à frente de propósito
END_BACK = 0.12
END_FWD = 0.95
END_INSET = 0.05            # entra 50 ms no início do silêncio
END_FALLBACK_BACK = 0.08
END_FALLBACK_FWD = 0.30

MIN_VALLEY = 0.060          # trecho contíguo mínimo para valer como vale
NEIGHBOR_GUARD = 0.06       # nunca invadir mais que vizinha -/+ 0,06 s


@dataclass
class SnapResult:
    original: float
    time: float
    kind: str                # "start" | "end"
    found_valley: bool
    valley_start: float | None
    valley_end: float | None
    valley_duration: float | None
    energy_db: float
    clamped_by_neighbor: bool
    reason: str

    @property
    def moved(self) -> float:
        return self.time - self.original

    def to_dict(self) -> dict:
        d = asdict(self)
        d["moved"] = round(self.moved, 4)
        return d


def _runs_in_window(env: Envelope, t0: float, t1: float,
                    min_duration: float) -> list[Region]:
    """Vales dentro da janela, com as bordas estendidas até a extensão real.

    Um vale que encosta na borda da janela quase sempre continua além dela;
    usar a borda cortada faria o encaixe cair no meio do silêncio em vez de
    na transição.
    """
    i0, i1 = env.slice_indices(t0, t1)
    mask = env.db[i0:i1] < env.silence_threshold
    out: list[Region] = []
    total = len(env.db)
    for s, e in _mask_runs(mask):
        a, b = i0 + s, i0 + e
        while a > 0 and env.db[a - 1] < env.silence_threshold:
            a -= 1
        while b < total and env.db[b] < env.silence_threshold:
            b += 1
        region = Region(env.time(a), env.time(b))
        # o critério de 60 ms vale para o pedaço visível na janela
        if (e - s) * env.hop >= min_duration - 1e-9:
            out.append(region)
    return out


def snap_start(env: Envelope, t: float, prev_neighbor_end: float | None = None,
               guard: float = NEIGHBOR_GUARD) -> SnapResult:
    """Move o INÍCIO de um trecho preservado para o vale mais próximo."""
    w0, w1 = t - START_BACK, t + START_FWD
    runs = _runs_in_window(env, w0, w1, MIN_VALLEY)
    if runs:
        valley = runs[-1]            # o ÚLTIMO vale da janela
        cand = valley.end - START_INSET
        found = True
        reason = (f"vale de {valley.duration*1000:.0f} ms em "
                  f"{valley.start:.3f}–{valley.end:.3f} s; borda encaixada "
                  f"{START_INSET*1000:.0f} ms antes do fim do vale")
        v0, v1 = valley.start, valley.end
    else:
        cand = env.argmin_time(t - START_FALLBACK, t + START_FALLBACK)
        found = False
        reason = (f"fala contínua, sem vale de {MIN_VALLEY*1000:.0f} ms na janela; "
                  f"usado o ponto de mínima energia em ±{START_FALLBACK*1000:.0f} ms")
        v0 = v1 = None

    cand = float(np.clip(cand, max(0.0, w0), w1))
    clamped = False
    if prev_neighbor_end is not None:
        limit = prev_neighbor_end + guard
        if cand < limit:
            cand = min(max(limit, w0), w1)
            clamped = True
            reason += (f"; limitado pela palavra vizinha (não invade além de "
                       f"{limit:.3f} s)")
    cand = float(np.clip(cand, 0.0, env.duration))
    return SnapResult(
        original=round(t, 4), time=round(cand, 4), kind="start",
        found_valley=found,
        valley_start=round(v0, 4) if v0 is not None else None,
        valley_end=round(v1, 4) if v1 is not None else None,
        valley_duration=round(v1 - v0, 4) if v0 is not None else None,
        energy_db=round(env.value_at(cand), 2),
        clamped_by_neighbor=clamped, reason=reason,
    )


def snap_end(env: Envelope, t: float, next_neighbor_start: float | None = None,
             guard: float = NEIGHBOR_GUARD) -> SnapResult:
    """Move o FIM de um trecho preservado para o vale mais próximo.

    A janela vai até +0,95 s porque o Whisper fecha o token antes do fim real
    da fala com frequência.
    """
    w0, w1 = t - END_BACK, t + END_FWD
    runs = _runs_in_window(env, w0, w1, MIN_VALLEY)
    if runs:
        valley = runs[0]             # o PRIMEIRO vale da janela
        cand = valley.start + END_INSET
        found = True
        reason = (f"vale de {valley.duration*1000:.0f} ms em "
                  f"{valley.start:.3f}–{valley.end:.3f} s; borda encaixada "
                  f"{END_INSET*1000:.0f} ms depois do início do vale")
        v0, v1 = valley.start, valley.end
    else:
        cand = env.argmin_time(t - END_FALLBACK_BACK, t + END_FALLBACK_FWD)
        found = False
        reason = (f"fala contínua, sem vale de {MIN_VALLEY*1000:.0f} ms na janela; "
                  f"usado o ponto de mínima energia em "
                  f"−{END_FALLBACK_BACK*1000:.0f}/+{END_FALLBACK_FWD*1000:.0f} ms")
        v0 = v1 = None

    cand = float(np.clip(cand, w0, w1))
    clamped = False
    if next_neighbor_start is not None:
        limit = next_neighbor_start - guard
        if cand > limit:
            cand = max(min(limit, w1), w0)
            clamped = True
            reason += (f"; limitado pela palavra vizinha (não invade além de "
                       f"{limit:.3f} s)")
    cand = float(np.clip(cand, 0.0, env.duration))
    return SnapResult(
        original=round(t, 4), time=round(cand, 4), kind="end",
        found_valley=found,
        valley_start=round(v0, 4) if v0 is not None else None,
        valley_end=round(v1, 4) if v1 is not None else None,
        valley_duration=round(v1 - v0, 4) if v0 is not None else None,
        energy_db=round(env.value_at(cand), 2),
        clamped_by_neighbor=clamped, reason=reason,
    )


def snap_boundary(env: Envelope, t: float, radius: float = 0.70) -> float:
    """Encaixa uma fronteira de velocidade no ponto de menor energia (4.2).

    Não é corte: o áudio segue contínuo. Só evita trocar de andamento no meio
    de uma palavra, que é audível e péssimo.
    """
    return round(env.argmin_time(max(0.0, t - radius), min(env.duration, t + radius)), 4)
