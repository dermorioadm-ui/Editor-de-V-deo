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
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from ..config import FFMPEG, AudioParams, ExportParams
from ..edit.timeline import Timeline
from ..edit.zoom import zoom_chain
from ..ffmpeg_utils import (FFmpegError, MediaInfo, decode_pcm, probe, run,
                            run_with_progress, write_wav)
from ..models import EditPlan
from ..subtitles import ass as ass_mod
from . import filters as F
from . import looks

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
    zoom: float = 1.0          # jogo de zoom do corte, aplicado neste encode
    out_start: float = 0.0     # preenchido com a soma das durações MEDIDAS
    t_start: float = 0.0       # posição na linha do tempo das legendas (nominal)
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


# Formatos derivados do MESMO take vertical. O tamanho de cada um não é
# escolhido pela tabela dos players: é escolhido pelo que a fonte entrega.
#
# De um 1080x1920, a maior janela quadrada é 1080x1080 — cabe inteira, zero
# esticada. A maior janela 16:9 é 1080x608, e aí a escolha importa: mandar
# para 1920x1080 é esticar 1,78x (fica mole e o anúncio parece amador);
# 1280x720 estica 1,18x, que é a mesma tolerância que o teto de zoom já usa.
# Por isso o 16:9 sai em 720p, e não em 1080p.
PROPORCOES = {
    "fonte": 0.0,
    "1:1": 1.0,
    "16:9": 16 / 9,
    "9:16": 9 / 16,
}
ALTURA_DERIVADA = {"1:1": 1080, "16:9": 720, "9:16": 1920}


def aspecto_do_export(export: ExportParams) -> str:
    a = str(getattr(export, "aspect", "fonte") or "fonte")
    return a if a in PROPORCOES else "fonte"


def _reducao(main: MediaInfo, export: ExportParams) -> float:
    """Quanto a escolha de "Resolução" encolhe o vídeo. 1,0 = nada."""
    w, _h = main.display_size
    scale = str(getattr(export, "scale", "source") or "source")
    if scale in ("", "source", "original") or w <= 0:
        return 1.0
    try:
        alvo = int(scale)
    except ValueError:
        return 1.0
    if alvo <= 0 or alvo >= w:
        return 1.0
    return alvo / w


def target_size(main: MediaInfo, export: ExportParams) -> tuple[int, int]:
    """Resolução de saída. Padrão: a da fonte — nunca reduzir sem pedido."""
    w, h = main.display_size
    aspecto = aspecto_do_export(export)
    prop_fonte = w / max(h, 1e-9)
    derivado = aspecto != "fonte" and abs(PROPORCOES[aspecto] - prop_fonte) > 0.01
    if derivado:
        alvo_h = ALTURA_DERIVADA.get(aspecto, h)
        alvo_w = int(round(alvo_h * PROPORCOES[aspecto]))
        w, h = alvo_w, alvo_h
    # "Resolução" é uma REDUÇÃO, não uma largura fixa. Tratá-la como largura
    # dava 720x404 no 16:9 quando o usuário pediu "720 de largura" olhando o
    # vertical — um tamanho que nenhum player espera. Como redução, 720 num
    # vertical de 1080 vira 0,667, e o 16:9 sai 854x480: a mesma economia,
    # numa geometria que existe.
    k = _reducao(main, export)
    if k < 1.0:
        w, h = int(round(w * k)), int(round(h * k))
    return w - (w % 2), h - (h % 2)


def fps_de_saida(main: MediaInfo, export: ExportParams) -> float:
    """Quadros por segundo da saída — nunca ACIMA dos da gravação.

    Inventar quadro que não foi filmado não melhora nada e custa o dobro.
    """
    fonte = float(main.fps or 30.0)
    pedido = float(getattr(export, "fps", 0.0) or 0.0)
    if pedido <= 0:
        return fonte
    return min(pedido, fonte)


