"""Regressões dos furos de arquitetura — roda pela API de verdade (TestClient).

Cada teste aqui corresponde a um bug que existiu:
1. a velocidade global era um no-op (razão calculada contra o próprio valor);
2. refazer a edição descartava palavras removidas à mão;
3. refazer a edição apagava textos de legenda editados;
4. cortar um trecho deixava overlays/cutaways/desfoques em cima do conteúdo errado;
5. a Timeline dos ops ignorava o fps e a seleção errava o alvo;
6. os quatro critérios de palma olhavam só o envelope, e uma palavra forte
   depois de uma pausa passava em todos — dava vinte perguntas por vídeo;
7. toda borda suja virava pergunta, mesmo quando a correção era óbvia;
8. a pausa que a pessoa dá ANTES de bater palma contava como fronteira de
   frase, e a palma descartava um trecho vazio em vez do take errado;
9. quando a frase era refeita SEM palma, as duas versões ficavam no vídeo;
10. o corte de silêncio nascia do BURACO ENTRE palavras — quando o Whisper
    esticava uma palavra por cima de uma pausa, o buraco não existia e o vale
    inteiro ia para o vídeo, para o usuário apagar na mão;
11. a legenda podia terminar em palavra pendurada ("perdeu também o");
12. os presets embutidos entravam no banco com INSERT OR IGNORE e toda
    melhoria posterior no código era ignorada em silêncio;
13. depois de palma ou assobio sobrava vazio na emenda.
"""
from __future__ import annotations

