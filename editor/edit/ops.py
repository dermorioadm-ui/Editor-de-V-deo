"""Edição manual — as mesmas regras de encaixe do corte automático.

Toda operação aqui devolve, junto com o resultado, PARA ONDE a borda foi
movida e POR QUÊ (Parte 6.2).
"""
from __future__ import annotations

from ..audio.envelope import Envelope
from ..config import CutParams
from ..models import Clip, RemovedRegion, new_id
from .snap import snap_end, snap_start
from .timeline import Timeline

EPS = 0.002


def _sorted(clips: list[Clip]) -> list[Clip]:
    return sorted(clips, key=lambda c: (c.source != "main", c.src_start))


def cut_source_range(clips: list[Clip], start: float, end: float,
                     source: str = "main") -> tuple[list[Clip], list[Clip]]:
    """Remove [start, end] da fonte. NUNCA estende um clipe por cima do buraco."""
    out: list[Clip] = []
    removed: list[Clip] = []
    for clip in clips:
        if clip.source != source or clip.kind == "photo":
            out.append(clip)
            continue
        a, b = clip.src_start, clip.src_end
        if end <= a + EPS or start >= b - EPS:
            out.append(clip)
            continue
        left = start - a
        right = b - end
        if left <= EPS and right <= EPS:
            removed.append(clip)
            continue
        if left > EPS:
            head = _clone(clip, a, min(start, b))
            head.cut_out = True
            head.snap_out = None
            out.append(head)
        if right > EPS:
            tail = _clone(clip, max(end, a), b)
            tail.cut_in = True
            tail.snap_in = None
            out.append(tail)
        removed.append(clip)
    return _sorted(out), removed


def _clone(clip: Clip, src_start: float, src_end: float) -> Clip:
    new = Clip(**{**clip.__dict__, "id": new_id("c_")})
    new.src_start = round(src_start, 4)
    new.src_end = round(src_end, 4)
    new.measured_duration = None
    return new


def delete_output_range(plan, env: Envelope, out_start: float, out_end: float,
                        params: CutParams, words: list[dict] | None = None) -> dict:
    """Deleta o trecho selecionado na timeline, encaixando as bordas no vale."""
    timeline = Timeline(plan.active_clips)
    a = timeline.to_source(out_start)
    b = timeline.to_source(max(out_end, out_start + 0.01))
    if not a or not b:
        return {"ok": False, "reason": "seleção fora da linha do tempo"}
    source, src_a = a
    _src_source, src_b = b
    if src_b <= src_a:
        src_a, src_b = src_b, src_a

    words = words or []
    prev_word = _word_before(words, src_a)
    next_word = _word_after(words, src_b)

    left = snap_end(env, src_a, guard=params.snap_neighbor_guard)
    right = snap_start(env, src_b, guard=params.snap_neighbor_guard)
    lo = round(left.time, 4)
    hi = round(right.time, 4)
    # o encaixe não pode comer as palavras preservadas dos dois lados
    if prev_word is not None:
        lo = max(lo, round(float(prev_word["end"]), 4))
    if next_word is not None:
        hi = min(hi, round(float(next_word["start"]), 4))
    if hi <= lo:
        hi = lo + 0.02
    plan.clips, _ = cut_source_range(plan.clips, lo, hi, source)
    plan.removed.append(RemovedRegion(start=lo, end=hi, reason="manual",
                                      detail="deletado na timeline"))
    return {
        "ok": True, "source": source, "start": lo, "end": hi,
        "snap_in": left.to_dict(), "snap_out": right.to_dict(),
        "explain": [
            f"borda esquerda: {src_a:.3f} s → {lo:.3f} s ({left.reason})",
            f"borda direita: {src_b:.3f} s → {hi:.3f} s ({right.reason})",
        ],
    }


def remove_words(plan, env: Envelope, words: list[dict], word_ids: list[int],
                 params: CutParams) -> dict:
    """Apaga o vídeo correspondente às palavras selecionadas (Parte 6.3)."""
    ids = sorted(set(int(i) for i in word_ids))
    if not ids:
        return {"ok": False, "reason": "nenhuma palavra selecionada"}
    groups: list[list[int]] = []
    for i in ids:
        if groups and i == groups[-1][-1] + 1:
            groups[-1].append(i)
        else:
            groups.append([i])

    applied = []
    for group in groups:
        first, last = group[0], group[-1]
        prev_word = words[first - 1] if first > 0 else None
        next_word = words[last + 1] if last + 1 < len(words) else None
        left = snap_end(env, words[first]["start"] - 0.001,
                        next_neighbor_start=None, guard=params.snap_neighbor_guard)
        right = snap_start(env, words[last]["end"] + 0.001,
                           prev_neighbor_end=None, guard=params.snap_neighbor_guard)
        lo = min(left.time, words[first]["start"])
        hi = max(right.time, words[last]["end"])
        if prev_word:
            lo = max(lo, prev_word["end"] + params.snap_neighbor_guard)
        if next_word:
            hi = min(hi, next_word["start"] - params.snap_neighbor_guard)
        if hi - lo < 0.02:
            applied.append({"words": group, "ok": False,
                            "reason": "não sobra espaço entre as palavras vizinhas "
                                      "— remover aqui quebraria a vizinha"})
            continue
        plan.clips, _ = cut_source_range(plan.clips, round(lo, 4), round(hi, 4))
        plan.removed.append(RemovedRegion(
            start=round(lo, 4), end=round(hi, 4), reason="texto",
            detail=" ".join(words[i]["text"] for i in group)))
        applied.append({"words": group, "ok": True,
                        "start": round(lo, 4), "end": round(hi, 4),
                        "explain": f"{left.reason}; {right.reason}"})
    return {"ok": any(a["ok"] for a in applied), "applied": applied}


