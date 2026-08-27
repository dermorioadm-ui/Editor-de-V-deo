"""Orquestra a exportação inteira (Parte 10.3, 10.4)."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..edit.timeline import Timeline
from ..ffmpeg_utils import MediaInfo, concat_demux, probe, write_wav
from ..models import EditPlan
from ..subtitles import ass as ass_mod
from .renderer import (AUDIO_SR, RenderResult, build_audio_track, mux,
                       plan_segments, process_audio, render_video_segments,
                       target_size)

SYNC_TOLERANCE = 0.030      # acima disso o vídeo é reescalado por timestamp


def export_project(
    plan: EditPlan,
    sources: dict,
    dest: Path,
    work: Path,
    build_cues: Callable[[Timeline], list[dict]],
    on_progress: Callable[[float, str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    hw: str | None = None,
) -> RenderResult:
    dest = Path(dest)
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    main: MediaInfo = sources["main"]["info"]
    width, height = target_size(main, plan.export)

    def report(frac: float, msg: str, lo: float = 0.0, hi: float = 1.0):
        if on_progress:
            on_progress(lo + (hi - lo) * max(0.0, min(1.0, frac)), msg)

    # 1) linha do tempo teórica e legendas de partida
    timeline = Timeline(plan.active_clips, main.fps)
    cues = build_cues(timeline)

    # 2) trechos de vídeo (cutaways já aplicados) — UM encode por trecho
    report(0.0, "planejando trechos")
    segs = plan_segments(plan, timeline, sources, main)
    media_paths = {k: v["path"] for k, v in sources.items()}
    segs = render_video_segments(
        segs, plan, main, cues, work / "segments", media_paths, hw,
        on_progress=lambda f, m: report(f, m, 0.02, 0.62), cancel=cancel)

    # 3) concat SEM reencodar
    report(0.0, "juntando os trechos (sem reencodar)", 0.62, 0.66)
    video_only = work / "video.mp4"
    concat_demux([s.file for s in segs], video_only)
    video_info = probe(video_only)

    # 4) durações medidas por clipe -> o áudio nasce já do tamanho certo
    clip_durations: dict[str, float] = {}
    for s in segs:
        clip_durations[s.clip_id] = clip_durations.get(s.clip_id, 0.0) + (s.measured or 0.0)
    for placed in timeline:
        if placed.clip.id in clip_durations:
            placed.clip.measured_duration = clip_durations[placed.clip.id]

    # 5) legendas refeitas sobre a linha do tempo REAL
    measured_timeline = Timeline(plan.active_clips, main.fps)
    cues_final = build_cues(measured_timeline)

    # 6) áudio em PCM, cadeia aplicada uma vez, AAC só no mux
    report(0.0, "montando o áudio", 0.66, 0.80)
    track = build_audio_track(plan, measured_timeline, sources, clip_durations,
                              on_progress=lambda f, m: report(f, m, 0.66, 0.78))
    raw_wav = work / "audio_raw.wav"
    write_wav(raw_wav, track, AUDIO_SR)
    processed = work / "audio.wav"
    report(0.0, "processando o áudio (highpass → compressor → loudnorm)", 0.80, 0.86)
    process_audio(raw_wav, processed, plan.audio, plan, sources,
                  duration=len(track) / AUDIO_SR)
    audio_info = probe(processed)

    # 7) sincronia: corrige no VÍDEO por timestamp, sem reencodar (10.3)
    drift = video_info.duration - audio_info.duration
    itsscale = None
    if abs(drift) > SYNC_TOLERANCE and video_info.duration > 0:
        itsscale = audio_info.duration / video_info.duration

    report(0.0, "muxando", 0.86, 0.94)
    dest.parent.mkdir(parents=True, exist_ok=True)
    mux(video_only, processed, dest, plan.export, itsscale)
    final = probe(dest)

    # 8) legendas em arquivo separado, na linha do tempo final
    srt_path = dest.with_suffix(".srt")
    ass_path = dest.with_suffix(".ass")
    srt_path.write_text(ass_mod.build_srt(cues_final, plan.style.uppercase),
                        encoding="utf-8")
    ass_mod.write_ass(ass_path, cues_final, plan.style, width, height)

    warnings: list[str] = []
    src_bitrate = main.v_bitrate or main.bitrate
    out_bitrate = final.v_bitrate or final.bitrate
    if src_bitrate and out_bitrate:
        drop = (1 - out_bitrate / src_bitrate) * 100
        if drop > 40:
            warnings.append(
                f"o bitrate de vídeo caiu {drop:.0f}% em relação à fonte "
                f"({src_bitrate/1e6:.1f} → {out_bitrate/1e6:.1f} Mbps). "
                f"Baixe o CRF para recuperar."
            )
    if itsscale:
        warnings.append(
            f"vídeo e áudio saíram com {abs(drift)*1000:.0f} ms de diferença; "
            f"corrigido reescalando os timestamps do vídeo em "
            f"{(itsscale-1)*100:+.3f}% (sem reencodar)."
        )

    report(1.0, "exportação concluída", 0.94, 1.0)
    return RenderResult(
        output=str(dest),
        duration=final.duration,
        video_duration=video_info.duration,
        audio_duration=audio_info.duration,
        drift=round(drift, 4),
        itsscale=itsscale,
        segments=[{"index": s.index, "kind": s.kind, "out_start": s.out_start,
                   "measured": s.measured or 0.0,
                   "theoretical": s.out_theoretical} for s in segs],
        bitrate=out_bitrate,
        source_bitrate=src_bitrate,
        subtitles=cues_final,
        srt_path=str(srt_path),
        ass_path=str(ass_path),
        warnings=warnings,
    )
