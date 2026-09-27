"""Orquestra a exportação inteira (Parte 10.3, 10.4)."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..edit.timeline import Timeline
from ..ffmpeg_utils import MediaInfo, concat_demux, probe, write_wav
from ..models import EditPlan
from ..subtitles import ass as ass_mod
from .renderer import (AUDIO_SR, RenderResult, build_audio_track, mux,
                       fps_de_saida, plan_segments, process_audio,
                       render_video_segments,
                       target_size)

SYNC_TOLERANCE = 0.030      # acima disso o vídeo é reescalado por timestamp


def _hash_audio(plan: EditPlan, timeline, clip_durations: dict,
                sources: dict, limpas: dict | None = None) -> str:
    """A identidade da FAIXA DE ÁUDIO. Se não muda, não se refaz.

    Entra tudo que decide como o áudio soa: quais pedaços da fonte tocam, em
    que ordem, em que velocidade e por quanto tempo; a cadeia de tratamento; e
    a trilha. NÃO entra nada de imagem — é justamente isso que faz um retoque
    visual não pagar o loudnorm do vídeo inteiro.
    """
    import hashlib
    import json

    blocos = [{
        "s": p.clip.source, "a": round(p.clip.src_start, 4),
        "b": round(p.clip.src_end, 4), "v": round(p.clip.speed, 4),
        "m": round(clip_durations.get(p.clip.id, 0.0), 4),
        # MUDO PELO CAMPO QUE EXISTE. Aqui estava
        # `bool(getattr(p.clip, "muted", False))`, e Clip nunca teve campo
        # `muted`: o getattr devolvia o padrão False para todo bloco, sempre.
        # O campo real é `audio`, que o render lê em renderer.py:1008 e a rota
        # escreve em server.py:1367. O efeito era o pior que este cache pode
        # ter: emudecer um bloco não mexia na chave, o audio.wav era
        # reaproveitado e A FALA CONTINUAVA NO ARQUIVO EXPORTADO — o usuário
        # mandava calar, via o bloco marcado como mudo na tela e ouvia a voz
        # no vídeo que foi para a pasta.
        "k": p.clip.kind, "mudo": p.clip.audio == "mute",
    } for p in timeline]
    payload = {
        "blocos": blocos,
        "audio": plan.audio.__dict__,
        "musica": plan.music,
        "fontes": {k: v.get("path") for k, v in sorted(sources.items())},
        # a voz limpa entra na chave: se o redutor não baixou numa exportação
        # e baixou na seguinte, o áudio tem de ser refeito com ela
        "limpas": {k: Path(v).name for k, v in sorted((limpas or {}).items())},
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True,
                                   default=str).encode()).hexdigest()[:16]


def _vozes_limpas(plan: EditPlan, timeline, sources: dict, report,
                  cancel, avisos: list) -> dict:
    """fonte -> WAV com a voz limpa, para cada gravação que toca no vídeo.

    Em geral já está pronta: a limpeza começa quando o arquivo chega. Se não
    está (ou o redutor ainda não foi baixado), é feita aqui, com o número na
    tela. Falhou? O vídeo sai com a voz original e um aviso — nunca sem som.
    """
    if not getattr(plan.audio, "voz_ia", False):
        return {}
    from ..audio import voz

    if not voz.suportado():
        return {}
    usadas = []
    for p in timeline:
        c = p.clip
        if c.kind == "photo" or c.audio == "mute" or c.source in usadas:
            continue
        if sources.get(c.source, {}).get("path"):
            usadas.append(c.source)
    limpas: dict[str, str] = {}
    for i, fonte in enumerate(usadas):
        caminho = sources[fonte]["path"]
        base = 0.66 + 0.10 * i / len(usadas)
        topo = 0.66 + 0.10 * (i + 1) / len(usadas)
        report(0.0, "voz de estúdio: limpando o ruído (IA, neste computador)",
               base, topo)
        try:
            limpas[fonte] = str(voz.fonte_limpa(
                caminho, plan.audio.voz_limpeza,
                progresso=lambda f: report(f, f"voz de estúdio: limpando o "
                                              f"ruído {f * 100:.0f}%", base, topo),
                cancelar=cancel))
        except Exception as exc:  # noqa: BLE001 — sai com a voz original
            if cancel and cancel():
                raise
            aviso = (f"a voz saiu SEM a limpeza de ruído por IA ({exc}). "
                     f"Exporte de novo para tentar outra vez.")
            if aviso not in avisos:
                avisos.append(aviso)
    return limpas


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
    import shutil as _shutil

    dest = Path(dest)
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    main: MediaInfo = sources["main"]["info"]
    pre_warnings: list[str] = []
    try:
        free = _shutil.disk_usage(work).free
        needed = max(int(main.size_bytes * 2.5), 500_000_000)
        if free < needed:
            pre_warnings.append(
                f"pouco espaço em disco: {free/1e9:.1f} GB livres onde a "
                f"exportação trabalha (~{needed/1e9:.1f} GB recomendados para "
                f"este vídeo). Apague exportações antigas ou as pastas work/ "
                f"de projetos velhos.")
    except OSError:
        pass
    width, height = target_size(main, plan.export)

    # A LEGENDA NA RÉGUA DO FORMATO DE SAÍDA. As legendas eram montadas com o
    # estilo da FONTE — 42 caracteres por linha no horizontal — e o vertical
    # derivado só cabe 24: o ASS usa WrapStyle 2 (sem quebra automática) e a
    # linha saía da tela pelos dois lados. Quem monta as legendas recebe o
    # estilo do formato quando aceita; e, aceitando ou não, nenhuma linha
    # passa da régua.
    import inspect

    from .renderer import regua_da_legenda
    from ..subtitles.linebreak import requebrar

    _pw, _ph, style_saida = regua_da_legenda(main, plan.export, plan.style)
    derivado = style_saida is not plan.style
    try:
        aceita_estilo = len(inspect.signature(build_cues).parameters) >= 2
    except (TypeError, ValueError):
        aceita_estilo = False

    def cues_no_formato(tl: Timeline) -> list[dict]:
        lista = build_cues(tl, style_saida) if (derivado and aceita_estilo) else build_cues(tl)
        if derivado:
            chars = int(getattr(style_saida, "max_chars_per_line", 0) or 0)
            linhas = int(getattr(style_saida, "max_lines", 2) or 2)
            if chars > 0:
                for c in lista:
                    c["text"] = requebrar(c.get("text", ""), chars, linhas)
        return lista

    def report(frac: float, msg: str, lo: float = 0.0, hi: float = 1.0):
        if on_progress:
            on_progress(lo + (hi - lo) * max(0.0, min(1.0, frac)), msg)

    # 1) linha do tempo teórica e legendas de partida
    # A grade da linha do tempo é a da SAÍDA, não a da gravação: exportando um
    # 60 fps a 30, quantizar em 60 deixaria metade das bordas entre dois
    # quadros de saída, e a sobra de cada bloco viraria deriva somada no
    # concat.
    fps_saida = fps_de_saida(main, plan.export)
    timeline = Timeline(plan.active_clips, fps_saida)
    cues = cues_no_formato(timeline)

    # 2) trechos de vídeo (cutaways já aplicados) — UM encode por trecho
    report(0.0, "planejando trechos")
    segs = plan_segments(plan, timeline, sources, main)
    media_paths = {k: v["path"] for k, v in sources.items()}
    # CADA SAÍDA TEM O SEU PRÓPRIO CACHE DE TRECHOS.
    #
    # Isto era um buraco caro e invisível: a prévia de 240p e a exportação
    # final gravavam no MESMO `work/segments`, com o manifesto indexado pelo
    # número do trecho. As chaves de conteúdo diferem (uma é 240p, a outra é a
    # resolução da fonte), então cada uma despejava a entrada da outra — e a
    # prévia ainda vinha com `restart`, que apagava a pasta inteira. Resultado
    # prático: TODA edição refazia o vídeo inteiro duas vezes, uma em 240p e
    # outra em tamanho real. Num vídeo de 9 minutos isso é a máquina travada
    # sem parar, que é exatamente o que o usuário estava sentindo.
    #
    # A geometria e a escala entram no nome da pasta, então prévia, final e
    # cada formato derivado guardam o seu e nenhum atrapalha o outro.
    aspecto = str(getattr(plan.export, "aspect", "fonte") or "fonte")
    escala = str(getattr(plan.export, "scale", "source") or "source")
    sub = f"segments-{aspecto.replace(':', 'x')}-{escala}"
    segs = render_video_segments(
        segs, plan, main, cues, work / sub, media_paths, hw,
        on_progress=lambda f, m: report(f, m, 0.02, 0.62), cancel=cancel)
    for s in segs:
        for aviso in getattr(s, "avisos", None) or []:
            if aviso not in pre_warnings:
                pre_warnings.append(aviso)

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
    measured_timeline = Timeline(plan.active_clips, fps_saida)
    cues_final = cues_no_formato(measured_timeline)

    # 6) áudio em PCM, cadeia aplicada uma vez, AAC só no mux
    #
    # COM CACHE. O loudnorm são duas passadas sobre a faixa inteira, e ela não
    # muda quando o retoque foi visual — trocar o texto de uma legenda, mexer
    # no zoom, no filtro. Sem cache, cada retoque pagava o áudio do vídeo
    # inteiro de novo, e era o que sobrava de "exportar tudo a cada ação"
    # depois que os trechos de vídeo já vinham do cache.
    limpas = _vozes_limpas(plan, measured_timeline, sources, report, cancel,
                           pre_warnings)
    report(0.0, "montando o áudio", 0.76, 0.80)
    chave_audio = _hash_audio(plan, measured_timeline, clip_durations, sources,
                              limpas)
    processed = work / "audio.wav"
    marca = work / "audio.key"
    reusa = (processed.exists() and marca.exists()
             and marca.read_text(encoding="utf-8").strip() == chave_audio)
    if reusa:
        report(1.0, "o áudio não mudou — reaproveitado", 0.66, 0.86)
    else:
        track = build_audio_track(plan, measured_timeline, sources, clip_durations,
                                  on_progress=lambda f, m: report(f, m, 0.76, 0.80),
                                  warnings=pre_warnings, limpas=limpas)
        raw_wav = work / "audio_raw.wav"
        write_wav(raw_wav, track, AUDIO_SR)
        report(0.0, ("tratando a voz (estúdio → compressor → loudnorm)"
                     if plan.audio.voz_estudio else
                     "processando o áudio (highpass → compressor → loudnorm)"),
               0.80, 0.86)
        process_audio(raw_wav, processed, plan.audio, plan, sources,
                      duration=len(track) / AUDIO_SR, limpa=bool(limpas))
        marca.write_text(chave_audio, encoding="utf-8")
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
    # o .ass entregue ao lado do MP4 usa a MESMA régua do que foi queimado:
    # a da fonte no formato da fonte, a do quadro derivado nos outros (ver
    # regua_da_legenda em renderer.py)
    ass_mod.write_ass(ass_path, cues_final, style_saida, _pw, _ph)

    warnings: list[str] = list(pre_warnings)
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