def restore_range(plan, start: float, end: float, source: str = "main",
                  speed: float | None = None, section: str = "explicacao") -> dict:
    """Recupera um trecho removido, inserindo-o na posição certa da fonte."""
    start, end = round(min(start, end), 4), round(max(start, end), 4)
    if end - start < 0.02:
        return {"ok": False, "reason": "trecho curto demais"}
    existing = [c for c in plan.clips if c.source == source and c.kind != "photo"]
    for c in existing:
        if c.src_start < end and c.src_end > start:
            start = min(start, c.src_start)
            end = max(end, c.src_end)
    keep = [c for c in plan.clips
            if not (c.source == source and c.kind != "photo"
                    and c.src_start < end and c.src_end > start)]
    neighbour = min(existing, key=lambda c: abs(c.src_start - start), default=None)
    clip = Clip(source=source, src_start=start, src_end=end,
                speed=speed if speed is not None else (neighbour.speed if neighbour else 1.0),
                section=neighbour.section if neighbour else section)
    keep.append(clip)
    plan.clips = _sorted(keep)
    plan.removed = [r for r in plan.removed
                    if not (r.start >= start - 0.01 and r.end <= end + 0.01)]
    return {"ok": True, "clip": clip.to_dict()}


def split_clip(plan, clip_id: str, out_time: float) -> dict:
    """Divide um bloco. As duas metades ficam contíguas — não é corte."""
    timeline = Timeline(plan.active_clips)
    target = next((p for p in timeline if p.clip.id == clip_id), None)
    if not target:
        return {"ok": False, "reason": "bloco não encontrado"}
    if not (target.out_start + 0.15 < out_time < target.out_end - 0.15):
        return {"ok": False, "reason": "ponto de divisão muito perto da borda"}
    clip = target.clip
    scale = target.scale or 1.0
    src_time = clip.src_start + (out_time - target.out_start) / scale
    idx = plan.clips.index(clip)
    left = _clone(clip, clip.src_start, src_time)
    right = _clone(clip, src_time, clip.src_end)
    left.cut_out = False
    right.cut_in = False
    left.snap_out = None
    right.snap_in = None
    plan.clips[idx:idx + 1] = [left, right]
    return {"ok": True, "left": left.to_dict(), "right": right.to_dict(),
            "src_time": round(src_time, 4)}


def merge_clips(plan, clip_ids: list[str]) -> dict:
    """Funde blocos — só quando são CONTÍGUOS na fonte.

    Fundir por cima de um corte restauraria o trecho removido. Esse é o bug da
    Parte 3.4 e não pode acontecer nem por pedido do usuário.
    """
    chosen = [c for c in plan.clips if c.id in set(clip_ids)]
    if len(chosen) < 2:
        return {"ok": False, "reason": "selecione dois blocos ou mais"}
    chosen.sort(key=lambda c: c.src_start)
    for a, b in zip(chosen, chosen[1:]):
        if a.source != b.source or abs(a.src_end - b.src_start) > EPS:
            return {
                "ok": False,
                "reason": ("esses blocos não são contíguos na fonte: fundir "
                           f"restauraria {b.src_start - a.src_end:.2f} s que "
                           "foram removidos. Recupere o trecho antes, se quiser."),
            }
    merged = _clone(chosen[0], chosen[0].src_start, chosen[-1].src_end)
    merged.cut_in = chosen[0].cut_in
    merged.cut_out = chosen[-1].cut_out
    merged.snap_in = chosen[0].snap_in
    merged.snap_out = chosen[-1].snap_out
    idx = min(plan.clips.index(c) for c in chosen)
    plan.clips = [c for c in plan.clips if c not in chosen]
    plan.clips.insert(idx, merged)
    return {"ok": True, "clip": merged.to_dict()}


def set_speed(plan, clip_id: str, speed: float, env: Envelope | None = None,
              snap_boundaries: bool = True) -> dict:
    from .snap import snap_boundary

    clips = plan.clips
    target = next((c for c in clips if c.id == clip_id), None)
    if not target:
        return {"ok": False, "reason": "bloco não encontrado"}
    old = target.speed
    target.speed = round(float(speed), 2)
    target.measured_duration = None
    moved = []
    if env is not None and snap_boundaries:
        i = clips.index(target)
        for j, other in ((i - 1, clips[i - 1] if i > 0 else None),
                         (i + 1, clips[i + 1] if i + 1 < len(clips) else None)):
            if other is None or other.source != target.source:
                continue
            left, right = (other, target) if j < i else (target, other)
            if abs(left.src_end - right.src_start) > EPS:
                continue      # é corte de verdade, não fronteira de velocidade
            if abs(left.speed - right.speed) < 1e-6:
                continue
            snapped = snap_boundary(env, left.src_end, radius=0.70)
            lo = left.src_start + 0.20
            hi = right.src_end - 0.20
            if hi > lo:
                snapped = max(lo, min(hi, snapped))
                moved.append({"from": left.src_end, "to": snapped})
                left.src_end = right.src_start = round(snapped, 4)
                left.measured_duration = right.measured_duration = None
    return {"ok": True, "clip": target.to_dict(), "old_speed": old,
            "boundaries_moved": moved}


def _word_before(words: list[dict], t: float) -> dict | None:
    prev = None
    for w in words:
        if w["end"] <= t:
            prev = w
        else:
            break
    return prev


def _word_after(words: list[dict], t: float) -> dict | None:
    for w in words:
        if w["start"] >= t:
            return w
    return None
