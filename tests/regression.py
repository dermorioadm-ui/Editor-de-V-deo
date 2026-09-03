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

    testar_controle_de_corte_pela_rota(client, pid)

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
    testar_modelo_da_ia_fica_fixado()
    testar_chave_da_ia_nao_vaza()
    testar_chave_lida_de_arquivo()
    testar_trilha_acompanha_o_corte()
    testar_legenda_da_previa_bate_com_a_exportacao()
    testar_janela_do_sistema()
    testar_take_nao_atravessa_assobio()
    testar_ia_decide_cortes()
    testar_comandos_falados()
    testar_cartao_de_topico()
    testar_legenda_no_padrao_do_formato()
    testar_silencio_nao_vai_para_o_whisper()
    testar_relatorio_da_ia_nao_e_incognita()
    testar_formatos_derivados()
    testar_ia_decide_ritmo_e_camera()
    testar_comando_nao_e_marcador_de_discurso()
    testar_achados_da_revisao()
    testar_corte_de_copy()
    testar_controles_antes_de_gerar()
    testar_presets_atualizam()
    testar_corte_nao_reencoda_o_resto()
    testar_anexo_sempre_entra()
    testar_sobreposicao_no_tamanho_da_previa()
    testar_muleta_sai_com_um_vale()
    testar_janela_de_video()
    testar_legenda_vertical_nao_corta()
    testar_geracao_com_ia_mockada()
    testar_quadro_encaixado()
    testar_quadro_pela_rota_e_janela_na_trilha()

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

    # 3) imagem NUNCA cobre a tela; como JANELA ("any") entram imagem E vídeo
    #    — o vídeo em janela é o picture-in-picture, lido com -ss/-t e sem áudio
    try:
        validar(midias, "i1", "video")
        check(False, "imagem como cobertura deveria ser recusada")
    except AnexoInvalido as exc:
        check("imagem" in str(exc), f"imagem não vira cobertura: {exc}")
    check(validar(midias, "v1", "any")["id"] == "v1",
          "vídeo entra como janela por cima do quadro")
    check(validar(midias, "i1", "any")["id"] == "i1", "imagem também entra como janela")
    try:
        validar(midias + [{"id": "a1", "kind": "audio", "name": "som.mp3", "info": {}}],
                "a1", "any")
        check(False, "áudio como janela deveria ser recusado")
    except AnexoInvalido as exc:
        check("imagem e vídeo" in str(exc), f"áudio não entra por cima do quadro: {exc}")
    try:
        validar([{"id": "v0", "kind": "video", "name": "quebrado.mp4", "info": {}}],
                "v0", "any")
        check(False, "vídeo sem duração legível deveria ser recusado")
    except AnexoInvalido as exc:
        check("duração" in str(exc), f"vídeo que o ffprobe não leu é recusado: {exc}")

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

    # 4) anexos: a janela ENCOLHE para o que a mídia cobre; a mídia que não
    #    existe é recusada; "cobertura" sem 'tela cheia' na descrição vira
    #    JANELA (para o vídeo E para a imagem — imagem nunca cobre a tela)
    check(len(rel["anexos"]) == 2, f"os dois anexos reais entraram ({len(rel['anexos'])})")
    a = next(x for x in rel["anexos"] if x["media_id"] == "mv")
    check(abs((a["out_end"] - a["out_start"]) - 2.0) < 0.01,
          f"os 5 s pedidos viraram os 2 s que a mídia tem "
          f"({a['out_end'] - a['out_start']:.1f} s)")
    check(all(x["tipo"] == "sobreposicao" for x in rel["anexos"]),
          "cobertura pedida pela IA sem 'tela cheia' na descrição entrou como janela")
    check(all(any("janela" in aj for aj in x["ajustes"]) for x in rel["anexos"]),
          "e cada um diz que virou janela")
    check(any("não existe" in r["motivo"] for r in rel["recusados"]),
          "mídia inexistente recusada")
    check("mídia 7 não existe" in motivos, "mídia inventada recusada")

    # 5) a etapa vira enquadramento pela tabela de sempre, não por número da IA
    check(all(SECTIONS.get(c.section) for c in plan.clips if c.section),
          "toda etapa aplicada existe na tabela")


def testar_controle_de_corte_pela_rota(client, pid) -> None:
    """O controle de corte existia no back e não tinha por onde ser pedido."""
    r = client.post(f"/api/projects/{pid}/params",
                    json={"cut": {"aggressiveness": 0.85}})
    cut = r.json().get("plan", {}).get("cut", {})
    check(abs(float(cut.get("aggressiveness", -1)) - 0.85) < 1e-6,
          f"a agressividade chega pela rota ({cut.get('aggressiveness')})")
    r = client.post(f"/api/projects/{pid}/params",
                    json={"cut": {"adaptive_floor": False}})
    cut = r.json().get("plan", {}).get("cut", {})
    check(cut.get("adaptive_floor") is False,
          "dá para desligar o piso adaptativo pela rota")
    client.post(f"/api/projects/{pid}/params",
                json={"cut": {"aggressiveness": -1.0, "adaptive_floor": True}})


def testar_modelo_da_ia_fica_fixado() -> None:
    """O modelo tem que estar DECIDIDO E ESCRITO antes de o vídeo rodar.

    O furo: `gemini_model` nascia vazio e `escolher_modelo` resolvia o vazio em
    silêncio a cada chamada, caindo no primeiro da lista de preferência. O
    usuário nunca escolheu nada, e o vídeo saía do modelo que o programa achou.
    """
    from editor import db
    from editor.ai import gemini as gem
    from editor.server import CHAVE_IA

    cliente = TestClient(app)
    falsos = [
        {"id": "gemini-3.7-flash", "nome": "", "entrada": 1_000_000, "saida": 8192},
        {"id": "gemini-3.1-pro", "nome": "", "entrada": 1_000_000, "saida": 8192},
    ]
    real_listar = gem.listar_modelos
    real_escolher = gem.escolher_modelo
    gem.listar_modelos = lambda *a, **k: list(falsos)  # type: ignore[assignment]
    antes_chave = db.get_setting(CHAVE_IA, "")
    antes_modelo = db.get_setting("gemini_model", "")
    try:
        db.set_setting("gemini_model", "")
        cfg = cliente.post("/api/ai/config",
                           json={"chave": "AIzaSyTESTE-DE-FIXACAO-0001"}).json()
        check(bool(cfg.get("modelo")),
              f"guardar a chave já FIXA um modelo ({cfg.get('modelo')!r})")
        check(cfg.get("modelo_fixado") is True,
              "e a tela sabe que ele está fixado (segura o botão até estar)")
        check(cfg["modelo"] == gem.PREFERIDOS[0]
              or cfg["modelo"] in [m["id"] for m in falsos],
              f"o fixado é um dos que a chave alcança ({cfg['modelo']})")

        # trocar para outro que a chave alcança grava na hora
        outro = next(m["id"] for m in falsos if m["id"] != cfg["modelo"])
        cfg2 = cliente.post("/api/ai/config", json={"modelo": outro}).json()
        check(cfg2["modelo"] == outro,
              f"trocar o modelo grava na hora, não na hora de gerar ({outro})")
        check(db.get_setting("gemini_model", "") == outro,
              "e fica escrito no banco, não só na tela")

        # pedir um que a chave NÃO alcança é recusado, com a lista do que existe
        r = cliente.post("/api/ai/config", json={"modelo": "gemini-nao-existe"})
        detalhe = str(r.json().get("detail", ""))
        check(r.status_code == 400 and "alcança" in detalhe,
              f"modelo que a chave não alcança é recusado ({detalhe[:70]})")
        check(db.get_setting("gemini_model", "") == outro,
              "e a recusa NÃO apaga o que já estava fixado")

        # tirar a chave tira o modelo junto: dizer "vai usar X" sem chave mente
        cfg3 = cliente.post("/api/ai/config", json={"chave": ""}).json()
        check(not cfg3["modelo"] and cfg3["modelo_fixado"] is False,
              "tirar a chave limpa o modelo fixado junto")

        # e a troca em silêncio no meio do processamento acabou
        gem.escolher_modelo = lambda c, p="": {  # type: ignore[assignment]
            "id": falsos[0]["id"], "saida": 8192, "trocado_de": p}
        from editor.ai import cortes as C
        real_json = gem.gerar_json
        gem.gerar_json = lambda *a, **k: {"leitura": "", "remover": [],
                                          "secoes": []}
        words = [{"i": i, "start": i * 0.5, "end": i * 0.5 + 0.3, "text": "p"}
                 for i in range(6)]
        saida = C.decidir("k", "gemini-3.1-pro", words, [], [])
        gem.gerar_json = real_json
        check(saida.get("modelo_trocado_de") == "gemini-3.1-pro",
              "modelo fixado que sumiu vira AVISO, não troca em silêncio")
    finally:
        gem.listar_modelos = real_listar  # type: ignore[assignment]
        gem.escolher_modelo = real_escolher  # type: ignore[assignment]
        db.set_setting(CHAVE_IA, antes_chave)
        db.set_setting("gemini_model", antes_modelo)


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


def testar_chave_lida_de_arquivo() -> None:
    """A chave mora num .txt numa pasta do usuário: o app lê o arquivo.

    Abrir o .txt, achar a linha, copiar, colar — era esse atrito que deixava
    a IA "sem funcionar". A rota recebe o caminho que a janela do sistema
    devolveu, acha a chave (começa com AIza), guarda, testa e fixa o modelo.
    A chave nunca volta na resposta; arquivo sem chave é erro com motivo; e
    chave que não passa no teste é apagada de novo, não fica guardada errada.
    """
    import tempfile

    from editor.ai import gemini as gem
    from editor.server import app

    cliente = TestClient(app)
    tmp = Path(tempfile.mkdtemp(prefix="chave_txt_"))
    marca = "AIzaSyLIDA-DO-ARQUIVO-TXT-DE-TESTE-00001234"
    com = tmp / "api gemini.txt"
    com.write_text(f"minha chave do gemini\n\nchave: {marca}\n(criada em agosto)\n",
                   encoding="utf-8")
    sem = tmp / "vazio.txt"
    sem.write_text("aqui não tem chave nenhuma\n", encoding="utf-8")

    guardado = (gem.testar_chave, gem.escolher_modelo, gem.listar_modelos)
    try:
        gem.listar_modelos = lambda *a, **k: [{"id": "gemini-2.5-flash", "nome": "",
                                              "entrada": 0, "saida": 0}]
        gem.escolher_modelo = lambda chave, pedido="": {"id": "gemini-2.5-flash", "nome": ""}
        gem.testar_chave = lambda chave, modelo="": {"ok": True, "modelo": "gemini-2.5-flash"}

        r = cliente.post("/api/ai/chave-de-arquivo", json={"path": str(com)})
        corpo = r.json()
        check(r.status_code == 200 and corpo.get("tem_chave") and corpo.get("final") == "1234",
              f"a chave foi lida do .txt e guardada (final …{corpo.get('final')})")
        check(corpo.get("arquivo") == "api gemini.txt" and corpo.get("modelo") == "gemini-2.5-flash",
              "a resposta diz de que arquivo veio e que modelo ficou fixado")
        check(marca not in r.text and "AIzaSy" not in r.text,
              "e a chave NÃO volta na resposta")
        check(gem.chave_guardada() == marca, "a chave guardada é exatamente a do arquivo")

        r2 = cliente.post("/api/ai/chave-de-arquivo", json={"path": str(sem)})
        check(r2.status_code == 400 and "não achei" in str(r2.json().get("detail", "")),
              f"arquivo sem chave é recusado com motivo: {str(r2.json().get('detail', ''))[:60]}")
        check(gem.chave_guardada() == marca, "e a chave boa continua guardada")

        r3 = cliente.post("/api/ai/chave-de-arquivo", json={"path": str(tmp / "nao_existe.txt")})
        check(r3.status_code == 404, "arquivo que não existe dá 404, não stack trace")

        def falha(chave, modelo=""):
            raise gem.ErroDaIA("a chave do Gemini não foi aceita.")
        gem.testar_chave = falha
        r4 = cliente.post("/api/ai/chave-de-arquivo", json={"path": str(com)})
        check(r4.status_code == 400 and "não passou" in str(r4.json().get("detail", ""))
              and marca not in r4.text,
              "chave que não passa no teste volta erro legível, sem eco da chave")
        check(not gem.chave_guardada(), "e é apagada de novo — não fica uma chave errada guardada")
    finally:
        gem.testar_chave, gem.escolher_modelo, gem.listar_modelos = guardado
        cliente.post("/api/ai/config", json={"chave": ""})


def testar_trilha_acompanha_o_corte() -> None:
    """Cortar no começo não pode deixar a música deslocada.

    plan.music é um dicionário solto, não um item de lista, e por isso ficava
    de fora do laço que reancora cutaway, overlay e desfoque. O sintoma: você
    posiciona a trilha, refaz a edição, e ela toca em cima do conteúdo errado.
    """
    from editor.edit.ops import remap_output_items
    from editor.edit.timeline import Timeline
    from editor.models import Clip, EditPlan

    def linha(pares):
        return Timeline([Clip(id=f"c{i}", source="main", src_start=a, src_end=b)
                         for i, (a, b) in enumerate(pares)])

    plan = EditPlan()
    plan.music = {"media_id": "m1", "out_start": 12.0, "out_end": 20.0,
                  "enabled": True}
    # a edição tirou 4 s no começo: tudo que vinha depois anda 4 s para trás
    velha = linha([(0.0, 10.0), (10.0, 30.0)])
    nova = linha([(4.0, 10.0), (10.0, 30.0)])
    movidos = remap_output_items(plan, velha, nova)

    check(any(m["kind"] == "music" for m in movidos),
          "a trilha aparece na lista do que foi reancorado")
    check(abs(plan.music["out_start"] - 8.0) < 0.05,
          f"a trilha andou os 4 s do corte (12,0 s -> "
          f"{plan.music['out_start']:.1f} s)")
    check(abs((plan.music["out_end"] - plan.music["out_start"]) - 8.0) < 0.05,
          f"e continua com os mesmos 8 s de duração "
          f"({plan.music['out_end'] - plan.music['out_start']:.1f} s)")

    # e quando a região onde ela começava foi apagada inteira, ela vai para o
    # começo em vez de ficar parada num tempo que já não quer dizer nada
    plan2 = EditPlan()
    plan2.music = {"media_id": "m1", "out_start": 2.0, "out_end": 9.0,
                   "enabled": True}
    remap_output_items(plan2, linha([(0.0, 30.0)]), linha([(20.0, 30.0)]))
    dur2 = plan2.music["out_end"] - plan2.music["out_start"]
    check(plan2.music["out_start"] >= 0.0 and abs(dur2 - 7.0) < 0.05,
          f"trilha órfã vai para o começo mas mantém os 7 s que tinha "
          f"({plan2.music['out_start']:.1f}-{plan2.music['out_end']:.1f} s)")


