"""Monta o plano de corte automático (Partes 3 e 4).

Invariante que vale para o arquivo inteiro:
    dois clipes só podem ser fundidos quando são CONTÍGUOS na fonte
    (``a.src_end == b.src_start``).
É isso que impede o bug da Parte 3.4 — o vício de fala que voltava sozinho
porque um bloco curto era fundido com o vizinho estendendo o fim por cima do
corte, restaurando o trecho recém-removido.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..audio.envelope import Envelope
from ..audio.segments import split_narrative
from ..config import CutParams, SpeedParams
from ..models import Clip, RemovedRegion
from . import speed as speed_mod
from .snap import snap_boundary, snap_end, snap_start

MIN_GAP = 0.08          # abaixo disso não vale a pena cortar: vira contíguo
MIN_VALLEY_REPAIR = 0.04  # o resgate aceita um vale mais curto que o encaixe
CONTIGUOUS_EPS = 0.002


@dataclass
class Span:
    start: float
    end: float
    first_word: int
    last_word: int
    snap_in: dict | None = None
    snap_out: dict | None = None
    cut_in: bool = True
    cut_out: bool = True
    gap_has_removed_words: bool = False   # o corte à direita remove palavras?
    refused_cut: bool = False             # corte recusado por cair em cima de fala
    min_start: float = -1e9               # o ar nunca pode recuar além disto
    max_end: float = 1e9                  # nem avançar além disto (palavra removida)

    @property
    def duration(self) -> float:
        return self.end - self.start


def build_spans(words: list[dict], env: Envelope, params: CutParams,
                removed_ids: set[int]) -> list[Span]:
    """Agrupa palavras preservadas e encaixa cada borda no vale de energia."""
    kept = [w for w in words if w["i"] not in removed_ids]
    if not kept:
        return []

    import bisect

    removed_sorted = sorted(removed_ids)

    def removed_between(a: int, b: int) -> bool:
        k = bisect.bisect_right(removed_sorted, a)
        return k < len(removed_sorted) and removed_sorted[k] < b

    groups: list[list[dict]] = [[kept[0]]]
    removed_flags: list[bool] = []
    for prev, cur in zip(kept, kept[1:]):
        gap = cur["start"] - prev["end"]
        has_removed = removed_between(prev["i"], cur["i"])
        if gap >= params.silence_min or has_removed:
            groups.append([cur])
            removed_flags.append(has_removed)
        else:
            groups[-1].append(cur)
    removed_flags.append(False)

    spans: list[Span] = []
    for gi, group in enumerate(groups):
        first, last = group[0], group[-1]
        prev_word = words[first["i"] - 1] if first["i"] > 0 else None
        next_word = words[last["i"] + 1] if last["i"] + 1 < len(words) else None

        s_in = snap_start(env, first["start"],
                          prev_neighbor_end=prev_word["end"] if prev_word else None,
                          guard=params.snap_neighbor_guard)
        s_out = snap_end(env, last["end"],
                         next_neighbor_start=next_word["start"] if next_word else None,
                         guard=params.snap_neighbor_guard)

        start = s_in.time - params.margin          # folga da Parte 3.2
        end = s_out.time + params.margin

        # A folga só pode crescer DENTRO do vale onde a borda foi encaixada.
        # Sem esta trava a folga empurra a borda de volta para cima da fala —
        # o encaixe acerta o vale e a folga de 150 ms desfaz o acerto.
        if s_in.found_valley and s_in.valley_start is not None:
            start = max(start, s_in.valley_start + 0.02)
        if s_out.found_valley and s_out.valley_end is not None:
            end = min(end, s_out.valley_end - 0.02)

        # a folga não pode passar por cima de uma palavra vizinha removida
        min_start, max_end = -1e9, 1e9
        if prev_word is not None and prev_word["i"] in removed_ids:
            min_start = prev_word["end"] + params.snap_neighbor_guard
            start = max(start, min_start)
        if next_word is not None and next_word["i"] in removed_ids:
            max_end = next_word["start"] - params.snap_neighbor_guard
            end = min(end, max_end)

        start = max(0.0, start)
        end = min(env.duration, end)
        if end - start < 0.02:
            end = min(env.duration, start + 0.02)
        spans.append(Span(round(start, 4), round(end, 4), first["i"], last["i"],
                          s_in.to_dict(), s_out.to_dict(),
                          gap_has_removed_words=removed_flags[gi],
                          min_start=min_start, max_end=max_end))

    spans = _split_on_silence(spans, env, params)
    _resolve_overlaps(spans, env, params)
    return spans


def _split_on_silence(spans: list[Span], env: Envelope,
                      params: CutParams) -> list[Span]:
    """Parte qualquer span que ainda tenha um vale de silêncio dentro.

    O agrupamento acima nasce do BURACO ENTRE palavras. Quando o alinhamento
    do Whisper estica uma palavra por cima de uma pausa, não existe buraco —
    e o vale ia inteiro para o vídeo. Aqui quem manda é o ENVELOPE: se há
    silêncio de verdade dentro do trecho, ele é cortado, custe o que custar
    ao que as palavras dizem.
    """
    out: list[Span] = []
    for span in spans:
        runs = [r for r in env.silence_runs(span.start, span.end,
                                            min_duration=params.silence_min)
                if r.start > span.start + 0.05 and r.end < span.end - 0.05]
        if not runs:
            out.append(span)
            continue
        cursor = span.start
        for r in runs:
            esq = max(cursor, r.start + params.air)
            dir_ = min(span.end, r.end - params.air)
            if esq - cursor < MIN_GAP or dir_ - esq < MIN_GAP:
                continue
            pedaco = Span(round(cursor, 4), round(esq, 4),
                          span.first_word, span.last_word,
                          span.snap_in if not out or out[-1].end < cursor else None,
                          None, cut_in=span.cut_in, cut_out=True,
                          min_start=span.min_start, max_end=span.max_end)
            out.append(pedaco)
            cursor = dir_
        if span.end - cursor > MIN_GAP:
            out.append(Span(round(cursor, 4), round(span.end, 4),
                            span.first_word, span.last_word,
                            None, span.snap_out, cut_in=True,
                            cut_out=span.cut_out,
                            gap_has_removed_words=span.gap_has_removed_words,
                            min_start=span.min_start, max_end=span.max_end))
        elif out:
            out[-1].end = round(span.end, 4)
            out[-1].snap_out = span.snap_out
            out[-1].cut_out = span.cut_out
    return out


def _resolve_overlaps(spans: list[Span], env: Envelope, params: CutParams) -> None:
    """Resolve bordas que se cruzaram e aplica o ar da Parte 3.3.

    Nunca funde por cima de um corte: quando duas bordas se encontram, as duas
    passam a valer o mesmo instante, os clipes ficam contíguos e nada é
    restaurado — porque nada foi removido ali.
    """
    extra_air = max(0.0, params.air - params.margin)
    for a, b in zip(spans, spans[1:]):
        gap = b.start - a.end
        if gap <= 0:
            # bordas se cruzaram: um único ponto, no vale entre as duas
            lo = min(a.end, b.start)
            hi = max(a.end, b.start)
            point = snap_boundary(env, (lo + hi) / 2.0, radius=max(0.05, (hi - lo)))
            point = max(min(point, hi), lo)
            a.end = b.start = round(point, 4)
            a.cut_out = b.cut_in = a.gap_has_removed_words
            continue
        if gap < MIN_GAP and not a.gap_has_removed_words:
            # corte de 50 ms não vale o risco: mantém o áudio
            mid = round((a.end + b.start) / 2.0, 4)
            a.end = b.start = mid
            a.cut_out = b.cut_in = False
            continue
        room = max(0.0, (gap - MIN_GAP) / 2.0)
        add = min(extra_air, room)
        # o ar não pode reinvadir uma palavra removida pelo usuário: sem esta
        # trava, 40 ms do começo da palavra removida vazavam de volta como um
        # estalo na emenda (o clamp do build_spans era desfeito aqui)
        a.end = round(min(a.end + add, a.max_end), 4)
        b.start = round(max(b.start - add, b.min_start), 4)
        # O resgate é sempre a ÚLTIMA palavra sobre a borda: o ar da 3.3
        # também é capaz de empurrar uma borda encaixada de volta para cima da
        # fala, e nesse caso é o ar que cede.
        if not _repair_edges(a, b, env):
            mid = round((a.end + b.start) / 2.0, 4)
            a.end = b.start = mid
            a.cut_out = b.cut_in = False
            a.refused_cut = b.refused_cut = True


def _repair_edges(a: Span, b: Span, env: Envelope) -> bool:
    """Dá uma segunda chance a uma borda que caiu em cima de fala.

    Quando a borda ficou acima de piso + 25 dB, a pausa que o Whisper prometeu
    tem que estar em algum lugar do intervalo removido — procura ali e usa o
    MEIO do vale (a ponta de um vale de 40 ms já é fala de novo).

    Devolve False quando não dá para salvar: aí o corte não deve existir.
    Invariante: nenhuma borda de corte real sobra em cima de fala.
    """
    if a.gap_has_removed_words:
        return True         # remoção pedida explicitamente: respeita o pedido

    def bad(t: float) -> bool:
        return env.value_at(t) > env.audit_threshold

    if not bad(a.end) and not bad(b.start):
        return True

    lo, hi = a.end, b.start
    reach = 0.20
    valleys = env.silence_runs(lo - reach, hi + reach, MIN_VALLEY_REPAIR)
    if valleys:
        if bad(a.end):
            v = valleys[0]
            a.end = round(min(max((v.start + v.end) / 2.0, lo - reach), hi), 4)
        if bad(b.start):
            v = valleys[-1]
            b.start = round(max(min((v.start + v.end) / 2.0, hi + reach), a.end), 4)
    else:
        quietest = env.argmin_time(lo - reach, hi + reach)
        if bad(a.end):
            a.end = round(min(quietest, hi), 4)
        if bad(b.start):
            b.start = round(max(quietest, a.end), 4)

    if b.start < a.end:
        return False
    return not bad(a.end) and not bad(b.start)


def enforce_min_block(spans: list[Span], params: CutParams) -> tuple[list[Span], list[dict]]:
    """Bloco mínimo — sem NUNCA estender por cima de um corte (Parte 3.4)."""
    notes: list[dict] = []
    out: list[Span] = []
    for span in spans:
        if span.duration >= params.min_block or not out:
            out.append(span)
            continue
        prev = out[-1]
        if abs(prev.end - span.start) < CONTIGUOUS_EPS:
            # contíguo na fonte: fundir aqui não restaura nada
            prev.end = span.end
            prev.last_word = span.last_word
            prev.snap_out = span.snap_out
            prev.cut_out = span.cut_out
            prev.gap_has_removed_words = span.gap_has_removed_words
            notes.append({"type": "merge", "at": span.start,
                          "detail": "bloco curto fundido com o vizinho contíguo "
                                    "(sem gap no meio, nada foi restaurado)"})
            continue
        if params.short_block_policy == "drop":
            notes.append({"type": "drop", "at": span.start,
                          "duration": round(span.duration, 3),
                          "detail": f"bloco de {span.duration*1000:.0f} ms descartado "
                                    f"(abaixo do mínimo de {params.min_block:.2f} s)"})
            continue
        notes.append({"type": "keep", "at": span.start,
                      "duration": round(span.duration, 3),
                      "detail": f"bloco de {span.duration*1000:.0f} ms mantido como "
                                f"bloco próprio — estender por cima do corte "
                                f"restauraria o trecho removido"})
        out.append(span)
    return out, notes


def assign_speed(spans: list[Span], words: list[dict], env: Envelope,
                 cut: CutParams, sp: SpeedParams,
                 total_duration: float) -> list[Clip]:
    """Subdivide os spans nas fronteiras narrativas e dá velocidade a cada bloco."""
    segments = split_narrative(words, env, cut.narrative_pause)
    seg_info = []
    total = max(total_duration, 1e-6)
    for i, seg in enumerate(segments):
        position = seg.start / total
        section, conf = speed_mod.classify(
            seg.text, position, seg.wps, seg.duration, i == len(segments) - 1
        )
        seg_info.append({
            "start": seg.start, "end": seg.end, "section": section,
            "confidence": conf,
            "speed": speed_mod.suggest_speed(section, seg.wps, sp),
            "wps": round(seg.wps, 2),
        })

    def section_at(t: float) -> dict:
        for info in seg_info:
            if info["start"] - 0.001 <= t <= info["end"] + 0.001:
                return info
        best = min(seg_info, key=lambda s: min(abs(s["start"] - t), abs(s["end"] - t)),
                   default=None)
        return best or {"section": "explicacao", "speed": 1.0, "confidence": 0.0}

    clips: list[Clip] = []
    for span in spans:
        # pontos de subdivisão: fronteiras narrativas dentro do span
        inner = [s["start"] for s in seg_info
                 if span.start + 0.35 < s["start"] < span.end - 0.35]
        points = [span.start, *sorted(inner), span.end]

        raw: list[tuple[float, float, dict]] = []
        for a, b in zip(points[:-1], points[1:]):
            if b - a < 0.05:
                continue
            raw.append((a, b, section_at((a + b) / 2.0)))
        if not raw:
            raw = [(span.start, span.end, section_at((span.start + span.end) / 2.0))]

        # 4.2 — fronteira entre velocidades diferentes cai em silêncio
        for k in range(1, len(raw)):
            prev_a, prev_b, prev_info = raw[k - 1]
            cur_a, cur_b, cur_info = raw[k]
            if abs(prev_info["speed"] - cur_info["speed"]) < 1e-6:
                continue
            snapped = snap_boundary(env, cur_a, radius=0.70)
            lo = prev_a + 0.20
            hi = cur_b - 0.20
            if hi > lo:
                snapped = max(lo, min(hi, snapped))
                raw[k - 1] = (prev_a, snapped, prev_info)
                raw[k] = (snapped, cur_b, cur_info)

        for k, (a, b, info) in enumerate(raw):
            first = k == 0
            last = k == len(raw) - 1
            clips.append(Clip(
                source="main",
                src_start=round(a, 4),
                src_end=round(b, 4),
                speed=speed_mod.apply_global(info["speed"], sp),
                base_speed=round(float(info["speed"]), 4),
                section=info["section"],
                kind="speech",
                cut_in=span.cut_in if first else False,
                cut_out=span.cut_out if last else False,
                snap_in=span.snap_in if first else None,
                snap_out=span.snap_out if last else None,
                label="",
            ))
    return clips


def removed_regions(spans: list[Span], duration: float,
                    takes: list[dict] | None = None) -> list[RemovedRegion]:
    """Complemento dos spans — o que foi removido, para desenhar em vermelho."""
    regions: list[RemovedRegion] = []
    take_ranges = [(t["start"], t["end"]) for t in (takes or [])
                   if not t.get("restored")]

    def reason_for(a: float, b: float) -> tuple[str, str]:
        mid = (a + b) / 2.0
        for ts, te in take_ranges:
            if ts - 0.02 <= mid <= te + 0.02:
                return "palma", "take descartado pela palma"
        return "silencio", "pausa acima do limiar do preset"

    cursor = 0.0
    for span in spans:
        if span.start - cursor > 0.02:
            reason, detail = reason_for(cursor, span.start)
            regions.append(RemovedRegion(start=round(cursor, 4),
                                         end=round(span.start, 4),
                                         reason=reason, detail=detail))
        cursor = max(cursor, span.end)
    if duration - cursor > 0.02:
        reason, detail = reason_for(cursor, duration)
        regions.append(RemovedRegion(start=round(cursor, 4), end=round(duration, 4),
                                     reason=reason, detail=detail))
    return regions


def resync_removed(clips: list, previous: list, duration: float) -> list:
    """Refaz as regiões removidas a partir das bordas ATUAIS dos clipes.

    Mover (ou desfazer) uma borda muda o que sai do vídeo. Sem isto o vermelho
    da timeline — e o botão "recuperar trecho" — continuavam descrevendo o
    corte antigo. O motivo de cada região é herdado da região que ocupava
    aquele lugar antes.
    """
    main = sorted([c for c in clips if c.enabled and c.source == "main"],
                  key=lambda c: c.src_start)

    def carry(a: float, b: float) -> tuple[str, str]:
        mid = (a + b) / 2.0
        for r in previous:
            if r.start - 0.02 <= mid <= r.end + 0.02:
                return r.reason, r.detail
        return "silencio", "pausa acima do limiar do preset"

    out: list[RemovedRegion] = []
    cursor = 0.0
    for clip in main:
        if clip.src_start - cursor > 0.02:
            reason, detail = carry(cursor, clip.src_start)
            out.append(RemovedRegion(start=round(cursor, 4),
                                     end=round(clip.src_start, 4),
                                     reason=reason, detail=detail))
        cursor = max(cursor, clip.src_end)
    if duration - cursor > 0.02:
        reason, detail = carry(cursor, duration)
        out.append(RemovedRegion(start=round(cursor, 4), end=round(duration, 4),
                                 reason=reason, detail=detail))
    return out


def words_removed_by_takes(words: list[dict], takes: list[dict]) -> set[int]:
    ids: set[int] = set()
    for take in takes:
        if take.get("restored"):
            continue
        a, b = float(take["start"]), float(take["end"])
        for w in words:
            # Só remove a palavra quando MAIS DA METADE dela está dentro do
            # take. Um encosto de 10 ms na borda não pode levar a palavra
            # inteira embora.
            overlap = min(w["end"], b) - max(w["start"], a)
            if overlap > 0.5 * (w["end"] - w["start"]):
                ids.add(w["i"])
    return ids


def build_auto_plan(words: list[dict], env: Envelope, cut: CutParams,
                    sp: SpeedParams, takes: list[dict],
                    extra_removed: set[int] | None = None) -> dict:
    """Pipeline completo: palavras + envelope -> clipes, removidos, notas."""
    removed_ids = words_removed_by_takes(words, takes)
    if extra_removed:
        removed_ids |= set(extra_removed)
    spans = build_spans(words, env, cut, removed_ids)
    refused = [
        {"type": "refused_cut", "at": round(sp.end, 3),
         "detail": "corte recusado: o Whisper marcou pausa aqui, mas o envelope "
                   "não tem nenhum vale e as duas bordas cairiam em cima de "
                   "fala. O áudio segue contínuo."}
        for sp in spans if sp.refused_cut
    ]
    spans, notes = enforce_min_block(spans, cut)
    notes = refused + notes
    clips = assign_speed(spans, words, env, cut, sp, env.duration)
    regions = removed_regions(spans, env.duration, takes)
    return {"clips": clips, "removed": regions, "notes": notes,
            "removed_word_ids": sorted(removed_ids), "spans": spans}