import shutil
import json
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
    # media_id de mentira era aceito e o overlay sumia calado no render;
    # agora a rota recusa, então o teste usa uma imagem de verdade
    import subprocess as _sp0
    selo = tmp / "selo.png"
    _sp0.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
              "-i", "color=c=red:s=200x200", "-frames:v", "1", str(selo)], check=True)
    mselo = client.post(f"/api/projects/{pid}/media",
                        json={"path": str(selo), "kind": "image"}).json()
    r_ov = client.post(f"/api/projects/{pid}/overlays",
                       json={"media_id": mselo["id"], "out_start": fim - 3.0,
                             "out_end": fim - 1.0})
    check(r_ov.status_code == 200, f"overlay com mídia real entra ({r_ov.status_code})")
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
    testar_take_da_palma()
    testar_repeticao()
    testar_zoom()
    testar_silencio()
    testar_assobio()
    testar_corte_rente()
    testar_agressividade()
    testar_quebra_pendurada()
    testar_assobio_nao_vira_palma()
    testar_marcador_nao_desfaz()
    testar_retomada_sem_teto()
    testar_anexo_nao_come_palavra()
    testar_legenda_na_mesma_regua()
    testar_controle_manual_de_zoom()
    testar_ia_opina_codigo_executa()
    testar_chave_da_ia_nao_vaza()
    testar_presets_atualizam()

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

    # borda de saída em cima de fala DE VERDADE (o nível ali tem que estar
    # acima do limiar da auditoria, senão o teste não está testando nada)
    corte = None
    for cand in [marks[0]["end"] - d for d in (0.25, 0.35, 0.45, 0.55, 0.65)]:
        if env.value_at(cand) > env.audit_threshold:
            corte = cand
            break
    check(corte is not None, "achei um ponto que é fala para sujar a borda")
    if corte is None:
        return
    clips = [clipe(marks[0]["start"] - 0.1, corte, "a"),
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


def testar_take_da_palma() -> None:
    """A palma descarta a frase em andamento — não um trecho vazio."""
    from editor.audio.clap import build_discarded_takes, detect_claps
    from tests.speech import ESPEAK, build_track, SR

    if not ESPEAK:
        print("  --    take da palma (espeak-ng não instalado)")
        return
    frases = [("O seu anúncio está parado e você não sabe por quê", 1.0),
              ("Eu descobri isso depois de perder três meses", 1.1),
              ("Eu descobri isso depois de perder três meses inteiros", 1.0),
              ("Clica no link aqui embaixo agora", 1.0)]
    samples, marks, _ = build_track(frases, claps_after={1})
    env = compute_envelope(samples, SR)
    words, i = [], 0
    for m in marks:
        if m.get("clap"):
            continue
        toks = m["text"].split()
        passo = (m["end"] - m["start"]) / len(toks)
        for k, tok in enumerate(toks):
            a = m["start"] + k * passo
            words.append({"i": i, "start": round(a, 3),
                          "end": round(a + passo * 0.92, 3),
                          "text": tok, "prob": 0.95})
            i += 1
    claps = detect_claps(samples, SR, env)
    takes = build_discarded_takes(env, claps, words)
    errada = marks[1]
    ok = bool(takes) and takes[0].start <= errada["start"] + 0.25 \
        and takes[0].end >= errada["end"] - 0.25
    check(ok, "a palma descarta a frase em andamento, não um trecho vazio"
              + (f" ({takes[0].start:.2f}-{takes[0].end:.2f}, "
                 f"esperado ~{errada['start']:.2f}-{errada['end']:.2f})"
                 if takes else " (nenhum take)"))
    check(bool(takes) and len(takes[0].text.split()) >= 4,
          "o take descartado tem o texto da frase errada dentro")
    check(all(c.enabled for c in claps),
          "palma nunca vira pergunta: já entra ativa")


def testar_repeticao() -> None:
    """Frase refeita sem palma: sai a primeira, fica a última."""
    from editor.edit.repeats import find_repeats
    from tests.speech import ESPEAK, build_track, SR

    if not ESPEAK:
        print("  --    repetição (espeak-ng não instalado)")
        return
    frases = [("O seu anúncio está parado e você não sabe por quê", 1.0),
              ("O problema não é o preço é a foto do anúncio", 0.9),
              ("O problema não é o preço é a foto do seu anúncio", 1.0),
              ("São trezentos e quarenta e sete anfitriões usando", 0.9),
              ("Clica no link aqui embaixo agora", 1.0)]
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
    reps = find_repeats(words, env)
    check(len(reps) == 1, f"achou exatamente a repetição ({len(reps)})")
    if reps:
        r = reps[0]
        check(abs(r.start - marks[1]["start"]) < 0.3,
              "sai a PRIMEIRA versão (a que deu errado)")
        check(abs(r.kept_start - marks[2]["start"]) < 0.3,
              "fica a ÚLTIMA versão")
    # as frases diferentes não podem se atrair
    check(not any(abs(r.start - marks[3]["start"]) < 0.3 for r in reps),
          "frase de assunto diferente não vira repetição")


def testar_zoom() -> None:
    """O zoom entre cenas, contra a especificação do usuário."""
    from editor.config import ZoomParams
    from editor.edit.zoom import (MIN_SCENE, MIN_STEP, ancora_alcancavel,
                                  assign_zoom, auditar, cenas, recorte,
                                  zoom_chain, zoom_maximo)
    from editor.models import Clip

    # --- teto pela resolução da fonte
    check(abs(zoom_maximo(1080, 1080) - 1.15) < 0.001,
          f"fonte = saída dá teto 1,15x ({zoom_maximo(1080, 1080):.2f})")
    check(abs(zoom_maximo(3840, 1080) - 1.25) < 0.001,
          f"fonte 4K para saída 1080 dá teto 1,25x ({zoom_maximo(3840, 1080):.2f})")

    # --- VSL com blocos de tamanhos reais, inclusive os de 0,13 s
    import random

    rng = random.Random(4)
    secoes = (["gancho"] * 3 + ["dor"] * 4 + ["mecanismo"] * 3
              + ["explicacao"] * 5 + ["revelacao"] * 3 + ["prova"] * 3
              + ["oferta"] * 3 + ["garantia"] * 2 + ["cta"] * 1)
    clips, t = [], 0.0
    for sec in secoes:
        d = rng.choice([0.13, 0.9, 2.4, 3.8, 5.5, 7.2])
        clips.append(Clip(source="main", src_start=t, src_end=t + d, section=sec))
        t += d + 0.5
    params = ZoomParams(seconds_per_scene=4.5, amplitude=0.08, max_zoom=1.15,
                        face_x=0.50, face_y=0.44)
    r = assign_zoom(clips, params, 1080, 1080)
    lista = cenas(clips)
    main = [c for c in clips if c.enabled]

    # 1. a troca SÓ pode acontecer em cima de um corte
    sem_corte = [b for a, b in zip(main, main[1:])
                 if abs(a.zoom - b.zoom) > 1e-6
                 and abs(a.src_end - b.src_start) < 0.002]
    check(not sem_corte,
          f"nenhuma troca de enquadramento fora de um corte ({len(sem_corte)})")

    # 2. bloco de 0,13 s não vira enquadramento próprio (era o efeito pisca)
    curtos = [c for c in clips if c.src_duration < 0.2]
    sozinhos = [c for c in curtos if any(x["clip_ids"] == [c.id] for x in lista)]
    check(curtos and not sozinhos,
          f"nenhum dos {len(curtos)} blocos de 0,13 s virou cena própria")

    # 3. nenhum enquadramento mais curto que o mínimo confortável
    curtas = [c for c in lista if c["duration"] < MIN_SCENE]
    check(not curtas,
          f"nenhum enquadramento abaixo de {MIN_SCENE:.1f} s ({len(curtas)})")

    # 4. diferença menor que 0,05 não lê como troca de plano. Onde a faixa da
    #    etapa não permite um passo desses (a VSL tem amplitude 0,08 e teto
    #    1,15: cabem poucos níveis), a troca sutil é preferível a repetir o
    #    valor — repetir funde as duas cenas numa só. Mas ela NUNCA pode ser
    #    zero, e tem que aparecer na auditoria.
    difs = [abs(lista[i]["zoom"] - lista[i - 1]["zoom"])
            for i in range(1, len(lista))]
    fracas = [d for d in difs if d < MIN_STEP]
    check(all(d > 1e-6 for d in difs),
          "nenhuma troca é zero (valor repetido fundiria as duas cenas)")
    check(len(fracas) <= len(difs) * 0.25,
          f"a maioria das trocas passa de {MIN_STEP} "
          f"({len(difs) - len(fracas)} de {len(difs)})")
    reportadas = sum(1 for a in auditar(clips, params, r["teto"])
                     if a["kind"] == "troca-fraca")
    check(reportadas >= len(fracas),
          f"toda troca sutil aparece na auditoria ({reportadas} para {len(fracas)})")

    # 5. o plano aberto reaparece: escada que só fecha sufoca o vídeo
    abertos = sum(1 for c in lista if abs(c["zoom"] - 1.0) < 0.02)
    check(abertos >= 2, f"o plano aberto volta para dar respiro ({abertos} de {len(lista)})")

    # 6. nada acima do teto que a fonte aguenta
    acima = [c for c in lista if c["zoom"] > r["teto"] + 1e-6]
    check(not acima, f"nenhum enquadramento acima do teto da fonte ({len(acima)})")

    # 7. recorte CONCÊNTRICO: o rosto não pode andar na tela
    ax, ay = params.anchor_x, params.anchor_y
    centros = [recorte(c["zoom"], 1080, 1920, ax, ay) for c in lista
               if c["zoom"] > 1.001]
    desvio = 0.0
    if centros:
        cys = [y + h / 2 for _x, y, _w, h in centros]
        cxs = [x + w / 2 for x, _y, w, _h in centros]
        desvio = max(max(cys) - min(cys), max(cxs) - min(cxs))
    check(desvio <= 4.0,
          f"o rosto fica parado entre enquadramentos ({desvio:.1f} px em 1920)")

    # 8. a âncora respeita o que a geometria permite
    ax2, ay2 = ancora_alcancavel(0.50, 0.44, 1.03)
    check(abs(ay2 - 0.4854) < 0.002,
          f"a âncora é puxada para o alcançável no menor zoom ({ay2:.4f})")

    # 9. amplitude por preset muda a intensidade
    story = ZoomParams(seconds_per_scene=2.5, amplitude=0.18, max_zoom=1.25)
    c2 = [Clip(source="main", src_start=i * 4.0, src_end=i * 4.0 + 3.0,
               section="gancho") for i in range(8)]
    r2 = assign_zoom(c2, story, 3840, 1080)
    maior_vsl = max((c["zoom"] for c in lista), default=1.0)
    maior_story = max(c.zoom for c in c2)
    check(maior_story > maior_vsl,
          f"Story fecha mais que VSL ({maior_story:.2f}x contra {maior_vsl:.2f}x)")
    check(r2["teto"] > r["teto"],
          f"fonte 4K libera teto maior ({r2['teto']:.2f} contra {r['teto']:.2f})")

    # 10. travar impede o recálculo de mexer
    c2[3].zoom_locked = True
    travado = c2[3].zoom
    assign_zoom(c2, ZoomParams(seconds_per_scene=9.9, amplitude=0.02), 3840, 1080)
    check(abs(c2[3].zoom - travado) < 1e-9,
          "bloco travado sobrevive ao recálculo automático")

    # 11. a cadeia de filtro
    ch = zoom_chain(1.14, 1080, 1920, 1080, 1920, 0.5, 0.485)
    check("crop=" in ch and "scale=w=1080:h=1920" in ch and "unsharp=" in ch,
          "a cadeia recorta, volta ao tamanho de saída e compensa com unsharp")
    check(zoom_chain(1.0, 1080, 1920, 1080, 1920, 0.5, 0.5) == "",
          "zoom 1,00x não põe filtro nenhum na cadeia")
    x, y, w, h = recorte(1.14, 1080, 1920, 0.5, 0.485)
    check(w % 2 == 0 and h % 2 == 0 and x % 2 == 0 and y % 2 == 0,
          f"largura e altura do recorte são pares ({w}x{h} em {x},{y})")

    # 12. a auditoria acha o que deve achar
    c2[0].zoom = 1.99
    avisos = auditar(c2, story, r2["teto"])
    check(any(a["kind"] == "acima-do-teto" for a in avisos),
          "a auditoria acusa enquadramento acima do teto da fonte")


def testar_silencio() -> None:
    """O vale de silêncio tem que sumir mesmo com a palavra esticada por cima."""
    import editor.edit.plan_builder as pb
    from editor.audio.align import long_silences_inside, trim_words
    from editor.config import CutParams, SpeedParams
    from editor.edit.plan_builder import build_auto_plan
    from tests.speech import ESPEAK, build_track, SR

    if not ESPEAK:
        print("  --    corte de silêncio (espeak-ng não instalado)")
        return
    frases = [("O seu anúncio está parado e você não sabe por quê", 3.5),
              ("Eu descobri isso depois de perder três meses", 2.8),
              ("São trezentos e quarenta e sete anfitriões usando", 4.2),
              ("Clica no link aqui embaixo agora", 1.0)]
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
                          "text": tok, "prob": 0.94})
            i += 1
    # o defeito real: a última palavra de cada frase esticada até a seguinte
    comecos = {m["start"] for m in marks}
    for w in words:
        seguinte = min((c for c in comecos if c > w["end"] + 0.5), default=None)
        if seguinte and any(abs(w["end"] - m["end"]) < 0.35 for m in marks):
            w["end"] = round(seguinte - 0.05, 3)

    cut = CutParams(silence_min=0.70, air=0.25, margin=0.15, min_block=1.0)

    def vales(ws: list[dict]) -> float:
        res = build_auto_plan(ws, env, cut, SpeedParams(), [], extra_removed=set())
        achados = long_silences_inside(res["clips"], env, cut.silence_min)
        return sum(v["duration"] for v in achados)

    # sem nenhuma das duas defesas, o vale ia inteiro para o vídeo
    real = pb._split_on_silence
    pb._split_on_silence = lambda spans, env_, params: spans
    try:
        antes = vales(words)
    finally:
        pb._split_on_silence = real
    check(antes > 3.0,
          f"o defeito existe mesmo: {antes:.1f} s de vale sem as defesas")

    check(vales(words) < 0.05,
          "a rede de segurança sozinha zera o vale (parte o span pelo envelope)")

    encaixadas, fixes = trim_words(words, env)
    check(len(fixes) >= 3,
          f"o encaixe acha as palavras esticadas ({len(fixes)})")
    pb._split_on_silence = lambda spans, env_, params: spans
    try:
        so_encaixe = vales(encaixadas)
    finally:
        pb._split_on_silence = real
    check(so_encaixe < 0.05,
          "o encaixe sozinho também zera o vale")
    check(vales(encaixadas) < 0.05,
          "com as duas defesas, nenhum vale sobra para apagar na mão")

    # o encaixe só ENCOLHE, nunca cresce — crescer restauraria silêncio
    cresceu = [w for w, e in zip(words, encaixadas)
               if e["start"] < w["start"] - 1e-6 or e["end"] > w["end"] + 1e-6]
    check(not cresceu, f"o encaixe nunca estica uma palavra ({len(cresceu)})")
    # e nunca joga a palavra para depois do vazio
    fora = [f for f in fixes if f["to"][0] > f["from"][0] + 0.5]
    check(not fora,
          f"a palavra fica onde foi ouvida, não no fim do vazio ({len(fora)})")


