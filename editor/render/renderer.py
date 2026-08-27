"""Exportação — UMA geração de encode (Parte 10).

O erro que mais custou ao usuário: aplicar cada alteração por cima do arquivo
já encodado. Três alterações viraram três gerações de H.264 e o bitrate caiu de
12,5 para 3,8 Mbps.

Aqui:
  * o plano é declarativo e nada renderiza até a exportação;
  * cada trecho é encodado UMA vez, direto da fonte original;
  * trechos de fontes diferentes saem com parâmetros idênticos e são
    concatenados com ``-c copy`` (sem reencodar);
  * as legendas são queimadas dentro DESSE mesmo encode, usando o deslocamento
    exato acumulado das durações já medidas — por isso não existe deriva;
  * o áudio é montado em PCM, sofre a cadeia uma vez e vira AAC no mux final.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from ..audio.envelope import HOP_SECONDS
from ..config import FFMPEG, AudioParams, ExportParams
from ..edit.timeline import Timeline
from ..ffmpeg_utils import (FFmpegError, MediaInfo, concat_demux, decode_pcm,
                            probe, run, run_with_progress, write_wav)
from ..models import EditPlan
from ..subtitles import ass as ass_mod
from . import filters as F

AUDIO_SR = 48000
FADE_MS = 12


@dataclass
class VideoSegment:
    index: int
    source_path: str
    kind: str                  # main | insert | cutaway | photo
    src_start: float
    src_duration: float
    speed: float
    out_theoretical: float
    clip_id: str
    info: MediaInfo | None = None
    photo: dict | None = None
    fit: dict | None = None
    out_start: float = 0.0     # preenchido com a soma das durações MEDIDAS
    measured: float | None = None
    file: str = ""

    @property
    def nominal(self) -> float:
        """Duração puramente teórica do trecho.

        Usada na janela das legendas e na chave do cache. Nunca depende de
        medições anteriores, senão reexportar o mesmo plano invalidaria o
        cache de trechos já encodados.
        """
        if self.kind == "photo":
            return self.out_theoretical
        return self.src_duration / max(self.speed, 1e-6)


@dataclass
class RenderResult:
    output: str
    duration: float
    video_duration: float
    audio_duration: float
    drift: float
    itsscale: float | None
    segments: list
    bitrate: int
    source_bitrate: int
    subtitles: list
    srt_path: str = ""
    ass_path: str = ""
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["segments"] = [{"index": s["index"], "kind": s["kind"],
                          "out_start": round(s["out_start"], 4),
                          "measured": round(s["measured"], 4),
                          "theoretical": round(s["theoretical"], 4),
                          "delta_ms": round((s["measured"] - s["theoretical"]) * 1000, 1)}
                         for s in self.segments]
        return d


# --------------------------------------------------------------- parâmetros
def encoder_args(export: ExportParams, info: MediaInfo,
                 hw: str | None = None) -> list[str]:
    """Parâmetros idênticos para TODOS os trechos — requisito do concat copy."""
    if hw:
        args = ["-c:v", hw]
        if "nvenc" in hw:
            args += ["-preset", "p5", "-rc", "vbr", "-cq", str(export.crf),
                     "-b:v", "0"]
        elif "qsv" in hw:
            args += ["-global_quality", str(export.crf)]
        elif "videotoolbox" in hw:
            args += ["-q:v", str(max(1, 100 - export.crf * 3))]
        else:
            args += ["-qp", str(export.crf)]
    elif export.codec == "h265":
        args = ["-c:v", "libx265", "-preset", export.preset,
                "-crf", str(export.crf), "-tag:v", "hvc1",
                "-x265-params", "log-level=error"]
    else:
        args = ["-c:v", "libx264", "-preset", export.preset,
                "-crf", str(export.crf), "-profile:v", "high", "-level", "4.2"]
    fps = info.fps or 30.0
    args += [
        "-pix_fmt", export.pix_fmt,
        "-g", str(max(2, int(round(fps * 2)))),
        "-video_track_timescale", "90000",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        "-an", "-sn", "-dn", "-map_metadata", "-1",
    ]
    return args


def target_size(main: MediaInfo, export: ExportParams) -> tuple[int, int]:
    """Resolução de saída. Padrão: a da fonte — nunca reduzir sem pedido."""
    w, h = main.display_size
    scale = str(getattr(export, "scale", "source") or "source")
    if scale in ("", "source", "original"):
        return w, h
    try:
        target_w = int(scale)
    except ValueError:
        return w, h
    if target_w <= 0 or target_w >= w:
        return w, h
    new_h = int(round(h * target_w / w))
    return target_w - (target_w % 2), new_h - (new_h % 2)


def _hash(payload: dict) -> str:
    return hashlib.sha1(json.dumps(payload, sort_keys=True,
                                   default=str).encode()).hexdigest()[:16]


# ------------------------------------------------------------- planejamento
def plan_segments(plan: EditPlan, timeline: Timeline, sources: dict,
                  main: MediaInfo) -> list[VideoSegment]:
    """Divide a linha do tempo em trechos de vídeo, aplicando os cutaways.

    Um cutaway substitui só o VÍDEO; o áudio original continua por baixo, então
    ele não mexe na linha do tempo de áudio.
    """
    segs: list[VideoSegment] = []
    cutaways = sorted([c for c in plan.cutaways if c.enabled],
                      key=lambda c: c.out_start)
    idx = 0
    for placed in timeline:
        clip = placed.clip
        path = sources.get(clip.source, {}).get("path")
        info = sources.get(clip.source, {}).get("info")
        if not path:
            continue
        pieces: list[tuple[float, float, object | None]] = []
        cursor = placed.out_start
        for cut in cutaways:
            if cut.out_end <= placed.out_start or cut.out_start >= placed.out_end:
                continue
            a = max(cut.out_start, placed.out_start)
            b = min(cut.out_end, placed.out_end)
            if a - cursor > 0.02:
                pieces.append((cursor, a, None))
            pieces.append((a, b, cut))
            cursor = b
        if placed.out_end - cursor > 0.02 or not pieces:
            pieces.append((cursor, placed.out_end, None))

        for out_a, out_b, cut in pieces:
            out_dur = out_b - out_a
            if out_dur <= 0.02:
                continue
            if cut is None:
                if clip.kind == "photo":
                    segs.append(VideoSegment(
                        index=idx, source_path=str(path), kind="photo",
                        src_start=0.0, src_duration=out_dur, speed=1.0,
                        out_theoretical=out_dur, clip_id=clip.id, info=info,
                        photo=clip.photo or {}, fit=clip.fit))
                else:
                    frac_a = (out_a - placed.out_start) / max(placed.out_duration, 1e-9)
                    frac_b = (out_b - placed.out_start) / max(placed.out_duration, 1e-9)
                    s0 = clip.src_start + frac_a * clip.src_duration
                    s1 = clip.src_start + frac_b * clip.src_duration
                    segs.append(VideoSegment(
                        index=idx, source_path=str(path),
                        kind="main" if clip.source == "main" else "insert",
                        src_start=s0, src_duration=s1 - s0, speed=clip.speed,
                        out_theoretical=out_dur, clip_id=clip.id, info=info,
                        fit=clip.fit))
            else:
                cpath = sources.get(cut.media_id, {}).get("path")
                cinfo = sources.get(cut.media_id, {}).get("info")
                if not cpath:
                    segs.append(VideoSegment(
                        index=idx, source_path=str(path), kind="main",
                        src_start=clip.src_start, src_duration=out_dur * clip.speed,
                        speed=clip.speed, out_theoretical=out_dur,
                        clip_id=clip.id, info=info))
                else:
                    offset = (out_a - cut.out_start) * cut.speed
                    segs.append(VideoSegment(
                        index=idx, source_path=str(cpath), kind="cutaway",
                        src_start=cut.media_start + offset,
                        src_duration=out_dur * cut.speed, speed=cut.speed,
                        out_theoretical=out_dur, clip_id=clip.id, info=cinfo,
                        fit=cut.fit))
            idx += 1
    return segs


# ------------------------------------------------------------------ vídeo
def _build_video_command(seg: VideoSegment, plan: EditPlan, main: MediaInfo,
                         cues: list[dict], ass_dir: Path,
                         media_paths: dict, hw: str | None) -> tuple[list[str], list[str]]:
    width, height = target_size(main, plan.export)
    fps = main.fps or 30.0
    inputs: list[str] = []
    pre: list[str] = []

    if seg.kind == "photo":
        pre += ["-loop", "1", "-t", f"{seg.out_theoretical:.6f}",
                "-i", seg.source_path]
    else:
        pre += ["-ss", f"{max(0.0, seg.src_start):.6f}",
                "-t", f"{max(0.02, seg.src_duration):.6f}",
                "-i", seg.source_path]

    chain: list[str] = []
    graph_parts: list[str] = []
    cur_tag = "0:v"

    if seg.kind == "photo":
        photo = seg.photo or {}
        kb = photo.get("ken_burns") or {}
        if kb.get("enabled"):
            kb_chain, zoom_expr = F.ken_burns_chain(
                width, height, seg.out_theoretical,
                float(kb.get("intensity", 0.12)), str(kb.get("direction", "in")))
            chain.append(kb_chain)
        else:
            zoom_expr = "1"
            chain.append(F.fit_chain(width, height))
        ann = F.annotation_chain(photo.get("annotations") or [], width, height,
                                 zoom_expr)
        if ann:
            chain.append(ann)
        chain.append(f"fps={fps}")
    else:
        chain.append(f"setpts=(PTS-STARTPTS)/{seg.speed:.6f}")
        chain.append(f"fps={fps}")
        info = seg.info
        needs_fit = bool(info and info.display_size != (width, height))
        tonemap_mode = (seg.fit or {}).get("tonemap", "auto")
        needs_tonemap = bool(
            info and (tonemap_mode is True
                      or (tonemap_mode == "auto" and info.is_hdr and not main.is_hdr))
        )
        if needs_tonemap:
            chain.append(F.TONEMAP)
        color = F.color_chain(seg.fit)
        if color:
            chain.append(color)
        if needs_fit:
            chain.append(F.fit_chain(width, height))
        elif seg.kind != "main":
            chain.append(f"scale={width}:{height}")

    graph_parts.append(f"[{cur_tag}]" + ",".join(chain) + "[__v0]")
    cur_tag = "__v0"

    blur_graph, has_blur = F.blur_chain(
        plan.blurs, seg.out_start, seg.out_start + seg.nominal,
        width, height, cur_tag, "__vb")
    if has_blur:
        graph_parts.append(blur_graph)
        cur_tag = "__vb"

    overlays = F.overlay_inputs(plan.overlays, seg.out_start,
                                seg.out_start + seg.nominal)
    if overlays:
        ov_graph, ov_inputs = F.overlay_chain(
            overlays, media_paths, seg.out_start, width, height,
            first_input_index=1, tag_in=cur_tag, tag_out="__vo")
        if ov_graph:
            graph_parts.append(ov_graph)
            cur_tag = "__vo"
            for p in ov_inputs:
                pre += ["-i", p]

    tail = []
    if plan.export.burn_subtitles and cues:
        window_end = seg.out_start + seg.nominal + 1.0
        ass_path = ass_dir / f"seg_{seg.index:04d}.ass"
        ass_mod.write_ass(ass_path, cues, plan.style, width, height,
                          time_offset=-seg.out_start,
                          window=(seg.out_start - 0.5, window_end))
        tail.append(F.subtitle_chain(ass_path))
    tail.append(f"format={plan.export.pix_fmt}")
    graph_parts.append(f"[{cur_tag}]" + ",".join(tail) + "[vout]")

    filtergraph = ";".join(graph_parts)
    args = [FFMPEG, "-y", "-v", "error", *pre,
            "-filter_complex", filtergraph, "-map", "[vout]"]
    args += encoder_args(plan.export, main, hw)
    return args, inputs


def render_video_segments(segs: list[VideoSegment], plan: EditPlan,
                          main: MediaInfo, cues: list[dict], work: Path,
                          media_paths: dict, hw: str | None,
                          on_progress: Callable | None = None,
                          cancel: Callable | None = None) -> list[VideoSegment]:
    """Encoda cada trecho UMA vez. Retomável: trecho com hash igual é reusado."""
    work.mkdir(parents=True, exist_ok=True)
    ass_dir = work / "ass"
    ass_dir.mkdir(exist_ok=True)
    manifest_path = work / "manifest.json"
    manifest = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}

    total_out = sum(s.out_theoretical for s in segs) or 1.0
    done_out = 0.0
    cursor = 0.0
    for seg in segs:
        seg.out_start = cursor
        key = _hash({
            "src": seg.source_path, "start": round(seg.src_start, 4),
            "dur": round(seg.src_duration, 4), "speed": seg.speed,
            "kind": seg.kind, "photo": seg.photo, "fit": seg.fit,
            "out_start": round(seg.out_start, 3),
            "nominal": round(seg.nominal, 4),
            "style": plan.style.__dict__, "export": plan.export.__dict__,
            "burn": plan.export.burn_subtitles,
            "cues": [(round(c["start"], 3), round(c["end"], 3), c["text"])
                     for c in cues
                     if c["end"] > seg.out_start - 0.5
                     and c["start"] < seg.out_start + seg.nominal + 1.0],
            "blurs": [b.to_dict() for b in plan.blurs
                      if b.out_end > seg.out_start
                      and b.out_start < seg.out_start + seg.nominal],
            "overlays": [o.to_dict() for o in plan.overlays
                         if o.out_end > seg.out_start
                         and o.out_start < seg.out_start + seg.nominal],
            "hw": hw, "size": target_size(main, plan.export),
        })
        dest = work / f"seg_{seg.index:04d}_{key}.mp4"
        cached = manifest.get(str(seg.index))
        if (cached and cached.get("key") == key and Path(cached["file"]).exists()):
            seg.file = cached["file"]
            seg.measured = float(cached["measured"])
        else:
            if cancel and cancel():
                raise KeyboardInterrupt("exportação cancelada")
            args, _ = _build_video_command(seg, plan, main, cues, ass_dir,
                                           media_paths, hw)
            for old in work.glob(f"seg_{seg.index:04d}_*.mp4"):
                old.unlink(missing_ok=True)
            base = done_out

            def prog(frac: float, _base=base, _seg=seg):
                if on_progress:
                    on_progress(
                        min(0.999, (_base + frac * _seg.out_theoretical) / total_out),
                        f"encodando trecho {_seg.index + 1}/{len(segs)}")

            run_with_progress([*args, str(dest)], seg.out_theoretical, prog, cancel)
            seg.file = str(dest)
            seg.measured = probe(dest).duration
            manifest[str(seg.index)] = {"key": key, "file": seg.file,
                                        "measured": seg.measured}
            manifest_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        done_out += seg.out_theoretical
        cursor += seg.measured
        if on_progress:
            on_progress(min(0.999, done_out / total_out),
                        f"trecho {seg.index + 1}/{len(segs)} pronto "
                        f"({seg.measured:.3f} s)")
    return segs


# ------------------------------------------------------------------- áudio
def _resample_exact(samples: np.ndarray, target: int) -> np.ndarray:
    if target <= 0:
        return np.zeros(0, dtype=np.float32)
    if len(samples) == target:
        return samples.astype(np.float32)
    if len(samples) == 0:
        return np.zeros(target, dtype=np.float32)
    src_x = np.linspace(0.0, 1.0, len(samples), dtype=np.float64)
    dst_x = np.linspace(0.0, 1.0, target, dtype=np.float64)
    return np.interp(dst_x, src_x, samples).astype(np.float32)


def _fade(samples: np.ndarray, ms: int = FADE_MS) -> np.ndarray:
    """Fade de 12 ms na entrada e na saída. Sem isso, emenda em fala estala."""
    n = int(AUDIO_SR * ms / 1000.0)
    if n <= 0 or len(samples) < 2 * n:
        return samples
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    samples = samples.copy()
    samples[:n] *= ramp
    samples[-n:] *= ramp[::-1]
    return samples


def build_audio_track(plan: EditPlan, timeline: Timeline, sources: dict,
                      clip_durations: dict, on_progress: Callable | None = None
                      ) -> np.ndarray:
    """Monta o áudio em PCM, cada bloco com a duração EXATA do vídeo medido."""
    chunks: list[np.ndarray] = []
    total = max(len(timeline), 1)
    for n, placed in enumerate(timeline):
        clip = placed.clip
        target = int(round(clip_durations.get(clip.id, placed.out_duration) * AUDIO_SR))
        if target <= 0:
            continue
        if clip.kind == "photo" or clip.audio == "mute":
            chunks.append(np.zeros(target, dtype=np.float32))
            continue
        path = sources.get(clip.source, {}).get("path")
        if not path:
            chunks.append(np.zeros(target, dtype=np.float32))
            continue
        af = None
        if abs(clip.speed - 1.0) > 1e-4:
            af = _atempo(clip.speed)
        try:
            pcm = decode_pcm(path, clip.src_start, clip.src_end,
                             sample_rate=AUDIO_SR, channels=1, filters=af)
        except FFmpegError:
            pcm = np.zeros(target, dtype=np.float32)
        pcm = _resample_exact(pcm, target)
        chunks.append(_fade(pcm))
        if on_progress:
            on_progress((n + 1) / total, f"áudio: bloco {n + 1}/{total}")
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks)


def _atempo(speed: float) -> str:
    """atempo preserva o tom. Encadeia quando a razão sai da faixa segura."""
    parts = []
    remaining = speed
    while remaining > 2.0:
        parts.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        parts.append("atempo=0.5")
        remaining /= 0.5
    parts.append(f"atempo={remaining:.6f}")
    return ",".join(parts)


def process_audio(raw_wav: Path, dest: Path, params: AudioParams,
                  plan: EditPlan, sources: dict, duration: float) -> Path:
    """Cadeia da Parte 9.1 aplicada UMA vez sobre a faixa inteira."""
    from ..audio.loudness import build_chain

    chain = build_chain(params)
    music = plan.music if plan.music and plan.music.get("enabled") else None
    cmd = [FFMPEG, "-y", "-v", "error", "-i", str(raw_wav)]
    if music:
        mpath = sources.get(music.get("media_id"), {}).get("path")
        if mpath:
            cmd += ["-stream_loop", "-1", "-i", str(mpath)]
            mix = F.music_chain(float(music.get("gain_db", -18)),
                                bool(music.get("ducking", True)),
                                float(music.get("duck_amount", 12)),
                                float(music.get("fade_in", 1.0)),
                                float(music.get("fade_out", 2.0)), duration)
            cmd += ["-filter_complex", f"{mix};[aout]{chain}[a]", "-map", "[a]"]
        else:
            music = None
    if not music:
        cmd += ["-af", chain]
    cmd += ["-ac", "1", "-ar", str(AUDIO_SR), "-c:a", "pcm_s16le",
            "-t", f"{duration:.6f}", str(dest)]
    run(cmd)
    return dest


# -------------------------------------------------------------------- mux
def mux(video: Path, audio: Path, dest: Path, export: ExportParams,
        itsscale: float | None = None) -> Path:
    cmd = [FFMPEG, "-y", "-v", "error"]
    if itsscale and abs(itsscale - 1.0) > 1e-9:
        cmd += ["-itsscale", f"{itsscale:.9f}"]
    cmd += ["-i", str(video), "-i", str(audio),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", export.audio_bitrate,
            "-ar", str(export.audio_rate), "-ac", "2",
            "-movflags", "+faststart", "-shortest", str(dest)]
    run(cmd)
    return dest


def estimate_bitrate(source: MediaInfo, export: ExportParams,
                     sample_seconds: float = 4.0) -> dict:
    """Encoda uma amostra curta só para estimar o bitrate antes de exportar."""
    import tempfile

    start = max(0.0, source.duration * 0.4)
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "probe.mp4"
        args = [FFMPEG, "-y", "-v", "error",
                "-ss", f"{start:.3f}", "-t", f"{sample_seconds:.3f}",
                "-i", source.path, "-map", "0:v:0"]
        args += encoder_args(export, source)
        try:
            run([*args, str(dest)])
            info = probe(dest)
        except FFmpegError as exc:
            return {"available": False, "error": str(exc)[:300]}
        est = int(info.size_bytes * 8 / max(info.duration, 1e-6))
    src_v = source.v_bitrate or source.bitrate
    drop = (1.0 - est / src_v) * 100 if src_v else 0.0
    return {
        "available": True,
        "estimated_video_bitrate": est,
        "source_video_bitrate": src_v,
        "drop_percent": round(drop, 1),
        "warn": drop > 40.0,
        "message": (f"queda estimada de {drop:.0f}% no bitrate de vídeo "
                    f"({src_v/1e6:.1f} → {est/1e6:.1f} Mbps)")
        if src_v else "bitrate da fonte desconhecido",
    }
