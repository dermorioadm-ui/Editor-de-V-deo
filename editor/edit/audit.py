"""Auditoria automática de bordas (Parte 3.5).

Depois de montar o plano, toda borda de corte REAL é conferida contra o
envelope. Borda em cima de fala (acima de piso + 25 dB) vira alerta com
correção de um clique.

Ponto de subdivisão — onde o áudio continua e só muda a velocidade — não é
corte e não deve alarmar.
"""
from __future__ import annotations

from ..audio.envelope import Envelope
from ..models import Clip
from .snap import snap_end, snap_start


def audit_edges(clips: list[Clip], env: Envelope, words: list[dict],
                removed_ids: set[int] | None = None) -> list[dict]:
    removed_ids = removed_ids or set()
    issues: list[dict] = []
    active = [c for c in clips if c.enabled and c.source == "main"]

    for i, clip in enumerate(active):
        prev = active[i - 1] if i else None
        nxt = active[i + 1] if i + 1 < len(active) else None
        contiguous_in = bool(prev and prev.source == clip.source
                             and abs(prev.src_end - clip.src_start) < 0.002)
        contiguous_out = bool(nxt and nxt.source == clip.source
                              and abs(clip.src_end - nxt.src_start) < 0.002)

        if not contiguous_in and clip.src_start > 0.03:
            issues.extend(_check(env, clip, "in", clip.src_start, words, removed_ids))
        if not contiguous_out and clip.src_end < env.duration - 0.03:
            issues.extend(_check(env, clip, "out", clip.src_end, words, removed_ids))
    return issues


def _check(env: Envelope, clip: Clip, side: str, t: float, words: list[dict],
           removed_ids: set[int]) -> list[dict]:
    out: list[dict] = []
    level = env.value_at(t)
    if level > env.audit_threshold:
        fix = (snap_start(env, t) if side == "in" else snap_end(env, t))
        out.append({
            "clip_id": clip.id, "side": side, "time": round(t, 4),
            "severity": "alto",
            "level_db": round(level, 2),
            "threshold_db": round(env.audit_threshold, 2),
            "message": (f"borda de corte em {t:.2f} s está a "
                        f"{level:.1f} dB (limiar {env.audit_threshold:.1f} dB): "
                        f"está cortando fala"),
            "suggestion": fix.time,
            "suggestion_reason": fix.reason,
        })

    # checagem independente do envelope: a borda parte uma palavra preservada?
    for w in words:
        if w["i"] in removed_ids:
            continue
        if w["start"] + 0.02 < t < w["end"] - 0.02:
            out.append({
                "clip_id": clip.id, "side": side, "time": round(t, 4),
                "severity": "alto",
                "level_db": round(level, 2),
                "threshold_db": round(env.audit_threshold, 2),
                "message": (f'borda em {t:.2f} s cai dentro da palavra '
                            f'"{w["text"]}" ({w["start"]:.2f}–{w["end"]:.2f} s)'),
                "suggestion": (w["start"] - 0.05 if side == "in" else w["end"] + 0.05),
                "suggestion_reason": "mover a borda para fora da palavra",
                "word": w["text"],
            })
            break
    return out


def audit_summary(issues: list[dict]) -> dict:
    return {
        "total": len(issues),
        "high": sum(1 for i in issues if i.get("severity") == "alto"),
        "ok": not issues,
    }


def apply_fix(clips: list[Clip], issue: dict) -> bool:
    """Aplica a correção sugerida de um alerta (o clique único da interface)."""
    for clip in clips:
        if clip.id != issue.get("clip_id"):
            continue
        t = float(issue["suggestion"])
        if issue["side"] == "in":
            if t < clip.src_end - 0.05:
                clip.src_start = round(t, 4)
                return True
        else:
            if t > clip.src_start + 0.05:
                clip.src_end = round(t, 4)
                return True
    return False