def testar_assobio() -> None:
    """Assobio contra fala, vogal, sibilante e palma."""
    import numpy as np

    from editor.audio.clap import detect_claps
    from editor.audio.whistle import calibrar, detect_whistles
    from tests.speech import ESPEAK, SR, say

    if not ESPEAK:
        print("  --    assobio (espeak-ng não instalado)")
        return

    def assobio(f0: float, dur: float = 0.7) -> np.ndarray:
        n = int(dur * SR)
        t = np.arange(n) / SR
        freq = f0 * (1.0 + 0.012 * np.sin(2 * np.pi * 5.5 * t))
        fase = 2 * np.pi * np.cumsum(freq) / SR
        x = np.sin(fase) + 0.06 * np.sin(2 * fase)
        env = np.minimum(1.0, np.minimum(t * 14, (dur - t) * 10))
        ruido = np.random.default_rng(3).normal(0, 0.02, n)
        return ((x * env + ruido * env) * 0.45).astype(np.float32)

    def palma(seed: int = 1) -> np.ndarray:
        rng = np.random.default_rng(seed)
        n = int(0.11 * SR)
        t = np.arange(n) / SR
        return (rng.normal(0, 1, n).astype(np.float32) * np.exp(-t * 42) * 0.95)

    partes, marcas, t = [], [], 0.0

    def por(x, rot=None):
        nonlocal t
        partes.append(x)
        if rot:
            marcas.append((rot, t + len(x) / SR / 2))
        t += len(x) / SR

    def sil(d):
        nonlocal t
        partes.append(np.zeros(int(d * SR), dtype=np.float32))
        t += d

    sil(0.4)
    por(say("Presta atenção nisso aqui porque muda tudo"))
    sil(0.35); por(assobio(1650), "assobio")
    sil(1.1); por(say("O problema não é o preço"))
    sil(0.35); por(palma(), "palma")
    sil(1.0); por(say("O problema não é o preço é a foto"))
    sil(0.35); por(assobio(1700, 0.45), "assobio")
    sil(0.9); por(say("Clica no link aqui embaixo agora"))
    # casos que NÃO podem virar assobio
    for v in ("aaaaaaaaaa", "iiiiiiiiii", "ssssssssss"):
        sil(0.6); por(say(v, speed=90))
    sil(0.5)
    x = np.clip(np.concatenate(partes)
                + np.random.default_rng(2).normal(0, 0.0012, int(t * SR)
                                                  ).astype(np.float32), -1, 1)
    env = compute_envelope(x, SR)

    reais = [m for m in marcas if m[0] == "assobio"]
    achados = detect_whistles(x, SR, env)
    certos = [a for a in achados if any(abs(a.time - m[1]) < 0.6 for m in reais)]
    falsos = [a for a in achados if a not in certos]
    check(len(certos) == len(reais),
          f"achou os {len(reais)} assobios ({len(certos)})")
    check(not falsos,
          f"vogal, sibilante e palma não viraram assobio ({len(falsos)} falso(s))")

    claps = detect_claps(x, SR, env)
    cruzados = [c for c in claps if any(abs(c.time - a.time) < 0.3 for a in achados)]
    check(not cruzados, f"palma e assobio não se confundem ({len(cruzados)})")

    # a energia grave é o critério que sustenta tudo: confere a margem
    check(all(a.grave < 0.02 for a in achados),
          f"todo assobio tem quase nada de grave "
          f"({max((a.grave for a in achados), default=0):.4f})")

    cal = np.concatenate([assobio(1680), np.zeros(int(0.6 * SR), dtype=np.float32),
                          assobio(1710), np.zeros(int(0.6 * SR), dtype=np.float32),
                          assobio(1655)])
    r = calibrar(cal, SR, env)
    check(r["ok"] and abs(r["freq"] - 1682) < 120,
          f"a calibração mede a frequência do usuário ({r.get('freq')} Hz)")