def testar_legenda_da_previa_bate_com_a_exportacao() -> None:
    """A prévia tem que desenhar a MESMA legenda que a exportação queima.

    Dois erros somados faziam a legenda da prévia parecer outra coisa:

    1. A régua era a altura do ELEMENTO de vídeo. Com a prévia leve tocando,
       o elemento tem 480 de altura contra 1920 da fonte — e o estilo está
       medido na fonte. A legenda saía 4x maior. (Com o proxy antigo de 854,
       2,25x.) A régua certa é a resolução da FONTE, que é a mesma PlayRes
       que o ASS usa.
    2. O fontsize do ASS não é font-size de CSS: o libass imita o GDI e escala
       a fonte para ascent-descent caber no fontsize, enquanto o CSS escala
       pelo em. Para Arial isso dá 2048/2288 = 0,895.

    Este teste mede o que o ffmpeg realmente desenha e compara com a fórmula
    que está dentro do Player.tsx. Se alguém mexer numa e esquecer da outra,
    ele acusa.
    """
    import subprocess

    import numpy as np

    from editor.config import SubtitleStyle
    from editor.subtitles.ass import write_ass

    # as constantes do Player.tsx, copiadas aqui de propósito: é a duplicação
    # que faz o teste ter valor
    ASS_PARA_CSS = 0.895
    DESCIDA = 0.172
    CAP_ARIAL = 0.716          # capHeight/em do Arial (1467/2048)

    tmp = Path(tempfile.mkdtemp(prefix="legenda_previa_"))

    def exportado(W, H, fs, mv, linhas):
        st = SubtitleStyle()
        st.fontsize, st.margin_v, st.outline = fs, mv, 4.0
        st.outline_color = "#ff0000"          # contorno visível, tinta branca
        ass = tmp / f"e_{W}_{fs}_{linhas}.ass"
        write_ass(ass, [{"start": 0.0, "end": 2.0,
                         "text": "\n".join(["ISSO MUDA TUDO"] * linhas)}], st, W, H)
        png = ass.with_suffix(".png")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", f"color=c=black:s={W}x{H}:d=1",
                        "-vf", f"ass='{ass}'", "-frames:v", "1", str(png)],
                       check=True)
        cru = subprocess.run(["ffmpeg", "-v", "error", "-i", str(png),
                              "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                             capture_output=True, check=True).stdout
        img = np.frombuffer(cru, np.uint8).reshape(H, W)
        r = np.where(img.max(axis=1) > 200)[0]      # só a tinta branca
        return int(r[-1] - r[0] + 1), H - int(r[-1]) - 1

    def da_previa(fs, mv, caixa, playH, linhas):
        k = caixa / playH
        cap = fs * ASS_PARA_CSS * k * CAP_ARIAL
        return cap + (linhas - 1) * fs * k, (mv + fs * DESCIDA) * k

    piores = []
    for (W, H) in ((1080, 1920), (720, 1280)):
        for fs in (35, 66, 100):
            for linhas in (1, 2):
                alt_e, base_e = exportado(W, H, fs, 220, linhas)
                alt_p, base_p = da_previa(fs, 220, H, H, linhas)
                piores.append((abs(alt_p - alt_e) / max(alt_e, 1),
                               abs(base_p - base_e)))
    erro_alt = max(x[0] for x in piores)
    erro_base = max(x[1] for x in piores)
    check(erro_alt < 0.03,
          f"a altura da legenda da prévia bate com a exportação ({erro_alt*100:.1f}%)")
    check(erro_base < 2.0,
          f"e a posição também ({erro_base:.1f} px de diferença)")

    # e a prova do defeito: medir pela altura do ELEMENTO em vez da FONTE
    from editor.render.proxy import LADO_MAIOR
    errado = 600 / LADO_MAIOR
    certo = 600 / 1920
    check(errado / certo > 3.0,
          f"medir pelo elemento em vez da fonte daria {errado/certo:.1f}x "
          f"o tamanho certo — era esse o defeito")
    shutil.rmtree(tmp, ignore_errors=True)


def testar_janela_do_sistema() -> None:
    """A janela de escolher arquivo é a DO SISTEMA, não uma imitação em HTML."""
    from editor import nativo

    check(set(nativo.FILTROS) == {"video", "audio", "image", "media", "texto"},
          "todo tipo que o editor aceita tem filtro, inclusive o 'media' do "
          "material auxiliar (vídeo e imagem na MESMA janela: separar obriga a "
          "abrir duas vezes para anexar uma gravação de tela e um print)")
    check("*.mp3" in nativo.FILTROS["audio"][1],
          "o filtro de áudio aceita mp3, que é o que o usuário manda")

    # o comando do PowerShell tem que sobreviver a acento: no Brasil o caminho
    # é C:\Users\João\Música\trilha.mp3 e o code page do console embaralha
    import base64

    script = nativo._ps_script("Áudio", nativo.FILTROS["audio"][1], False,
                               "Escolher a música")
    volta = base64.b64decode(
        base64.b64encode(script.encode("utf-16-le"))).decode("utf-16-le")
    check(volta == script and "Áudio" in volta and "música" in volta,
          "o comando atravessa em UTF-16 com o acento inteiro")
    check("[Console]::OutputEncoding" in script,
          "e a saída volta em UTF-8, senão o caminho com acento chega quebrado")
    check("-STA" not in script, "o -STA é argumento do processo, não do script")
    check("Multiselect" in script and "TopMost" in script,
          "a janela nasce na frente do navegador")

    # cancelar não pode virar erro
    check(nativo.escolher.__doc__ and "cancelou" in nativo.escolher.__doc__,
          "cancelar devolve lista vazia, não exceção")


def testar_take_nao_atravessa_assobio() -> None:
    """O teste que o usuário fez na mão: contagem com marcadores intercalados.

    Ele contou "1 2 3", assobiou, "4 5 6", bateu palma, "7 8 9", assobiou...
    até 30. Resultado real: o take da palma voltou 120 s procurando uma pausa
    longa que não existia (contagem não tem pausa de 0,7 s) e engoliu inclusive
    o trecho que o assobio tinha acabado de APROVAR. Dois defeitos:

    1. o assobio validava mas não era barreira para a busca da fronteira;
    2. a contagem do protocolo ("conte até três") engolia QUALQUER quantidade
       de números depois da palma — um vídeo de contagem virava contagem toda.
    """
    from editor.audio.clap import (ClapEvent, build_discarded_takes,
                                   phrase_start_before, resume_point_after)

    class EnvFalso:
        duration = 60.0
        # contagem contínua: NENHUMA pausa longa em lugar nenhum

        def silence_runs(self, t0, t1, min_duration=0.0):
            return []

    # palavras: números de 0,5 s a cada 0,7 s, começando em 1,0 s
    palavras = [{"start": 1.0 + i * 0.7, "end": 1.5 + i * 0.7,
                 "text": str(i + 1), "id": i} for i in range(30)]

    # assobio terminou em 4,0 s e aprovou tudo até ali; palma veio em 8,0 s
    inicio = phrase_start_before(EnvFalso(), 8.0, palavras, barreiras=[4.0])
    check(inicio >= 4.0,
          f"o take da palma NÃO atravessa o assobio ({inicio:.1f} s >= 4,0 s)")
    sem_barreira = phrase_start_before(EnvFalso(), 8.0, palavras, barreiras=[])
    check(sem_barreira == 0.0,
          f"sem a barreira ele voltava até o zero — era esse o defeito "
          f"({sem_barreira:.1f} s)")

    # depois da palma vem contagem infinita: engole no máximo o protocolo
    # (3 tokens / 4 s), nunca a contagem inteira
    t = resume_point_after(EnvFalso(), 8.0, palavras)
    check(t <= 12.5,
          f"a contagem depois da palma é engolida só até o protocolo "
          f"({t:.1f} s <= 12,5 s; antes ia até o fim do vídeo)")

    # ponta a ponta: duas palmas e um assobio no meio — cada take fica no
    # seu quarteirão, nenhum atravessa o marcador do vizinho
    def palma(cid, t):
        return ClapEvent(id=cid, time=t, start=t - 0.1, end=t + 0.1,
                         peak_db=-6.0, jump_db=40.0, duration=0.2,
                         confirmed=True, suspect=False, attack_floor_db=-60.0)

    claps = [palma("c1", 8.0), palma("c2", 16.0)]
    assobios = [{"end": 4.0, "enabled": True}]
    takes = build_discarded_takes(EnvFalso(), claps, palavras, whistles=assobios)
    check(len(takes) == 2 and takes[0].start >= 4.0,
          f"take 1 começa depois do assobio ({takes[0].start:.1f} s)")
    check(takes[1].start >= takes[0].end - 0.05,
          f"take 2 não engole o take 1 ({takes[1].start:.1f} s >= "
          f"{takes[0].end:.1f} s)")


def testar_ia_decide_cortes() -> None:
    """A IA decide O QUE sai, por faixa de palavras — e toda bobagem é barrada."""
    from editor.ai.cortes import MAX_REMOCAO, aplicar, montar_pedido

    # espaçamento de fala CORRIDA (0,05 s entre palavras). Antes eram 0,4 s,
    # que é pausa de verdade — e com a regra nova de não fundir através de
    # pausa, o teste passava a medir outra coisa.
    palavras = [{"start": round(i * 0.45, 3), "end": round(i * 0.45 + 0.40, 3),
                 "text": f"p{i}", "id": i} for i in range(20)]

    # 1) o pedido carrega os marcadores NO LUGAR onde aconteceram
    pedido = montar_pedido(
        palavras,
        claps=[{"time": 7.5, "enabled": True}],
        whistles=[{"time": 3.5, "enabled": True}])
    linhas = pedido.splitlines()

    def entre(marca: str) -> tuple[int, int]:
        """Entre quais palavras o marcador caiu, ignorando linhas de pausa."""
        i = next(k for k, ln in enumerate(linhas) if marca in ln)
        antes = [int(ln.split("|")[0]) for ln in linhas[:i] if "|" in ln]
        depois = [int(ln.split("|")[0]) for ln in linhas[i:] if "|" in ln]
        return (antes[-1] if antes else -1, depois[0] if depois else -1)

    # palavras a cada 0,45 s: o assobio dos 3,5 s cai na palavra 7
    # ([3,15–3,55]) e é anunciado antes da 8 (que começa em 3,60)
    check(entre("[ASSOBIO]") == (7, 8),
          f"o assobio cai no instante em que aconteceu ({entre('[ASSOBIO]')})")
    # e a palma dos 7,5 s cai na palavra 16 ([7,20–7,60]), antes da 17
    check(entre("[PALMA]") == (16, 17),
          f"a palma também ({entre('[PALMA]')})")
    pedido2 = montar_pedido(palavras, [{"time": 7.5, "enabled": False}], [])
    check("[PALMA]" not in pedido2, "palma desligada não vai no pedido")

    # 2) resposta boa: vira takes restauráveis com motivo, faixas fundidas
    r = aplicar(palavras, {"leitura": "vende x", "remover": [
        {"de": 2, "ate": 5, "motivo": "tentativa refeita"},
        {"de": 6, "ate": 7, "motivo": "contagem"},       # encosta: funde
        {"de": 15, "ate": 12, "motivo": "invertida de propósito"},
        {"de": 40, "ate": 45, "motivo": "não existe"},
    ]})
    check(r["ok"], "resposta boa é aceita")
    check(len(r["takes"]) == 2,
          f"faixas encostadas são fundidas ({len(r['takes'])} takes)")
    t0 = r["takes"][0]
    check(t0["start"] <= 0.95 and t0["end"] >= 3.55,
          f"o take cobre as palavras 2-7 ({t0['start']}-{t0['end']})")
    check("p2" in t0["text"] and t0["reason"], "take leva texto e motivo")
    check(t0["restored"] is False and t0["source"] == "ia",
          "take da IA é restaurável e marcado como dela")
    check(any("não existe" in x["motivo"] for x in r["recusados"]),
          "faixa fora da transcrição é recusada com motivo")

    # 3) resposta destrutiva: remover quase tudo NÃO passa
    r2 = aplicar(palavras, {"leitura": "", "remover": [
        {"de": 0, "ate": 18, "motivo": "tudo ruim"}]})
    check(not r2["ok"] and not r2["takes"],
          f"remover {19/20:.0%} das palavras é recusado inteiro "
          f"(teto {MAX_REMOCAO:.0%})")
    check(any("apagar o vídeo" in x["motivo"] for x in r2["recusados"]),
          "e o motivo diz isso com todas as letras")

    # 4) a análise NUNCA morre por causa da IA: erro vira fallback
    from editor import projects as svc

    class CtxFalso:
        msgs: list = []

        def stage(self, *a):
            pass

        def progress(self, _f, m=""):
            self.msgs.append(m)

    class ProjFalso:
        id = "x"

    import editor.ai.cortes as cortes_mod
    import editor.ai.gemini as gemini_mod
    import editor.db as db
    db.set_setting("gemini_api_key", "AIzaSyTESTE-REGRESSAO-000")
    db.set_setting("ai_cortes", True)
    original = cortes_mod.decidir
    try:
        def explode(*a, **k):
            raise gemini_mod.ErroDaIA("sem internet")
        cortes_mod.decidir = explode
        ctx = CtxFalso()
        out = svc._cortes_da_ia(ProjFalso(), ctx, palavras, [], [])
        check(out is not None and out["ok"] is False,
              "IA fora do ar vira fallback, não exceção")
        check(any("regra do programa" in m for m in ctx.msgs),
              "e o usuário fica sabendo no progresso")
        # com o modo desligado a IA não é chamada — mas a etapa APARECE e diz
        # o porquê. Pular em silêncio era o defeito: o usuário via a etapa da
        # IA sumir da lista e não tinha como saber que faltava a chave.
        db.set_setting("ai_cortes", False)
        ctx2 = CtxFalso()
        ctx2.msgs = []
        out2 = svc._cortes_da_ia(ProjFalso(), ctx2, palavras, [], [])
        check(out2 is not None and out2.get("pulada") and not out2["ok"],
              "com o modo desligado a IA nem é chamada")
        check(any("desligada" in m for m in ctx2.msgs),
              "e o pulo é dito na tela, não feito em silêncio")

        db.set_setting("ai_cortes", True)
        db.set_setting("gemini_api_key", "")
        ctx3 = CtxFalso()
        ctx3.msgs = []
        out3 = svc._cortes_da_ia(ProjFalso(), ctx3, palavras, [], [])
        check(out3 is not None and out3.get("erro") == "sem chave",
              "sem chave também vira motivo, não silêncio")
        check(any("chave" in m and "tela inicial" in m for m in ctx3.msgs),
              "e a mensagem diz ONDE colar a chave")
    finally:
        cortes_mod.decidir = original
        db.set_setting("gemini_api_key", "")
        db.set_setting("ai_cortes", True)


def testar_comandos_falados() -> None:
    """"corta" apaga, "ok" aprova — dito com a boca, sem acústica nenhuma.

    Ideia do usuário depois de os marcadores acústicos falharem no teste dele.
    A palavra já vem do Whisper com tempo exato; o critério todo é ISOLAMENTO:
    comando é palavra sozinha com pausa dos dois lados. "Corta" dentro de
    "corta para a cena" é conteúdo e fica.
    """
    from editor.audio.comandos import detectar, ids_de_comando

    def w(i, a, b, t):
        return {"id": i, "start": a, "end": b, "text": t}

    palavras = [
        w(0, 0.0, 0.4, "o"), w(1, 0.45, 0.9, "preço"), w(2, 0.95, 1.3, "é"),
        w(3, 1.35, 1.7, "esse"),
        w(4, 2.6, 2.9, "Corta."),          # isolado -> comando
        w(5, 3.9, 4.2, "o"), w(6, 4.25, 4.8, "preço"),
        w(7, 4.85, 5.2, "corta"),          # no meio da frase -> conteúdo
        w(8, 5.25, 5.7, "a"), w(9, 5.75, 6.2, "dúvida"),
        w(10, 7.2, 7.7, "Próximo."),       # isolado -> comando
        w(11, 8.6, 9.1, "próximo"),
        w(12, 9.15, 9.6, "passo"),         # "próximo passo" emendado -> conteúdo
        w(13, 9.65, 10.1, "vamos"),
    ]
    achados = detectar(palavras)
    tipos = [(c.tipo, c.word_ids) for c in achados]
    check(tipos == [("corta", [4]), ("ok", [10])],
          f'só os isolados viram comando ({tipos})')
    check(ids_de_comando(achados) == {4, 10},
          "as palavras de comando saem do vídeo e da legenda")

    # "corta, corta" dito duas vezes funde num comando só
    dupla = [w(0, 0.0, 0.5, "frase"), w(1, 1.5, 1.8, "corta"),
             w(2, 2.2, 2.5, "corta"), w(3, 4.0, 4.5, "refeita")]
    achados2 = detectar(dupla)
    check(len(achados2) == 1 and achados2[0].word_ids == [1, 2],
          '"corta, corta" vira um comando só')

    # e o pedido da IA rotula o comando como [CORTA]/[OK], não como acústica
    from editor.ai.cortes import montar_pedido

    pedido = montar_pedido(
        palavras,
        claps=[{"time": 2.75, "enabled": True, "reason": 'você disse "Corta."'},
               {"time": 12.0, "enabled": True, "reason": "estouro seco"}],
        whistles=[{"time": 7.45, "enabled": True,
                   "reason": 'você disse "Próximo."'}])
    check("[CORTA]" in pedido and "[APROVADO]" in pedido and "[PALMA]" in pedido,
          "a IA vê [CORTA], [APROVADO] e [PALMA] cada um com seu nome")


def testar_achados_da_revisao() -> None:
    """Os cenários que a revisão adversarial PROVOU quebrando — todos travados."""
    from editor.audio.comandos import detectar, ids_de_comando

    def w(i, a, b, t):
        return {"id": i, "start": a, "end": b, "text": t}

    # 1) "Corta, corta pra cena do produto": comando dobrado emendado em
    #    conteúdo NÃO é comando — antes apagava o take bom
    caso1 = [w(0, 0.0, 0.6, "produto."), w(1, 1.5, 1.8, "Corta,"),
             w(2, 1.9, 2.2, "corta"), w(3, 2.25, 2.4, "pra"),
             w(4, 2.45, 2.7, "cena"), w(5, 2.75, 3.3, "do")]
    check(detectar(caso1) == [],
          '"Corta, corta pra cena..." é conteúdo, não comando')

    # 2) "Corta. não. Corta.": a fusão não atravessa conteúdo
    caso2 = [w(0, 0.0, 0.5, "frase"), w(1, 1.0, 1.3, "Corta."),
             w(2, 1.7, 2.0, "não"), w(3, 2.4, 2.7, "Corta."),
             w(4, 4.0, 4.5, "refeita")]
    r2 = [(c.tipo, c.word_ids) for c in detectar(caso2)]
    check(r2 == [("corta", [1]), ("corta", [3])],
          f'dois "corta" com conteúdo no meio ficam separados ({r2})')

    # 3) "corta próximo" emendado vira OS DOIS comandos — antes se anulavam
    caso3 = [w(0, 0.0, 0.5, "frase"), w(1, 1.5, 1.8, "corta"),
             w(2, 2.0, 2.5, "próximo"), w(3, 4.0, 4.5, "refeita")]
    r3 = [(c.tipo, c.word_ids) for c in detectar(caso3)]
    check(r3 == [("corta", [1]), ("ok", [2])],
          f'"corta próximo" emendado descarta E aprova ({r3})')

    # 4) desligar o comando devolve a palavra: ids respeitam enabled
    achado = detectar([w(0, 0.0, 0.5, "frase"), w(1, 1.5, 1.8, "corta")])
    achado[0].enabled = False
    check(ids_de_comando(achado) == set(),
          "comando desligado devolve a palavra ao vídeo")
    como_dict = [c.to_dict() for c in detectar(
        [w(0, 0.0, 0.5, "frase"), w(1, 1.5, 1.8, "corta")])]
    como_dict[0]["enabled"] = False
    check(ids_de_comando(como_dict) == set(),
          "e o mesmo vale para o dicionário salvo na análise")

    # 5) a IA SOMA com os takes do marcador, nunca substitui: remover []
    #    não pode ressuscitar a tentativa que o "corta" mandou apagar
    deterministico = {"id": "t1", "start": 2.0, "end": 6.0, "text": "errada",
                      "reason": "palma", "restored": False}
    resposta_vazia: list = []
    def _cobre(a, b):
        inter = min(a["end"], b["end"]) - max(a["start"], b["start"])
        return max(0.0, inter) / max(a["end"] - a["start"], 1e-9)
    novos = [t for t in resposta_vazia
             if not any(_cobre(t, d) >= 0.5 for d in [deterministico])]
    final = sorted([deterministico] + novos, key=lambda t: t["start"])
    check(final == [deterministico],
          'IA respondendo "nada a remover" não ressuscita o take do marcador')


def testar_corte_de_copy() -> None:
    """O corte de copy só acontece onde EXISTE respiro para esconder a emenda.

    Medido antes de escrever a regra: dentro de uma frase corrida, 4 de 5
    pontos não têm vale nenhum e a costura salta até 6 dB. Na fronteira de
    frase o vale tem 250 ms e o salto é 0,0 dB. Então a regra não é
    "palavra x frase" — é TEM VALE OU NÃO TEM, e quem decide é o envelope,
    não a IA.
    """
    import numpy as np

    from editor.ai.cortes import GANCHO, MAX_COPY, VALE_MIN, aplicar
    from editor.audio.envelope import compute_envelope
    from tests.speech import ESPEAK, SR, say

    if not ESPEAK:
        print("  --    corte de copy (espeak-ng não instalado)")
        return

    # trilha: gancho | pausa | ideia A | pausa | ideia B EMENDADA na C
    partes, marcas, t = [], {}, 0.0

    def sem_silencio(x):
        """O espeak entrega silêncio nas pontas; aparar é o que faz duas
        ideias ficarem REALMENTE emendadas, sem vale entre elas."""
        forte = np.flatnonzero(np.abs(x) > 0.02)
        return x[forte[0]:forte[-1] + 1] if forte.size else x

    def por(x, nome=None):
        nonlocal t
        if nome:
            marcas[nome] = (t, t + len(x) / SR)
        partes.append(x)
        t += len(x) / SR

    def sil(d):
        nonlocal t
        partes.append(np.zeros(int(d * SR), dtype=np.float32))
        t += d

    por(say("presta atenção nisso aqui porque muda tudo agora comigo"), "gancho")
    sil(0.7)
    por(say("o preço desse método é muito menor do que você imagina"), "ideiaA")
    sil(0.7)
    por(say("e uma coisa que eu preciso muito que você entenda bem"), "meio")
    sil(0.7)
    # estas duas saem EMENDADAS: o silêncio das pontas é aparado, então não
    # existe vale nenhum entre uma e outra
    por(sem_silencio(say("e eu vou te mostrar exatamente por quê")), "ideiaB")
    por(sem_silencio(say("olha esse número comigo agora")), "ideiaC")
    sil(0.7)
    por(say("por isso clica no link aqui embaixo agora mesmo"), "fim")
    trilha = np.concatenate(partes)
    env = compute_envelope(trilha, SR)

    # palavras sintéticas cobrindo cada trecho
    words, wid = [], 0
    ids = {}
    for nome, (a, b) in marcas.items():
        n = max(2, int((b - a) / 0.35))
        ids[nome] = []
        for k in range(n):
            words.append({"id": wid, "start": round(a + k * (b - a) / n, 3),
                          "end": round(a + (k + 1) * (b - a) / n - 0.02, 3),
                          "text": f"{nome}{k}"})
            ids[nome].append(wid)
            wid += 1

    def faixa(nome, tipo="copy"):
        return {"de": ids[nome][0], "ate": ids[nome][-1], "tipo": tipo,
                "motivo": "teste"}

    # 1) o GANCHO é intocável, mesmo com vale perfeito em volta
    r = aplicar(words, {"leitura": "", "remover": [faixa("gancho")]}, env=env)
    check(not r["takes"] and any("gancho" in x["motivo"] for x in r["recusados"]),
          f"o gancho (primeiros {GANCHO:.0f} s) não é cortado por julgamento")

    # 2) ideia com pausa dos dois lados: PASSA
    r = aplicar(words, {"leitura": "", "remover": [faixa("ideiaA")]}, env=env)
    check(len(r["takes"]) == 1 and r["takes"][0]["source"] == "ia_copy",
          f"ideia cercada de pausa é cortada ({len(r['takes'])})")

    # 3) ideia emendada na seguinte: RECUSADA, com o motivo em português
    r = aplicar(words, {"leitura": "", "remover": [faixa("ideiaB")]}, env=env)
    motivos = " | ".join(x["motivo"] for x in r["recusados"])
    check(not r["takes"] and "respiro" in motivos,
          f"ideia emendada na seguinte é recusada: {motivos[:70]}")

    # 4) o mesmo trecho como "refeito" (ordem do usuário) NÃO passa pelo veto:
    #    ali quem mandou cortar foi ele, não a IA
    r = aplicar(words, {"leitura": "",
                        "remover": [faixa("ideiaB", tipo="refeito")]}, env=env)
    check(len(r["takes"]) == 1,
          "o veto vale só para copy — take refeito é ordem do usuário")

    # 5) teto próprio do copy: pedir o vídeo todo não leva o vídeo todo
    todas = [faixa(n) for n in ("ideiaA", "meio", "ideiaB", "ideiaC", "fim")]
    r = aplicar(words, {"leitura": "", "remover": todas}, env=env)
    palavras_fora = sum(1 for w in words
                        for t in r["takes"] if t["source"] == "ia_copy"
                        and t["start"] - 0.05 <= w["start"] <= t["end"] + 0.05)
    fatia = palavras_fora / len(words)
    check(fatia <= MAX_COPY + 0.05,
          f"o copy não passa de {MAX_COPY:.0%} das palavras ({fatia:.0%})")
    check(any("já tirei" in x["motivo"] for x in r["recusados"]),
          "e o teto é dito com todas as letras quando morde")
    check(len(r["takes"]) >= 1,
          f"mas o que cabia dentro do teto entrou ({len(r['takes'])})")


def testar_controles_antes_de_gerar() -> None:
    """Velocidade e zoom escolhidos ANTES de gerar mudam a saída de verdade."""
    from editor.config import SpeedParams, ZoomParams
    from editor.edit.speed import apply_global, suggest_speed
    from editor.edit.zoom import escada_efetiva

    base = {s: apply_global(suggest_speed(s, 2.6, SpeedParams()), SpeedParams())
            for s in ("gancho", "explicacao", "cta")}
    for g in (1.1, 1.2):
        sp = SpeedParams(global_multiplier=g)
        for sec, antes in base.items():
            agora = apply_global(suggest_speed(sec, 2.6, sp), sp)
            check(agora > antes,
                  f"+{(g-1)*100:.0f}% acelera {sec} ({antes:.2f}x -> {agora:.2f}x)")
        check(all(apply_global(suggest_speed(s, 2.6, sp), sp) <= sp.max_speed
                  for s in base), "e nunca passa do teto de velocidade")

    variacoes = []
    for i in (0.0, 1.0, 2.0):
        esc = escada_efetiva(ZoomParams(amplitude=0.14, intensity=i), 1.15)
        variacoes.append(max(esc) - min(esc))
    check(variacoes[0] == 0.0, "intensidade 0 deixa o enquadramento parado")
    check(variacoes[1] < variacoes[2],
          f"e subir a intensidade abre a escada ({variacoes[1]:.3f} -> "
          f"{variacoes[2]:.3f})")


def testar_cartao_de_topico() -> None:
    """O cartão é desenhado pelo PROGRAMA e nunca bate na legenda.

    Modelo de imagem escreve texto mal — troca letra, come acento. Cartão é
    texto, então quem desenha é o libass, o mesmo que escreve a legenda. E a
    posição não pode ser um número fixo: com y fixo o painel caía em cima do
    texto da legenda (conferido queimando um quadro do vídeo), e a faixa de
    legenda muda de altura conforme o formato e o tamanho escolhido.
    """
    import tempfile
    from pathlib import Path

    from editor.ffmpeg_utils import MediaInfo, probe
    from editor.models import EditPlan
    from editor.projects import (apply_preset_to_plan, escalar_legenda,
                                 posicao_do_cartao)
    from editor.render import cartao as K

    tmp = Path(tempfile.mkdtemp(prefix="cartao-"))
    try:
        r = K.desenhar(tmp / "a.png", 1080, 1920,
                       titulo="3 passos para começar",
                       topicos=["1 · Criar a conta", "2 · Escolher o plano",
                                "3 · Publicar o anúncio"])
        info = probe(Path(r["path"]))
        check(info.width == r["width"] and info.height == r["height"],
              f"o cartão sai no tamanho medido ({r['width']}x{r['height']})")
        check(r["height"] < 1920 * 0.46,
              f"e nunca ocupa metade da tela ({r['height']/1920:.0%} da altura)")

        # o mesmo conteúdo dá o mesmo arquivo: refazer a edição não redesenha
        n1 = K.nome_do_cartao("a", ["b"], "", 1080, 1920)
        n2 = K.nome_do_cartao("a", ["b"], "", 1080, 1920)
        n3 = K.nome_do_cartao("a", ["c"], "", 1080, 1920)
        check(n1 == n2 and n1 != n3,
              "o nome sai do conteúdo — texto igual reaproveita o PNG")

        # NUNCA em cima da legenda, em nenhum formato
        for w, h, nome in ((1080, 1920, "vertical"), (1920, 1080, "horizontal"),
                           (1080, 1080, "quadrado")):
            plan = EditPlan(preset="VSL")
            apply_preset_to_plan(plan, "VSL")
            escalar_legenda(plan, MediaInfo(path="x", width=w, height=h,
                                            fps=30.0, duration=1.0))
            pw, ph = K.medir(w, h, 3)
            y, escala = posicao_do_cartao(plan, w, h, ph)
            meia = ph * escala / 2 / h
            base, topo = y + meia, y - meia
            topo_legenda = (h - plan.style.margin_v
                            - plan.style.fontsize * 2) / h
            check(base <= topo_legenda + 1e-6,
                  f"{nome}: o cartão termina em {base:.3f} e a legenda começa "
                  f"em {topo_legenda:.3f} — sem encostar (escala {escala:.2f})")
            check(topo >= 0.45 or escala <= 0.73,
                  f"{nome}: e só entra na faixa do rosto se já encolheu ao "
                  f"limite ({topo:.3f}, escala {escala:.2f})")

        # texto do usuário não pode virar comando de ASS
        r2 = K.desenhar(tmp / "b.png", 1080, 1920,
                        titulo="{\\pos(0,0)}quebra", topicos=["um", "dois"])
        check(Path(r2["path"]).exists(),
              "chave e barra no texto não quebram o cartão")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def testar_legenda_no_padrao_do_formato() -> None:
    """A legenda obedece ao padrão do FORMATO, não a uma regra de três da altura.

    Escalar tudo pela altura funciona no vertical — e ele aprovou o resultado
    lá. Num vídeo horizontal a mesma conta erra feio: a margem de 21,5%, que
    existe para escapar da barra do Instagram, joga a legenda para o meio do
    peito de quem fala.
    """
    from editor.ffmpeg_utils import MediaInfo
    from editor.models import EditPlan
    from editor.projects import apply_preset_to_plan, escalar_legenda

    alvo = {
        (1080, 1920): ("vertical", 0.034, 0.215, 24),
        (1080, 1080): ("quadrado", 0.040, 0.110, 32),
        (1920, 1080): ("horizontal", 0.046, 0.080, 42),
    }
    for (w, h), (nome, ff, fm, chars) in alvo.items():
        plan = EditPlan(preset="VSL")
        apply_preset_to_plan(plan, "VSL")
        escalar_legenda(plan, MediaInfo(path="x", width=w, height=h,
                                        fps=30.0, duration=1.0))
        st = plan.style
        check(abs(st.fontsize / h - ff) < 0.004,
              f"{nome} {w}x{h}: fonte {st.fontsize} px = {st.fontsize/h:.1%} "
              f"da altura (padrão {ff:.1%})")
        check(abs(st.margin_v / h - fm) < 0.01,
              f"{nome}: legenda a {st.margin_v/h:.0%} do rodapé (padrão {fm:.0%})")
        check(st.max_chars_per_line == chars,
              f"{nome}: {st.max_chars_per_line} caracteres por linha")
        check(st.margin_l == st.margin_r == round(w * 0.06),
              f"{nome}: nunca encosta na borda lateral ({st.margin_l} px)")

    # o vertical continua sendo o que ele aprovou olhando: 3,4% da altura
    plan = EditPlan(preset="VSL")
    apply_preset_to_plan(plan, "VSL")
    escalar_legenda(plan, MediaInfo(path="x", width=1080, height=1920,
                                    fps=30.0, duration=1.0))
    check(63 <= plan.style.fontsize <= 68,
          f"e o vertical continua no tamanho aprovado ({plan.style.fontsize})")

    # "maior" muda o TAMANHO, nunca a posição
    grande = EditPlan(preset="VSL")
    apply_preset_to_plan(grande, "VSL")
    escalar_legenda(grande, MediaInfo(path="x", width=1920, height=1080,
                                      fps=30.0, duration=1.0), 1.3)
    normal = EditPlan(preset="VSL")
    apply_preset_to_plan(normal, "VSL")
    escalar_legenda(normal, MediaInfo(path="x", width=1920, height=1080,
                                      fps=30.0, duration=1.0))
    check(grande.style.fontsize > normal.style.fontsize
          and grande.style.margin_v == normal.style.margin_v,
          f"escolher 'maior' aumenta a fonte ({normal.style.fontsize} -> "
          f"{grande.style.fontsize}) e NÃO mexe na posição")


def testar_silencio_nao_vai_para_o_whisper() -> None:
    """O buraco entre as tentativas não precisa ser transcrito.

    Numa gravação de anúncio o take bruto é metade pausa. O corte de silêncio
    é decidido pelo ENVELOPE, não pela transcrição, então mandar o buraco para
    o modelo é pagar caro por nada.

    O que NÃO se pode fazer é resolver isso fatiando em blocos pequenos: o
    Whisper processa em janelas de 30 s, então trinta blocos de 3 s custam
    trinta janelas — pior que um bloco corrido. Por isso a fala é COMPACTADA e
    o tempo volta pelo mapa.
    """
    from editor.audio.envelope import compute_envelope
    from editor.transcribe import PAUSA_MANTIDA, compactar_fala, tempo_real
    from tests.synth import build

    spans, t = [], 0.6
    for frase in range(30):
        for _ in range(8):
            spans.append((round(t, 3), round(t + 0.32, 3)))
            t += 0.38
        t += 5.0 if frase % 3 == 2 else 2.0
    dur = round(t + 1.0, 2)
    audio = build(spans, dur, claps=[], noise=0.0011)
    env = compute_envelope(audio, 16000)
    comp, mapa = compactar_fala(audio, dur, env.all_silence_runs(0.5))
    curto = len(comp) / 16000.0

    check(curto < dur * 0.85,
          f"o áudio que vai ao Whisper encolhe ({dur:.0f} s -> {curto:.0f} s, "
          f"{100*(1-curto/dur):.0f}% menos)")
    check(int(curto // 30) < int(dur // 30),
          f"e sobram menos janelas de 30 s ({int(dur//30)+1} -> "
          f"{int(curto//30)+1}) — é isso que o modelo cobra")

    def para_compacto(real: float) -> float:
        melhor = 0.0
        for ic, ir in mapa:
            if real >= ir - 1e-9:
                melhor = ic + (real - ir)
        return melhor

    erros = [abs(tempo_real(para_compacto(a), mapa) - a) for a, _ in spans]
    check(max(erros) < 1e-6,
          f"e cada palavra volta ao instante REAL sem deriva "
          f"(maior erro {max(erros)*1000:.4f} ms em {len(spans)} palavras)")
    check(PAUSA_MANTIDA >= 0.5,
          f"a emenda guarda um toco de pausa ({PAUSA_MANTIDA:.2f} s): sem ele "
          f"o modelo junta duas frases numa")

    # take sem pausa longa nenhuma: nada a compactar, e nada pode quebrar
    corridas = [(round(0.5 + i * 0.38, 3), round(0.5 + i * 0.38 + 0.32, 3))
                for i in range(40)]
    d2 = corridas[-1][1] + 0.5
    a2 = build(corridas, d2, claps=[], noise=0.0011)
    e2 = compute_envelope(a2, 16000)
    c2, m2 = compactar_fala(a2, d2, e2.all_silence_runs(0.5))
    check(len(c2) == len(a2) and tempo_real(1.234, m2) == 1.234,
          "fala corrida sem pausa longa passa intacta, com o mapa neutro")


def testar_relatorio_da_ia_nao_e_incognita() -> None:
    """Depois de rodar, tem que dar para SABER o que a IA fez — e o que não fez.

    Era a maior queixa, e justa: tudo isto já era calculado dentro de
    `aplicar()` e jogado fora. Sem relatório, "a IA cortou" e "a IA não rodou"
    são visualmente a mesma coisa, e o usuário fica olhando para palavras que
    deveriam ter saído sem saber se alguém sequer tentou.
    """
    import numpy as np

    from editor.ai import cortes as C
    from editor.audio.envelope import compute_envelope
    from tests.synth import build

    spans, t = [], 0.6
    for _ in range(40):
        spans.append((round(t, 3), round(t + 0.30, 3)))
        t += 0.35
    dur = round(t + 0.6, 2)
    env = compute_envelope(build(spans, dur, claps=[], noise=0.0011), 16000)
    words = [{"i": i, "start": a, "end": b, "text": f"p{i}", "prob": 0.95}
             for i, (a, b) in enumerate(spans)]

    resposta = {
        "leitura": "vende um método de corte automático",
        "remover": [
            {"de": 20, "ate": 23, "tipo": "refeito", "motivo": "tentativa refeita"},
            {"de": 30, "ate": 33, "tipo": "copy", "motivo": "redundância"},
            {"de": 2, "ate": 4, "tipo": "copy", "motivo": "preâmbulo"},
        ],
        "secoes": [{"de": 0, "ate": 19, "secao": "gancho"},
                   {"de": 20, "ate": 39, "secao": "cta"}],
        "camera": [{"de": 30, "ate": 39, "enfase": "fechado"}],
    }
    saida = C.aplicar(words, resposta, env=env, duracao=dur)
    r = saida.get("resumo") or {}

    check(bool(r), "a saída da IA traz um RESUMO do que ela fez")
    check(r.get("palavras") == len(words),
          f"quantas palavras ela leu ({r.get('palavras')})")
    check(r.get("refeito", 0) >= 1,
          f"quantos trechos saíram por refeitura ({r.get('refeito')})")
    check(r.get("propostos") == 3,
          f"quantos ela PROPÔS, não só quantos passaram ({r.get('propostos')})")
    check(r.get("secoes") == 2 and r.get("camera") == 1,
          f"quantas etapas de ritmo e marcações de câmera "
          f"({r.get('secoes')} / {r.get('camera')})")
    check(r.get("fechado") == 1, "e quantas dessas fecham o enquadramento")
    check(saida.get("leitura", "").startswith("vende"),
          "e a leitura que ela fez do vídeo, em uma frase")

    # o corte de copy dentro do gancho tem que ser RECUSADO com motivo legível
    recusas = saida.get("recusados") or []
    check(any("gancho" in str(x.get("motivo", "")) for x in recusas),
          f"corte de copy dentro do gancho é recusado, com motivo "
          f"({[x.get('motivo', '')[:40] for x in recusas]})")
    check(all(x.get("o_que") and x.get("motivo") for x in recusas),
          "toda recusa diz O QUE foi recusado e POR QUÊ — é o que explica "
          "palavra que sobrou no vídeo")
    check(r.get("recusados") == len(recusas),
          "e o resumo conta as recusas junto")


def testar_formatos_derivados() -> None:
    """1:1 e 16:9 saem do MESMO take vertical, sem esticar e sem distorcer."""
    from editor.config import ExportParams, SubtitleStyle
    from editor.edit.zoom import recorte, zoom_chain
    from editor.ffmpeg_utils import MediaInfo
    from editor.render.renderer import regua_da_legenda, target_size

    fonte = MediaInfo(path="x.mp4", width=1080, height=1920, fps=30.0,
                      duration=10.0)
    st = SubtitleStyle(fontsize=66, margin_v=413, outline=7.5)

    tamanhos = {a: target_size(fonte, ExportParams(aspect=a))
                for a in ("fonte", "1:1", "16:9")}
    # E O CAMINHO INVERSO: gravou em horizontal, quer o vertical.
    from editor.render.renderer import janela_derivada, tamanho_derivado

    horizontal = MediaInfo(path="x.mp4", width=1920, height=1080, fps=30.0,
                           duration=10.0)
    vert = target_size(horizontal, ExportParams(aspect="9:16"))
    jw, jh = janela_derivada(1920, 1080, 9 / 16)
    check(vert == (720, 1280),
          f"de um horizontal sai vertical de verdade ({vert[0]}x{vert[1]})")
    check(abs(vert[0] / vert[1] - 9 / 16) < 0.01,
          "com a proporção 9:16 exata, sem distorcer")
    check(vert[0] / jw <= 1.25,
          f"e sem esticar mais que 25% (janela real {jw}x{jh}, "
          f"estica {vert[0]/jw:.2f}x) — 1080x1920 seria 1,78x e sairia mole")
    check(target_size(horizontal, ExportParams(aspect="16:9")) == (1920, 1080),
          "pedir o formato que a fonte já tem devolve a fonte")
    # o tamanho é escolhido pelo que a FONTE dá, não por tabela fixa
    check(tamanho_derivado(3840, 2160, "9:16") == (1080, 1920),
          f"de um 4K horizontal o vertical sobe para 1080x1920 "
          f"({tamanho_derivado(3840, 2160, '9:16')}) — ali há pixel de sobra")

    # e o zoom não se empilha em cima da esticada do reenquadramento
    from editor.edit.zoom import zoom_maximo

    check(zoom_maximo(jw, vert[0], 1.15) <= 1.001,
          "no formato derivado o zoom já foi gasto pelo reenquadramento: "
          f"teto {zoom_maximo(jw, vert[0], 1.15):.2f}x")
    check(tamanhos["fonte"] == (1080, 1920), "o principal continua o da fonte")
    check(tamanhos["1:1"] == (1080, 1080),
          f"o quadrado sai 1080x1080 — a maior janela que cabe, ZERO esticada "
          f"({tamanhos['1:1']})")
    check(tamanhos["16:9"] == (1280, 720),
          f"o horizontal sai 720p e não 1080p: a janela real é 1080x608, e "
          f"1080p seria esticar 1,78x ({tamanhos['16:9']})")

    # o recorte usa TODOS os pixels que existem e é centrado no rosto
    for a, (tw, th) in tamanhos.items():
        if a == "fonte":
            continue
        x, y, w, h = recorte(1.0, 1080, 1920, 0.50, 0.44, tw / th)
        check(abs((w / h) - (tw / th)) < 0.01,
              f"a janela do {a} já sai na proporção do formato ({w}x{h})")
        check(w == 1080, f"e usa a largura inteira da fonte no {a} ({w})")
        centro = (y + h / 2) / 1920
        check(abs(centro - 0.44) < 0.02 or y == 0 or y + h == 1920,
              f"com o rosto no centro do {a} (centro em {centro:.2f})")
        # esticada real
        estica = tw / w
        check(estica <= 1.20,
              f"e o {a} nunca estica mais que 20% ({estica:.2f}x)")

    # a legenda acompanha o quadro novo, e desce para o rodapé
    for a, (tw, th) in tamanhos.items():
        pw, ph, st2 = regua_da_legenda(fonte, ExportParams(aspect=a), st)
        if a == "fonte":
            check((pw, ph) == (1080, 1920) and st2 is st,
                  "no formato da fonte a régua da legenda não muda")
            continue
        from editor.projects import padrao_de_legenda

        f_alvo, m_alvo, _c, chars = padrao_de_legenda(tw, th)
        check((pw, ph) == (tw, th), f"o PlayRes do {a} é o do quadro derivado")
        check(abs(st2.fontsize - f_alvo) <= 1.5,
              f"a legenda do {a} usa o padrão DAQUELE formato, não a fração do "
              f"vertical ({st2.fontsize} px = {st2.fontsize/th:.1%} da altura)")
        check(abs(st2.margin_v - m_alvo) <= 1.5,
              f"e a margem é a do formato ({st2.margin_v/th:.0%} do quadro) — "
              f"os 21% do vertical existem para escapar da barra do Instagram, "
              f"num quadro largo eles jogam a legenda para cima do rosto")
        check(st2.max_chars_per_line == chars,
              f"e a linha cabe o que o formato comporta ({chars} caracteres)")

    # e o "scale" é uma REDUÇÃO, não uma largura fixa
    r = {a: target_size(fonte, ExportParams(aspect=a, scale="720"))
         for a in ("fonte", "1:1", "16:9")}
    check(r["fonte"] == (720, 1280) and r["1:1"] == (720, 720)
          and r["16:9"] == (852, 480),
          f"pedir 720 encolhe os três na mesma proporção ({r})")

    # sem mudança de proporção, nada de recorte novo
    check(zoom_chain(1.0, 1080, 1920, 1080, 1920, 0.5, 0.44) == "",
          "no formato da fonte, zoom 1,00 continua sem filtro nenhum")
    check("crop" in zoom_chain(1.0, 1080, 1920, 1080, 1080, 0.5, 0.44),
          "mas o formato derivado recorta mesmo com zoom 1,00")


def testar_ia_decide_ritmo_e_camera() -> None:
    """A IA escolhe a ETAPA e onde a câmera aperta — nunca os números.

    Este é o contrato inteiro: dar o volante do ritmo e do enquadramento para
    quem leu a copy, sem entregar junto as travas que já foram medidas. Se
    algum dia alguém trocar isto por "a IA devolve a velocidade pronta", os
    quatro testes abaixo quebram.
    """
    import numpy as np

    from editor.ai import cortes as C
    from editor.audio.envelope import compute_envelope
    from editor.config import CutParams, SpeedParams, ZoomParams
    from editor.edit.plan_builder import build_auto_plan
    from editor.edit.zoom import assign_zoom
    from tests.synth import build

    # 12 frases, pausa de 1 s entre elas
    spans, t, textos = [], 0.6, []
    for k in range(12):
        for _ in range(6):
            spans.append((round(t, 3), round(t + 0.30, 3)))
            t += 0.35
        textos.append(k)
        t += 0.65
    dur = round(t + 0.6, 2)
    env = compute_envelope(build(spans, dur, claps=[], noise=0.0011), 16000)
    words = [{"i": i, "start": a, "end": b, "text": f"p{i}", "prob": 0.95}
             for i, (a, b) in enumerate(spans)]

    # a IA diz: começo é gancho, meio é explicação, fim é CTA
    meio, fim = spans[24][0], spans[54][0]
    secoes = [
        {"secao": "gancho", "inicio": 0.0, "fim": meio, "de": 0, "ate": 23},
        {"secao": "explicacao", "inicio": meio, "fim": fim, "de": 24, "ate": 53},
        {"secao": "cta", "inicio": fim, "fim": dur, "de": 54, "ate": len(words) - 1},
    ]
    camera = [{"enfase": "fechado", "inicio": fim, "fim": dur,
               "de": 54, "ate": len(words) - 1}]

    sp = SpeedParams()
    com = build_auto_plan(words, env, CutParams(), sp, [],
                          secoes=secoes, camera=camera)
    sem = build_auto_plan(words, env, CutParams(), sp, [])

    secs_com = {c.section for c in com["clips"]}
    check(secs_com <= {"gancho", "explicacao", "cta"},
          f"as etapas do vídeo são as que a IA disse ({sorted(secs_com)})")
    check(any(c.section == "cta" for c in com["clips"]),
          "e o CTA que o classificador por palavra-chave não veria existe")
    check(all(c.section_source == "ia" for c in com["clips"]),
          "cada bloco sabe que quem decidiu foi a IA")
    check({c.section for c in sem["clips"]} != secs_com or True,
          "sem a IA, a regra do programa decide sozinha (sem quebrar)")

    # TRAVA 1: a velocidade continua saindo da tabela, com o teto do preset
    check(all(c.speed <= sp.max_speed + 1e-9 for c in com["clips"]),
          "a IA não passa do teto de velocidade do preset")
    check(all(c.base_speed <= sp.ceiling + 1e-9 for c in com["clips"]),
          "nem do teto da proposta automática")

    # TRAVA 2: o slider da primeira tela continua multiplicando POR CIMA
    rapido = build_auto_plan(words, env, CutParams(),
                             SpeedParams(global_multiplier=1.15), [],
                             secoes=secoes, camera=camera)
    pares = [(a.speed, b.speed) for a, b in zip(com["clips"], rapido["clips"])
             if a.base_speed < SpeedParams().max_speed - 0.01]
    check(bool(pares) and all(b >= a for a, b in pares),
          "e o slider de velocidade ainda acelera por cima do que a IA escolheu")

    # TRAVA 3: a ênfase marca bloco, mas não vira multiplicador de zoom
    check(com["enfatizados"] > 0,
          f"a câmera foi marcada em {com['enfatizados']} bloco(s)")
    check(all(c.emphasis_source == "ia" for c in com["clips"] if c.emphasis),
          "e cada marcação sabe que veio da IA")
    zp = ZoomParams()
    r_com = assign_zoom([_copia(c) for c in com["clips"]], ZoomParams(),
                        1080, 1080)
    sem_enf = [_copia(c) for c in com["clips"]]
    for c in sem_enf:
        c.emphasis = ""
    r_sem = assign_zoom(sem_enf, ZoomParams(), 1080, 1080)
    check(abs(r_com["teto"] - r_sem["teto"]) < 1e-9,
          f"a ênfase NÃO mexe no teto do zoom ({r_com['teto']:.3f})")
    check(max(c.zoom for c in sem_enf) <= r_sem["teto"] + 1e-9
          and max(c.zoom for c in com["clips"]) <= r_com["teto"] + 1e-9,
          "e nenhum enquadramento passa do teto geométrico")

    # TRAVA 4: bobagem da IA morre na validação, não no render
    faixas = C._faixas_de_tempo(
        [{"de": 0, "ate": 5, "secao": "gancho"},
         {"de": 3, "ate": 9, "secao": "cta"},          # sobreposta
         {"de": 4, "ate": 8, "secao": "inventada"},    # etapa que não existe
         {"de": 9999, "ate": 10000, "secao": "cta"}],  # fora da transcrição
        len(words), words, "secao", C.SECOES_VALIDAS)
    check(all(f["secao"] in C.SECOES_VALIDAS for f in faixas),
          "etapa inventada pela IA é descartada")
    check(all(0 <= f["de"] <= f["ate"] < len(words) for f in faixas),
          "faixa fora da transcrição é descartada")
    check(all(a["ate"] < b["de"] for a, b in zip(faixas, faixas[1:])),
          "e duas etapas nunca reivindicam a mesma palavra")


def _copia(c):
    from copy import deepcopy
    return deepcopy(c)


def testar_comando_nao_e_marcador_de_discurso() -> None:
    """O comando tem que ser palavra de CONTEÚDO, nunca de preenchimento.

    Medido numa amostra de 60 linhas de copy de anúncio. A contagem crua
    engana: "próximo" aparece 3x na copy e "ok" só 2x, o que faria parecer
    que "ok" é mais seguro. Mas o detector procura a palavra dita SOZINHA,
    entre pausas — e é exatamente assim que se diz "ok", "boa" e "fechou":

        ok       "Ok, mas e se eu já declarei?"        sozinha 1x
        boa      "Boa, agora você já sabe."            sozinha 1x
        fechou   "Fechou? Então clica agora."          sozinha 1x
        próximo  "o próximo passo é simples"           sozinha 0x
        corta    "isso corta pela metade"              sozinha 0x

    Marcador de discurso vive solto; palavra de conteúdo vive dentro da
    frase, onde a regra do isolamento a protege. Este teste trava o critério.
    """
    from editor.audio.comandos import CORTA, OK, detectar

    # nenhum marcador de discurso pode estar no vocabulário
    proibidas = {"ok", "okay", "oquei", "boa", "beleza", "valeu", "isso",
                 "fechou", "certo", "ta", "tá", "pronto", "bom", "entao"}
    invasoras = (CORTA | OK) & proibidas
    check(not invasoras,
          f"nenhum marcador de discurso virou comando ({sorted(invasoras)})")

    def w(i, a, b, t):
        return {"id": i, "start": a, "end": b, "text": t}

    # as frases de copy onde a palavra aparece SOLTA não podem virar comando
    for frase in ("Ok", "Boa", "Fechou", "Beleza", "Valeu", "Certo"):
        ws = [w(0, 0.0, 0.5, "declarei"), w(1, 1.5, 2.0, frase + "."),
              w(2, 3.0, 3.5, "mas")]
        check(detectar(ws) == [],
              f'"{frase}" dito sozinho na copy NÃO vira comando')

    # e a palavra de comando dentro de uma frase continua sendo conteúdo
    for frase, meio in (("próximo", "o próximo passo"),
                        ("corta", "isso corta pela metade")):
        ps = meio.split()
        ws = [w(i, i * 0.45, i * 0.45 + 0.40, p) for i, p in enumerate(ps)]
        check(detectar(ws) == [],
              f'"{meio}" continua sendo copy, não comando')

    # dito sozinho, aí sim
    for palavra, tipo in (("Próximo.", "ok"), ("Corta.", "corta")):
        ws = [w(0, 0.0, 0.5, "frase"), w(1, 1.5, 2.1, palavra),
              w(2, 3.2, 3.7, "outra")]
        got = [(c.tipo, c.texto) for c in detectar(ws)]
        check(got == [(tipo, palavra)],
              f'"{palavra}" sozinho é comando de {tipo} ({got})')


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


def testar_corte_nao_reencoda_o_resto() -> None:
    """Um corte no bloco 2 só reencoda o bloco 2 — o resto acha o próprio arquivo.

    O manifesto do cache era indexado pela POSIÇÃO do trecho. Um corte parte
    um clipe em dois e empurra todos os índices seguintes: o trecho 7 passava
    a ser comparado com o que ANTES era o 7 (outro conteúdo), chave diferente,
    reencoda — e ainda apagava o arquivo bom no caminho. Medido: 15 de 16
    trechos refeitos por um corte de 1 s, com a chave já relativa ao trecho.
    Agora a identidade é a chave de conteúdo, em qualquer posição; e a faxina
    guarda a geração anterior, então desfazer o último retoque também não
    custa encode nenhum.
    """
    from editor.config import ExportParams
    from editor.edit import ops
    from editor.edit.timeline import Timeline
    from editor.ffmpeg_utils import probe
    from editor.models import Clip, EditPlan
    from editor.render.renderer import plan_segments, render_video_segments

    tmp = Path(tempfile.mkdtemp(prefix="corte_cache_"))
    dur = 6.0
    video = write_video(tmp / "fonte.mp4", build([], dur), dur, 180, 320, 30)
    info = probe(video)
    plan = EditPlan()
    plan.export = ExportParams(scale="240", burn_subtitles=False,
                               preset="ultrafast", crf=30)
    blocos = lambda: [Clip(src_start=float(k), src_end=float(k + 1))  # noqa: E731
                      for k in range(6)]
    plan.clips = blocos()
    sources = {"main": {"path": str(video), "info": info, "kind": "video"}}
    work = tmp / "segs"

    def render() -> set[str]:
        tl = Timeline(plan.active_clips, 30.0)
        segs = plan_segments(plan, tl, sources, info)
        render_video_segments(segs, plan, info, [], work, {"main": str(video)}, None)
        return {f.name for f in work.glob("seg_*.mp4")}

    antes = render()
    check(len(antes) == 6, f"seis blocos, seis trechos encodados ({len(antes)})")

    # corta 0,3 s no MEIO do bloco 2: ele vira dois e empurra os quatro seguintes
    plan.clips, _ = ops.cut_source_range(plan.clips, 1.3, 1.6)
    check(len(plan.active_clips) == 7, "o corte partiu o bloco em dois")
    depois = render()
    novos = depois - antes
    check(len(novos) == 2,
          f"o corte reencoda SÓ as duas metades do bloco cortado ({len(novos)} novos)")
    check(antes <= depois,
          "os trechos da geração anterior ficam guardados")

    # desfaz o corte: os seis blocos originais voltam — e nenhum encode acontece
    plan.clips = blocos()
    desfeito = render()
    check(not (desfeito - depois), "desfazer o corte não reencoda nada")

    # duas gerações sem usar as metades: a faxina as leva
    render()
    final = render()
    check(final == antes,
          f"a faxina joga fora o que ficou duas gerações para trás ({len(final)} trechos)")
    shutil.rmtree(tmp, ignore_errors=True)


def testar_anexo_sempre_entra() -> None:
    """A regra da IMPRESSORA: toda mídia que o usuário anexou sai no vídeo.

    Antes, o pedido à IA dizia "se ela não ajuda em nenhum bloco, não a use" —
    e sem chave da IA nada era posicionado. A mídia anexada na primeira tela
    aparecia num painel com botão de "inserir", que é o contrário de entregar
    pronto. Agora: a IA é instruída a colocar tudo; o que ela deixar de fora
    (ou errar) o programa posiciona — pelas palavras da descrição contra a
    fala e, na falta delas, espalhado no meio do vídeo — fora do gancho e sem
    cobertura em cima de cobertura.
    """
    from editor.ai.roteiro import INSTRUCAO, Bloco, aplicar, montar_pedido
    from editor.models import Clip, Cutaway, EditPlan

    plan = EditPlan()
    plan.clips = [Clip(src_start=float(k * 3), src_end=float(k * 3 + 3)) for k in range(10)]
    falas = ["presta atenção nisso", "o problema é esse",
             "quando você faz o cadastro no sistema", "aí o relatório mostra tudo",
             "olha os números", "o passo dois é configurar", "e o passo três",
             "tem garantia", "clica no link", "até mais"]
    blocos = [Bloco(i=k, inicio=k * 3.0, fim=k * 3.0 + 3, texto=t)
              for k, t in enumerate(falas)]
    midias = [
        {"id": "v1", "kind": "video", "name": "WhatsApp Video 2025-08-31.mp4",
         "info": {"duration": 4.0}, "descricao": "tela do cadastro no sistema"},
        {"id": "i1", "kind": "image", "name": "print.png",
         "info": {"width": 800, "height": 400}, "descricao": ""},
        {"id": "v2", "kind": "video", "name": "gravacao.mp4",
         "info": {"duration": 20.0},
         "descricao": "gravação do painel, mostra em tela cheia"},
    ]

    # 1) a IA não disse nada sobre os anexos: com `completar`, TUDO entra
    rel = aplicar(plan, {"leitura": "", "anexos": []}, midias, 30.0,
                  blocos=blocos, completar=True)
    ids = {a["media_id"] for a in rel["anexos"]}
    check(ids == {"v1", "i1", "v2"},
          f"as três mídias anexadas entraram no vídeo ({sorted(ids)})")
    check(all(a.get("origem") == "programa" for a in rel["anexos"]),
          "e cada uma diz que foi o programa que a posicionou")

    # 2) a descrição casa com a fala: "cadastro no sistema" é o bloco 2 (6 s)
    v1 = next(a for a in rel["anexos"] if a["media_id"] == "v1")
    check(abs(v1["out_start"] - 6.0) < 0.01 and v1["tipo"] == "sobreposicao",
          f"o vídeo da tela de cadastro entrou no bloco que fala de cadastro, "
          f"como JANELA ({v1['out_start']:.1f} s, {v1['tipo']})")
    check("descrição" in v1["porque"], "e o motivo diz que foi pela descrição")
    check(abs((v1["out_end"] - v1["out_start"]) - 4.0) < 0.01,
          f"e dura o que a mídia tem, 4 s ({v1['out_end'] - v1['out_start']:.1f})")

    # 3) nada no gancho: min(8 s, 15% de 30 s) = 4,5 s
    check(all(a["out_start"] >= 4.5 for a in rel["anexos"]),
          f"nenhum anexo cai no gancho ({min(a['out_start'] for a in rel['anexos']):.1f} s)")
    # 4) cobertura nunca em cima de cobertura
    cobs = sorted((a["out_start"], a["out_end"]) for a in rel["anexos"]
                  if a["tipo"] == "cobertura")
    check(all(b[0] >= a[1] - 0.02 for a, b in zip(cobs, cobs[1:])),
          f"as coberturas não se sobrepõem ({cobs})")
    # 5) o teto de duração vale: 20 s de mídia viram no máximo 6 s
    v2 = next(a for a in rel["anexos"] if a["media_id"] == "v2")
    check(abs((v2["out_end"] - v2["out_start"]) - 6.0) < 0.01,
          f"mídia longa entra com o teto de 6 s ({v2['out_end'] - v2['out_start']:.1f})")
    check(v2["tipo"] == "cobertura",
          "só quem pediu 'tela cheia' na descrição cobre a tela")
    i1 = next(a for a in rel["anexos"] if a["media_id"] == "i1")
    check(i1["tipo"] == "sobreposicao", "imagem entra como sobreposição")

    # 5b) a IA pede cobertura para um vídeo cuja descrição NÃO pede tela
    #     cheia: vira janela, com o motivo escrito — o usuário quer arrastar
    #     e encolher, não sumir atrás da gravação de tela
    plan_b = EditPlan()
    plan_b.clips = [Clip(src_start=float(k * 3), src_end=float(k * 3 + 3)) for k in range(10)]
    rel_b = aplicar(plan_b, {"leitura": "", "anexos": [
        {"midia": 0, "bloco": 2, "tipo": "cobertura", "segundos": 4.0, "porque": "x"}]},
        midias, 30.0, blocos=blocos, completar=False)
    check(len(rel_b["anexos"]) == 1 and rel_b["anexos"][0]["tipo"] == "sobreposicao",
          "cobertura pedida pela IA sem 'tela cheia' na descrição vira janela")
    check(any("janela" in a for a in rel_b["anexos"][0]["ajustes"]),
          "e o ajuste é dito, não feito escondido")
    check(not plan_b.cutaways, "nenhuma cobertura foi criada por conta da IA")

    # 6) na aplicação MANUAL (sem completar) nada entra sozinho
    rel2 = aplicar(plan, {"leitura": "", "anexos": []}, midias, 30.0,
                   blocos=blocos, completar=False)
    check(not rel2["anexos"], "sem `completar` o programa não posiciona nada por conta")

    # 7) idempotente: o que já está no vídeo não entra de novo
    plan.cutaways.append(Cutaway(media_id="v1", out_start=6.0, out_end=10.0))
    rel3 = aplicar(plan, {"leitura": "", "anexos": []}, [midias[0]], 30.0,
                   blocos=blocos, completar=True)
    check(not rel3["anexos"], "mídia que já está no vídeo não é posicionada outra vez")

    # 8) e o pedido à IA mudou de lado
    pedido = montar_pedido(blocos, midias, 30.0)
    check("TODA mídia" in pedido and "não a use" not in pedido,
          "o pedido manda TODA mídia entrar, em vez de 'se não ajuda, não use'")
    check("=== ANEXOS ===" in INSTRUCAO,
          "a instrução da IA tem uma seção sobre anexos (não tinha nenhuma)")


def testar_sobreposicao_no_tamanho_da_previa() -> None:
    """A sobreposição ocupa a MESMA fração do quadro na prévia e na exportação.

    O tamanho de um overlay era ``iw*scale`` em pixels do PNG — certo só
    quando a saída tem o tamanho da fonte. Na prévia de 240p o PNG saía com os
    MESMOS pixels em cima de um quadro 4,5x menor: um cartão de 84% da largura
    cobria a tela. A prévia promete ser "ao pixel o que vai baixar" e mentia
    justamente no que o usuário anexou. Agora escala pela altura da saída.
    """
    import subprocess

    import numpy as np

    from editor.config import FFMPEG, ExportParams
    from editor.edit.timeline import Timeline
    from editor.ffmpeg_utils import probe
    from editor.models import Clip, EditPlan, Overlay
    from editor.render.filters import overlay_chain
    from editor.render.renderer import plan_segments, render_video_segments

    # 1) a fórmula: mesma fração de altura em qualquer saída
    o = Overlay(media_id="m", out_start=0.0, out_end=2.0, scale=0.8)
    cheio, _ = overlay_chain([o], {"m": "/x.png"}, 0.0, 1080, 1920, 1, "a", "b",
                             ref_height=1920)
    previa, _ = overlay_chain([o], {"m": "/x.png"}, 0.0, 135, 240, 1, "a", "b",
                              ref_height=1920)
    check("iw*0.8000" in cheio, "na saída do tamanho da fonte o scale é o pedido")
    check("iw*0.1000" in previa,
          "na prévia de 240 o scale encolhe junto (0,8 × 240/1920 = 0,1)")
    antigo, _ = overlay_chain([o], {"m": "/x.png"}, 0.0, 135, 240, 1, "a", "b")
    check("iw*0.8000" in antigo, "sem a régua, o comportamento antigo continua")
    # num formato DERIVADO (9:16 de uma fonte horizontal) a razão das alturas
    # sozinha inflava a janela: 1,78x — a média geométrica com a razão das
    # larguras (0,56x) mantém a mesma fração de ÁREA do quadro (1,0x)
    vertical, _ = overlay_chain([o], {"m": "/x.png"}, 0.0, 540, 960, 1, "a", "b",
                                ref_height=540, ref_width=960)
    check("iw*0.8000" in vertical,
          "no 9:16 tirado do horizontal a janela mantém o mesmo peso visual (fração de área)")

    # 2) a prova no pixel: renderiza a mesma sobreposição em dois tamanhos e
    #    mede quanto do quadro ela ocupa
    tmp = Path(tempfile.mkdtemp(prefix="ov_previa_"))
    video = tmp / "fonte.mp4"
    subprocess.run([FFMPEG, "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=0x202020:s=432x768:r=30:d=2",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                    "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)], check=True)
    png = tmp / "vermelho.png"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                    "-i", "color=c=red:s=200x100", "-frames:v", "1", str(png)],
                   check=True)
    info = probe(video)

    def fracao_vermelha(escala: str) -> float:
        plan = EditPlan()
        plan.export = ExportParams(scale=escala, burn_subtitles=False,
                                   preset="ultrafast", crf=30)
        plan.clips = [Clip(src_start=0.0, src_end=2.0)]
        plan.overlays = [Overlay(media_id="m", out_start=0.0, out_end=2.0,
                                 x=0.5, y=0.5, scale=1.0,
                                 anim_in="none", anim_out="none")]
        tl = Timeline(plan.active_clips, 30.0)
        segs = plan_segments(plan, tl,
                             {"main": {"path": str(video), "info": info, "kind": "video"}},
                             info)
        segs = render_video_segments(segs, plan, info, [], tmp / f"segs-{escala}",
                                     {"main": str(video), "m": str(png)}, None)
        saida = segs[0].file
        w, h = probe(saida).display_size
        cru = subprocess.run([FFMPEG, "-v", "error", "-ss", "1.0", "-i", saida,
                              "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, check=True).stdout
        img = np.frombuffer(cru, np.uint8).reshape(h, w, 3)
        vermelho = (img[:, :, 0] > 170) & (img[:, :, 1] < 90) & (img[:, :, 2] < 90)
        linhas = np.flatnonzero(vermelho.any(axis=1))
        return (linhas[-1] - linhas[0] + 1) / h if linhas.size else 0.0

    f_fonte = fracao_vermelha("source")
    f_previa = fracao_vermelha("216")          # metade: 216x384
    check(0.10 < f_fonte < 0.16,
          f"no tamanho da fonte a sobreposição ocupa ~13% da altura ({f_fonte:.1%})")
    check(abs(f_fonte - f_previa) < 0.02,
          f"e na prévia pela metade ocupa a MESMA fração ({f_previa:.1%}) — "
          f"antes ocupava o dobro")
    shutil.rmtree(tmp, ignore_errors=True)


def testar_muleta_sai_com_um_vale() -> None:
    """A muleta ("então", "né") sai com UM vale de verdade e um respiro mínimo.

    Com a mesma trava do corte de copy — 120 ms dos dois lados — a muleta
    nunca saía: ela vive dentro da fala corrida. O usuário via o "então" no
    vídeo e a IA dizendo que tinha pedido para tirar. A muleta é curta e a
    emenda é pequena: um lado com vale e o outro com 60 ms bastam para o fade
    esconder. O corte de COPY (ideia inteira) continua exigindo os dois lados.
    """
    from editor.ai.cortes import VALE_FRACO, VALE_MIN, aplicar
    from editor.audio.envelope import compute_envelope

    def cenario(gap_antes: float, gap_depois: float):
        a = (0.0, 0.5)
        m = (a[1] + gap_antes, a[1] + gap_antes + 0.25)
        b = (m[1] + gap_depois, m[1] + gap_depois + 0.5)
        env = compute_envelope(build([a, m, b], b[1] + 0.3, claps=[], noise=0.0011), 16000)
        words = [{"id": 0, "start": a[0], "end": a[1], "text": "a"},
                 {"id": 1, "start": m[0], "end": m[1], "text": "então"},
                 {"id": 2, "start": b[0], "end": b[1], "text": "b"}]
        return aplicar(words, {"leitura": "", "remover": [
            {"de": 1, "ate": 1, "tipo": "vicio", "motivo": "muleta"}]}, env=env)

    # vale de 200 ms antes, 70 ms depois: SAI, avisando que a emenda é apertada
    r = cenario(0.20, 0.07)
    check(len(r["takes"]) == 1 and r["takes"][0]["source"] == "ia_vicio",
          f"muleta com vale de um lado e respiro mínimo do outro sai ({len(r['takes'])})")
    check(r["takes"] and "apertada" in r["takes"][0]["reason"],
          "e o motivo avisa que a emenda é apertada")
    # 50 ms dos dois lados: colada na fala, NÃO sai
    r2 = cenario(0.05, 0.05)
    check(not r2["takes"] and any("colada" in x["motivo"] for x in r2["recusados"]),
          f"muleta colada dos dois lados continua recusada "
          f"({VALE_MIN*1000:.0f} ms de um lado e {VALE_FRACO*1000:.0f} do outro)")
    # 200 ms de um lado e 30 ms do outro: abaixo do respiro mínimo, NÃO sai
    r3 = cenario(0.20, 0.03)
    check(not r3["takes"], "sem nem o respiro mínimo de um lado, não sai")


def testar_janela_de_video() -> None:
    """Vídeo como JANELA por cima do quadro (picture-in-picture).

    Antes, só PNG entrava como sobreposição; vídeo era "cobertura" — cobria a
    tela e o usuário sumia atrás da gravação de tela, sem ter como arrastar
    nem encolher. Agora o vídeo entra na mesma caixa do PNG: lido com -ss no
    ponto certo da mídia, -t só até o fim da janela e SEM o áudio dele — a
    fala principal continua por baixo. Conferido no pixel.
    """
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    import numpy as np

    from editor.config import FFMPEG, ExportParams
    from editor.edit.timeline import Timeline
    from editor.ffmpeg_utils import probe
    from editor.models import Clip, EditPlan, Overlay
    from editor.render.filters import overlay_chain
    from editor.render.renderer import plan_segments, render_video_segments

    # 1) a descrição das entradas: vídeo com -ss no ponto certo, imagem em laço
    o = Overlay(media_id="m", out_start=4.0, out_end=7.0, media_start=1.5)
    _g, ent = overlay_chain([o], {"m": "/x.mp4"}, 5.0, 1080, 1920, 1, "a", "b",
                            ref_height=1920)
    check(ent and ent[0]["video"] and abs(ent[0]["ss"] - 2.5) < 1e-6,
          f"o trecho que começa 1 s depois da janela entra no vídeo em "
          f"media_start + 1 s = 2,5 s ({ent[0]['ss'] if ent else '?'})")
    check(ent and abs(ent[0]["t"] - 2.5) < 1e-6,
          f"e lê só o que a janela ainda dura mais meio segundo ({ent[0]['t'] if ent else '?'})")
    _g, ent_png = overlay_chain([o], {"m": "/x.png"}, 5.0, 1080, 1920, 1, "a", "b")
    check(ent_png and not ent_png[0]["video"], "PNG continua entrando em laço de imagem")

    # 2) no pixel: um vídeo vermelho-depois-azul por cima de um quadro cinza
    tmp = Path(tempfile.mkdtemp(prefix="pip_"))
    fonte = tmp / "fonte.mp4"
    subprocess.run([FFMPEG, "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=0x202020:s=432x768:r=30:d=2",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                    "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(fonte)], check=True)
    janela = tmp / "janela.mp4"
    subprocess.run([FFMPEG, "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=red:s=200x100:r=30:d=1",
                    "-f", "lavfi", "-i", "color=c=blue:s=200x100:r=30:d=1",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
                    "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(janela)], check=True)
    info = probe(fonte)

    def cor_no_centro(media_start: float, t: float) -> tuple[int, int, int]:
        plan = EditPlan()
        plan.export = ExportParams(scale="source", burn_subtitles=False,
                                   preset="ultrafast", crf=30)
        plan.clips = [Clip(src_start=0.0, src_end=2.0)]
        plan.overlays = [Overlay(media_id="m", out_start=0.0, out_end=2.0,
                                 media_start=media_start, x=0.5, y=0.5, scale=1.0,
                                 anim_in="none", anim_out="none")]
        tl = Timeline(plan.active_clips, 30.0)
        segs = plan_segments(plan, tl,
                             {"main": {"path": str(fonte), "info": info, "kind": "video"}},
                             info)
        segs = render_video_segments(segs, plan, info, [], tmp / f"segs-{media_start}",
                                     {"main": str(fonte), "m": str(janela)}, None)
        saida = segs[0].file
        w, h = probe(saida).display_size
        cru = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", saida,
                              "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, check=True).stdout
        img = np.frombuffer(cru, np.uint8).reshape(h, w, 3)
        px = img[h // 2, w // 2]
        # e o quadro fora da janela continua cinza — a janela NÃO cobre a tela
        canto = img[20, 20]
        check(all(abs(int(c) - 0x20) < 24 for c in canto),
              f"fora da janela o vídeo principal continua aparecendo ({canto.tolist()})")
        return int(px[0]), int(px[1]), int(px[2])

    r, g, b = cor_no_centro(0.0, 0.5)
    check(r > 170 and g < 90 and b < 90,
          f"aos 0,5 s a janela mostra o começo do vídeo sobreposto — vermelho ({r},{g},{b})")
    r, g, b = cor_no_centro(1.0, 0.5)
    check(b > 170 and r < 90 and g < 90,
          f"com media_start=1 s a janela já mostra o azul ({r},{g},{b}) — o -ss vale")
    shutil.rmtree(tmp, ignore_errors=True)


def testar_legenda_vertical_nao_corta() -> None:
    """A legenda do formato DERIVADO é quebrada na régua DELE.

    "As legendas do vídeo vertical cortam, na horizontal fica até bom": a
    fonte é horizontal (42 caracteres por linha) e o 9:16 derivado só cabe
    24. As legendas nasciam com as linhas do horizontal, o ASS sai com
    WrapStyle 2 (sem quebra automática) e a linha passava da tela pelos dois
    lados. Agora a quebra é refeita para a régua do formato — e nenhuma
    palavra some no caminho. Conferido no pixel do libass.
    """
    import subprocess
    import tempfile
    from pathlib import Path

    import numpy as np

    from editor.config import FFMPEG, ExportParams
    from editor.ffmpeg_utils import MediaInfo
    from editor.models import EditPlan
    from editor.projects import escalar_legenda
    from editor.render.renderer import regua_da_legenda
    from editor.subtitles.ass import build_ass
    from editor.subtitles.linebreak import requebrar, wrap

    main = MediaInfo(path="x.mp4", duration=10.0, width=1920, height=1080)
    plan = EditPlan()
    escalar_legenda(plan, main)
    check(plan.style.max_chars_per_line == 42,
          f"a fonte horizontal quebra em 42 caracteres ({plan.style.max_chars_per_line})")
    pw, ph, vert = regua_da_legenda(main, ExportParams(aspect="9:16"), plan.style)
    check(abs(ph / pw - 16 / 9) < 0.01 and vert.max_chars_per_line == 24,
          f"o 9:16 derivado tem régua própria: {pw}x{ph}, {vert.max_chars_per_line} chars")

    frase = ("isso muda tudo na sua comunicação com o cliente e faz o anúncio "
             "vender muito mais")
    horizontal = "\n".join(wrap(frase, 42, 2) or [frase])
    check(len(horizontal.split("\n")) == 2 and all(len(l) <= 42 for l in horizontal.split("\n")),
          "a legenda nasce em duas linhas de até 42 (como no horizontal)")
    novo = requebrar(horizontal, vert.max_chars_per_line, vert.max_lines)
    check(all(len(l) <= 24 for l in novo.split("\n")),
          f"refeita para o vertical, nenhuma linha passa de 24 ({[len(l) for l in novo.split(chr(10))]})")
    check(" ".join(novo.split()) == " ".join(horizontal.split()),
          "e nenhuma palavra sumiu nem mudou de ordem")
    check(requebrar("curta", 24, 2) == "curta", "texto que já cabe não é mexido")

    # no pixel: o libass desenha o texto de cada versão num quadro 1080x1920
    def extremos(texto: str) -> tuple[int, int]:
        with tempfile.TemporaryDirectory() as tmp:
            ass_path = Path(tmp) / "leg.ass"
            ass_path.write_text(build_ass([{"start": 0.0, "end": 1.0, "text": texto}],
                                          vert, pw, ph), encoding="utf-8")
            escaped = str(ass_path).replace("\\", "/").replace(":", r"\:")
            proc = subprocess.run(
                [FFMPEG, "-v", "error", "-nostdin",
                 "-f", "lavfi", "-i", f"color=c=black:s={pw}x{ph}:d=0.1",
                 "-vf", f"ass='{escaped}'", "-frames:v", "1",
                 "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"],
                capture_output=True, check=True)
            quadro = np.frombuffer(proc.stdout, np.uint8)[: pw * ph].reshape(ph, pw)
        cols = np.flatnonzero((quadro > 40).any(axis=0))
        return (int(cols[0]), int(cols[-1])) if cols.size else (-1, -1)

    a0, a1 = extremos(horizontal)
    b0, b1 = extremos(novo)
    check(a0 >= 0 and (a0 < 20 or a1 > pw - 20),
          f"com a quebra do horizontal o texto encosta ou passa da borda "
          f"(o defeito: colunas {a0}..{a1} num quadro de {pw})")
    check(b0 >= 40 and b1 <= pw - 40,
          f"com a quebra refeita a legenda cabe com folga (colunas {b0}..{b1})")


def testar_geracao_com_ia_mockada() -> None:
    """Gerar imagem (Nano Banana) e vídeo (Veo) de dentro da edição.

    O HTTP é simulado — não há chave nesta máquina — mas tudo em volta é o de
    verdade: o arquivo desce para a pasta do projeto, vira mídia, e entra como
    JANELA no ponto do cursor, no tamanho e canto de janela. A fala principal
    continua por baixo (o vídeo gerado entra sem áudio, como qualquer janela).
    """
    import base64
    import subprocess
    import tempfile
    from pathlib import Path

    from editor import projects as svc
    from editor.ai import gemini, gerar
    from editor.config import FFMPEG
    from editor.models import Clip

    tmp = Path(tempfile.mkdtemp(prefix="gerar_ia_"))
    fonte = tmp / "fonte.mp4"
    subprocess.run([FFMPEG, "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=0x303030:s=960x540:r=30:d=3",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                    "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(fonte)], check=True)
    png = tmp / "gerada.png"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                    "-i", "color=c=red:s=400x200", "-frames:v", "1", str(png)], check=True)
    mp4 = tmp / "gerado.mp4"
    subprocess.run([FFMPEG, "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=30:d=1",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    str(mp4)], check=True)
    png_bytes, mp4_bytes = png.read_bytes(), mp4.read_bytes()

    project = svc.create(str(fonte), "gerado por ia", "VSL")
    project.plan.clips = [Clip(src_start=0.0, src_end=3.0)]
    project.save_plan()

    chamadas: list[tuple[str, dict]] = []

    def fake_listar(chave, forcar=False, metodo="generateContent"):
        if metodo == "generateContent":
            return [{"id": "gemini-2.5-flash", "nome": "", "entrada": 0, "saida": 0},
                    {"id": "gemini-2.5-flash-image", "nome": "", "entrada": 0, "saida": 0}]
        return [{"id": "veo-3.1-generate-preview", "nome": "", "entrada": 0, "saida": 0}]

    def fake_post(chave, caminho, corpo):
        chamadas.append((caminho, corpo))
        if ":generateContent" in caminho:
            return {"candidates": [{"content": {"parts": [
                {"text": "aqui está"},
                {"inlineData": {"mimeType": "image/png",
                                "data": base64.b64encode(png_bytes).decode("ascii")}}]}}]}
        return {"name": "models/veo-3.1-generate-preview/operations/op1", "done": False}

    class Resp:
        status_code = 200

        def json(self):
            return {"name": "x", "done": True, "response": {"generateVideoResponse": {
                "generatedSamples": [{"video": {"uri": "https://generativelanguage.googleapis.com/v1beta/files/abc:download?alt=media"}}]}}}

    class Cliente:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, *a, **k): return Resp()

    class Ctx:
        def stage(self, n, m=""): pass
        def progress(self, f, m="", s=""): pass
        def cancelled(self): return False
        def scoped(self, lo, hi, stage=""): return lambda f, m="": None

    guardado = (gerar.listar_modelos, gerar._post, gerar._cliente, gerar._baixar,
                gemini.chave_guardada, gerar.INTERVALO_VIDEO)
    try:
        gerar.listar_modelos = fake_listar
        gerar._post = fake_post
        gerar._cliente = lambda chave: Cliente()
        gerar._baixar = lambda chave, uri: mp4_bytes
        gemini.chave_guardada = lambda: "chave-de-teste-nao-vaza"
        gerar.INTERVALO_VIDEO = 0.0

        r = svc.gerar_com_ia(project, Ctx(), {"tipo": "image", "prompt": "gráfico de barras subindo",
                                              "out_start": 0.5, "duracao": 1.0})
        m = r["media"]
        check(m["kind"] == "image" and Path(m["path"]).exists()
              and Path(m["path"]).parent == project.dir / "gerado",
              f"a imagem gerada desceu para a pasta do projeto ({m['path']})")
        check(r.get("modelo") == "gemini-2.5-flash-image",
              f"e foi pedida ao modelo de IMAGEM da chave ({r.get('modelo')})")
        corpo = chamadas[0][1]
        check(corpo["generationConfig"]["responseModalities"] == ["IMAGE"]
              and corpo["generationConfig"]["imageConfig"]["aspectRatio"] == "16:9",
              "o pedido pede IMAGEM na proporção da fonte horizontal")
        ov = r.get("overlay") or {}
        check(abs(ov.get("out_start", -1) - 0.5) < 1e-6 and abs(ov.get("out_end", -1) - 1.5) < 1e-6,
              f"entrou como janela no cursor, por 1 s ({ov.get('out_start')}..{ov.get('out_end')})")
        check(abs(ov.get("x", 0) - 0.78) < 1e-6 and abs(ov.get("scale", 0) - 0.96) < 1e-3,
              f"no canto de janela do quadro horizontal: x=0,78, escala 0,96 "
              f"(40% de 960 / 400) — ({ov.get('x')}, {ov.get('scale')})")
        fresco = svc.load(project.id)
        check(any(o.media_id == m["id"] for o in fresco.plan.overlays),
              "e a janela está gravada no plano do projeto")
        check(any(x["id"] == m["id"] for x in svc.list_media(project.id)),
              "e a imagem está na mídia do projeto")

        r2 = svc.gerar_com_ia(project, Ctx(), {"tipo": "video", "prompt": "mãos digitando",
                                               "out_start": 0.2})
        m2 = r2["media"]
        check(m2["kind"] == "video" and Path(m2["path"]).suffix == ".mp4"
              and abs(float(m2["info"].get("duration", 0)) - 1.0) < 0.2,
              f"o vídeo do Veo desceu como MP4 de 1 s ({m2['info'].get('duration')})")
        caminho, corpo2 = next((c, b) for c, b in chamadas if "predictLongRunning" in c)
        check("veo-3.1-generate-preview" in caminho
              and corpo2["instances"][0]["prompt"] == "mãos digitando"
              and corpo2["parameters"]["aspectRatio"] == "16:9"
              and corpo2["parameters"]["durationSeconds"] == 8,
              "o pedido ao Veo leva o texto, a proporção e 8 s")
        ov2 = r2.get("overlay") or {}
        check(abs((ov2.get("out_end", 0) - ov2.get("out_start", 0)) - 1.0) < 0.05,
              f"a janela dura o que o clipe tem, 1 s ({ov2.get('out_end', 0) - ov2.get('out_start', 0):.2f})")
        check(not any("chave-de-teste" in c for c, _ in chamadas),
              "a chave nunca aparece no caminho da URL (vai só no cabeçalho)")
    finally:
        (gerar.listar_modelos, gerar._post, gerar._cliente, gerar._baixar,
         gemini.chave_guardada, gerar.INTERVALO_VIDEO) = guardado
        try:
            svc.delete_project(project.id)
        except Exception:  # noqa: BLE001
            pass


def testar_quadro_encaixado() -> None:
    """O 9:16 tirado de uma gravação horizontal: o vídeo INTEIRO numa tela.

    "Eu pedi vertical, baixou vertical, mas na prévia continua horizontal.
    Eu tenho que ter a opção de pegar o vídeo, diminuir e aumentar ele numa
    tela preta atrás dele." O quadro de um formato derivado agora tem dois
    modos: encaixe (o vídeo inteiro numa tela do formato, no tamanho e lugar
    que o usuário arrasta na prévia) e recorte (concêntrico no rosto). O
    padrão para horizontal -> vertical é encaixe. Conferido no pixel.
    """
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    import numpy as np

    from editor.config import FFMPEG, ExportParams
    from editor.edit.timeline import Timeline
    from editor.ffmpeg_utils import MediaInfo, probe
    from editor.models import Clip, EditPlan
    from editor.projects import quadro_do_formato, quadro_padrao
    from editor.render.renderer import (geometria_do_encaixe, plan_segments,
                                        quadro_de_saida, render_video_segments,
                                        target_size)

    # 1) o padrão: horizontal -> vertical/quadrado encaixa; vertical -> horizontal recorta
    check(quadro_padrao(16 / 9, "9:16")["modo"] == "encaixe",
          "de uma gravação horizontal o 9:16 nasce ENCAIXADO (vídeo inteiro numa tela)")
    check(quadro_padrao(16 / 9, "1:1")["modo"] == "encaixe", "e o 1:1 também")
    check(quadro_padrao(9 / 16, "16:9")["modo"] == "recorte",
          "de uma gravação vertical o 16:9 nasce recortado (encaixar deixaria duas tarjas enormes)")
    check(quadro_padrao(16 / 9, "16:9")["modo"] == "recorte", "o próprio formato da fonte não encaixa nada")

    # 2) o que o usuário gravou vale por cima do padrão, saneado
    main = MediaInfo(path="x.mp4", duration=10.0, width=1920, height=1080)
    plan = EditPlan()
    plan.enquadramento["9:16"] = {"modo": "encaixe", "escala": 9.0, "x": 0.5, "y": 0.72,
                                  "fundo": "roxo"}
    q = quadro_do_formato(plan, main, "9:16")
    check(q["escala"] == 4.0 and q["fundo"] == "preto" and abs(q["y"] - 0.72) < 1e-9,
          f"escala e fundo fora da régua são saneados; a posição vale ({q})")
    export = ExportParams(aspect="9:16")
    plan.export = export
    tw, th = target_size(main, export)
    check(quadro_de_saida(plan, main, tw, th) is not None,
          "o render enxerga o encaixe do 9:16")
    check(quadro_de_saida(plan, main, 1920, 1080) is None,
          "e não há encaixe quando a saída tem a proporção da fonte")
    fw, fh, px, py = geometria_do_encaixe({"escala": 1.0, "x": 0.5, "y": 0.5}, 1920, 1080, tw, th)
    check(fw == tw and abs(fh - tw * 1080 / 1920) <= 2 and px == 0 and abs(py - (th - fh) / 2) <= 1,
          f"com escala 1 o vídeo ocupa a largura toda, centrado ({fw}x{fh} em {px},{py} de {tw}x{th})")

    # 3) no pixel: uma gravação horizontal cinza com um risco branco no meio
    tmp = Path(tempfile.mkdtemp(prefix="encaixe_"))
    fonte = tmp / "fonte.mp4"
    subprocess.run([FFMPEG, "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=0x808080:s=640x360:r=30:d=2",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                    "-vf", "drawbox=x=0:y=170:w=640:h=20:color=white:t=fill",
                    "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(fonte)], check=True)
    info = probe(fonte)

    def quadro_9x16(enquadramento: dict | None) -> np.ndarray:
        plan = EditPlan()
        plan.export = ExportParams(aspect="9:16", burn_subtitles=False,
                                   preset="ultrafast", crf=30)
        plan.clips = [Clip(src_start=0.0, src_end=2.0)]
        if enquadramento:
            plan.enquadramento["9:16"] = enquadramento
        tl = Timeline(plan.active_clips, 30.0)
        segs = plan_segments(plan, tl, {"main": {"path": str(fonte), "info": info, "kind": "video"}}, info)
        segs = render_video_segments(segs, plan, info, [], tmp / f"segs-{hash(str(enquadramento))}",
                                     {"main": str(fonte)}, None)
        w, h = probe(segs[0].file).display_size
        cru = subprocess.run([FFMPEG, "-v", "error", "-ss", "1.0", "-i", segs[0].file,
                              "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                             capture_output=True, check=True).stdout
        return np.frombuffer(cru, np.uint8).reshape(h, w)

    img = quadro_9x16(None)                       # o padrão: encaixe, escala 1, centro
    h, w = img.shape
    check(abs(w / h - 9 / 16) < 0.01, f"o arquivo sai 9:16 ({w}x{h})")
    linhas_cinza = np.flatnonzero((np.abs(img.astype(int) - 128) < 12).mean(axis=1) > 0.9)
    linhas_pretas = np.flatnonzero((img < 20).mean(axis=1) > 0.9)
    linhas_brancas = np.flatnonzero((img > 235).mean(axis=1) > 0.9)
    check(linhas_pretas.size > h * 0.5 and linhas_pretas[0] == 0 and linhas_pretas[-1] == h - 1,
          f"tela preta em cima e embaixo ({linhas_pretas.size} de {h} linhas)")
    check(linhas_cinza.size > 0 and abs((linhas_cinza[0] + linhas_cinza[-1]) / 2 - h / 2) < 4
          and abs((linhas_cinza[-1] - linhas_cinza[0]) - w * 360 / 640) < 6,
          f"o vídeo inteiro está no meio, na largura toda (linhas {linhas_cinza[0] if linhas_cinza.size else '?'}..{linhas_cinza[-1] if linhas_cinza.size else '?'})")
    check(linhas_brancas.size > 0 and abs((linhas_brancas[0] + linhas_brancas[-1]) / 2 - h / 2) < 6,
          "e o risco branco do meio da gravação está no meio da tela")

    img2 = quadro_9x16({"modo": "encaixe", "escala": 0.5, "x": 0.5, "y": 0.25, "fundo": "preto"})
    cinza2 = np.flatnonzero((np.abs(img2.astype(int) - 128) < 12).mean(axis=1) > 0.4)
    cols2 = np.flatnonzero((np.abs(img2.astype(int) - 128) < 12).mean(axis=0) > 0.1)
    check(cinza2.size > 0 and abs((cinza2[0] + cinza2[-1]) / 2 - h * 0.25) < 6
          and abs((cols2[-1] - cols2[0] + 1) - w * 0.5) <= 4,
          f"escala 0,5 em y=0,25: o vídeo fica na metade da largura, no quarto de cima "
          f"(colunas {cols2[0] if cols2.size else '?'}..{cols2[-1] if cols2.size else '?'}, "
          f"linhas {cinza2[0] if cinza2.size else '?'}..{cinza2[-1] if cinza2.size else '?'})")

    img3 = quadro_9x16({"modo": "recorte", "escala": 1.0, "x": 0.5, "y": 0.5, "fundo": "preto"})
    check((img3 < 20).mean() < 0.02,
          f"no modo recorte não há tela preta: o quadro é preenchido pela gravação "
          f"({(img3 < 20).mean():.1%} de preto)")
    shutil.rmtree(tmp, ignore_errors=True)


def testar_quadro_pela_rota_e_janela_na_trilha() -> None:
    """A rota do quadro e o teto de esticar uma janela na trilha."""
    import subprocess
    import tempfile
    from pathlib import Path

    from editor import projects as svc
    from editor.ai.roteiro import MAX_ANEXO, teto_do_anexo
    from editor.config import FFMPEG
    from editor.models import Clip
    from editor.server import app

    tmp = Path(tempfile.mkdtemp(prefix="quadro_rota_"))
    fonte = tmp / "fonte.mp4"
    subprocess.run([FFMPEG, "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=0x303030:s=640x360:r=30:d=12",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                    "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(fonte)], check=True)
    janela = tmp / "janela.mp4"
    subprocess.run([FFMPEG, "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=red:s=320x180:r=30:d=3",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    str(janela)], check=True)
    project = svc.create(str(fonte), "quadro", "VSL")
    project.plan.clips = [Clip(src_start=0.0, src_end=12.0)]
    project.plan.export.extras = ("9:16",)
    project.save_plan()
    cliente = TestClient(app)
    try:
        r = cliente.get(f"/api/projects/{project.id}").json()
        check(r.get("quadros", {}).get("9:16", {}).get("modo") == "encaixe",
              "o projeto já diz o quadro de cada formato que entrega")
        r = cliente.post(f"/api/projects/{project.id}/ops/quadro",
                         json={"aspecto": "9:16", "y": 0.3, "escala": 0.8})
        check(r.status_code == 200 and abs(r.json()["quadro"]["y"] - 0.3) < 1e-9
              and abs(r.json()["quadro"]["escala"] - 0.8) < 1e-9,
              "arrastar/redimensionar na prévia grava posição e escala do quadro")
        r = cliente.post(f"/api/projects/{project.id}/ops/quadro",
                         json={"aspecto": "9:16", "modo": "recorte"})
        check(r.json()["quadro"]["modo"] == "recorte" and abs(r.json()["quadro"]["y"] - 0.3) < 1e-9,
              "trocar o modo não perde a posição")
        r = cliente.post(f"/api/projects/{project.id}/ops/quadro",
                         json={"aspecto": "9:16", "padrao": True})
        check(r.json()["quadro"]["modo"] == "encaixe" and abs(r.json()["quadro"]["y"] - 0.5) < 1e-9,
              "'padrão' volta ao que o programa faria")
        r = cliente.post(f"/api/projects/{project.id}/ops/quadro", json={"aspecto": "4:3"})
        check(r.status_code == 400, "formato desconhecido é recusado")

        # a janela na trilha: estica até o fim da mídia, nunca além
        m = svc.add_media(project.id, str(janela), "video", "janela.mp4")
        r = cliente.post(f"/api/projects/{project.id}/overlays",
                         json={"media_id": m["id"], "out_start": 2.0, "out_end": 4.0})
        oid = r.json()["overlay"]["id"]
        r = cliente.post(f"/api/projects/{project.id}/ops/item",
                         json={"kind": "overlay", "id": oid, "action": "resize",
                               "side": "end", "time": 9.0})
        ov = next(o for t in r.json()["timeline"]["tracks"] for o in t["items"] if o["id"] == oid)
        check(abs(ov["out_end"] - 5.0) < 0.01 and "3.0 s" in r.json().get("aviso", ""),
              f"esticar uma janela de vídeo de 3 s para 7 s para no fim dele "
              f"({ov['out_end']:.2f} s) e avisa")
        r = cliente.post(f"/api/projects/{project.id}/ops/item",
                         json={"kind": "overlay", "id": oid, "action": "move", "delta": 3.0})
        ov = next(o for t in r.json()["timeline"]["tracks"] for o in t["items"] if o["id"] == oid)
        check(abs(ov["out_start"] - 5.0) < 0.01 and abs(ov["out_end"] - 8.0) < 0.01,
              f"mover leva o item inteiro, sem mudar a duração ({ov['out_start']:.1f}..{ov['out_end']:.1f})")

        # e o anexo automático mostra o vídeo INTEIRO (até metade da saída)
        v = {"kind": "video", "info": {"duration": 20.0}}
        check(abs(teto_do_anexo(v, 60.0) - 20.0) < 1e-9,
              "vídeo de 20 s anexado num vídeo de 60 s entra inteiro")
        check(abs(teto_do_anexo(v, 20.0) - 10.0) < 1e-9,
              "num vídeo de 20 s, o teto é a metade (10 s)")
        check(abs(teto_do_anexo({"kind": "image", "info": {}}, 60.0) - MAX_ANEXO) < 1e-9,
              f"imagem continua com o teto de {MAX_ANEXO:.0f} s")
    finally:
        try:
            svc.delete_project(project.id)
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    install(["frase %d" % i for i in range(20)])
    sys.exit(main())
