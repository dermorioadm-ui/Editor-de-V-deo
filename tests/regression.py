"""Regressões dos furos de arquitetura — roda pela API de verdade (TestClient).

Cada teste aqui corresponde a um bug que existiu:
1. a velocidade global era um no-op (razão calculada contra o próprio valor);
2. refazer a edição descartava palavras removidas à mão;
3. refazer a edição apagava textos de legenda editados;
4. cortar um trecho deixava overlays/cutaways/desfoques em cima do conteúdo errado;
5. a Timeline dos ops ignorava o fps e a seleção errava o alvo.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["EDITOR_DATA_DIR"] = tempfile.mkdtemp(prefix="editor-reg-")

import numpy as np
from fastapi.testclient import TestClient

from editor import projects as svc
from editor.audio.envelope import compute_envelope
from editor.ffmpeg_utils import extract_wav, read_wav_mono
from editor.server import app
from tests.e2e import Ctx
from tests.fake_whisper import install
from tests.synth import build, write_video

FALHAS: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  OK    " if cond else "  FALHA ") + label)
    if not cond:
        FALHAS.append(label)


def seed_project(client: TestClient, tmp: Path) -> str:
    spans = []
    t = 0.6
    for k in range(14):
        spans.append((round(t, 3), round(t + 0.42, 3)))
        t += 0.42 + (1.0 if k % 3 == 2 else 0.12)
    dur = t + 0.8
    src = write_video(tmp / "reg.mp4", build(spans, dur, noise=0.001), dur)
    r = client.post("/api/projects", json={"source_path": str(src), "preset": "VSL"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    p = svc.load(pid)
    extract_wav(src, p.wav, 16000, 1)
    samples, sr = read_wav_mono(p.wav)
    env = compute_envelope(samples, sr)
    np.save(p.envelope_file, env.db)
    svc._envelope_cache[pid] = env
    texto = ("alfa bravo charlie delta eco fox golf hotel india julia "
             "kilo lima mike nove").split()
    words = [{"i": i, "start": a, "end": b, "text": texto[i], "prob": 0.95}
             for i, (a, b) in enumerate(spans)]
    p.analysis = {"duration": dur, "words": words, "claps": [], "takes": [],
                  "fillers": [], "manual_removed_word_ids": [],
                  "envelope": {"hop": env.hop, "sample_rate": sr,
                               "noise_floor": env.noise_floor,
                               "duration": env.duration}}
    p.save_analysis()
    svc.auto_edit(p, Ctx(quiet=True))
    return pid


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    client = TestClient(app)
    pid = seed_project(client, tmp)

    tl = client.get(f"/api/projects/{pid}").json()["timeline"]
    speeds0 = {b["id"]: b["speed"] for b in tl["blocks"]}
    print(f"projeto semeado: {len(tl['blocks'])} blocos, {len(tl['subtitles'])} legendas")

    # ---- 1) velocidade global de verdade -------------------------------
    client.post(f"/api/projects/{pid}/ops/speed", json={"global": 1.2})
    tl1 = client.get(f"/api/projects/{pid}").json()["timeline"]
    speeds1 = [b["speed"] for b in tl1["blocks"]]
    subiu = all(s1 > s0 - 1e-9 for s1, s0 in zip(speeds1, speeds0.values())) and \
        any(s1 > s0 + 0.05 for s1, s0 in zip(speeds1, speeds0.values()))
    check(subiu, "velocidade global 1.20x realmente acelera os blocos")
    client.post(f"/api/projects/{pid}/ops/speed", json={"global": 1.0})
    tl2 = client.get(f"/api/projects/{pid}").json()["timeline"]
    voltou = all(abs(b["speed"] - speeds0[b["id"]]) <= 0.02 for b in tl2["blocks"]
                 if b["id"] in speeds0)
    check(voltou, "voltar a 1.00x restaura as velocidades originais (±0.02)")

    # ---- 2) remoção manual sobrevive ao refazer edição -----------------
    r = client.post(f"/api/projects/{pid}/ops/remove-words",
                    json={"word_ids": [4]}).json()
    check(r.get("ok", False), "remover a palavra 'eco' pelo texto")
    proj = client.get(f"/api/projects/{pid}").json()
    check(4 in proj["analysis"]["manual_removed_word_ids"],
          "a remoção fica registrada como manual")
    p = svc.load(pid)
    svc.auto_edit(p, Ctx(quiet=True))          # refazer edição
    proj = client.get(f"/api/projects/{pid}").json()
    check(4 in proj["analysis"]["removed_word_ids"],
          "'eco' continua removida depois de refazer a edição")

    # ---- 3) texto de legenda editado sobrevive -------------------------
    subs = proj["timeline"]["subtitles"]
    alvo = subs[1]
    client.put(f"/api/projects/{pid}/subtitles/{alvo['id']}",
               json={"text": "TEXTO MEU, NÃO MEXE"})
    p = svc.load(pid)
    svc.auto_edit(p, Ctx(quiet=True))
    subs2 = client.get(f"/api/projects/{pid}").json()["timeline"]["subtitles"]
    check(any(s["text"] == "TEXTO MEU, NÃO MEXE" for s in subs2),
          "texto editado da legenda sobrevive ao refazer a edição")

    # ---- 4) overlay reancorado depois de um corte anterior -------------
    tl = client.get(f"/api/projects/{pid}").json()["timeline"]
    fim = tl["duration"]
    client.post(f"/api/projects/{pid}/overlays",
                json={"media_id": "x", "out_start": fim - 3.0, "out_end": fim - 1.0})
    blocos = tl["blocks"]
    b0 = blocos[0]
    r = client.post(f"/api/projects/{pid}/ops/delete-range",
                    json={"start": b0["out_start"] + 0.1,
                          "end": b0["out_start"] + 0.7}).json()
    removido = r.get("end", 0) - r.get("start", 0)
    tl3 = client.get(f"/api/projects/{pid}").json()["timeline"]
    ov = tl3["overlays"][0]
    esperado = (fim - 3.0) - (tl["duration"] - tl3["duration"])
    check(abs(ov["out_start"] - esperado) < 0.15,
          f"overlay acompanhou o corte ({fim-3.0:.2f}s → {ov['out_start']:.2f}s, "
          f"esperado ~{esperado:.2f}s)")

    # ---- 5) fps na Timeline dos ops ------------------------------------
    # o delete acima usou tempos da timeline quantizada; se o backend usasse a
    # não-quantizada, a região removida não bateria com o pedido
    check(0.3 < removido < 1.6,
          f"delete-range removeu uma região plausível ({removido:.2f}s)")

    # ---- 6) desfazer restaura plano E estado de remoções juntos --------
    proj = client.get(f"/api/projects/{pid}").json()
    plano_antes = proj["plan"]
    removidas_antes = proj["analysis"]["removed_word_ids"]
    client.post(f"/api/projects/{pid}/ops/remove-words", json={"word_ids": [7]})
    proj2 = client.get(f"/api/projects/{pid}").json()
    check(7 in proj2["analysis"]["removed_word_ids"], "palavra 7 removida")
    # desfazer = mandar o snapshot antigo de volta (plano + listas)
    client.post(f"/api/projects/{pid}/plan", json={
        "plan": plano_antes,
        "removed_word_ids": removidas_antes,
        "manual_removed_word_ids": proj["analysis"].get("manual_removed_word_ids", []),
    })
    proj3 = client.get(f"/api/projects/{pid}").json()
    check(7 not in proj3["analysis"]["removed_word_ids"],
          "desfazer tira a palavra 7 da lista de removidas (estado sincronizado)")
    check(any(7 in s.get("word_ids", []) for s in proj3["timeline"]["subtitles"]),
          "a palavra 7 volta às legendas depois do desfazer")

    print()
    if FALHAS:
        print(f"{len(FALHAS)} FALHA(S):")
        for f in FALHAS:
            print("  -", f)
        return 1
    print("todas as regressões passam")
    return 0


if __name__ == "__main__":
    install(["frase %d" % i for i in range(20)])
    sys.exit(main())