def testar_corte_rente() -> None:
    """Depois do marcador não pode sobrar vazio."""
    import numpy as np

    from editor.config import CutParams, SpeedParams
    from editor.edit.plan_builder import build_auto_plan
    from tests.speech import ESPEAK, SR, say

    if not ESPEAK:
        print("  --    corte rente (espeak-ng não instalado)")
        return
    a1 = say("Presta atenção nisso aqui porque muda tudo")
    a2 = say("O problema não é o preço é a foto")
    a3 = say("Clica no link aqui embaixo agora")
    partes, t = [], 0.0

    def por(x):
        nonlocal t
        partes.append(x)
        r = (t, t + len(x) / SR)
        t += len(x) / SR
        return r

    def sil(d):
        nonlocal t
        partes.append(np.zeros(int(d * SR), dtype=np.float32))
        t += d

    sil(0.5); f1 = por(a1)
    marcador = t + 0.15                 # o assobio cairia aqui
    sil(6.0)                            # ele demora 6 s para recomeçar
    f2 = por(a2)
    sil(1.4)                            # pausa normal, SEM marcador
    f3 = por(a3)
    sil(0.5)
    x = np.clip(np.concatenate(partes)
                + np.random.default_rng(1).normal(0, 0.0012, int(t * SR)
                                                  ).astype(np.float32), -1, 1)
    env = compute_envelope(x, SR)
    words, i = [], 0
    for (t0, t1), txt in ((f1, "a b c d e f g"), (f2, "h i j k l m"),
                          (f3, "n o p q r s")):
        toks = txt.split()
        passo = (t1 - t0) / len(toks)
        for k, tok in enumerate(toks):
            s0 = t0 + k * passo
            words.append({"i": i, "start": round(s0, 3),
                          "end": round(s0 + passo * 0.9, 3), "text": tok})
            i += 1
    cut = CutParams(silence_min=0.70, air=0.25, margin=0.15, min_block=1.0,
                    adaptive_floor=False)

    def sobra(markers):
        r = build_auto_plan(words, env, cut, SpeedParams(), [], markers=markers)
        cl = sorted([c for c in r["clips"] if c.enabled], key=lambda c: c.src_start)
        fim = max(w["end"] for w in words if w["end"] < marcador)
        esq = max((c.src_end for c in cl if c.src_end <= marcador + 0.5), default=None)
        return None if esq is None else (esq - fim)

    sem = sobra(None)
    com = sobra([marcador])
    check(sem is not None and com is not None, "as duas emendas foram medidas")
    if sem is None or com is None:
        return
    check(com < sem - 0.05,
          f"o marcador cola a emenda ({sem * 1000:.0f} ms -> {com * 1000:.0f} ms)")
    check(com < 0.12,
          f"quase nada de silêncio sobra depois do marcador ({com * 1000:.0f} ms)")


def testar_agressividade() -> None:
    """O controle único e o piso medido na fala do usuário."""
    import random

    from editor.config import CutParams
    from editor.edit.plan_builder import aplicar_agressividade, piso_de_silencio

    valores = [aplicar_agressividade(CutParams(aggressiveness=a))
               for a in (0.0, 0.5, 1.0)]
    check(valores[0].silence_min > valores[1].silence_min > valores[2].silence_min,
          "subir o controle corta pausas cada vez menores")
    check(all(v.margin <= v.air for v in valores),
          "a margem nunca passa o ar (senão a geometria come mais do que o corte pediu)")
    check(aplicar_agressividade(CutParams()).silence_min == CutParams().silence_min,
          "sem o controle, os três parâmetros do preset valem como estão")

    def fala(base: float) -> list[dict]:
        rng = random.Random(3)
        t, w = 0.0, []
        for i in range(120):
            d = rng.uniform(0.2, 0.5)
            w.append({"i": i, "start": round(t, 3), "end": round(t + d, 3), "text": "x"})
            t += d + (rng.uniform(base * 0.5, base * 1.6) if i % 9
                      else rng.uniform(0.9, 1.8))
        return w

    lento, _ = piso_de_silencio(fala(0.28), CutParams(silence_min=0.70))
    rapido, _ = piso_de_silencio(fala(0.08), CutParams(silence_min=0.70))
    check(lento > rapido,
          f"quem fala devagar ganha piso maior ({lento:.2f} s contra {rapido:.2f} s)")
    check(rapido < 0.70 and lento < 0.70,
          "o piso medido destrava pausas que o preset deixaria passar")
    check(piso_de_silencio(fala(0.28),
                           CutParams(silence_min=0.70, adaptive_floor=False))[0] == 0.70,
          "dá para desligar o piso adaptativo")


def testar_quebra_pendurada() -> None:
    """A legenda não pode terminar esperando a próxima palavra."""
    from editor.config import SubtitleStyle
    from editor.subtitles.linebreak import build_cues, termina_pendurado

    texto = ("você que tem AirBnB já perdeu também o prazer de administrar, "
             "até porque você, no final das contas, arca com todos os prejuízos. "
             "Por mais que você pague 20% para uma administradora, no final de "
             "tudo, ela traz o problema para você e o que ela faz? Só responde "
             "aos hóspedes, coisa que a inteligência artificial poderia fazer.")
    t, words = 0.0, []
    for i, tok in enumerate(texto.split()):
        d = 0.11 + len(tok) * 0.052
        words.append({"i": i, "start": round(t, 3), "end": round(t + d, 3), "text": tok})
        t += d + (0.34 if tok[-1] in ".?!" else 0.16 if tok[-1] == "," else 0.045)
    st = SubtitleStyle(fontsize=35, max_chars_per_line=24, max_lines=2,
                       max_duration=2.6)
    cues = build_cues(words, st)

    pend = [c for c in cues if termina_pendurado(c["text"])]
    check(not pend, f"nenhuma legenda termina pendurada ({len(pend)})")
    curtas = [c for c in cues[:-1]
              if len(c["text"].replace("\n", " ").split()) <= 1]
    check(not curtas, f"nenhuma legenda de uma palavra só ({len(curtas)})")
    orfas = [c for c in cues if "\n" in c["text"]
             and min(len(l) for l in c["text"].split("\n")) < 6]
    check(not orfas, f"nenhuma linha órfã de um fiapo ({len(orfas)})")
    check(termina_pendurado("perdeu também o") and not termina_pendurado("perdeu tudo."),
          "a regra sabe distinguir palavra pendurada de fim de ideia")