def regua_da_legenda(main: MediaInfo, export: ExportParams,
                     style) -> tuple[int, int, object]:
    """PlayRes e estilo da legenda para ESTE formato de saída.

    No formato da fonte o ASS é escrito na resolução da FONTE, sempre: o
    fontsize, o contorno e as margens do estilo são pixels absolutos
    calibrados para ela, e PlayRes na resolução do render fazia a mesma
    legenda ocupar 47% da largura no export 1080, 71% no 720 e 100% no 480.

    Num formato DERIVADO a régua muda junto com o quadro. Uma margem de 220 px
    medida num quadro de 1920 de altura é 11% da tela; a mesma margem num 16:9
    de 720 seria 31% — a legenda subiria para o meio do rosto. Então o estilo
    é reescalado pela razão das alturas, exatamente como na criação do
    projeto, e o PlayRes passa a ser o do quadro derivado.
    """
    from dataclasses import replace

    w, h = main.display_size
    tw, th = target_size(main, export)
    prop_fonte = w / max(h, 1e-9)
    prop_saida = tw / max(th, 1e-9)
    if abs(prop_fonte - prop_saida) <= 0.01:
        return w, h, style
    # O formato derivado usa O PADRÃO DAQUELE FORMATO, o mesmo que a criação
    # do projeto usa para o formato da fonte — não uma regra de três da altura.
    # A escolha do usuário (menor/maior) viaja como PROPORÇÃO: se ele pediu
    # 1,2x do padrão no vertical, o quadrado e o horizontal também saem 1,2x
    # do padrão deles.
    from ..projects import padrao_de_legenda

    f_fonte, _mf, _cf, _chf = padrao_de_legenda(w, h)
    fator = (style.fontsize / f_fonte) if f_fonte > 0 else 1.0
    fonte, margem, contorno, chars = padrao_de_legenda(tw, th)
    novo = replace(
        style,
        fontsize=max(8, int(round(fonte * fator))),
        margin_v=max(0, int(round(margem))),
        margin_l=max(0, int(round(tw * 0.06))),
        margin_r=max(0, int(round(tw * 0.06))),
        outline=round(max(1.0, contorno), 2),
        shadow=round(max(0.0, contorno * 0.25), 2),
        max_chars_per_line=chars,
    )
    return tw, th, novo


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
            if clip.kind == "photo":
                # cutaway por cima de foto partiria o Ken Burns em pedaços que
                # reiniciam o zoom do zero; a foto vence
                break
            if cut.out_end <= placed.out_start or cut.out_start >= placed.out_end:
                continue
            # dois cutaways sobrepostos gerariam o MESMO intervalo de saída
            # duas vezes (duração explode e o A/V dessincroniza): o segundo
            # começa onde o primeiro terminou
            a = max(cut.out_start, placed.out_start, cursor)
            b = min(cut.out_end, placed.out_end)
            if b - a <= 0.02:
                continue
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
                        t_start=out_a,
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
                        t_start=out_a, fit=clip.fit, zoom=clip.zoom))
            else:
                cpath = sources.get(cut.media_id, {}).get("path")
                cinfo = sources.get(cut.media_id, {}).get("info")
                if not cpath:
                    # a mídia do cutaway sumiu: mostra o trecho CORRESPONDENTE
                    # do principal (antes voltava ao INÍCIO do clipe e repetia
                    # conteúdo já exibido)
                    frac_a = (out_a - placed.out_start) / max(placed.out_duration, 1e-9)
                    frac_b = (out_b - placed.out_start) / max(placed.out_duration, 1e-9)
                    segs.append(VideoSegment(
                        index=idx, source_path=str(path), kind="main",
                        src_start=clip.src_start + frac_a * clip.src_duration,
                        src_duration=(frac_b - frac_a) * clip.src_duration,
                        speed=clip.speed, out_theoretical=out_dur,
                        clip_id=clip.id, info=info, t_start=out_a,
                        zoom=clip.zoom))
                else:
                    offset = (out_a - cut.out_start) * cut.speed
                    segs.append(VideoSegment(
                        index=idx, source_path=str(cpath), kind="cutaway",
                        src_start=cut.media_start + offset,
                        src_duration=out_dur * cut.speed, speed=cut.speed,
                        out_theoretical=out_dur, clip_id=clip.id, info=cinfo,
                        t_start=out_a, fit=cut.fit))
            idx += 1
    return segs


