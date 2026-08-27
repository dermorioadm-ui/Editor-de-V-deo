"""Teste ponta a ponta: vídeo sintético -> plano -> exportação -> conferência."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

os.environ.setdefault("EDITOR_DATA_DIR", tempfile.mkdtemp(prefix="editor-test-"))

from editor import db, projects as svc                      # noqa: E402
from editor.audio.clap import build_discarded_takes, detect_claps  # noqa: E402
from editor.audio.envelope import compute_envelope          # noqa: E402
from editor.edit.timeline import Timeline                   # noqa: E402
from editor.ffmpeg_utils import probe, read_wav_mono, extract_wav  # noqa: E402
from tests.synth import SR, build, write_video              # noqa: E402

TEXT = ("Presta atenção nisso aqui . "
        "O problema é que você perde cliente todo santo dia . "
        "Então eu montei um jeito de simplesmente cortar sozinho . "
        "São 347 clientes com 82 % de retenção comprovada . "
        "O investimento é de R $ 97 por mês . "
        "E você tem garantia de 30 dias , clica no link .")


class Ctx:
    def __init__(self, quiet=False):
        self.quiet = quiet
        self.last = ""

    def progress(self, f, message="", stage=""):
        if message and message != self.last and not self.quiet:
            print(f"    [{f*100:5.1f}%] {message}")
            self.last = message

    def stage(self, name, message=""):
        self.progress(0, message or name, name)

    def check(self):
        pass

    def cancelled(self):
        return False


def make_source(tmp: Path):
    tokens = TEXT.split()
    spans, t = [], 0.6
    sentence = 0
    for tok in tokens:
        d = 0.10 + len(tok) * 0.055
        spans.append((round(t, 3), round(t + d, 3)))
        t += d
        if tok in ".,":
            t += 1.0 if tok == "." else 0.35
            sentence += 1
        else:
            t += 0.05
    duration = round(t + 1.0, 2)
    audio = build(spans, duration, claps=[], noise=0.0011)
    video = write_video(tmp / "fonte.mp4", audio, duration)
    words = [{"i": i, "start": a, "end": b, "text": tok, "prob": 0.95}
             for i, ((a, b), tok) in enumerate(zip(spans, tokens))]
    return video, words, duration


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="e2e-"))
    print("1) gerando vídeo de teste 1080x1920 …")
    src, words, duration = make_source(tmp)
    info = probe(src)
    print(f"   {info.width}x{info.height} @ {info.fps:.2f} fps, {info.duration:.2f} s, "
          f"{(info.v_bitrate or info.bitrate)/1e6:.2f} Mbps")

    print("2) criando projeto e injetando a análise (sem faster-whisper aqui) …")
    project = svc.create(str(src), "teste", "VSL")
    extract_wav(src, project.wav, 16000, 1)
    samples, sr = read_wav_mono(project.wav)
    env = compute_envelope(samples, sr)
    np.save(project.envelope_file, env.db)
    svc._envelope_cache[project.id] = env
    claps = detect_claps(samples, sr, env)
    takes = [t.to_dict() for t in build_discarded_takes(env, claps, words)]
    from editor.subtitles.fillers import annotate
    project.analysis = {
        "duration": info.duration, "words": words, "segments": [],
        "claps": [c.to_dict() for c in claps], "takes": takes,
        "fillers": annotate(words, env),
        "envelope": {"hop": env.hop, "sample_rate": sr,
                     "noise_floor": env.noise_floor, "duration": env.duration},
    }
    project.save_analysis()
    print(f"   piso de ruído {env.noise_floor:.1f} dB | palmas {len(claps)} | "
          f"vícios {len(project.analysis['fillers'])}")

    print("3) edição automática …")
    res = svc.auto_edit(project, Ctx())
    print(f"   {res['clips']} blocos | {res['subtitles']} legendas | "
          f"auditoria {res['audit']} | duração {res['duration']:.2f} s "
          f"(fonte {duration:.2f} s)")
    project = svc.load(project.id)
    for c in project.plan.clips:
        print(f"     {c.src_start:7.3f}-{c.src_end:7.3f}  v={c.speed:.2f}  "
              f"{c.section:11s} corte_in={c.cut_in} corte_out={c.cut_out}")

    print("4) conferindo que nenhuma palavra preservada foi partida …")
    broken = []
    for w in words:
        covered = any(c.src_start - 1e-6 <= w["start"] and w["end"] <= c.src_end + 1e-6
                      for c in project.plan.clips)
        partial = any(c.src_start < w["end"] - 0.02 and c.src_end > w["start"] + 0.02
                      for c in project.plan.clips) and not covered
        if partial:
            broken.append(w["text"])
    print(f"   palavras partidas: {len(broken)} {broken[:6]}")

    print("5) exportando (uma geração de encode) …")
    t0 = time.time()
    out = svc.export(project, Ctx(quiet=True), {"filename": "saida.mp4"})
    print(f"   {time.time()-t0:.1f} s | {Path(out['output']).name}")
    print(f"   vídeo {out['video_duration']:.3f} s | áudio {out['audio_duration']:.3f} s "
          f"| deriva {out['drift']*1000:+.1f} ms | itsscale={out['itsscale']}")
    deltas = [s["delta_ms"] for s in out["segments"]]
    print(f"   trechos: {len(out['segments'])} | soma dos desvios por bloco: "
          f"{sum(deltas):+.1f} ms | maior {max(map(abs, deltas)):.1f} ms")
    print(f"   bitrate saída {out['bitrate']/1e6:.2f} Mbps | fonte "
          f"{out['source_bitrate']/1e6:.2f} Mbps")
    for w in out["warnings"]:
        print(f"   ! {w}")

    print("6) validando o arquivo final …")
    final = probe(out["output"])
    plan_duration = project.plan.duration
    print(f"   {final.width}x{final.height} @ {final.fps:.2f} fps | "
          f"{final.duration:.3f} s (plano {plan_duration:.3f} s) | "
          f"v={final.v_codec} a={final.a_codec}")
    from editor.render.validate import validate_export
    report = validate_export(Path(out["output"]), [w["text"] for w in words],
                             out["subtitles"], project.plan.audio,
                             info.v_bitrate or info.bitrate, plan_duration)
    print("   container:", json.dumps(report["container"], ensure_ascii=False)[:200])
    print("   áudio:", json.dumps(report["audio"], ensure_ascii=False)[:220])

    print("7) sincronia legenda x corte …")
    tl = Timeline(project.plan.active_clips)
    subs = out["subtitles"]
    print(f"   {len(subs)} legendas, primeira em {subs[0]['start']:.2f} s, "
          f"última termina em {subs[-1]['end']:.2f} s (vídeo {final.duration:.2f} s)")
    assert subs[-1]["end"] <= final.duration + 0.4, "legenda além do fim do vídeo"
    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