def testar_assobio_nao_vira_palma() -> None:
    """O marcador de ACERTEI não pode apagar a frase que ele aprovou.

    Um assobio com sopro numa sala viva passa em planura espectral e em razão
    agudo/grave — dois de três critérios — e entrava na lista de palmas com
    enabled=True. A partir dali build_discarded_takes apagava a frase inteira
    anterior: a regra 3 quebrada pelo marcador que existe para protegê-la.
    Reproduzido em 6 de 12 combinações de duração e sopro antes do conserto.
    """
    import numpy as np

    from editor.audio.clap import detect_claps, timbre_features
    from editor.audio.envelope import compute_envelope

    SR = 16000
    rng = np.random.default_rng(7)

    def sala(y, forca=0.15, seed=11):
        r = np.random.default_rng(seed)
        n = int(0.12 * SR)
        ir = r.normal(0, 1, n) * np.exp(-np.arange(n) / (0.025 * SR))
        ir[0] = 1.0
        wet = np.convolve(y, ir)[:len(y)]
        wet = wet / max(float(np.abs(wet).max()), 1e-9) * float(np.abs(y).max())
        out = (1 - forca) * y + forca * wet + r.normal(0, forca * 0.02, len(y))
        return (out / max(float(np.abs(out).max()), 1e-9) * 0.45).astype(np.float32)

    def assobio(dur, f0, sopro):
        t = np.arange(int(dur * SR)) / SR
        vib = 1.0 + 0.008 * np.sin(2 * np.pi * 5.0 * t)
        x = np.sin(2 * np.pi * f0 * t * vib) + 0.18 * np.sin(2 * np.pi * 2 * f0 * t * vib)
        n = 129
        k = np.arange(n) - n // 2
        h = np.sinc(k * 2 * (1.6 * f0) / SR) - np.sinc(k * 2 * (0.7 * f0) / SR)
        ar = np.convolve(rng.normal(0, sopro, t.size), h * np.hanning(n), mode="same")
        e = np.ones_like(t)
        r = int(0.03 * SR)
        e[:r] = np.linspace(0, 1, r)
        e[-r:] = np.linspace(1, 0, r)
        y = (x + ar) * e
        return (y / float(np.abs(y).max()) * 0.45).astype(np.float32)

    def palma(seed):
        r = np.random.default_rng(seed)
        n = int(0.09 * SR)
        y = r.normal(0, 1, n) * np.exp(-np.arange(n) / (0.010 * SR))
        return (y / float(np.abs(y).max()) * 0.45).astype(np.float32)

    def fala(dur, seed=3):
        r = np.random.default_rng(seed)
        t = np.arange(int(dur * SR)) / SR
        f0 = 120 + 12 * np.sin(2 * np.pi * 1.3 * t)
        ph = np.cumsum(2 * np.pi * f0 / SR)
        y = sum(np.sin(k * ph) / k for k in range(1, 14))
        y = y * (0.5 + 0.5 * np.abs(np.sin(2 * np.pi * 2.6 * t)))
        y = y + r.normal(0, 0.02, t.size)
        return (y / float(np.abs(y).max()) * 0.40).astype(np.float32)

    def sil(d):
        return rng.normal(0, 0.0006, int(d * SR)).astype(np.float32)

    def veredito(sig):
        trilha = np.concatenate([fala(3.0), sil(0.6), sig, sil(0.6), fala(3.0)])
        env = compute_envelope(trilha, SR)
        meio = 3.6 + len(sig) / SR / 2
        achou = [c for c in detect_claps(trilha, SR, env) if abs(c.time - meio) < 0.7]
        return achou, trilha, 3.6, 3.6 + len(sig) / SR

    falsos, conc_assobio = 0, []
    for f0 in (2400, 3000, 3400):
        for dur in (0.20, 0.30, 0.40, 0.55):
            for sopro in (0.10, 0.20):
                sig = sala(assobio(dur, f0, sopro))
                achou, trilha, a, b = veredito(sig)
                conc_assobio.append(timbre_features(trilha, SR, a, b)["concentracao"])
                if achou:
                    falsos += 1
    check(falsos == 0,
          f"nenhum assobio virou palma em {len(conc_assobio)} combinações ({falsos})")

    # e a palma de verdade continua sendo palma — o conserto não pode cegar
    achadas, conc_palma = 0, []
    for seed in (1, 2, 9, 15):
        sig = sala(palma(seed), 0.06)
        achou, trilha, a, b = veredito(sig)
        conc_palma.append(timbre_features(trilha, SR, a, b)["concentracao"])
        if achou:
            achadas += 1
    check(achadas == 4, f"as 4 palmas continuam sendo palma ({achadas})")

    margem = min(conc_assobio) / max(max(conc_palma), 1e-9)
    check(margem >= 3.0,
          f"concentração separa com folga: palma até {max(conc_palma):.3f}, "
          f"assobio a partir de {min(conc_assobio):.3f} ({margem:.1f}x)")


def testar_marcador_nao_desfaz() -> None:
    """A emenda de marcador é intocável: _uncut não devolve o vazio.

    settle_edges fecha um buraco de silêncio de até 6 s quando a borda fica
    suja. Num corte rente a borda fica MAIS perto da fala, então a chance de
    disparar sobe — e o corte que o assobio pediu voltava atrás sozinho, em
    silêncio, sem nada na tela dizendo que tinha voltado.
    """
    from editor.edit.audit import _uncut
    from editor.models import Clip

    class EnvFalso:
        duration = 30.0

        def value_at(self, t):
            return -60.0

    def par(fim, comeco):
        return [Clip(id="a", source="main", src_start=0.0, src_end=fim),
                Clip(id="b", source="main", src_start=comeco, src_end=comeco + 3.0)]

    issue = {"clip_id": "b", "side": "in", "time": 8.0, "message": ""}

    # sem marcador: o buraco de 4 s fecha, como sempre fez
    clips = par(5.0, 9.0)
    feito = _uncut(clips, EnvFalso(), [], set(), dict(issue), [])
    check(feito is not None and clips[0].src_end == 9.0,
          "sem marcador, o buraco de silêncio ainda é fechado")

    # com marcador dentro: não fecha, e a emenda continua colada
    clips = par(5.0, 9.0)
    feito = _uncut(clips, EnvFalso(), [], set(), dict(issue), [7.0])
    check(feito is None and clips[0].src_end == 5.0,
          "o buraco que veio de palma/assobio NÃO é desfeito")

    # marcador longe: volta a fechar (a trava é local, não geral)
    clips = par(5.0, 9.0)
    feito = _uncut(clips, EnvFalso(), [], set(), dict(issue), [22.0])
    check(feito is not None and clips[0].src_end == 9.0,
          "marcador longe não trava buraco nenhum")