# ------------------------------------------------------------------ vídeo
def _build_video_command(seg: VideoSegment, plan: EditPlan, main: MediaInfo,
                         cues: list[dict], ass_dir: Path,
                         media_paths: dict, hw: str | None) -> tuple[list[str], list[str]]:
    width, height = target_size(main, plan.export)
    # RÉGUA DA LEGENDA: o ASS é escrito sempre na resolução da FONTE, nunca na
    # do render. O fontsize, o contorno e as margens do estilo são pixels
    # absolutos, calibrados para a fonte por escalar_legenda(); PlayRes na
    # resolução do render fazia a mesma legenda ocupar 47% da largura no
    # export 1080, 71% no 720 e 100% no 480 — de ponta a ponta da tela. Com
    # PlayRes fixo, o libass escala tudo junto e dá 47,5% nas três. (Medido
    # com o filtro ass do próprio ffmpeg, texto "ISSO MUDA TUDO".)
    play_w, play_h, style_saida = regua_da_legenda(main, plan.export, plan.style)
    fps = fps_de_saida(main, plan.export)
    inputs: list[str] = []
    pre: list[str] = []

    if seg.kind == "photo":
        # -framerate na ENTRADA: sem isso a imagem entra a 25 fps e a foto sai
        # com a duração errada depois do zoompan.
        pre += ["-loop", "1", "-framerate", f"{fps}",
                "-t", f"{seg.out_theoretical:.6f}", "-i", seg.source_path]
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
            # compõe no dobro do tamanho para o zoom não amolecer a imagem
            chain.append(F.fit_chain(width * 2, height * 2))
            kb_chain, zoom_expr = F.ken_burns_chain(
                width, height, seg.nominal,
                float(kb.get("intensity", 0.12)), str(kb.get("direction", "in")),
                fps)
            chain.append(kb_chain)
        else:
            zoom_expr = "1"
            chain.append(F.fit_chain(width, height))
        ann = F.annotation_chain(photo.get("annotations") or [], width, height,
                                 zoom_expr)
        if ann:
            chain.append(ann)
        if not kb.get("enabled"):
            chain.append(f"fps={fps}")
    else:
        chain.append(f"setpts=(PTS-STARTPTS)/{seg.speed:.6f}")
        chain.append(f"fps={fps}")
        info = seg.info
        needs_fit = bool(info and info.display_size != (width, height))
        fit = seg.fit or {}
        tonemap_mode = fit.get("tonemap", "auto")
        needs_tonemap = bool(
            info and (tonemap_mode is True
                      or (tonemap_mode == "auto" and info.is_hdr and not main.is_hdr))
        )
        if needs_tonemap:
            chain.append(F.tonemap_chain(
                str(fit.get("tonemap_mode", "transferencia")),
                float(fit.get("npl", 100.0)),
                str(fit.get("tonemap_operator", "hable")),
                float(fit.get("desat", 0.0)),
            ))
        color = F.color_chain(seg.fit)
        if color:
            chain.append(color)
        # Enquadramento: recorte CONCÊNTRICO no rosto direto na resolução da
        # FONTE, e só então a reescala para a saída. Recortar da fonte usa
        # todos os pixels que existem; recortar depois do fit jogaria metade
        # fora antes de esticar de volta.
        sw, sh = (info.display_size if info else (width, height))
        zc = zoom_chain(seg.zoom, sw, sh, width, height,
                        plan.zoom.anchor_x, plan.zoom.anchor_y, plan.zoom.unsharp)
        if zc:
            chain.append(zc)
        elif needs_fit:
            chain.append(F.fit_chain(width, height))
        elif seg.kind != "main":
            chain.append(f"scale={width}:{height}")

    graph_parts.append(f"[{cur_tag}]" + ",".join(chain) + "[__v0]")
    cur_tag = "__v0"

    blur_graph, has_blur = F.blur_chain(
        plan.blurs, seg.t_start, seg.t_start + seg.nominal,
        width, height, cur_tag, "__vb")
    if has_blur:
        graph_parts.append(blur_graph)
        cur_tag = "__vb"

    overlays = F.overlay_inputs(plan.overlays, seg.t_start,
                                seg.t_start + seg.nominal)
    if overlays:
        ov_graph, ov_inputs = F.overlay_chain(
            overlays, media_paths, seg.t_start, width, height,
            first_input_index=1, tag_in=cur_tag, tag_out="__vo")
        if ov_graph:
            graph_parts.append(ov_graph)
            cur_tag = "__vo"
            for p in ov_inputs:
                # -loop 1: um PNG entra como UM quadro em t=0; o fade avaliava
                # o alpha nesse único quadro (0) e a sobreposição com fade —
                # o padrão — saía 100% invisível no export
                pre += ["-loop", "1", "-framerate", f"{fps}",
                        "-t", f"{seg.nominal + 1.0:.3f}", "-i", p]

    tail = []
    # Look de cinema: vale para o vídeo inteiro, entra ANTES da legenda —
    # legenda queimada não pode virar sépia junto com a imagem, senão o
    # contorno preto some e o texto some com ele.
    lk = looks.look_chain(plan.look, plan.look_vignette)
    if lk:
        tail.append(lk)
    if plan.export.burn_subtitles and cues:
        window_end = seg.t_start + seg.nominal + 1.0
        ass_path = ass_dir / f"seg_{seg.index:04d}.ass"
        ass_mod.write_ass(ass_path, cues, style_saida, play_w, play_h,
                          time_offset=-seg.t_start,
                          window=(seg.t_start - 0.5, window_end))
        tail.append(F.subtitle_chain(ass_path))
    tail.append(f"format={plan.export.pix_fmt}")
    graph_parts.append(f"[{cur_tag}]" + ",".join(tail) + "[vout]")

    filtergraph = ";".join(graph_parts)
    args = [FFMPEG, "-y", "-v", "error", *pre,
            "-filter_complex", filtergraph, "-map", "[vout]"]
    args += encoder_args(plan.export, main, hw)
    if overlays:
        # o overlay (framesync) repete o último quadro do principal enquanto a
        # entrada do PNG tiver quadros — sem esta trava, cada trecho com
        # sobreposição saía mais longo que o planejado e a soma inflava o vídeo
        args += ["-t", f"{seg.out_theoretical:.6f}"]
    return args, inputs


