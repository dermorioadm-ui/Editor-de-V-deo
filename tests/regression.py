"""Regressões dos furos de arquitetura — roda pela API de verdade (TestClient).

Cada teste aqui corresponde a um bug que existiu:
1. a velocidade global era um no-op (razão calculada contra o próprio valor);
2. refazer a edição descartava palavras removidas à mão;
3. refazer a edição apagava textos de legenda editados;
4. cortar um trecho deixava overlays/cutaways/desfoques em cima do conteúdo errado;
5. a Timeline dos ops ignorava o fps e a seleção errava o alvo;
6. os quatro critérios de palma olhavam só o envelope, e uma palavra forte
   depois de uma pausa passava em todos — dava vinte perguntas por vídeo;
7. toda borda suja virava pergunta, mesmo quando a correção era óbvia.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
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

    # ---- 7) prévia 480p não corrompe os parâmetros da exportação final ----
    job = client.post(f"/api/projects/{pid}/preview", json={"scale": "480"}).json()
    for _ in range(240):
        j = {x["id"]: x for x in client.get("/api/jobs").json()}[job["id"]]
        if j["status"] in ("ok", "erro", "cancelado"):
            break
        time.sleep(0.5)
    check(j["status"] == "ok", f"prévia 480p roda ({j['status']} {j.get('error','')[:60]})")
    exp = client.get(f"/api/projects/{pid}").json()["plan"]["export"]
    check(exp["scale"] == "source" and exp["crf"] != 26,
          f"exportação final continua intacta (scale={exp['scale']}, crf={exp['crf']})")

    # ---- 8) dois cliques no mesmo botão = um job só --------------------
    # a exportação é lenta o bastante para o segundo clique chegar com o
    # primeiro job ainda vivo
    j1 = client.post(f"/api/projects/{pid}/export", json={"filename": "dd.mp4"}).json()
    j2 = client.post(f"/api/projects/{pid}/export", json={"filename": "dd.mp4"}).json()
    check(j1["id"] == j2["id"], "segundo clique devolve o MESMO job (dedup)")
    for _ in range(600):
        j = {x["id"]: x for x in client.get("/api/jobs").json()}[j1["id"]]
        if j["status"] in ("ok", "erro", "cancelado"):
            break
        time.sleep(0.5)
    check(j["status"] == "ok", f"a exportação deduplicada termina ({j['status']})")

    # ---- 9) inserir foto reancoradas overlays --------------------------
    tlx = client.get(f"/api/projects/{pid}").json()["timeline"]
    ov_before = tlx["overlays"][0]["out_start"]
    import subprocess as _sp
    foto = tmp / "foto.png"
    _sp.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", "color=c=blue:s=640x480", "-frames:v", "1", str(foto)], check=True)
    mfoto = client.post(f"/api/projects/{pid}/media",
                        json={"path": str(foto), "kind": "image"}).json()
    client.post(f"/api/projects/{pid}/insert",
                json={"media_id": mfoto["id"], "kind": "photo",
                      "at": 1.0, "duration": 2.0})
    tly = client.get(f"/api/projects/{pid}").json()["timeline"]
    ov_after = tly["overlays"][0]["out_start"]
    check(abs(ov_after - (ov_before + 2.0)) < 0.15,
          f"overlay acompanhou a foto inserida ({ov_before:.2f}s → {ov_after:.2f}s, "
          f"esperado ~{ov_before+2.0:.2f}s)")

    # ---- 10) nudge de tempo da legenda sobrevive ao rebuild -------------
    subs = client.get(f"/api/projects/{pid}").json()["timeline"]["subtitles"]
    alvo2 = subs[0]
    auto_end = alvo2["end"]
    for _ in range(3):
        cur = client.get(f"/api/projects/{pid}").json()["timeline"]["subtitles"][0]
        client.put(f"/api/projects/{pid}/subtitles/{alvo2['id']}",
                   json={"end": cur["end"] + 0.1})
    client.post(f"/api/projects/{pid}/subtitles/rebuild")
    depois = client.get(f"/api/projects/{pid}").json()["timeline"]["subtitles"][0]
    check(abs(depois["end"] - (auto_end + 0.3)) < 0.05,
          f"nudge de +0.3s no fim sobrevive ao rebuild "
          f"({auto_end:.2f}s auto → {depois['end']:.2f}s)")

    # ---- 11) nudge não inverte a legenda --------------------------------
    r = client.put(f"/api/projects/{pid}/subtitles/{depois['id']}",
                   json={"end": depois["start"] - 1.0}).json()
    check(r["subtitle"]["end"] >= r["subtitle"]["start"] + 0.19,
          "end nunca fica antes do start (clampado)")

    # ---- 12) texto editado não duplica quando o estilo rechunka ---------
    subs = client.get(f"/api/projects/{pid}").json()["timeline"]["subtitles"]
    marcado = next((x for x in subs if len(x.get("word_ids", [])) >= 2), subs[0])
    print(f"      (marcado: {marcado['text'][:30]!r} ids={marcado['word_ids']})")
    client.put(f"/api/projects/{pid}/subtitles/{marcado['id']}",
               json={"text": "FRASE UNICA MARCADA"})
    client.post(f"/api/projects/{pid}/params",
                json={"style": {"max_chars_per_line": 12},
                      "rebuild_subtitles": True})
    subs2 = client.get(f"/api/projects/{pid}").json()["timeline"]["subtitles"]
    ocorrencias = sum(1 for x in subs2
                      if "FRASE UNICA MARCADA" in x["text"].replace("\n", " "))
    if ocorrencias != 1:
        print("      novos cues:", [(x["text"][:22], x.get("word_ids")) for x in subs2][:8])
    check(ocorrencias == 1,
          f"texto editado aparece exatamente 1x após rechunk ({ocorrencias}x)")
    client.post(f"/api/projects/{pid}/params",
                json={"style": {"max_chars_per_line": 24},
                      "rebuild_subtitles": True})

    # ------------------------------------------------------------- timbre
    # 6. palma x palavra forte: sem o timbre, os dois passam nos mesmos
    #    quatro critérios de envelope.
    print()
    testar_timbre()
    testar_bordas()

    print()
    if FALHAS:
        print(f"{len(FALHAS)} FALHA(S):")
        for f in FALHAS:
            print("  -", f)
        return 1
    print("todas as regressões passam")
    return 0


def testar_timbre() -> None:
    """Palma tem que ser palma, e palavra forte não pode virar pergunta."""
    from editor.audio.clap import detect_claps
    from tests.speech import ESPEAK, build_track, SR

    if not ESPEAK:
        print("  --    timbre de palma (espeak-ng não instalado)")
        return
    frases = [("Presta atenção nisso aqui", 0.9), ("Olha isso", 1.0),
              ("Para tudo", 0.9), ("Pá", 1.0), ("Quarenta reais só hoje", 0.8),
              ("Chega", 1.0), ("Tá", 0.9), ("Isso muda o jogo agora", 0.8)]
    samples, marks, _ = build_track(frases, claps_after={2, 5})
    env = compute_envelope(samples, SR)
    claps = detect_claps(samples, SR, env)
    reais = [m["start"] for m in marks if m.get("clap")]
    achou = sum(1 for t in reais
                if any(abs(c.time - t) < 0.5 and c.confirmed for c in claps))
    falsos = [c for c in claps
              if not any(abs(c.time - t) < 0.5 for t in reais)]
    check(achou == len(reais),
          f"as {len(reais)} palmas reais foram confirmadas ({achou})")
    check(not falsos,
          f"nenhuma palavra forte virou palma ({len(falsos)} falso(s))")


def testar_bordas() -> None:
    """O que dá para acertar sozinho não pode virar pergunta."""
    from editor.edit.audit import audit_edges, settle_edges
    from editor.models import Clip
    from tests.speech import ESPEAK, build_track, SR

    if not ESPEAK:
        print("  --    auditoria de bordas (espeak-ng não instalado)")
        return
    frases = [("Presta atenção nisso aqui porque muda tudo", 1.2),
              ("O problema é que você perde cliente todo dia", 1.2),
              ("Então eu montei um jeito de cortar sozinho", 1.2),
              ("Clica no link aqui embaixo agora", 1.2)]
    samples, marks, _ = build_track(frases, claps_after=set())
    env = compute_envelope(samples, SR)
    words, i = [], 0
    for m in marks:
        toks = m["text"].split()
        passo = (m["end"] - m["start"]) / len(toks)
        for k, tok in enumerate(toks):
            a = m["start"] + k * passo
            words.append({"i": i, "start": round(a, 3),
                          "end": round(a + passo * 0.92, 3),
                          "text": tok, "prob": 0.95})
            i += 1

    def clipe(a: float, b: float, cid: str) -> Clip:
        return Clip(id=cid, source="main", src_start=round(a, 3),
                    src_end=round(b, 3), speed=1.0, section="gancho",
                    cut_in=True, cut_out=True)

    # borda de saída dentro da fala, com pausa depois: dá para abrir a borda
    clips = [clipe(marks[0]["start"] - 0.1, marks[0]["end"] - 0.25, "a"),
             clipe(marks[1]["start"] - 0.1, marks[1]["end"] + 0.1, "b")]
    antes = len(audit_edges(clips, env, words, set()))
    sobra, feitos = settle_edges(clips, env, words, set())
    check(antes > 0 and not sobra and feitos,
          f"borda suja resolvida sozinha ({antes} antes, {len(sobra)} depois)")

    # buraco com palavra removida dentro: fechar traria a fala de volta
    removidas = {w["i"] for w in words
                 if marks[1]["start"] <= w["start"] < marks[1]["end"]}
    clips = [clipe(marks[0]["start"] - 0.1, marks[0]["end"] - 0.25, "a"),
             clipe(marks[2]["start"] - 0.1, marks[2]["end"] + 0.1, "b")]
    settle_edges(clips, env, words, removidas)
    fechou = abs(clips[0].src_end - clips[1].src_start) < 0.002
    check(not fechou, "o buraco com palavra removida dentro NÃO foi fechado")


if __name__ == "__main__":
    install(["frase %d" % i for i in range(20)])
    sys.exit(main())