def testar_retomada_sem_teto() -> None:
    """Demorar 30 s para recomeçar não pode deixar vazio para trás.

    "SE EU FALAR EM 10 S O CORTE TEM QUE SER NO LIMITE." O teto de 6 s cortava
    o take no meio do vazio; o resto do silêncio caía na regra comum e ainda
    ganhava ar dos dois lados.
    """
    import numpy as np

    from editor.audio.clap import resume_point_after
    from editor.audio.envelope import compute_envelope

    SR = 16000
    rng = np.random.default_rng(5)

    def fala(dur, seed=3):
        r = np.random.default_rng(seed)
        t = np.arange(int(dur * SR)) / SR
        ph = np.cumsum(2 * np.pi * (120 + 12 * np.sin(2 * np.pi * 1.3 * t)) / SR)
        y = sum(np.sin(k * ph) / k for k in range(1, 14))
        y = y * (0.5 + 0.5 * np.abs(np.sin(2 * np.pi * 2.6 * t)))
        return ((y / float(np.abs(y).max())) * 0.40 + r.normal(0, 0.02, t.size)
                ).astype(np.float32)

    for espera in (4.0, 10.0, 30.0):
        vazio = rng.normal(0, 0.0006, int(espera * SR)).astype(np.float32)
        trilha = np.concatenate([fala(2.0), vazio, fala(2.0)])
        env = compute_envelope(trilha, SR)
        palavras = [{"start": 0.1, "end": 1.9, "text": "errei"},
                    {"start": 2.0 + espera, "end": 3.9 + espera, "text": "de novo"}]
        t = resume_point_after(env, 2.0, palavras)
        sobra = (2.0 + espera) - t
        check(sobra <= 0.35,
              f"espera de {espera:.0f} s: sobra {sobra * 1000:.0f} ms de vazio")


def testar_anexo_nao_come_palavra() -> None:
    """Cobertura mais curta que a janela cortava o fim da frase.

    O caminho: o ffmpeg entrega um segmento curto, render_video_segments grava
    a duração medida, export soma essa duração menor, build_audio_track pede um
    alvo menor e _resample_exact corta o PCM em samples[:alvo]. O fim da frase
    some, e o único sintoma era um aviso de texto invertido. Regra 3, quebrada
    em silêncio, sem nenhum teste em cima.
    """
    from editor.anexos import AnexoInvalido, encaixar, sem_sobreposicao, validar

    video = {"id": "v1", "kind": "video", "name": "corte.mp4",
             "info": {"duration": 2.0}}
    imagem = {"id": "i1", "kind": "image", "name": "selo.png", "info": {}}
    midias = [video, imagem]

    # 1) a janela ENCOLHE para o que a mídia cobre — nunca o contrário
    j = encaixar(video, out_start=10.0, out_end=16.0, limite=60.0)
    check(abs((j.out_end - j.out_start) - 2.0) < 0.01,
          f"janela de 6 s com mídia de 2 s virou {j.out_end - j.out_start:.2f} s")
    check(any("encurtei" in a for a in j.ajustes),
          "o encurtamento é dito, não feito escondido")

    # e com velocidade: 2x consome o dobro da mídia por segundo de saída
    j2 = encaixar(video, 10.0, 16.0, speed=2.0, limite=60.0)
    check(abs((j2.out_end - j2.out_start) - 1.0) < 0.01,
          f"a 2x a mesma mídia cobre metade ({j2.out_end - j2.out_start:.2f} s)")

    # já o media_start come da sobra
    j3 = encaixar(video, 10.0, 16.0, media_start=1.5, limite=60.0)
    check(abs((j3.out_end - j3.out_start) - 0.5) < 0.01,
          f"entrando em 1,5 s sobra 0,5 s ({j3.out_end - j3.out_start:.2f} s)")

    # 2) mídia que não existe: erro na hora de pedir, não no render
    for alvo, esperado in (("", "faltou"), ("nao_existe", "não está no projeto")):
        try:
            validar(midias, alvo, "video")
            check(False, f"mídia '{alvo}' deveria ter sido recusada")
        except AnexoInvalido as exc:
            check(esperado in str(exc), f"mídia '{alvo}' recusada: {exc}")

    # 3) tipo trocado nos dois sentidos
    try:
        validar(midias, "i1", "video")
        check(False, "imagem como cobertura deveria ser recusada")
    except AnexoInvalido as exc:
        check("imagem" in str(exc), f"imagem não vira cobertura: {exc}")
    try:
        validar(midias, "v1", "image")
        check(False, "vídeo como sobreposição deveria ser recusado")
    except AnexoInvalido as exc:
        check("vídeo" in str(exc), f"vídeo não vira sobreposição: {exc}")

    # 4) fora do vídeo
    try:
        encaixar(video, 70.0, 72.0, limite=60.0)
        check(False, "instante fora do vídeo deveria ser recusado")
    except AnexoInvalido as exc:
        check("fora do vídeo" in str(exc), f"anexo fora do vídeo recusado: {exc}")
    j4 = encaixar(video, 59.0, 62.0, limite=60.0)
    check(j4.out_end <= 60.0 and any("passava" in a for a in j4.ajustes),
          f"o que passava do fim foi aparado ({j4.out_end:.1f} s)")

    # 5) duas coberturas no mesmo lugar: o render descartava a segunda calado
    class Falso:
        def __init__(self, a, b):
            self.id, self.out_start, self.out_end, self.enabled = "c1", a, b, True
    try:
        sem_sobreposicao([Falso(4.0, 8.0)], 6.0, 10.0)
        check(False, "cobertura sobreposta deveria ser recusada")
    except AnexoInvalido as exc:
        check("no mesmo lugar" in str(exc), f"cobertura sobreposta recusada: {exc}")
    sem_sobreposicao([Falso(4.0, 8.0)], 8.0, 12.0)
    check(True, "encostar não é sobrepor")
    sem_sobreposicao([Falso(4.0, 8.0)], 6.0, 10.0, ignorar="c1")
    check(True, "arrastar a própria cobertura não colide consigo mesma")

    # 6) sobra curta demais vira erro, não sujeira no concat
    try:
        encaixar({"id": "v", "kind": "video", "info": {"duration": 0.1}},
                 1.0, 5.0, limite=60.0)
        check(False, "janela de 0,1 s deveria ser recusada")
    except AnexoInvalido as exc:
        check("curta demais" in str(exc), f"janela mínima respeitada: {exc}")