def workers_de_encode() -> int:
    """Quantos ffmpeg rodar ao mesmo tempo.

    O custo que domina a exportação não é o encode: é o CUSTO FIXO por trecho
    (abrir o processo, buscar no arquivo, iniciar o x264, subir as threads).
    Medido: um passe único de 20 s custa 18 s; os MESMOS 20 s partidos em 20
    trechos custam 51 s. São ~2 s por trecho que a CPU passa quase parada — e
    um corte de silêncio produz um trecho por frase, então numa VSL de 3 min
    esse custo fixo sozinho passa de 5 minutos.

    Rodar vários em paralelo cobre o custo fixo de um com o encode do outro.
    Metade dos núcleos (teto de 4) porque cada ffmpeg já usa várias threads:
    passar disso só troca throughput por troca de contexto.
    """
    try:
        n = os.cpu_count() or 2
    except Exception:  # noqa: BLE001
        n = 2
    return max(1, min(4, n // 2))


def _chave_do_trecho(seg: VideoSegment, plan: EditPlan, main: MediaInfo,
                     cues: list[dict], hw: str | None) -> tuple[str, list, list, list]:
    """A identidade do trecho: se ela não muda, o arquivo encodado vale."""
    # As janelas são as MESMAS do render (t_start): legendas e estilo só entram
    # quando são queimadas, e a posição só quando algo posicional toca o trecho
    # — sem isso, editar uma legenda com burn desligado (ou qualquer edição
    # noutro ponto) invalidava trechos que não mudaram um pixel.
    seg_cues = [(round(c["start"], 3), round(c["end"], 3), c["text"])
                for c in cues
                if c["end"] > seg.t_start - 0.5
                and c["start"] < seg.t_start + seg.nominal + 1.0] \
        if plan.export.burn_subtitles else []
    seg_blurs = [b.to_dict() for b in plan.blurs
                 if b.enabled and b.out_end > seg.t_start
                 and b.out_start < seg.t_start + seg.nominal]
    seg_overlays = [o.to_dict() for o in plan.overlays
                    if o.enabled and o.out_end > seg.t_start
                    and o.out_start < seg.t_start + seg.nominal]
    positional = bool(seg_cues or seg_blurs or seg_overlays)
    key = _hash({
        "src": seg.source_path, "start": round(seg.src_start, 4),
        "dur": round(seg.src_duration, 4), "speed": seg.speed,
        "kind": seg.kind, "photo": seg.photo, "fit": seg.fit,
        "zoom": round(seg.zoom, 4),
        "face": (round(plan.zoom.anchor_x, 4), round(plan.zoom.anchor_y, 4)),
        "unsharp": plan.zoom.unsharp,
        "look": plan.look, "look_vignette": plan.look_vignette,
        "t_start": round(seg.t_start, 3) if positional else None,
        "nominal": round(seg.nominal, 4),
        "style": plan.style.__dict__ if seg_cues else None,
        "aspect": aspecto_do_export(plan.export),
        "export": plan.export.__dict__,
        "cues": seg_cues, "blurs": seg_blurs, "overlays": seg_overlays,
        "hw": hw, "size": target_size(main, plan.export),
        "fps": fps_de_saida(main, plan.export),
    })
    return key, seg_cues, seg_blurs, seg_overlays


def render_video_segments(segs: list[VideoSegment], plan: EditPlan,
                          main: MediaInfo, cues: list[dict], work: Path,
                          media_paths: dict, hw: str | None,
                          on_progress: Callable | None = None,
                          cancel: Callable | None = None) -> list[VideoSegment]:
    """Encoda cada trecho UMA vez, vários ao mesmo tempo.

    Retomável: trecho com hash igual é reusado, então reexportar depois de um
    retoque só reencoda o que o retoque tocou.

    A ORDEM não é negociável para o resultado — o `concat -c copy` depende de
    `cursor` acumular as durações MEDIDAS na sequência certa. Por isso o
    paralelismo fica só no encode: a acumulação continua sendo uma passada
    sequencial depois que todos os arquivos existem.
    """
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

    # 1) quem já está pronto e quem precisa encodar (barato, sequencial)
    pendentes: list[tuple[VideoSegment, str, Path]] = []
    for seg in segs:
        key, _c, _b, _o = _chave_do_trecho(seg, plan, main, cues, hw)
        cached = manifest.get(str(seg.index))
        if cached and cached.get("key") == key and Path(cached["file"]).exists():
            seg.file = cached["file"]
            seg.measured = float(cached["measured"])
            continue
        pendentes.append((seg, key, work / f"seg_{seg.index:04d}_{key}.mp4"))

    # 2) encoda os que faltam, vários ao mesmo tempo
    if pendentes:
        feito_out = sum(s.out_theoretical for s in segs
                        if not any(s is p[0] for p in pendentes))
        trava = threading.Lock()
        estado = {"out": feito_out, "n": 0}

        def encoda(item: tuple[VideoSegment, str, Path]) -> None:
            seg, key, dest = item
            if cancel and cancel():
                raise KeyboardInterrupt("exportação cancelada")
            args, _ = _build_video_command(seg, plan, main, cues, ass_dir,
                                           media_paths, hw)
            for old in work.glob(f"seg_{seg.index:04d}_*.mp4"):
                old.unlink(missing_ok=True)
            run_with_progress([*args, str(dest)], seg.out_theoretical, None, cancel)
            seg.file = str(dest)
            seg.measured = probe(dest).duration
            # o manifesto é compartilhado: uma escrita de cada vez
            with trava:
                manifest[str(seg.index)] = {"key": key, "file": seg.file,
                                            "measured": seg.measured}
                manifest_path.write_text(json.dumps(manifest, indent=1),
                                         encoding="utf-8")
                estado["out"] += seg.out_theoretical
                estado["n"] += 1
                if on_progress:
                    on_progress(min(0.999, estado["out"] / total_out),
                                f"encodando trecho {estado['n']}/{len(pendentes)}")

        n_workers = min(workers_de_encode(), len(pendentes))
        if n_workers <= 1:
            for item in pendentes:
                encoda(item)
        else:
            with ThreadPoolExecutor(max_workers=n_workers) as pool:
                futuros = [pool.submit(encoda, item) for item in pendentes]
                erro: BaseException | None = None
                for fut in futuros:
                    try:
                        fut.result()
                    except BaseException as exc:  # noqa: BLE001
                        # o primeiro erro manda; os outros já foram cancelados
                        # ou vão terminar sozinhos e não têm o que dizer
                        erro = erro or exc
                if erro is not None:
                    raise erro

    # 3) a linha do tempo, em ordem, com as durações que saíram de verdade
    cursor = 0.0
    for seg in segs:
        seg.out_start = cursor
        cursor += seg.measured
    if on_progress:
        on_progress(0.999, f"{len(segs)} trecho(s) prontos")
    return segs


# ------------------------------------------------------------------- áudio
def _resample_exact(samples: np.ndarray, target: int) -> tuple[np.ndarray, bool]:
    """Ajusta o PCM ao tamanho exato do bloco de vídeo medido.

    O interp existe para absorver deriva de MILISSEGUNDOS. Quando a fonte tem
    menos áudio que vídeo (trilha que acaba antes, microfone que caiu), esticar
    o que sobrou viraria um time-stretch grave e dessincronizante — nesses
    casos o déficit vira silêncio e o chamador é avisado.

    Devolve (pcm, esticou_demais).
    """
    if target <= 0:
        return np.zeros(0, dtype=np.float32), False
    n = len(samples)
    if n == target:
        return samples.astype(np.float32), False
    if n == 0:
        return np.zeros(target, dtype=np.float32), True
    if abs(n - target) > max(2048, int(target * 0.01)):
        if n < target:
            out = np.concatenate([samples.astype(np.float32),
                                  np.zeros(target - n, dtype=np.float32)])
        else:
            out = samples[:target].astype(np.float32)
        return out, True
    src_x = np.linspace(0.0, 1.0, n, dtype=np.float64)
    dst_x = np.linspace(0.0, 1.0, target, dtype=np.float64)
    return np.interp(dst_x, src_x, samples).astype(np.float32), False


def _fade(samples: np.ndarray, ms: int = FADE_MS) -> np.ndarray:
    """Fade de 12 ms na entrada e na saída. Sem isso, emenda em fala estala."""
    n = int(AUDIO_SR * ms / 1000.0)
    if len(samples) < 2 * n:
        # bloco mais curto que duas rampas: encolhe a rampa em vez de pular o
        # fade — pular deixava exatamente o estalo que o fade evita
        n = len(samples) // 2
    if n <= 0:
        return samples
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    samples = samples.copy()
    samples[:n] *= ramp
    samples[-n:] *= ramp[::-1]
    return samples


def build_audio_track(plan: EditPlan, timeline: Timeline, sources: dict,
                      clip_durations: dict, on_progress: Callable | None = None,
                      warnings: list | None = None) -> np.ndarray:
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
        pcm, stretched = _resample_exact(pcm, target)
        if stretched and warnings is not None:
            warnings.append(
                f"o áudio da fonte é mais curto que o vídeo no bloco que começa "
                f"em {placed.out_start:.1f} s — o que falta virou silêncio em "
                f"vez de esticar a voz")
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
    """Cadeia da Parte 9.1 aplicada UMA vez sobre a faixa inteira.

    O loudnorm roda em duas passadas (mede, depois aplica em modo linear).
    A passada única erra o alvo em mais de 1 LU e ainda encurta a faixa —
    medi 3200 amostras a menos numa faixa de 31 s.

    Com trilha, o mix (voz + música com ducking) é montado ANTES da medição:
    medir só a voz e normalizar o mix erraria o alvo e o pico exatamente no
    caso em que há mais energia na faixa.
    """
    from ..audio.loudness import (build_pre_chain, loudnorm_second_pass,
                                  measure_loudnorm)

    # "mudo" desliga a trilha sem perder o ajuste: o usuário testa com e sem
    music = (plan.music if plan.music and plan.music.get("enabled")
             and not plan.music.get("muted") else None)
    mpath = sources.get(music.get("media_id"), {}).get("path") if music else None
    stage_src = raw_wav
    if music and mpath:
        mix_path = dest.with_name(dest.stem + "_mix.wav")
        graph = F.music_chain(float(music.get("gain_db", -18)),
                              bool(music.get("ducking", True)),
                              float(music.get("duck_amount", 12)),
                              float(music.get("fade_in", 1.0)),
                              float(music.get("fade_out", 2.0)), duration,
                              float(music.get("out_start", 0.0) or 0.0),
                              music.get("out_end"),
                              music.get("curva"))
        run([FFMPEG, "-y", "-v", "error", "-i", str(raw_wav),
             "-stream_loop", "-1", "-i", str(mpath),
             "-filter_complex", graph, "-map", "[aout]",
             "-ac", "1", "-ar", str(AUDIO_SR), "-c:a", "pcm_s16le",
             "-t", f"{duration:.6f}", str(mix_path)])
        stage_src = mix_path

    pre = build_pre_chain(params)
    measured = measure_loudnorm(stage_src, pre, params)
    chain = ",".join(x for x in (pre, loudnorm_second_pass(params, measured)) if x)
    run([FFMPEG, "-y", "-v", "error", "-i", str(stage_src), "-af", chain,
         "-ac", "1", "-ar", str(AUDIO_SR), "-c:a", "pcm_s16le", str(dest)])

    # Trava o comprimento exato. Qualquer filtro que engula ou acrescente
    # amostras vira dessincronia acumulada ao longo de dezenas de blocos.
    target = int(round(duration * AUDIO_SR))
    from ..ffmpeg_utils import read_wav_mono

    samples, sr = read_wav_mono(dest)
    if abs(len(samples) - target) > 0:
        if len(samples) > target:
            samples = samples[:target]
        else:
            samples = np.concatenate(
                [samples, np.zeros(target - len(samples), dtype=np.float32)])
        write_wav(dest, samples, AUDIO_SR)
    if stage_src is not raw_wav:
        Path(stage_src).unlink(missing_ok=True)
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