def testar_legenda_na_mesma_regua() -> None:
    """A legenda queimada tem que ocupar a MESMA fatia da tela em toda saída.

    O ASS era escrito com PlayRes = resolução do RENDER, mas fontsize, contorno
    e margens do estilo são pixels absolutos calibrados para a FONTE. Medido
    com o filtro ass do ffmpeg, texto "ISSO MUDA TUDO", estilo de 1080x1920:

        export 1080x1920 -> 47,4% da largura
        export  720x1280 -> 71,1%
        export  480x854  -> 100,0%   (de ponta a ponta da tela)

    A prévia 480p renderizada passava pelo mesmo caminho — é parte do motivo
    de a legenda aparecer gigante nela.
    """
    import subprocess

    import numpy as np

    from editor.config import SubtitleStyle
    from editor.subtitles.ass import write_ass

    tmp = Path(tempfile.mkdtemp(prefix="legenda_regua_"))
    st = SubtitleStyle()
    st.fontsize, st.margin_v, st.outline, st.shadow = 66, 414, 7.5, 1.9
    cues = [{"start": 0.0, "end": 2.0, "text": "ISSO MUDA TUDO"}]

    def fatia(w: int, h: int) -> float:
        ass = tmp / f"s_{w}.ass"
        write_ass(ass, cues, st, 1080, 1920)   # PlayRes = a FONTE, sempre
        png = tmp / f"s_{w}.png"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", f"color=c=black:s={w}x{h}:d=1",
                        "-vf", f"ass='{ass}'", "-frames:v", "1", str(png)],
                       check=True)
        cru = subprocess.run(["ffmpeg", "-v", "error", "-i", str(png),
                              "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                             capture_output=True, check=True).stdout
        img = np.frombuffer(cru, np.uint8).reshape(h, w)
        cols = np.where(img.max(axis=0) > 40)[0]
        return (cols[-1] - cols[0] + 1) / w if cols.size else 0.0

    medidas = {f"{w}x{h}": fatia(w, h)
               for w, h in ((1080, 1920), (720, 1280), (480, 854))}
    espalha = max(medidas.values()) - min(medidas.values())
    check(espalha < 0.02,
          "a legenda ocupa a mesma fatia em toda resolução ("
          + ", ".join(f"{k} {v*100:.1f}%" for k, v in medidas.items()) + ")")
    check(all(v < 0.60 for v in medidas.values()),
          f"nenhuma saída tem legenda de ponta a ponta (máx {max(medidas.values())*100:.0f}%)")
    shutil.rmtree(tmp, ignore_errors=True)


def testar_controle_manual_de_zoom() -> None:
    """Os três caminhos manuais de enquadramento — todos quebrados antes."""
    from editor.edit.speed import classify
    from editor.models import SECTIONS
    from editor.projects import _enquadramentos_travados, _restaurar_travados

    # 1) as dez etapas são alcançáveis. cta somava em garantia (bug puro) e
    #    mecanismo/monetizacao não tinham saco de palavras nenhum — três
    #    etapas mortas, entre elas a de enquadramento mais fechado (cta, 1,12)
    frases = ["clica no link agora", "garanto sem risco", "o metodo passo a passo",
              "quanto voce fatura por mes", "o preco e esse por apenas",
              "o problema e que ninguem", "descobri a verdade", "presta atencao",
              "olha o resultado comprovado", "e assim que funciona na pratica"]
    achou = {classify(t, pos, wps, 3.0, pos > 0.9)[0]
             for t in frases for pos in (0.0, 0.2, 0.5, 0.75, 0.95)
             for wps in (1.5, 2.5, 3.6)}
    faltam = sorted(set(SECTIONS) - achou)
    check(not faltam, f"as {len(SECTIONS)} etapas são alcançáveis (faltavam {faltam})")
    check(classify("clica no link aqui embaixo agora", 0.92, 2.5, 3.0, True)[0] == "cta",
          "chamada para ação vira CTA, não Garantia")

    # 2) a trava de enquadramento sobrevive a refazer a edição
    class Falso:
        def __init__(self, a, b, zoom=1.0, locked=False):
            self.src_start, self.src_end = a, b
            self.zoom, self.zoom_locked, self.source = zoom, locked, "main"

    antigos = [Falso(0.0, 4.0), Falso(4.0, 9.0, 1.18, True), Falso(9.0, 12.0)]
    guardados = _enquadramentos_travados(antigos)
    check(len(guardados) == 1, f"guardou a trava ({len(guardados)})")
    # a reedição mexeu nas bordas, como sempre mexe
    novos = [Falso(0.0, 3.8), Falso(3.9, 8.7), Falso(8.9, 12.0)]
    voltaram = _restaurar_travados(novos, guardados)
    check(voltaram == 1 and novos[1].zoom_locked and abs(novos[1].zoom - 1.18) < 1e-6,
          "a trava voltou para o bloco certo depois de refazer a edição")
    check(not novos[0].zoom_locked and not novos[2].zoom_locked,
          "nenhum bloco vizinho foi travado por engano")

    # e quando o bloco antigo virou outra coisa, a trava NÃO é chutada adiante
    picado = [Falso(0.0, 4.0), Falso(4.0, 5.0), Falso(11.0, 12.0)]
    check(_restaurar_travados(picado, guardados) == 0,
          "trava não é chutada num bloco que sobrou pela metade")


def testar_ia_opina_codigo_executa() -> None:
    """A IA nunca escreve edição direto — e o que não cabe é RECUSADO.

    Sem chamar a rede: a resposta do modelo é montada à mão, inclusive as
    respostas ruins que um modelo dá de verdade (bloco que não existe, etapa
    inventada, ênfase em tudo, anexo maior que a mídia, dois anexos no mesmo
    lugar, mídia do tipo errado, bloco travado pelo usuário).
    """
    from editor.ai.roteiro import ENFASES, aplicar, blocos_do_plano, montar_pedido
    from editor.models import SECTIONS, Clip, EditPlan

    plan = EditPlan()
    plan.clips = [Clip(id=f"c{i}", source="main", src_start=i * 4.0,
                       src_end=i * 4.0 + 3.5) for i in range(6)]
    plan.clips[4].zoom_locked = True         # o usuário travou este
    plan.clips[4].zoom = 1.15
    palavras = [{"start": i * 4.0 + 0.2, "end": i * 4.0 + 3.0, "id": i,
                 "text": f"frase{i}"} for i in range(6)]

    blocos = blocos_do_plano(plan, palavras)
    check(len(blocos) == 6, f"seis blocos com fala ({len(blocos)})")
    check(all(b.texto for b in blocos), "todo bloco leva o texto que caiu nele")

    midias = [
        {"id": "mv", "kind": "video", "name": "b-roll.mp4",
         "info": {"duration": 2.0}},
        {"id": "mi", "kind": "image", "name": "print.png", "info": {}},
    ]
    pedido = montar_pedido(blocos, midias, 24.0)
    check("b-roll.mp4" in pedido and "frase0" in pedido,
          "o pedido leva o texto e a lista de mídias")
    check("/" not in pedido.replace("Facebook/Instagram", ""),
          "nenhum caminho de arquivo vai no pedido")

    resposta = {
        "leitura": "vende um método de tráfego",
        "blocos": [
            {"i": 0, "etapa": "gancho", "enfase": "fechado", "porque": "abre"},
            {"i": 1, "etapa": "dor", "enfase": "aberto", "porque": "contexto"},
            {"i": 2, "etapa": "cta", "enfase": "fechado", "porque": "pico"},
            {"i": 3, "etapa": "prova", "enfase": "fechado", "porque": "numero"},
            {"i": 4, "etapa": "oferta", "enfase": "fechado", "porque": "preco"},
            {"i": 5, "etapa": "inventada", "enfase": "fechado", "porque": "?"},
            {"i": 99, "etapa": "gancho", "enfase": "normal", "porque": "?"},
        ],
        "anexos": [
            {"midia": 0, "bloco": 1, "tipo": "cobertura", "segundos": 5.0,
             "porque": "ilustra"},
            {"midia": 1, "bloco": 2, "tipo": "cobertura", "segundos": 3.0,
             "porque": "tipo errado"},
            {"midia": 7, "bloco": 0, "tipo": "sobreposicao", "segundos": 2.0,
             "porque": "mídia que não existe"},
        ],
    }
    rel = aplicar(plan, resposta, midias, duracao_saida=24.0)

    # 1) o que não existe e o que é inventado são RECUSADOS, com motivo
    motivos = " | ".join(r["motivo"] for r in rel["recusados"])
    check(any("não existe" in r["motivo"] for r in rel["recusados"]),
          "bloco inexistente recusado")
    check(any("etapa desconhecida" in r["motivo"] for r in rel["recusados"]),
          "etapa inventada recusada")
    check(any("travou" in r["motivo"] for r in rel["recusados"]),
          "bloco travado pelo usuário é intocável para a IA")
    check(plan.clips[4].zoom_locked and abs(plan.clips[4].zoom - 1.15) < 1e-9,
          "a trava do usuário continua exatamente como estava")
    check(plan.clips[4].section != "oferta", "e a etapa dele não foi trocada")

    # 2) etapa e ênfase entraram nos blocos válidos, marcadas como da IA
    check(plan.clips[0].section == "gancho" and plan.clips[0].section_source == "ia",
          "a etapa da IA entrou e ficou marcada como dela")
    check(plan.clips[1].emphasis == "aberto", "respiro virou plano aberto")
    check(plan.clips[1].emphasis in ("", *ENFASES), "ênfase sempre de um valor válido")

    # 3) ênfase com parcimônia: 4 "fechado" em 6 blocos viram no máximo 2
    fechados = [c for c in plan.clips if c.emphasis == "fechado"]
    check(len(fechados) <= max(1, len(plan.clips) // 3),
          f"ponto alto em no máximo um terço dos blocos ({len(fechados)} de 6)")
    check(any("nada" in r["motivo"] for r in rel["recusados"]),
          "e o excesso de ênfase é dito, não cortado calado")

    # 4) anexos: a janela ENCOLHE para o que a mídia cobre; tipo errado e
    #    mídia inexistente são recusados
    check(len(rel["anexos"]) == 1, f"só um anexo entrou ({len(rel['anexos'])})")
    a = rel["anexos"][0]
    check(abs((a["out_end"] - a["out_start"]) - 2.0) < 0.01,
          f"os 5 s pedidos viraram os 2 s que a mídia tem "
          f"({a['out_end'] - a['out_start']:.1f} s)")
    check("é uma imagem" in motivos, "imagem como cobertura recusada")
    check("mídia 7 não existe" in motivos, "mídia inventada recusada")

    # 5) a etapa vira enquadramento pela tabela de sempre, não por número da IA
    check(all(SECTIONS.get(c.section) for c in plan.clips if c.section),
          "toda etapa aplicada existe na tabela")


def testar_chave_da_ia_nao_vaza() -> None:
    """A chave fica em texto puro no SQLite. Ela não pode sair por rota nenhuma.

    O app escuta em 127.0.0.1, mas o iniciar-rede.bat existe justamente para
    revisar do celular — e aí qualquer um na rede local alcança as rotas.
    """
    from editor.server import app

    cliente = TestClient(app)
    marca = "AIzaSyCHAVE-DE-TESTE-QUE-NAO-PODE-VAZAR-9876"
    try:
        cliente.post("/api/ai/config", json={"chave": marca})
        cfg = cliente.get("/api/ai/config").json()
        check("AIzaSy" not in json.dumps(cfg),
              "a rota de config devolve o estado, nunca a chave")
        check(cfg["tem_chave"] and cfg["final"] == "9876",
              f"mas devolve o final, para reconhecer qual chave está lá ({cfg['final']})")

        for rota in ("/api/health", "/api/projects", "/api/presets"):
            corpo = cliente.get(rota).text
            check(marca not in corpo and "AIzaSy" not in corpo,
                  f"a chave não aparece em {rota}")

        # e o erro de chave inválida sai em português, não como stack trace
        r = cliente.post("/api/ai/test")
        detalhe = str(r.json().get("detail", ""))
        check(r.status_code == 400 and "chave" in detalhe.lower()
              and marca not in detalhe,
              f"chave recusada com motivo legível e sem eco da chave: {detalhe[:60]}")
    finally:
        cliente.post("/api/ai/config", json={"chave": ""})


def testar_presets_atualizam() -> None:
    """Melhoria no preset embutido tem que CHEGAR em quem já instalou."""
    import json as _json
    import tempfile as _tmp

    from editor import db as _db
    from editor.presets import PRESETS_VERSION

    anterior = os.environ.get("EDITOR_DATA_DIR")
    novo = _tmp.mkdtemp(prefix="preset-")
    os.environ["EDITOR_DATA_DIR"] = novo
    try:
        import importlib

        import editor.config as _cfg
        importlib.reload(_cfg)
        importlib.reload(_db)
        _db.connect()
        from editor.presets import get_preset
        vsl = get_preset("VSL")
        check(vsl is not None and vsl["style"]["fontsize"] == 35,
              f"o preset novo entra com fonte 35 ({vsl['style']['fontsize']})")
        check("zoom" in (vsl or {}),
              "o preset novo traz os parâmetros de zoom")

        # simula o banco de quem instalou antes: preset velho + versão velha
        velho = {**vsl, "style": {**vsl["style"], "fontsize": 64}}
        _db.ex("UPDATE presets SET data_json=? WHERE name='VSL'",
               (_json.dumps(velho),))
        _db.ex("INSERT INTO presets(name,data_json,builtin,updated_at) VALUES(?,?,0,0)",
               ("Meu preset", _json.dumps({"name": "Meu preset",
                                           "style": {"fontsize": 99}})))
        _db.ex("INSERT INTO settings(key,value) VALUES('presets_version','1') "
               "ON CONFLICT(key) DO UPDATE SET value='1'")
        _db._initialized = False
        _db.connect()
        check(get_preset("VSL")["style"]["fontsize"] == 35,
              "o preset embutido é atualizado quando a versão sobe")
        check(get_preset("Meu preset")["style"]["fontsize"] == 99,
              "o preset que o usuário salvou NÃO é tocado")
        check(int(_db.get_setting("presets_version")) == PRESETS_VERSION,
              "a versão fica gravada")
    finally:
        if anterior:
            os.environ["EDITOR_DATA_DIR"] = anterior
        import importlib

        import editor.config as _cfg
        importlib.reload(_cfg)
        importlib.reload(_db)
        _db.connect()


if __name__ == "__main__":
    install(["frase %d" % i for i in range(20)])
    sys.exit(main())
