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
    testar_cartoes_so_por_pedido()
    testar_biblioteca_de_musicas()
    testar_corte_na_primeira_tela()
    testar_armadilhas_de_musica_e_cartao()
    testar_resumo_para_caber()
    testar_keyframes_animam_de_verdade()
    testar_marcos_acompanham_o_corte()
    testar_marcos_nao_reencodam_o_resto()
    testar_faixas_empilham_e_rotas_aceitam()
    testar_emudecer_um_bloco_sai_no_arquivo()
    testar_efeitos_mexem_no_pixel()
    testar_dividir_bloco_nao_compartilha_interior()
    testar_varios_videos_no_mesmo_projeto()
    testar_previa_e_render_fazem_a_mesma_conta()
    testar_ripple_fecha_o_buraco()
    testar_gravar_dentro_do_app()
    testar_pacote_numa_esteira_so()
    testar_gravacao_e_teleprompter_na_tela()
    testar_olhar_na_lente()
    testar_tomadas_em_sequencia_nunca_por_cima()
    testar_nenhum_campo_branco_no_branco()
    testar_nenhuma_cor_fora_da_paleta()
    testar_som_nao_estoura_com_trilha()
    testar_formato_de_feed_e_sem_legenda()
    testar_broll_depois_da_edicao()
    testar_trocar_a_musica()
    testar_banco_de_broll()
    testar_linha_do_tempo_com_varias_gravacoes()
    testar_broll_automatico()
    testar_previa_mostra_o_que_baixa()
    testar_relogio_e_aviso_de_pronto()
    testar_trilha_toca_do_comeco_ao_fim()

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
    from dataclasses import replace as replace_style

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

    # o que o usuário moveu na prévia vale em TODO formato: a margem viaja
    # como proporção do padrão, igual ao tamanho
    subiu = replace_style(plan.style, margin_v=int(plan.style.margin_v * 1.5))
    _pw2, _ph2, vert_subiu = regua_da_legenda(main, ExportParams(aspect="9:16"), subiu)
    check(abs(vert_subiu.margin_v / max(vert.margin_v, 1) - 1.5) < 0.02,
          f"subir a legenda 50% na prévia sobe 50% no vertical também "
          f"({vert.margin_v} → {vert_subiu.margin_v})")
    _pw3, _ph3, vert_igual = regua_da_legenda(main, ExportParams(aspect="9:16"), plan.style)
    check(vert_igual.margin_v == vert.margin_v,
          "e sem ninguém mexer, a altura é exatamente a do padrão do formato")

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
    check(quadro_padrao(16 / 9, "9:16")["modo"] == "recorte",
          "de uma gravação horizontal o 9:16 nasce PREENCHENDO a tela — nada "
          "de duas tarjas pretas por padrão")
    check(quadro_padrao(16 / 9, "1:1")["modo"] == "recorte", "e o 1:1 também")
    check(quadro_padrao(9 / 16, "16:9")["modo"] == "recorte", "o vertical para 16:9 idem")
    check(quadro_padrao(16 / 9, "16:9")["modo"] == "recorte", "o próprio formato da fonte idem")

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

    img = quadro_9x16(None)                       # o PADRÃO: preenche a tela
    h, w = img.shape
    check(abs(w / h - 9 / 16) < 0.01, f"o arquivo sai 9:16 ({w}x{h})")
    check((img < 20).mean() < 0.02,
          f"o padrão PREENCHE a tela: nada de duas tarjas pretas "
          f"({(img < 20).mean():.1%} de preto)")
    linhas_brancas = np.flatnonzero((img > 235).mean(axis=1) > 0.9)
    check(linhas_brancas.size > 0 and abs((linhas_brancas[0] + linhas_brancas[-1]) / 2 - h / 2) < 8,
          "e o risco branco do meio da gravação continua no meio da tela")

    # a tarja preta continua existindo — como OPÇÃO, a um clique na prévia
    enc = quadro_9x16({"modo": "encaixe", "escala": 1.0, "x": 0.5, "y": 0.5,
                       "fundo": "preto"})
    pretas = np.flatnonzero((enc < 20).mean(axis=1) > 0.9)
    cinza = np.flatnonzero((np.abs(enc.astype(int) - 128) < 12).mean(axis=1) > 0.9)
    check(pretas.size > h * 0.5 and cinza.size > 0
          and abs((cinza[0] + cinza[-1]) / 2 - h / 2) < 4,
          f"escolhendo 'encaixar', o vídeo inteiro fica no meio de uma tela "
          f"preta ({pretas.size} linhas de tarja)")

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
        check(r.get("quadros", {}).get("9:16", {}).get("modo") == "recorte",
              "o projeto já diz o quadro de cada formato que entrega, "
              "preenchendo a tela por padrão")
        r = cliente.post(f"/api/projects/{project.id}/ops/quadro",
                         json={"aspecto": "9:16", "y": 0.3, "escala": 0.8})
        check(r.status_code == 200 and abs(r.json()["quadro"]["y"] - 0.3) < 1e-9
              and abs(r.json()["quadro"]["escala"] - 0.8) < 1e-9,
              "arrastar/redimensionar na prévia grava posição e escala do quadro")
        r = cliente.post(f"/api/projects/{project.id}/ops/quadro",
                         json={"aspecto": "9:16", "modo": "encaixe"})
        check(r.json()["quadro"]["modo"] == "encaixe" and abs(r.json()["quadro"]["y"] - 0.3) < 1e-9,
              "trocar o modo não perde a posição")
        r = cliente.post(f"/api/projects/{project.id}/ops/quadro",
                         json={"aspecto": "9:16", "padrao": True})
        check(r.json()["quadro"]["modo"] == "recorte" and abs(r.json()["quadro"]["y"] - 0.5) < 1e-9,
              "'padrão' volta ao que o programa faria (preencher)")
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


def testar_cartoes_so_por_pedido() -> None:
    """Cartão só existe quando o usuário PEDE, na edição.

    "A IA tá criando elementos por conta própria e não tá legal." O plano
    automático não pede mais cartão nenhum (nem no esquema); a única porta é
    o pedido escrito — um hook de abertura, os passos, o número — e a IA
    escreve só isso. Os pedidos se somam; refazer a edição não apaga o que
    ele pediu.
    """
    import subprocess
    import tempfile
    from pathlib import Path

    from editor import projects as svc
    from editor.ai import gemini, roteiro
    from editor.config import FFMPEG
    from editor.models import Clip
    from editor.server import app

    check("cartoes" not in roteiro._esquema(True)["properties"]
          and "cartoes" not in roteiro._esquema(False)["properties"],
          "o esquema do plano automático não tem mais cartões")
    check("CARTÕES" not in roteiro.INSTRUCAO and "cartão" not in roteiro.INSTRUCAO.lower(),
          "e a instrução do plano automático não fala em cartão")
    check("frase" in str(roteiro.ESQUEMA_CARTOES) and "hook" in roteiro.INSTRUCAO_CARTOES.lower(),
          "o pedido de cartões tem o tipo 'frase' (o hook) e fala dele")

    tmp = Path(tempfile.mkdtemp(prefix="cartoes_pedido_"))
    fonte = tmp / "fonte.mp4"
    subprocess.run([FFMPEG, "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=0x303030:s=640x360:r=30:d=12",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                    "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(fonte)], check=True)
    project = svc.create(str(fonte), "cartoes", "VSL")
    project.plan.clips = [Clip(src_start=float(k * 3), src_end=float(k * 3 + 3)) for k in range(4)]
    project.analysis["words"] = [
        {"i": k, "start": k * 3.0 + 0.2, "end": k * 3.0 + 2.8, "text": f"fala{k}"}
        for k in range(4)]
    project.save_plan()
    svc.save_analysis(project) if hasattr(svc, "save_analysis") else None
    try:
        from editor import db
        db.ex("UPDATE projects SET analysis_json=? WHERE id=?",
              (db.jdumps(project.analysis), project.id))
    except Exception:  # noqa: BLE001
        pass

    pedidos: list[str] = []

    def fake_gerar_json(chave, modelo, instrucao, pedido, esquema, **kw):
        pedidos.append(pedido)
        check(instrucao is roteiro.INSTRUCAO_CARTOES and esquema is roteiro.ESQUEMA_CARTOES,
              "o pedido de cartões vai com a instrução e o esquema DELE")
        if "hook" in pedido.lower():
            return {"leitura": "um hook", "cartoes": [
                {"bloco": 0, "tipo": "frase", "titulo": "Você perde cliente todo dia",
                 "segundos": 3.0}]}
        return {"leitura": "os passos", "cartoes": [
            {"bloco": 2, "tipo": "topicos", "titulo": "3 passos",
             "topicos": ["Criar a conta", "Escolher o plano", "Publicar"], "segundos": 4.0}]}

    class Ctx:
        def stage(self, n, m=""): pass
        def progress(self, f, m="", s=""): pass
        def cancelled(self): return False

    guardado = (gemini.gerar_json, gemini.escolher_modelo, gemini.chave_guardada)
    try:
        gemini.gerar_json = fake_gerar_json
        gemini.escolher_modelo = lambda chave, pedido="": {"id": "gemini-2.5-flash", "saida": 4096}
        gemini.chave_guardada = lambda: "chave-de-teste"

        r = svc.cartoes_por_pedido(project, Ctx(), "um hook de abertura com a promessa")
        check(len(r["cartoes"]) == 1 and r["cartoes"][0].get("media_id", "").startswith("k_"),
              f"o pedido de hook virou UM cartão desenhado ({len(r['cartoes'])})")
        check("PEDIDO DO USUÁRIO" in pedidos[-1] and "hook de abertura" in pedidos[-1]
              and "fala2" in pedidos[-1],
              "o pedido leva a fala em blocos e as palavras do usuário")
        fresco = svc.load(project.id)
        k1 = [o for o in fresco.plan.overlays if str(o.media_id).startswith("k_")]
        check(len(k1) == 1 and abs(k1[0].out_start - 0.0) < 0.01,
              f"o hook está no plano, no bloco 0 ({[o.out_start for o in k1]})")

        r2 = svc.cartoes_por_pedido(fresco, Ctx(), "os três passos em cartão de tópicos")
        fresco = svc.load(project.id)
        k2 = [o for o in fresco.plan.overlays if str(o.media_id).startswith("k_")]
        check(len(k2) == 2 and len(r2["cartoes"]) == 1,
              f"o segundo pedido SOMA ao primeiro, não substitui ({len(k2)} cartões)")

        # o plano automático (anexos) NÃO apaga os cartões pedidos
        rel = svc.aplicar_plano_da_ia(fresco, {"leitura": "", "blocos": [], "anexos": []},
                                      so_anexos=True)
        fresco = svc.load(project.id)
        k3 = [o for o in fresco.plan.overlays if str(o.media_id).startswith("k_")]
        check(len(k3) == 2 and rel.get("cartoes") == [],
              f"refazer o plano automático deixa os cartões pedidos em paz ({len(k3)})")

        cliente = TestClient(app)
        rr = cliente.delete(f"/api/projects/{project.id}/cartoes")
        check(rr.status_code == 200 and rr.json()["removidos"] == 2
              and not [o for o in svc.load(project.id).plan.overlays
                       if str(o.media_id).startswith("k_")],
              "'limpar cartões' tira os dois")
        cfg = cliente.get("/api/ai/config").json()
        check("cartoes" not in cfg, "a config da IA não tem mais o interruptor de cartões")
    finally:
        gemini.gerar_json, gemini.escolher_modelo, gemini.chave_guardada = guardado
        try:
            svc.delete_project(project.id)
        except Exception:  # noqa: BLE001
            pass


def testar_biblioteca_de_musicas() -> None:
    """Toda música de fundo que entra fica GUARDADA, e a biblioteca acumula."""
    import subprocess
    import tempfile
    from pathlib import Path

    from editor import projects as svc
    from editor.config import FFMPEG, MEDIA_DIR
    from editor.server import app

    tmp = Path(tempfile.mkdtemp(prefix="musicas_"))
    fonte = tmp / "fonte.mp4"
    subprocess.run([FFMPEG, "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=0x303030:s=320x180:r=30:d=2",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                    "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(fonte)], check=True)
    mp3a, mp3b = tmp / "trilha calma.mp3", tmp / "trilha forte.mp3"
    for f, hz in ((mp3a, 220), (mp3b, 440)):
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                        "-i", f"sine=frequency={hz}:duration=2", "-c:a", "libmp3lame",
                        str(f)], check=True)
    cliente = TestClient(app)
    antes = {m["id"] for m in cliente.get("/api/musicas").json()}
    project = svc.create(str(fonte), "musicas", "VSL")
    try:
        m1 = svc.add_media(project.id, str(mp3a), "audio")
        lista = [m for m in cliente.get("/api/musicas").json() if m["id"] not in antes]
        check(len(lista) == 1 and lista[0]["name"] == "trilha calma.mp3",
              "a música que entrou no projeto foi para a biblioteca")
        check(Path(lista[0]["path"]).exists() and (MEDIA_DIR / "musicas") in Path(lista[0]["path"]).parents,
              "copiada para a pasta de dados (sobrevive ao arquivo original sumir)")
        check(m1["path"] == str(mp3a.resolve()), "o projeto continua apontando o arquivo que o usuário deu")
        svc.add_media(project.id, str(mp3a), "audio")
        lista = [m for m in cliente.get("/api/musicas").json() if m["id"] not in antes]
        check(len(lista) == 1, "a mesma música não entra duas vezes na biblioteca")
        r = cliente.post("/api/musicas", json={"path": str(mp3b)})
        lista = [m for m in cliente.get("/api/musicas").json() if m["id"] not in antes]
        check(r.status_code == 200 and len(lista) == 2,
              f"guardar direto pela rota acumula ({len(lista)})")
        # usar uma guardada num projeto NOVO: o caminho da biblioteca serve
        guardada = next(m for m in lista if m["name"] == "trilha forte.mp3")
        m2 = svc.add_media(project.id, guardada["path"], "audio")
        check(m2["kind"] == "audio" and float(m2["info"].get("duration") or 0) > 1.5,
              "a música guardada entra num projeto como qualquer outra")
        rid = guardada["id"]
        r = cliente.delete(f"/api/musicas/{rid}")
        lista = [m for m in cliente.get("/api/musicas").json() if m["id"] not in antes]
        check(r.status_code == 200 and all(m["id"] != rid for m in lista),
              "apagar tira da biblioteca")
        check(cliente.delete("/api/musicas/nao-existe").status_code == 404,
              "apagar o que não existe dá 404")
        for m in lista:
            cliente.delete(f"/api/musicas/{m['id']}")
    finally:
        try:
            svc.delete_project(project.id)
        except Exception:  # noqa: BLE001
            pass


def testar_corte_na_primeira_tela() -> None:
    """A borda do corte de silêncio vem na RECEITA da primeira tela."""
    from editor.models import EditPlan
    from editor.server import aplicar_receita

    class P:
        plan = EditPlan()
        info = None

    p = P()
    aplicar_receita(p, {"cut": {"aggressiveness": 0.85}, "speed": {"global_multiplier": 1.0}})
    check(abs(p.plan.cut.aggressiveness - 0.85) < 1e-9,
          "o slider 'corte do silêncio' da primeira tela grava a agressividade do corte")
    aplicar_receita(p, {"speed": {"global_multiplier": 1.1}})
    check(abs(p.plan.cut.aggressiveness - 0.85) < 1e-9,
          "e uma receita sem ele não mexe no que já estava")


def testar_armadilhas_de_musica_e_cartao() -> None:
    """Os seis defeitos que a revisão adversarial achou nas features novas.

    Todos são de correção, nenhum de estilo: um deles TRAVAVA o servidor.
    """
    import subprocess
    import tempfile
    import threading
    from pathlib import Path

    from editor import db, projects as svc
    from editor.config import FFMPEG, MEDIA_DIR
    from editor.models import Clip, Overlay
    from editor.render import cartao as K
    from editor.server import app

    tmp = Path(tempfile.mkdtemp(prefix="armadilhas_"))

    def audio(nome: str, dur: float, freq: int) -> Path:
        dest = tmp / nome
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                        "-i", f"sine=frequency={freq}:duration={dur}",
                        "-c:a", "libmp3lame", str(dest)], check=True)
        return dest

    # 1) NOME COM PONTO NO MEIO NÃO TRAVA. "01. Intro.mp3" é o formato padrão
    #    de export de álbum: o laço que procurava um nome livre colapsava o
    #    nome ("01.mp3") a cada volta e girava para sempre, dentro da rota.
    a1 = audio("01. Intro.mp3", 1.0, 440)
    (tmp / "b").mkdir(exist_ok=True)
    a2 = tmp / "b" / "01. Intro.mp3"
    audio("tmp_a2.mp3", 2.0, 660).replace(a2)
    a3 = tmp / "c"
    a3.mkdir(exist_ok=True)
    a3 = a3 / "01. Intro.mp3"
    audio("tmp_a3.mp3", 3.0, 880).replace(a3)

    resultado: dict = {}

    def guardar_tres():
        try:
            resultado["r"] = [svc.guardar_musica(str(x)) for x in (a1, a2, a3)]
        except Exception as exc:  # noqa: BLE001
            resultado["erro"] = exc

    t = threading.Thread(target=guardar_tres, daemon=True)
    t.start()
    t.join(60)
    check(not t.is_alive(),
          "três músicas com o MESMO nome '01. Intro.mp3' e tamanhos diferentes "
          "são guardadas sem travar (o laço de nome não existe mais)")
    if t.is_alive():
        return
    check("erro" not in resultado, f"e sem erro ({resultado.get('erro')})")
    tres = resultado.get("r") or []
    check(len({r["path"] for r in tres}) == 3,
          f"cada uma foi para o seu arquivo ({[Path(r['path']).name for r in tres]})")

    # 2) A IDENTIDADE É O CONTEÚDO. Nome e tamanho iguais não são a mesma
    #    música: dois MP3 do mesmo tempo e bitrate têm os mesmos bytes.
    for r, origem in zip(tres, (a1, a2, a3)):
        check(Path(r["path"]).read_bytes() == origem.read_bytes(),
              f"a guardada '{Path(r['path']).name}' tem o conteúdo do arquivo certo")
    de_novo = svc.guardar_musica(str(a2))
    check(de_novo.get("repetida") and de_novo["path"] == tres[1]["path"],
          "o MESMO arquivo, guardado de novo, é reconhecido e não duplica")
    copia = tmp / "outro nome.mp3"
    copia.write_bytes(a2.read_bytes())
    igual = svc.guardar_musica(str(copia))
    check(igual.get("repetida"),
          "o mesmo conteúdo com outro nome também não duplica (é o mesmo som)")

    # 3) APAGAR NÃO PODE LEVAR O ARQUIVO DE UMA TRILHA EM USO. O caminho em
    #    `media` passa por resolve(); comparar a string crua deixava passar.
    fonte = tmp / "fonte.mp4"
    subprocess.run([FFMPEG, "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30:d=2",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                    "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(fonte)], check=True)
    projeto = svc.create(str(fonte), "armadilhas", "VSL")
    try:
        guardada = tres[0]
        svc.add_media(projeto.id, guardada["path"], "audio")   # usa a guardada
        svc.apagar_musica(guardada["id"])
        check(Path(guardada["path"]).exists(),
              "apagar da biblioteca NÃO apaga o arquivo que um projeto usa")
        livre = tres[2]
        svc.apagar_musica(livre["id"])
        check(not Path(livre["path"]).exists(),
              "e apaga o arquivo que ninguém usa")

        # 4) O CARTÃO NÃO É MÍDIA ANEXADA. Sem o filtro, o plano seguinte o
        #    tratava como anexo do usuário e o reinseria como janela no canto.
        projeto = svc.load(projeto.id)
        projeto.plan.clips = [Clip(src_start=float(k), src_end=float(k + 1))
                              for k in range(2)]
        png = tmp / "cartao.png"
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=white:s=400x200", "-frames:v", "1",
                        str(png)], check=True)
        db.ex("INSERT INTO media(id, project_id, path, kind, name, info_json, "
              "descricao, created_at) VALUES (?,?,?,?,?,?,?,?)",
              ("k_teste123", projeto.id, str(png), "image", "cartão: teste",
               db.jdumps({"width": 400, "height": 200}), "", time.time()))
        projeto.plan.overlays.append(Overlay(media_id="k_teste123",
                                             out_start=0.0, out_end=1.0))
        projeto.save_plan()
        rel = svc.aplicar_plano_da_ia(svc.load(projeto.id),
                                      {"leitura": "", "blocos": [], "anexos": []},
                                      so_anexos=True)
        fresco = svc.load(projeto.id)
        janelas = [o for o in fresco.plan.overlays
                   if o.media_id == "k_teste123" and o.out_start > 0.001]
        check(not janelas and not any(a["media_id"] == "k_teste123"
                                      for a in rel.get("anexos", [])),
              f"o cartão não é reinserido como janela pelo plano seguinte "
              f"({[(o.media_id, o.out_start) for o in fresco.plan.overlays]})")

        # 5) APAGAR O CARTÃO NA PRÉVIA LEVA A LINHA DE MÍDIA JUNTO
        cliente = TestClient(app)
        oid = next(o.id for o in fresco.plan.overlays if o.media_id == "k_teste123")
        rr = cliente.delete(f"/api/projects/{projeto.id}/overlays/{oid}")
        check(rr.status_code == 200, "a rota de apagar sobreposição respondeu")
        ks = [m for m in svc.list_media(projeto.id) if m["id"].startswith("k_")]
        check(not ks,
              f"apagar o cartão na prévia não deixa linha órfã no banco ({ks})")
    finally:
        try:
            svc.delete_project(projeto.id)
        except Exception:  # noqa: BLE001
            pass

    # 6) O CARTÃO DE FRASE (O HOOK) NÃO SAI CORTADO. Ele leva a frase inteira
    #    no título, o ASS é WrapStyle 2 (sem quebra) e a linha saía cortada no
    #    meio da palavra, calada.
    frase = "Você está perdendo cliente todo santo dia por isso"
    linhas = K.cabe_na_largura(1080, 1920, frase)
    check(linhas >= 2, f"uma frase de hook longa ocupa mais de uma linha ({linhas})")
    r = K.desenhar(tmp / "hook.png", 1080, 1920, titulo=frase)
    import numpy as np
    cru = subprocess.run([FFMPEG, "-v", "error", "-i", r["path"], "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    img = np.frombuffer(cru, np.uint8)[: r["width"] * r["height"]] \
        .reshape(r["height"], r["width"])
    # a tinta do texto é clara sobre o fundo escuro do painel
    colunas = np.flatnonzero((img > 200).sum(axis=0) > 0)
    check(colunas.size > 0 and int(colunas[-1]) <= r["width"] - 6,
          f"o texto do hook cabe no painel, sem encostar na borda "
          f"(última coluna com tinta {int(colunas[-1]) if colunas.size else '?'} "
          f"de {r['width']})")
    linhas_com_tinta = np.flatnonzero((img > 200).sum(axis=1) > 0)
    check(linhas_com_tinta.size > 0
          and int(linhas_com_tinta[-1]) <= r["height"] - 4,
          "e não vaza pelo pé do painel")

    # 7) DOIS PEDIDOS NÃO PODEM PÔR CARTÃO EM CIMA DE CARTÃO. Eles se acumulam
    #    agora; a trava antiga só olhava o lote da vez, e dois pedidos de
    #    abertura caíam ambos no bloco 0, um imprimindo por cima do outro.
    from editor.ai import roteiro as R
    from editor.models import EditPlan

    plano = EditPlan()
    plano.clips = [Clip(src_start=float(k * 3), src_end=float(k * 3 + 3))
                   for k in range(4)]
    porindice = {i: c for i, c in enumerate(plano.active_clips)}
    resp = {"cartoes": [{"bloco": 0, "tipo": "frase", "titulo": "Outra frase",
                         "segundos": 3.0}]}
    recusados: list = []
    segundos = R._cartoes_pedidos(resp, plano, porindice, 12.0, recusados,
                                  teto=1, ocupados=[(0.0, 3.0)])
    check(not segundos and any("já está no vídeo" in x["motivo"] for x in recusados),
          f"um segundo cartão em cima de um que já está no vídeo é recusado, "
          f"com motivo ({[x['motivo'] for x in recusados]})")
    livre: list = []
    ok2 = R._cartoes_pedidos({"cartoes": [{"bloco": 2, "tipo": "frase",
                                           "titulo": "No fim", "segundos": 3.0}]},
                             plano, porindice, 12.0, livre, teto=1,
                             ocupados=[(0.0, 3.0)])
    check(len(ok2) == 1 and not livre,
          "e num instante livre ele entra normalmente")

    # 8) CARTÃO QUE NÃO PÔDE SER DESENHADO NÃO É "CARTÃO NO VÍDEO"
    projeto2 = svc.create(str(fonte), "cartao que falha", "VSL")
    try:
        projeto2.plan.clips = [Clip(src_start=0.0, src_end=2.0)]
        projeto2.analysis["words"] = [{"i": 0, "start": 0.2, "end": 1.8,
                                       "text": "fala"}]
        projeto2.save_plan()
        projeto2.save_analysis()

        from editor.ai import gemini as G
        from editor.render import cartao as KK

        guardado = (R.pedir_cartoes, G.chave_guardada, KK.desenhar)
        try:
            G.chave_guardada = lambda: "chave-de-teste"
            R.pedir_cartoes = lambda *a, **k: {"leitura": "", "_modelo": "x",
                                               "cartoes": [{"bloco": 0, "tipo": "frase",
                                                            "titulo": "Vai falhar",
                                                            "segundos": 2.0}]}

            def quebra(*a, **k):
                raise RuntimeError("ffmpeg não desenhou")
            KK.desenhar = quebra

            class Ctx2:
                def stage(self, n, m=""): pass
                def progress(self, f, m="", s=""): pass
                def cancelled(self): return False

            r2 = svc.cartoes_por_pedido(projeto2, Ctx2(), "um hook")
            check(not r2["cartoes"],
                  f"o cartão que falhou NÃO é contado como cartão no vídeo "
                  f"({r2['cartoes']})")
            check(any("não consegui desenhar" in x["motivo"] for x in r2["recusados"]),
                  f"e a falha volta escrita, não em silêncio ({r2['recusados']})")
        finally:
            R.pedir_cartoes, G.chave_guardada, KK.desenhar = guardado

        # 9) DESFAZER NÃO DEVOLVE SOBREPOSIÇÃO SEM MÍDIA (fantasma na trilha)
        fresco2 = svc.load(projeto2.id)
        fresco2.plan.overlays.append(Overlay(media_id="k_sumiu", out_start=0.0,
                                             out_end=1.0))
        plano_com_fantasma = fresco2.plan.to_dict()
        cliente2 = TestClient(app)
        rr = cliente2.post(f"/api/projects/{projeto2.id}/plan",
                           json={"plan": plano_com_fantasma})
        check(rr.status_code == 200, "a rota de desfazer respondeu")
        depois = svc.load(projeto2.id)
        check(not any(o.media_id == "k_sumiu" for o in depois.plan.overlays),
              "desfazer NÃO devolve a sobreposição cuja mídia já foi apagada")
    finally:
        try:
            svc.delete_project(projeto2.id)
        except Exception:  # noqa: BLE001
            pass
    shutil.rmtree(tmp, ignore_errors=True)


def testar_resumo_para_caber() -> None:
    """"Quero 60 segundos": a IA escolhe o que sai até o vídeo caber.

    Encurtar acelerando a fala destrói o anúncio e cortar mais silêncio não
    chega perto: o que resolve é escolher O QUE SAI. As faixas passam pelas
    mesmas travas do corte de copy (vale nas duas bordas, gancho protegido),
    com o teto aberto até o que o alvo exige. Sem chave, quem resume é o
    programa, desligando os blocos das etapas menos essenciais.
    """
    import subprocess
    import tempfile
    from pathlib import Path

    from editor import projects as svc
    from editor.ai import cortes as C, gemini, resumo as R
    from editor.config import FFMPEG
    from editor.models import Clip
    from editor.server import app

    # 1) o teto de copy do resumo é MAIOR que o da edição normal
    check(R.TETO_RESUMO > C.MAX_COPY,
          f"o resumo pode tirar mais que os {C.MAX_COPY:.0%} do corte normal "
          f"({R.TETO_RESUMO:.0%})")
    palavras = [{"i": k, "start": k * 0.5, "end": k * 0.5 + 0.4, "text": f"p{k}"}
                for k in range(40)]
    pedido = R.montar_pedido(palavras, 120.0, 60.0)
    check("120" in pedido and "60" in pedido and "p10" in pedido,
          "o pedido leva a duração atual, o alvo e a transcrição numerada")
    check("gancho" in R.INSTRUCAO.lower() and "chamada para ação" in R.INSTRUCAO.lower(),
          "a instrução proíbe tirar o gancho e o CTA")

    # 2) o recuo sem IA: desliga os blocos menos essenciais, nunca o 1º nem o último
    clips = []
    for k, etapa in enumerate(["gancho", "dor", "explicacao", "prova",
                               "explicacao", "oferta", "cta"]):
        c = Clip(src_start=float(k * 10), src_end=float(k * 10 + 10))
        c.section = etapa
        clips.append(c)
    fora = R.pelo_programa(clips, 40.0)
    check(len(fora) == 3, f"70 s para caber em 40 s: três blocos saem ({len(fora)})")
    check(all(c.section not in ("gancho", "cta") for c in fora),
          f"e o gancho e o CTA não são tocados ({[c.section for c in fora]})")
    check(fora[0].section == "explicacao",
          f"a explicação sai antes da prova e da oferta ({fora[0].section})")
    check(not R.pelo_programa(clips, 999.0), "o que já cabe não é encurtado")

    # 3) o caminho inteiro, com a IA simulada
    from tests.speech import build_track, make_video

    tmp = Path(tempfile.mkdtemp(prefix="resumo_"))
    FALAS = ["Presta atenção nisso aqui que é rápido",
             "O problema é que você perde cliente todo dia",
             "Isso mesmo, você perde cliente todo santo dia",
             "Clica no link agora e resolve"]
    amostras, _marcas, duracao = build_track([(f, 0.9) for f in FALAS],
                                             noise=0.0011)
    fonte = make_video(tmp / "fonte.mp4", amostras, duracao, 320, 240, 30)
    install(FALAS)
    projeto = svc.create(str(fonte), "resumo", "VSL")
    cliente = TestClient(app)
    try:
        ctx = Ctx(quiet=True)
        svc.analyze(projeto, ctx)
        svc.auto_edit(projeto, ctx)
        antes = svc.duracao_de_saida(projeto)
        check(antes > 3.0, f"o vídeo de teste tem {antes:.1f} s montados")

        # a IA manda tirar a terceira frase (a repetida)
        palavras_vivas = projeto.analysis.get("words") or []
        check(len(palavras_vivas) > 10,
              f"a transcrição do teste tem palavras ({len(palavras_vivas)})")
        # a terceira frase ("isso mesmo, você perde cliente todo santo dia") é
        # a repetida: é ela que a IA vai mandar tirar
        alvo_i = [w["i"] for w in palavras_vivas
                  if str(w.get("text", "")).strip().lower()
                  in ("isso", "mesmo", "santo")]
        if alvo_i:
            alvo_i = list(range(min(alvo_i), max(alvo_i) + 1))
        guardado = (R.pedir, gemini.chave_guardada)
        try:
            gemini.chave_guardada = lambda: "chave-de-teste"
            R.pedir = lambda *a, **k: {
                "leitura": "cortei a repetida", "_modelo": "gemini-teste",
                "remover": ([{"de": alvo_i[0], "ate": alvo_i[-1], "tipo": "copy",
                              "motivo": "repete a anterior"}] if alvo_i else [])}
            r = svc.resumir_para_alvo(projeto, ctx, alvo=max(1.0, antes - 1.5))
        finally:
            R.pedir, gemini.chave_guardada = guardado
        depois = svc.duracao_de_saida(svc.load(projeto.id))
        check(r.get("ok"), f"o resumo rodou ({r.get('motivo') or r.get('erro') or ''})")
        check(depois < antes - 0.3,
              f"o vídeo encurtou de {antes:.1f} s para {depois:.1f} s")
        check(r.get("antes") and r.get("depois"),
              "o relatório diz de quanto para quanto")

        # 4) pedir um alvo que o vídeo já cumpre não mexe em nada
        r2 = svc.resumir_para_alvo(svc.load(projeto.id), ctx, alvo=999.0)
        check(r2.get("pulada"), "alvo que o vídeo já cumpre não corta nada")

        # 5) a rota existe e recusa alvo vazio
        rr = cliente.post(f"/api/projects/{projeto.id}/ops/resumir", json={"alvo": 0})
        check(rr.status_code == 400, "resumir sem alvo é recusado com motivo")
        rr = cliente.post(f"/api/projects/{projeto.id}/ops/resumir", json={"alvo": 30})
        check(rr.status_code == 200 and rr.json().get("kind") == "resumo",
              "e com alvo vira um job")
        check(abs(svc.load(projeto.id).plan.alvo_duracao - 30.0) < 1e-9,
              "o alvo fica gravado no plano")

        # 6) a receita da primeira tela grava o alvo
        rr = cliente.post(f"/api/projects/{projeto.id}/params",
                          json={"alvo_duracao": 45})
        check(abs(rr.json()["plan"]["alvo_duracao"] - 45.0) < 1e-9,
              "o 'resumir para' da primeira tela viaja na receita")
    finally:
        try:
            svc.delete_project(projeto.id)
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(tmp, ignore_errors=True)


def testar_keyframes_animam_de_verdade() -> None:
    """Uma sobreposição que ANDA, CRESCE, some e aparece — no pixel.

    Antes, uma janela anexada ficava parada onde foi solta: posição, tamanho e
    opacidade eram um número só, para o clipe inteiro. Quem faz criativo precisa
    do contrário — o cartão entra deslizando, a janela cresce enquanto a pessoa
    fala, o selo some no fim.

    O motor já existia e servia a UMA coisa: o ``_piecewise`` do desfoque, que
    faz a caixa acompanhar um rosto. Agora ele é geral (editor/render/animacao.py),
    com curva de aceleração, e vale para posição, escala, opacidade e rotação.

    NADA DISSO REENCODA O VÍDEO DE BASE. Os filtros entram na cadeia da própria
    sobreposição, que é uma entrada separada do ffmpeg, e nas expressões de
    posição do ``overlay``. Continua uma geração de encode.

    O que este teste prova é o PIXEL, não o campo gravado: o vermelho está no
    lugar certo e do tamanho certo em cada instante, e o número que a prévia
    usaria é o mesmo que saiu no arquivo.
    """
    import subprocess
    import tempfile
    from pathlib import Path

    from editor.config import FFMPEG
    from editor.models import Overlay
    from editor.render import animacao as A
    from editor.render import mascara as Msk
    from editor.render.filters import janela_no_trecho, overlay_chain

    # 1) o motor: um marco só é valor fixo; dois marcos iguais não é animação
    check(not A.tem_animacao([{"t": 0, "x": 0.3}], "x"),
          "um marco sozinho é valor fixo, não animação")
    check(not A.tem_animacao([{"t": 0, "x": 0.3}, {"t": 2, "x": 0.3}], "x"),
          "dois marcos com o mesmo valor também não é animação")
    check(A.tem_animacao([{"t": 0, "x": 0.3}, {"t": 2, "x": 0.7}], "x"),
          "dois marcos com valores diferentes, aí sim")

    # um marco fala SÓ das propriedades que traz: mexer na escala num instante
    # não pode arrastar a posição junto
    mistos = [{"t": 0.0, "x": 0.2, "scale": 1.0}, {"t": 2.0, "scale": 2.0}]
    check(not A.tem_animacao(mistos, "x"),
          "marco que só traz escala não inventa animação de posição")
    check(A.tem_animacao(mistos, "scale"), "mas anima a escala que ele traz")

    # fora dos marcos o valor SEGURA — extrapolar joga a janela para fora da tela
    kf = [{"t": 1.0, "x": 0.2}, {"t": 3.0, "x": 0.8}]
    check(abs(A.valor_em(kf, "x", 0.0) - 0.2) < 1e-9
          and abs(A.valor_em(kf, "x", 9.0) - 0.8) < 1e-9,
          "antes do primeiro e depois do último marco o valor segura, não extrapola")
    check(abs(A.valor_em(kf, "x", 2.0) - 0.5) < 1e-9,
          "no meio, interpolação linear")
    suave = [{"t": 0.0, "x": 0.0}, {"t": 2.0, "x": 1.0, "easing": "suave"}]
    check(abs(A.valor_em(suave, "x", 1.0) - 0.5) < 1e-9
          and A.valor_em(suave, "x", 0.5) < 0.25,
          "a curva suave sai devagar e chega devagar (meio igual, quartos mais lentos)")

    tmp = Path(tempfile.mkdtemp(prefix="kf_"))
    try:
        base = tmp / "base.mp4"
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=black:s=320x240:r=10:d=2",
                        "-c:v", "libx264", "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p", str(base)], check=True)
        png = tmp / "ov.png"
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=red:s=100x100:d=1", "-frames:v", "1",
                        str(png)], check=True)

        def montar(ov: Overlay) -> tuple[str, list]:
            """O mesmo preparo que o renderer faz antes de montar o grafo."""
            mascaras, comandos = {}, {}
            if getattr(ov, "mask", None):
                m = Msk.preparar(ov.mask, str(png), tmp / "sobrepor")
                if m:
                    mascaras[ov.id] = m
            kfs = getattr(ov, "keyframes", None) or []
            if A.tem_animacao(kfs, "opacity"):
                ini, fim = janela_no_trecho(ov, 0.0)
                texto = A.texto_dos_comandos(kfs, "opacity", ini, fim - ini, 10.0,
                                             "colorchannelmixer", "aa",
                                             repouso=ov.opacity)
                if texto:
                    (tmp / "sobrepor").mkdir(parents=True, exist_ok=True)
                    alvo = tmp / "sobrepor" / f"op_{ov.id}.txt"
                    alvo.write_text(texto, encoding="utf-8")
                    comandos[ov.id] = str(alvo)
            return overlay_chain([ov], {"m": str(png)}, 0.0, 320, 240, 1,
                                 "0:v", "vout", ref_height=240, ref_width=320,
                                 mascaras=mascaras, comandos=comandos)

        def render(ov: Overlay, saida: str) -> Path:
            g, ent = montar(ov)
            cmd = [FFMPEG, "-y", "-v", "error", "-i", str(base)]
            for e in ent:
                cmd += ["-loop", "1", "-framerate", "10", "-t", "3", "-i", e["path"]]
            out = tmp / saida
            cmd += ["-filter_complex", g, "-map", "[vout]", "-t", "2", str(out)]
            subprocess.run(cmd, check=True)
            return out

        def linha(v: Path, t: float) -> list[int]:
            d = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t}", "-i", str(v),
                                "-frames:v", "1", "-f", "rawvideo",
                                "-pix_fmt", "gray", "-"],
                               capture_output=True).stdout
            return [i for i, px in enumerate(d[120 * 320:121 * 320]) if px > 40]

        # 2) ANDA e CRESCE: x de 0,2 a 0,8 e escala de 0,2 a 0,8 em 2 s
        marcos = [{"t": 0.0, "x": 0.2, "scale": 0.2},
                  {"t": 2.0, "x": 0.8, "scale": 0.8}]
        ov = Overlay(media_id="m", out_start=0.0, out_end=2.0, anim_in="none",
                     anim_out="none", y=0.5, keyframes=marcos)
        v = render(ov, "anda.mp4")
        erros_x, erros_w = [], []
        for t in (0.0, 0.5, 1.0, 1.5, 1.9):
            xs = linha(v, t)
            if not xs:
                erros_x.append(("sumiu", t))
                continue
            centro = (xs[0] + xs[-1]) / 2 / 320
            esperado_x = A.valor_em(marcos, "x", t, repouso=0.5)
            esperado_w = A.valor_em(marcos, "scale", t, repouso=1.0) * 100
            erros_x.append(abs(centro - esperado_x))
            erros_w.append(abs(len(xs) - esperado_w))
        ok_x = bool(erros_x) and all(isinstance(e, float) and e < 0.02
                                     for e in erros_x)
        ok_w = bool(erros_w) and all(e < 3 for e in erros_w)
        detalhe_x = (f"erro máximo de {max(erros_x):.4f} da largura" if ok_x
                     else f"medidas: {erros_x}")
        detalhe_w = (f"erro máximo de {max(erros_w):.1f} px" if ok_w
                     else f"medidas: {erros_w}")
        check(ok_x, f"a janela ANDA para onde os marcos mandam ({detalhe_x})")
        check(ok_w, f"e CRESCE do tamanho que eles mandam ({detalhe_w})")
        check(ok_x and ok_w,
              "o número que a prévia calcula é o mesmo que saiu no arquivo "
              "(valor_em == pixel medido)")

        # 3) OPACIDADE: de 10% a 100% — o brilho do vermelho tem que subir
        marcos_o = [{"t": 0.0, "opacity": 0.1}, {"t": 2.0, "opacity": 1.0}]
        ov2 = Overlay(media_id="m", out_start=0.0, out_end=2.0, anim_in="none",
                      anim_out="none", x=0.5, y=0.5, keyframes=marcos_o)
        v2 = render(ov2, "some.mp4")

        def brilho(t: float) -> int:
            d = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t}", "-i", str(v2),
                                "-frames:v", "1", "-f", "rawvideo",
                                "-pix_fmt", "gray", "-"],
                               capture_output=True).stdout
            return d[120 * 320 + 160]

        b0, b1, b2 = brilho(0.0), brilho(1.0), brilho(1.9)
        check(b0 < b1 < b2 and b0 < 20 and b2 > 60,
              f"a sobreposição APARECE ao longo do tempo (brilho {b0} → {b1} → {b2})")

        # 4) MÁSCARA: elipse fura os cantos e deixa o miolo
        alfa = subprocess.run(
            [FFMPEG, "-v", "error", "-loop", "1", "-i", str(png), "-frames:v", "1",
             "-vf", f"format=rgba,{A.geq_alfa([A.mascara_fator({'shape': 'elipse', 'feather': 0.06})])}",
             "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
            capture_output=True).stdout
        centro = alfa[(50 * 100 + 50) * 4 + 3]
        canto = alfa[(3 * 100 + 3) * 4 + 3]
        check(centro == 255 and canto == 0,
              f"a máscara de elipse deixa o miolo e fura o canto "
              f"(alfa centro={centro}, canto={canto})")
        reto = subprocess.run(
            [FFMPEG, "-v", "error", "-loop", "1", "-i", str(png), "-frames:v", "1",
             "-vf", f"format=rgba,{A.geq_alfa([A.mascara_fator({'shape': 'retangulo', 'feather': 0.06})])}",
             "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
            capture_output=True).stdout
        check(reto[(3 * 100 + 3) * 4 + 3] == 255,
              "e a de retângulo mantém o canto (as formas são mesmo diferentes)")

        # 5) NENHUM geq no caminho do render — é a armadilha de desempenho.
        # Medido nesta máquina, numa janela em tela cheia (1080x1920, 60
        # quadros): crua 1,2 s; com geq de máscara 24,3 s; com geq de opacidade
        # 15,1 s. Com PNG pronto + alphamerge + sendcmd: 2,5 s. Numa janela de
        # 60 s a diferença passa de dez minutos de espera.
        ov3 = Overlay(id="o_teste", media_id="m", out_start=0.0, out_end=2.0,
                      opacity=0.5, mask={"shape": "elipse"},
                      keyframes=[{"t": 0.0, "opacity": 0.2},
                                 {"t": 2.0, "opacity": 0.9}])
        g3, ent3 = montar(ov3)
        check("geq=" not in g3,
              "o render NÃO usa geq por quadro (em tela cheia custaria 20x)")
        check("alphamerge" in g3 and len(ent3) == 2,
              f"a máscara entra como PNG pronto num alphamerge "
              f"({len(ent3)} entradas: a mídia e a máscara)")
        check("sendcmd" in g3 and "colorchannelmixer" in g3,
              "e a opacidade animada vai por sendcmd no colorchannelmixer")
        check("scale2ref" not in g3,
              "e sem scale2ref, que em tela cheia fez o sistema matar o processo")

        # 6) sem marco nenhum, NADA muda: nem alphamerge, nem eval=frame,
        # nem rotate, nem sendcmd — o caminho de antes, intacto
        simples = Overlay(media_id="m", out_start=0.0, out_end=2.0)
        g4, ent4 = overlay_chain([simples], {"m": str(png)}, 0.0, 320, 240, 1,
                                 "a", "b", ref_height=240, ref_width=320)
        antes_do_overlay = g4.split("overlay=")[0]
        check("alphamerge" not in g4 and "sendcmd" not in g4
              and "rotate=" not in g4 and "eval=frame" not in antes_do_overlay
              and len(ent4) == 1,
              "sobreposição sem marcos não paga por nada disso e continua "
              "com uma entrada só")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def testar_marcos_acompanham_o_corte() -> None:
    """Cortar dez segundos no começo não pode atrasar o movimento da janela.

    A caixa já era reancorada (``remap_output_items``): o instante de saída vira
    instante da FONTE pela linha antiga e volta pela nova. Os MARCOS dela não
    eram — só os do desfoque. O resultado era a janela no lugar certo e o
    movimento no lugar errado, que é pior que não animar.
    """
    from editor.edit.ops import remap_output_items
    from editor.edit.timeline import Timeline
    from editor.models import Clip, EditPlan, Overlay

    # antes: um bloco de 0 a 20 s da fonte. depois: os 10 primeiros segundos
    # saíram, então a fonte 10..20 vira a saída 0..10.
    antes = Timeline([Clip(src_start=0.0, src_end=20.0)])
    depois = Timeline([Clip(src_start=10.0, src_end=20.0)])
    plan = EditPlan()
    plan.overlays = [Overlay(id="o_1", media_id="m", out_start=12.0, out_end=16.0,
                             keyframes=[{"t": 12.0, "x": 0.1},
                                        {"t": 16.0, "x": 0.9}])]
    remap_output_items(plan, antes, depois)
    o = plan.overlays[0]
    check(abs(o.out_start - 2.0) < 0.01 and abs(o.out_end - 6.0) < 0.01,
          f"a janela anda com o corte (12–16 s → {o.out_start:.1f}–{o.out_end:.1f} s)")
    ts = [round(float(k["t"]), 2) for k in o.keyframes]
    check(ts == [2.0, 6.0],
          f"E OS MARCOS DELA TAMBÉM ({ts} — sem isto, o movimento chegava atrasado)")


def testar_marcos_nao_reencodam_o_resto() -> None:
    """Um corte no começo não pode reencodar o vídeo por causa dos marcos.

    A chave de cache de cada trecho é de CONTEÚDO, e tudo que é posicional
    entra nela RELATIVO ao começo do trecho — é isso que faz um corte no minuto
    2 não mexer na chave do minuto 5. Mas ``_relativo`` descia só em
    ``out_start`` e ``out_end``: os MARCOS de animação entravam com tempo
    absoluto. Bastava um corte no começo para o ``t`` de todos os marcos
    seguintes mudar, e com ele a chave de todo trecho que a janela cobre.

    Isto valia para os marcos do desfoque desde que eles existem. Passou a
    valer para os da sobreposição no instante em que ela ganhou animação — e é
    exatamente o defeito que a chave relativa existe para impedir.
    """
    import subprocess

    from editor.config import FFMPEG, ExportParams
    from editor.edit import ops
    from editor.edit.timeline import Timeline
    from editor.ffmpeg_utils import probe
    from editor.models import Clip, EditPlan, Overlay
    from editor.render.renderer import plan_segments, render_video_segments

    tmp = Path(tempfile.mkdtemp(prefix="marcos_cache_"))
    try:
        dur = 6.0
        video = write_video(tmp / "fonte.mp4", build([], dur), dur, 180, 320, 30)
        info = probe(video)
        png = tmp / "selo.png"
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=yellow:s=40x40:d=1", "-frames:v", "1",
                        str(png)], check=True)
        plan = EditPlan()
        plan.export = ExportParams(scale="240", burn_subtitles=False,
                                   preset="ultrafast", crf=30)
        blocos = lambda: [Clip(src_start=float(k), src_end=float(k + 1))  # noqa: E731
                          for k in range(6)]
        plan.clips = blocos()
        # a janela cobre os dois ÚLTIMOS blocos, bem longe de onde o corte vai
        plan.overlays = [Overlay(id="o_selo", media_id="sel", out_start=4.0,
                                 out_end=6.0, y=0.5, anim_in="none",
                                 anim_out="none",
                                 keyframes=[{"t": 4.0, "x": 0.2},
                                            {"t": 6.0, "x": 0.8}])]
        sources = {"main": {"path": str(video), "info": info, "kind": "video"}}
        caminhos = {"main": str(video), "sel": str(png)}
        work = tmp / "segs"

        def render() -> set[str]:
            tl = Timeline(plan.active_clips, 30.0)
            segs = plan_segments(plan, tl, sources, info)
            render_video_segments(segs, plan, info, [], work, caminhos, None)
            return {f.name for f in work.glob("seg_*.mp4")}

        antes = render()
        check(len(antes) == 6, f"seis blocos com a janela animada no fim "
                              f"({len(antes)} trechos)")

        # corta 0,3 s no meio do bloco 2 e reancora a janela, como o servidor faz
        tl_antes = Timeline(plan.active_clips, 30.0)
        plan.clips, _ = ops.cut_source_range(plan.clips, 1.3, 1.6)
        tl_depois = Timeline(plan.active_clips, 30.0)
        ops.remap_output_items(plan, tl_antes, tl_depois)
        o = plan.overlays[0]
        check(abs(o.out_start - 3.7) < 0.05,
              f"a janela andou com o corte (4,0 s → {o.out_start:.2f} s)")
        check(abs(float(o.keyframes[0]["t"]) - 3.7) < 0.05,
              f"e o primeiro marco também ({o.keyframes[0]['t']})")

        depois = render()
        novos = depois - antes
        check(len(novos) == 2,
              f"o corte reencoda SÓ as duas metades do bloco cortado — os "
              f"trechos da janela animada acham o próprio arquivo "
              f"({len(novos)} novos; com o t absoluto eram 4)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def testar_faixas_empilham_e_rotas_aceitam() -> None:
    """Faixa decide quem fica na frente, e a tela consegue pedir isso.

    Duas metades, as duas necessárias:

    1) EMPILHAMENTO. ``overlay_chain`` encadeia uma sobreposição sobre a outra
       na ordem em que as recebe, e quem vem depois fica por cima. Essa ordem
       era a da lista, e a ordem da lista é a ordem em que o usuário anexou:
       para pôr um cartão ATRÁS de uma janela já anexada não havia gesto —
       só apagar as duas e anexar na ordem contrária. Conferido no pixel.

    2) O CAMINHO ATÉ LÁ. Rotação, faixa, marcos e máscara existiam no modelo e
       o render já os consumia, mas NENHUMA rota os aceitava: a capacidade
       estava inalcançável pela tela. E o que vem de fora passa a ser
       normalizado — o render monta expressão de ffmpeg com esses números, e um
       marco malformado não dá um marco errado, dá um trecho que o ffmpeg se
       recusa a encodar.
    """
    import subprocess
    import tempfile
    from pathlib import Path

    from editor import projects as svc
    from editor.config import FFMPEG
    from editor.models import Clip, Overlay
    from editor.render import animacao as A
    from editor.render.filters import overlay_chain, overlay_inputs
    from editor.server import app

    # ---- 1) empilhamento, no pixel ------------------------------------
    tmp = Path(tempfile.mkdtemp(prefix="faixas_"))
    try:
        base = tmp / "base.mp4"
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=black:s=320x240:r=10:d=1",
                        "-c:v", "libx264", "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p", str(base)], check=True)
        cores = {}
        for nome, cor in (("vermelho", "red"), ("azul", "blue")):
            alvo = tmp / f"{nome}.png"
            subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                            "-i", f"color=c={cor}:s=120x120:d=1", "-frames:v", "1",
                            str(alvo)], check=True)
            cores[nome] = alvo

        def quem_esta_na_frente(track_vermelho: int, track_azul: int) -> str:
            """Renderiza as duas na MESMA janela e no MESMO lugar e lê o centro."""
            ov = [
                Overlay(id="o_vm", media_id="vm", out_start=0.0, out_end=1.0,
                        x=0.5, y=0.5, anim_in="none", anim_out="none",
                        track=track_vermelho),
                Overlay(id="o_az", media_id="az", out_start=0.0, out_end=1.0,
                        x=0.5, y=0.5, anim_in="none", anim_out="none",
                        track=track_azul),
            ]
            caminhos = {"vm": str(cores["vermelho"]), "az": str(cores["azul"])}
            ordenadas = overlay_inputs(ov, 0.0, 1.0)
            g, ent = overlay_chain(ordenadas, caminhos, 0.0, 320, 240, 1,
                                   "0:v", "vout", ref_height=240, ref_width=320)
            cmd = [FFMPEG, "-y", "-v", "error", "-i", str(base)]
            for e in ent:
                cmd += ["-loop", "1", "-framerate", "10", "-t", "2", "-i", e["path"]]
            saida = tmp / f"pilha_{track_vermelho}{track_azul}.mp4"
            cmd += ["-filter_complex", g, "-map", "[vout]", "-t", "1", str(saida)]
            subprocess.run(cmd, check=True)
            px = subprocess.run([FFMPEG, "-v", "error", "-ss", "0.5", "-i", str(saida),
                                 "-frames:v", "1", "-f", "rawvideo",
                                 "-pix_fmt", "rgb24", "-"],
                                capture_output=True).stdout
            i = (120 * 320 + 160) * 3
            r, _g, b = px[i], px[i + 1], px[i + 2]
            return "vermelho" if r > b else "azul"

        check(quem_esta_na_frente(0, 1) == "azul",
              "com o azul na faixa de cima, o azul aparece")
        check(quem_esta_na_frente(1, 0) == "vermelho",
              "invertendo as faixas, o vermelho aparece — é a faixa que manda, "
              "não a ordem em que foi anexado")

        # desempate: mesma faixa volta a ser a ordem da lista, que é o que
        # plano antigo (tudo em track=0) sempre fez
        ids = [o.id for o in overlay_inputs(
            [Overlay(id=x, out_start=0, out_end=2) for x in ("p", "q", "r")], 0, 2)]
        check(ids == ["p", "q", "r"],
              "na mesma faixa vale a ordem da lista — plano antigo desenha igual")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- 2) as rotas aceitam, e limpam o que vem sujo -----------------
    tmp2 = Path(tempfile.mkdtemp(prefix="faixas_rota_"))
    projeto = None
    try:
        fonte = tmp2 / "fonte.mp4"
        subprocess.run([FFMPEG, "-y", "-v", "error",
                        "-f", "lavfi", "-i", "color=c=0x303030:s=320x240:r=30:d=6",
                        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                        "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", str(fonte)], check=True)
        selo = tmp2 / "selo.png"
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=yellow:s=60x60:d=1", "-frames:v", "1",
                        str(selo)], check=True)
        projeto = svc.create(str(fonte), "faixas", "VSL")
        projeto.plan.clips = [Clip(src_start=0.0, src_end=6.0)]
        projeto.save_plan()
        midia = svc.add_media(projeto.id, str(selo), "image", "selo")
        cliente = TestClient(app)

        r = cliente.post(f"/api/projects/{projeto.id}/overlays", json={
            "media_id": midia["id"], "out_start": 1.0, "out_end": 4.0,
            "rotation": 12.5, "track": 2,
            "keyframes": [{"t": 1.0, "x": 0.2}, {"t": 4.0, "x": 0.8,
                                                 "easing": "suave"}],
            "mask": {"shape": "elipse", "feather": 0.1},
        })
        check(r.status_code == 200, f"a rota aceita criar com os campos novos ({r.status_code})")
        o = r.json()["overlay"]
        check(abs(o["rotation"] - 12.5) < 1e-6 and o["track"] == 2,
              f"rotação e faixa chegam (rotation={o['rotation']}, track={o['track']})")
        check(len(o["keyframes"]) == 2 and o["keyframes"][1]["easing"] == "suave",
              f"os marcos chegam com a curva ({o['keyframes']})")
        check((o["mask"] or {}).get("shape") == "elipse",
              f"a máscara chega ({o['mask']})")
        oid = o["id"]

        # o PUT também, e o que vem sujo é limpo em vez de virar buraco no vídeo
        r2 = cliente.put(f"/api/projects/{projeto.id}/overlays/{oid}", json={
            "track": 5, "rotation": -30.0,
            "keyframes": [{"t": "isto nao e numero", "x": 0.5},
                          {"t": 2.0, "x": 99.0},
                          {"t": 1.0, "opacity": 0.4, "easing": "xpto"},
                          "nem isto e um marco"],
            "mask": {"shape": "poligono_maluco"},
        })
        check(r2.status_code == 200, f"o PUT aceita ({r2.status_code})")
        o2 = r2.json()["overlay"]
        check(o2["track"] == 5 and abs(o2["rotation"] + 30.0) < 1e-6,
              "faixa e rotação atualizam pelo PUT")
        ts = [m["t"] for m in o2["keyframes"]]
        check(ts == [1.0, 2.0],
              f"o marco com t inválido e o que não é dicionário caem; o resto "
              f"fica em ordem ({ts})")
        check(o2["keyframes"][1]["x"] <= 3.0,
              f"x fora de faixa é limitado em vez de virar expressão absurda "
              f"({o2['keyframes'][1]['x']})")
        check("easing" not in o2["keyframes"][0],
              "curva desconhecida cai para linear em vez de entrar no plano")
        check(o2["mask"] is None,
              f"forma que não existe não vira máscara ({o2['mask']})")

        # e o marco do desfoque exige as quatro chaves da caixa
        r3 = cliente.post(f"/api/projects/{projeto.id}/blurs",
                          json={"out_start": 1.0, "out_end": 2.0})
        if r3.status_code == 200:
            bid = r3.json()["blur"]["id"]
            r4 = cliente.put(f"/api/projects/{projeto.id}/blurs/{bid}", json={
                "keyframes": [{"t": 1.0, "x": 0.3, "y": 0.3, "w": 0.2, "h": 0.2},
                              {"t": 1.5, "x": 0.4}]})
            if r4.status_code == 200:
                kfs = r4.json()["blur"]["keyframes"]
                check(len(kfs) == 1,
                      f"marco de desfoque sem as quatro chaves da caixa cai — "
                      f"completar com o padrão faria a caixa saltar de cima do "
                      f"rosto ({len(kfs)} de 2)")
    finally:
        if projeto is not None:
            try:
                svc.delete_project(projeto.id)
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(tmp2, ignore_errors=True)


def testar_emudecer_um_bloco_sai_no_arquivo() -> None:
    """Mandar calar um bloco tem que calar no arquivo, não só na tela.

    O áudio final tem cache: o loudnorm são duas passadas sobre a faixa
    inteira, e ela não muda quando o retoque foi visual. A chave desse cache
    perguntava ``getattr(clip, "muted", False)`` — e Clip NUNCA teve campo
    ``muted``. O getattr devolvia o padrão para todo bloco, sempre. O campo
    real é ``audio``.

    O efeito era o pior que um cache pode ter: o usuário emudecia o bloco, a
    tela mostrava o bloco mudo, a chave não mexia, o audio.wav era
    reaproveitado — e a voz continuava no vídeo que foi para a pasta. Este
    teste exporta duas vezes e OUVE o resultado.
    """
    import subprocess
    import tempfile
    from pathlib import Path

    import numpy as np

    from editor import projects as svc
    from editor.config import ExportParams
    from editor.edit.timeline import Timeline
    from editor.ffmpeg_utils import extract_wav, read_wav_mono
    from editor.models import Clip
    from editor.render.export import _hash_audio
    from tests.e2e import Ctx

    # a chave tem que reagir ao campo certo
    from editor.models import EditPlan
    plano = EditPlan()
    plano.clips = [Clip(src_start=0.0, src_end=2.0), Clip(src_start=2.0, src_end=4.0)]
    fontes = {"main": {"path": "/x.mp4"}}
    antes = _hash_audio(plano, Timeline(plano.active_clips, 30.0), {}, fontes)
    plano.clips[1].audio = "mute"
    depois = _hash_audio(plano, Timeline(plano.active_clips, 30.0), {}, fontes)
    check(antes != depois,
          "emudecer um bloco MUDA a chave do áudio (era o campo inexistente "
          "'muted' — a chave nunca mexia)")

    # e no arquivo: dois blocos com tom, o segundo emudecido na segunda volta
    tmp = Path(tempfile.mkdtemp(prefix="mudo_"))
    projeto = None
    try:
        dur = 4.0
        fonte = write_video(tmp / "fonte.mp4", build([(0.2, 1.8), (2.2, 3.8)], dur),
                            dur, 180, 320, 30)
        projeto = svc.create(str(fonte), "mudo", "VSL")
        projeto.plan.clips = [Clip(src_start=0.0, src_end=2.0),
                              Clip(src_start=2.0, src_end=4.0)]
        projeto.plan.export = ExportParams(scale="240", burn_subtitles=False,
                                           preset="ultrafast", crf=30)
        projeto.save_plan()
        ctx = Ctx(quiet=True)

        def energia_do_fim() -> float:
            r = svc.export(svc.load(projeto.id), ctx,
                           {"filename": "mudo.mp4", "overwrite": True,
                            "output_dir": str(tmp)})
            saida = Path(r["output"])
            wav = tmp / "saida.wav"
            extract_wav(saida, wav, 16000, 1)
            amostras, sr = read_wav_mono(wav)
            # a metade final do arquivo é o segundo bloco
            metade = amostras[len(amostras) // 2:]
            return float(np.sqrt(np.mean(metade.astype(np.float64) ** 2)))

        com_som = energia_do_fim()
        p2 = svc.load(projeto.id)
        p2.plan.clips[1].audio = "mute"
        p2.save_plan()
        mudo = energia_do_fim()
        check(com_som > 1e-4,
              f"o segundo bloco tinha som antes ({com_som:.5f})")
        check(mudo < com_som * 0.35,
              f"e depois de mandar calar ele CALOU no arquivo "
              f"({com_som:.5f} → {mudo:.5f}) — com a chave antiga o áudio "
              f"vinha do cache e a voz continuava lá")
    finally:
        if projeto is not None:
            try:
                svc.delete_project(projeto.id)
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(tmp, ignore_errors=True)


def testar_efeitos_mexem_no_pixel() -> None:
    """Efeito é grandeza medida no quadro, nunca campo gravado.

    Um teste que confere se ``effects`` entrou no plano prova que o JSON foi
    salvo, não que o vídeo mudou. Aqui cada efeito é medido pelo que ele faz:
    o desfoque derruba o gradiente, a vinheta escurece o canto e não o centro,
    o flash é um pico de brilho que começa e acaba, o chroma deixa o fundo
    aparecer e o tremor tira a janela do lugar ao longo do tempo.

    Tudo roda DENTRO do mesmo passe de encode do trecho — nenhum efeito
    acrescenta geração de compressão.
    """
    import subprocess
    import tempfile
    from pathlib import Path

    import numpy as np

    from editor.config import FFMPEG, ExportParams
    from editor.edit.timeline import Timeline
    from editor.ffmpeg_utils import probe
    from editor.models import Clip, EditPlan, Overlay
    from editor.render import animacao as A
    from editor.render.filters import overlay_chain
    from editor.render.renderer import plan_segments, render_video_segments

    tmp = Path(tempfile.mkdtemp(prefix="efeitos_"))
    try:
        # ---- efeitos do QUADRO INTEIRO, pelo caminho de render de verdade ---
        fonte = tmp / "fonte.mp4"
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                        "-i", "testsrc2=s=320x240:r=30:d=2",
                        "-c:v", "libx264", "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p", str(fonte)], check=True)
        info = probe(fonte)

        def render_com(efeitos: list, marca: str) -> Path:
            plan = EditPlan()
            plan.export = ExportParams(scale="240", burn_subtitles=False,
                                       preset="ultrafast", crf=28)
            plan.clips = [Clip(src_start=0.0, src_end=2.0, effects=efeitos)]
            tl = Timeline(plan.active_clips, 30.0)
            segs = plan_segments(plan, tl,
                                 {"main": {"path": str(fonte), "info": info,
                                           "kind": "video"}}, info)
            work = tmp / f"w_{marca}"
            render_video_segments(segs, plan, info, [], work,
                                  {"main": str(fonte)}, None)
            return sorted(work.glob("seg_*.mp4"))[0]

        def quadro(v: Path, t: float) -> np.ndarray:
            """O quadro em cinza, no tamanho que o arquivo REALMENTE tem.

            Adivinhar a largura a partir do número de bytes dá uma matriz
            torta, e uma matriz torta mede gradiente de coisa nenhuma — o
            número sai plausível e não quer dizer nada.
            """
            w, h = probe(v).display_size
            d = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t}", "-i", str(v),
                                "-frames:v", "1", "-f", "rawvideo",
                                "-pix_fmt", "gray", "-"],
                               capture_output=True).stdout
            return np.frombuffer(d[:w * h], dtype=np.uint8).reshape(h, w).astype(float)

        def gradiente(q: np.ndarray) -> float:
            return float(np.abs(np.diff(q, axis=1)).mean())

        # O DESFOQUE é medido por MONOTONICIDADE, não por um limiar escolhido a
        # dedo: mais sigma tem que dar menos gradiente, sempre. Um limiar fixo
        # aqui diria mais sobre a fonte de teste que sobre o filtro — numa
        # imagem de blocos lisos como a testsrc2, o que sobra depois do
        # desfoque são as bordas dos blocos, que são degraus fortes e
        # sobrevivem a qualquer sigma.
        limpo = render_com([], "limpo")
        borrado = render_com(
            A.normalizar_efeitos([{"kind": "desfoque", "sigma": 8}],
                                 A.EFEITOS_DO_CLIPE), "borrado")
        muito = render_com(
            A.normalizar_efeitos([{"kind": "desfoque", "sigma": 30}],
                                 A.EFEITOS_DO_CLIPE), "muito")
        g0 = gradiente(quadro(limpo, 1.0))
        g1 = gradiente(quadro(borrado, 1.0))
        g2 = gradiente(quadro(muito, 1.0))
        check(g0 > g1 > g2,
              f"o DESFOQUE derruba o gradiente, e mais sigma derruba mais "
              f"({g0:.2f} → {g1:.2f} → {g2:.2f})")
        check(g1 < g0 * 0.7,
              f"e a queda é grande já no sigma baixo ({g1 / g0:.2f} do original)")

        vinhetado = render_com(
            A.normalizar_efeitos([{"kind": "vinheta", "amount": 1.0}],
                                 A.EFEITOS_DO_CLIPE), "vinheta")
        qv, ql = quadro(vinhetado, 1.0), quadro(limpo, 1.0)
        canto_v = qv[:20, :20].mean()
        canto_l = ql[:20, :20].mean()
        h, w = qv.shape
        centro_v = qv[h // 2 - 10:h // 2 + 10, w // 2 - 10:w // 2 + 10].mean()
        centro_l = ql[h // 2 - 10:h // 2 + 10, w // 2 - 10:w // 2 + 10].mean()
        check(canto_v < canto_l * 0.7,
              f"a VINHETA escurece o canto ({canto_l:.0f} → {canto_v:.0f})")
        check(centro_v > centro_l * 0.75,
              f"e deixa o centro quase igual ({centro_l:.0f} → {centro_v:.0f})")

        piscado = render_com(
            A.normalizar_efeitos([{"kind": "flash", "at": 1.0, "duration": 0.3,
                                   "amount": 0.8}], A.EFEITOS_DO_CLIPE), "flash")
        antes = quadro(piscado, 0.5).mean()
        durante = quadro(piscado, 1.15).mean()
        depois = quadro(piscado, 1.7).mean()
        check(durante > antes * 1.15 and durante > depois * 1.15,
              f"o FLASH é um pico que começa e acaba "
              f"({antes:.0f} → {durante:.0f} → {depois:.0f})")

        # ---- efeitos da SOBREPOSIÇÃO ---------------------------------------
        base = tmp / "base.mp4"
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=0x0000FF:s=320x240:r=10:d=1",
                        "-c:v", "libx264", "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p", str(base)], check=True)
        verde = tmp / "verde.png"
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=0x00FF00:s=120x120:d=1", "-frames:v", "1",
                        str(verde)], check=True)

        def render_ov(ov: Overlay, nome: str) -> Path:
            g, ent = overlay_chain([ov], {"m": str(verde)}, 0.0, 320, 240, 1,
                                   "0:v", "vout", ref_height=240, ref_width=320)
            cmd = [FFMPEG, "-y", "-v", "error", "-i", str(base)]
            for e in ent:
                cmd += ["-loop", "1", "-framerate", "10", "-t", "2", "-i", e["path"]]
            saida = tmp / f"{nome}.mp4"
            cmd += ["-filter_complex", g, "-map", "[vout]", "-t", "1", str(saida)]
            subprocess.run(cmd, check=True)
            return saida

        def rgb(v: Path, t: float, x: int, y: int) -> tuple:
            d = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t}", "-i", str(v),
                                "-frames:v", "1", "-f", "rawvideo",
                                "-pix_fmt", "rgb24", "-"],
                               capture_output=True).stdout
            i = (y * 320 + x) * 3
            return d[i], d[i + 1], d[i + 2]

        comum = {"media_id": "m", "out_start": 0.0, "out_end": 1.0,
                 "x": 0.5, "y": 0.5, "anim_in": "none", "anim_out": "none"}
        sem = render_ov(Overlay(id="o_sem", **comum), "ov_sem")
        _r0, g0v, b0 = rgb(sem, 0.5, 160, 120)
        check(g0v > 150 and b0 < 100,
              f"sem chroma, o verde cobre o fundo azul (G={g0v}, B={b0})")
        com = render_ov(Overlay(id="o_ck", effects=A.normalizar_efeitos(
            [{"kind": "chroma", "color": "0x00FF00", "similarity": 0.3}],
            A.EFEITOS_DA_SOBREPOSICAO), **comum), "ov_chroma")
        _r1, g1v, b1 = rgb(com, 0.5, 160, 120)
        check(b1 > 150 and g1v < 100,
              f"com CHROMA, o verde some e o fundo azul aparece "
              f"(G={g1v}, B={b1})")

        # TREMOR: a janela sai do lugar ao longo do tempo
        tremido = render_ov(Overlay(id="o_tr", effects=A.normalizar_efeitos(
            [{"kind": "tremor", "amplitude": 0.08, "frequency": 3.0}],
            A.EFEITOS_DA_SOBREPOSICAO), **comum), "ov_tremor")

        def centro_x(v: Path, t: float) -> float:
            d = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t}", "-i", str(v),
                                "-frames:v", "1", "-f", "rawvideo",
                                "-pix_fmt", "rgb24", "-"],
                               capture_output=True).stdout
            linha = [x for x in range(320)
                     if d[(120 * 320 + x) * 3 + 1] > 150]
            return (linha[0] + linha[-1]) / 2.0 if linha else -1.0

        posicoes = [centro_x(tremido, t) for t in (0.0, 0.1, 0.2, 0.3)]
        parado = [centro_x(sem, t) for t in (0.0, 0.1, 0.2, 0.3)]
        variou = max(posicoes) - min(posicoes)
        firme = max(parado) - min(parado)
        check(variou > 8 and firme < 2,
              f"o TREMOR move a janela ao longo do tempo (variação {variou:.1f} px) "
              f"e sem ele ela fica firme ({firme:.1f} px)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def testar_dividir_bloco_nao_compartilha_interior() -> None:
    """Dividir um bloco tem que dar dois blocos INDEPENDENTES.

    ``_clone`` fazia ``Clip(**clip.__dict__)``, que é cópia rasa: a lista de
    efeitos, o dicionário do fit, o da foto e os dois snaps ficavam sendo O
    MESMO objeto nas duas metades. Mexer no efeito de uma mexia na outra — e
    mexia calado, porque nada no plano denuncia dois itens apontando para a
    mesma lista. O sintoma que o usuário veria é o pior tipo: ele ajusta uma
    metade, olha a outra e ela mudou sozinha.
    """
    from editor.edit.ops import _clone, cut_source_range
    from editor.models import Clip

    c = Clip(src_start=0.0, src_end=4.0,
             effects=[{"kind": "vinheta", "amount": 0.5}],
             fit={"brightness": 0.1},
             snap_in={"reason": "vale"})
    a = _clone(c, 0.0, 2.0)
    b = _clone(c, 2.0, 4.0)
    a.effects[0]["amount"] = 0.9
    a.fit["brightness"] = 0.9
    a.effects.append({"kind": "desfoque", "sigma": 5})
    a.snap_in["reason"] = "outro"
    check(abs(b.effects[0]["amount"] - 0.5) < 1e-9 and len(b.effects) == 1,
          f"mexer no efeito de uma metade não mexe na outra "
          f"({b.effects[0]['amount']}, {len(b.effects)} efeito(s))")
    check(abs(b.fit["brightness"] - 0.1) < 1e-9,
          f"nem no fit ({b.fit['brightness']})")
    check(b.snap_in["reason"] == "vale",
          f"nem no snap ({b.snap_in['reason']})")
    check(abs(c.effects[0]["amount"] - 0.5) < 1e-9,
          "e o bloco original também fica intacto")

    # e pelo caminho de verdade: um corte no meio parte o bloco em dois
    partidos, _ = cut_source_range(
        [Clip(src_start=0.0, src_end=4.0,
              effects=[{"kind": "desfoque", "sigma": 6}])], 1.8, 2.2)
    check(len(partidos) == 2, f"o corte partiu em dois ({len(partidos)})")
    partidos[0].effects[0]["sigma"] = 40
    check(abs(partidos[1].effects[0]["sigma"] - 6) < 1e-9,
          f"e as duas metades do corte real também são independentes "
          f"({partidos[1].effects[0]['sigma']})")


def testar_varios_videos_no_mesmo_projeto() -> None:
    """Três tomadas soltas, um vídeo pronto — cada uma cortada no ÁUDIO DELA.

    O miolo do editor já era agnóstico de fonte: Timeline empilha clipes de
    arquivos diferentes, o render resolve o caminho por clipe, e
    ``build_auto_plan`` é função pura de (palavras, envelope, parâmetros). O
    que estava cravado em "um vídeo só" era a camada de análise: UM envelope,
    UMA transcrição, e o número da palavra recomeçando do zero a cada vez.

    O terceiro era o mais perigoso dos três. O "i" da palavra é a identidade
    que o programa inteiro usa — removed_word_ids, Subtitle.word_ids — e duas
    gravações colidindo nele fariam apagar a palavra 12 de uma sumir a palavra
    12 da outra.

    E o segundo é o que fere a regra 3 em silêncio: o corte encaixa a borda no
    vale de energia, e com o envelope do arquivo errado a borda cai num vale
    que não existe naquele áudio — em cima de palavra — enquanto o encaixe
    devolve uma explicação convincente sobre o vale errado.

    O que este teste mede é isso: que a segunda gravação foi cortada contra o
    som dela.
    """
    import subprocess
    import tempfile
    from pathlib import Path

    import numpy as np

    from editor import projects as svc
    from editor.audio.envelope import compute_envelope
    from editor.config import ExportParams
    from editor.edit.timeline import Timeline
    from editor.ffmpeg_utils import extract_wav, read_wav_mono
    from editor.models import Clip
    from tests.e2e import Ctx

    tmp = Path(tempfile.mkdtemp(prefix="varios_"))
    projeto = None
    try:
        # DUAS gravações com a fala em lugares DIFERENTES. É a diferença que
        # torna o teste capaz de pegar o envelope trocado: cortar a segunda com
        # o envelope da primeira daria blocos em cima do silêncio dela.
        #
        # FALA DE VERDADE (espeak) na segunda, não tom sintético: o Whisper
        # falso acha as regiões pelo envelope REAL, e o tom de tests/synth não
        # produz região de fala nenhuma — o teste passaria por vazio.
        from tests.speech import build_track, make_video

        falas_a = [(0.5, 1.3), (2.6, 3.4), (4.7, 5.5)]
        dur_a = 6.2
        video_a = write_video(tmp / "a.mp4", build(falas_a, dur_a, noise=0.001),
                              dur_a, 180, 320, 30)
        amostras_b, marcas_b, dur_b = build_track(
            [("delta um dois", 0.9), ("eco tres quatro", 0.7)])
        video_b = make_video(tmp / "b.mp4", amostras_b, dur_b, 320, 180, 30)
        falas_b = [(m["start"], m["end"]) for m in marcas_b]

        projeto = svc.create(str(video_a), "varios", "VSL")
        # análise do principal, à mão (a de verdade é lenta e já tem teste)
        extract_wav(video_a, projeto.wav, 16000, 1)
        amostras, sr = read_wav_mono(projeto.wav)
        env_a = compute_envelope(amostras, sr)
        np.save(projeto.envelope_file, env_a.db)
        svc._envelope_cache[projeto.id] = env_a
        texto_a = ["alfa", "bravo", "charlie"]
        projeto.analysis = {
            "duration": dur_a,
            "words": [{"i": i, "start": a, "end": b, "text": texto_a[i],
                       "prob": 0.95} for i, (a, b) in enumerate(falas_a)],
            "claps": [], "takes": [], "fillers": [],
            "manual_removed_word_ids": [],
            "envelope": {"hop": env_a.hop, "sample_rate": sr,
                         "noise_floor": env_a.noise_floor,
                         "duration": env_a.duration}}
        projeto.save_analysis()
        projeto.plan.clips = [Clip(src_start=0.0, src_end=dur_a)]
        projeto.plan.export = ExportParams(scale="240", burn_subtitles=False,
                                           preset="ultrafast", crf=30)
        projeto.save_plan()

        # a SEGUNDA gravação entra pelo caminho de verdade
        midia = svc.add_media(projeto.id, str(video_b), "video", "tomada 2")
        install(["delta um dois", "eco tres quatro"])
        info = svc.analisar_midia(svc.load(projeto.id), midia["id"], Ctx(quiet=True))
        p = svc.load(projeto.id)

        check(info["ordem"] == 1 and info["base_i"] == svc.BASE_POR_FONTE,
              f"a gravação acrescentada ganha uma FAIXA de números só dela "
              f"(base {info['base_i']})")
        numeros_a = {w["i"] for w in p.words}
        numeros_b = {w["i"] for w in p.words_de(midia["id"])}
        check(numeros_a and numeros_b and not (numeros_a & numeros_b),
              f"os números das palavras NÃO colidem "
              f"({sorted(numeros_a)[:3]} contra {sorted(numeros_b)[:3]})")
        check(p.envelope_de(midia["id"]).exists()
              and p.envelope_de(midia["id"]) != p.envelope_file,
              "e ela tem o PRÓPRIO envelope, em arquivo separado")
        env_b = p.envelope(midia["id"])
        check(env_b is not None and abs(env_b.duration - dur_b) < 0.3,
              f"que é o áudio dela mesma ({env_b.duration if env_b else '?'} s "
              f"contra {dur_b} s)")

        # a edição automática monta as duas
        svc.auto_edit(svc.load(projeto.id), Ctx(quiet=True))
        p = svc.load(projeto.id)
        por_fonte: dict = {}
        for c in p.plan.active_clips:
            por_fonte.setdefault(c.source, []).append(c)
        check(set(por_fonte) == {"main", midia["id"]},
              f"a linha do tempo tem blocos das DUAS gravações ({list(por_fonte)})")

        ordem = [c.source for c in p.plan.active_clips]
        check(ordem == sorted(ordem, key=lambda x: x != "main"),
              f"e na ordem da montagem: o principal primeiro ({ordem})")

        # O QUE IMPORTA: os blocos da segunda caem em cima da FALA dela
        blocos_b = por_fonte[midia["id"]]
        def fala_em(t: float) -> bool:
            return any(a - 0.25 <= t <= b + 0.25 for a, b in falas_b)
        meios = [(c.src_start + c.src_end) / 2 for c in blocos_b]
        acertos = sum(1 for t in meios if fala_em(t))
        check(blocos_b and acertos == len(meios),
              f"os blocos da segunda gravação caem em cima da fala DELA "
              f"({acertos} de {len(meios)}; falas em {falas_b})")
        # A REGRA 3, NA SEGUNDA GRAVAÇÃO. Esta é a asserção que importa: se o
        # corte dela tivesse sido decidido contra o envelope do primeiro
        # arquivo, a borda cairia num vale que não existe neste áudio — em
        # cima de palavra. Nenhuma palavra viva pode estar partida por uma
        # borda de bloco.
        palavras_b = p.words_de(midia["id"])
        removidas = set(p.analysis.get("removed_word_ids") or [])
        vivas = [w for w in palavras_b if w["i"] not in removidas]
        partidas = [w for w in vivas
                    if not any(c.src_start - 0.03 <= w["start"]
                               and w["end"] <= c.src_end + 0.03
                               for c in blocos_b)]
        check(vivas and not partidas,
              f"NENHUMA palavra da segunda gravação foi partida pelo corte "
              f"({len(vivas)} palavra(s) viva(s), "
              f"{[(w['text'], round(w['start'], 2)) for w in partidas]})")
        # e o silêncio dela saiu: o que sobrou é menor que o arquivo
        cobertura = sum(c.src_duration for c in blocos_b)
        check(cobertura < dur_b - 0.5,
              f"e o silêncio dela saiu ({cobertura:.1f} s de {dur_b:.1f} s)")

        # legenda: as palavras das duas gravações viram legenda
        svc.rebuild_subtitles(p)
        p.save_plan()
        textos = " ".join(s_.text for s_ in p.plan.subtitles).lower()
        check("alfa" in textos or "bravo" in textos,
              f"a legenda tem palavra da primeira gravação ({textos[:60]}…)")
        check("delta" in textos or "eco" in textos,
              f"E da segunda ({textos[:110]}…)")
        tl = Timeline(p.plan.active_clips, 30.0)
        fora = [s_ for s_ in p.plan.subtitles if s_.start > tl.duration + 0.5]
        check(not fora,
              f"e nenhuma legenda caiu fora da linha do tempo ({len(fora)})")

        # o corte de uma gravação NÃO mexe na outra
        antes_a = len(por_fonte["main"])
        from editor.edit import ops
        p.plan.clips, _ = ops.cut_source_range(p.plan.clips, 1.7, 2.3,
                                               source=midia["id"])
        depois_a = len([c for c in p.plan.active_clips if c.source == "main"])
        check(depois_a == antes_a,
              f"cortar dentro da segunda gravação não mexe na primeira "
              f"({antes_a} → {depois_a} blocos)")
    finally:
        if projeto is not None:
            try:
                svc.delete_project(projeto.id)
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(tmp, ignore_errors=True)


def testar_previa_e_render_fazem_a_mesma_conta() -> None:
    """A prévia interpola em TypeScript e o render em expressão de ffmpeg.

    São DUAS implementações da mesma conta, e duas implementações divergem
    sozinhas com o tempo. Um teste que só confere se a linha de código existe
    (``check("valorEm" in player)``) não pega divergência nenhuma: ele prova que
    alguém escreveu a chamada, não que ela dá o número certo.

    Este teste gera uma tabela (t, valor) pelos DOIS lados e compara número a
    número, nas quatro curvas. Numa sobreposição animada, errar a curva não
    desalinha um pixel: põe a janela num lugar no vídeo e noutro na tela — e a
    promessa do produto é que a prévia seja ao pixel o que vai baixar.

    Pula com aviso se não houver Node: o README promete que o dono não precisa
    instalar Node para usar o editor, então a suíte não pode exigir.
    """
    import json
    import shutil as _shutil
    import subprocess
    import tempfile
    from pathlib import Path

    from editor.render import animacao as A

    node = _shutil.which("node")
    esbuild = Path("frontend/node_modules/.bin/esbuild")
    if not node or not esbuild.exists():
        print("  --    prévia x render: sem Node/esbuild aqui, pulado "
              "(o editor não precisa deles)")
        return

    casos = [
        # (nome, marcos, propriedade, repouso)
        ("linear", [{"t": 1.0, "x": 0.2}, {"t": 3.0, "x": 0.8}], "x", 0.5),
        ("suave", [{"t": 0.0, "scale": 0.5},
                   {"t": 2.0, "scale": 1.5, "easing": "suave"}], "scale", 1.0),
        ("entra", [{"t": 0.5, "opacity": 0.0},
                   {"t": 2.5, "opacity": 1.0, "easing": "entra"}], "opacity", 1.0),
        ("sai", [{"t": 0.0, "rotation": 0.0},
                 {"t": 4.0, "rotation": 90.0, "easing": "sai"}], "rotation", 0.0),
        ("tres marcos", [{"t": 0.0, "y": 0.1},
                         {"t": 1.0, "y": 0.9, "easing": "suave"},
                         {"t": 2.0, "y": 0.3, "easing": "sai"}], "y", 0.25),
        # marco que fala de OUTRA propriedade não pode mexer nesta
        ("marco de outra prop", [{"t": 0.0, "x": 0.2, "scale": 1.0},
                                 {"t": 2.0, "scale": 2.0}], "x", 0.5),
        ("sem marco", [], "x", 0.37),
    ]
    instantes = [round(-0.5 + i * 0.25, 4) for i in range(24)]

    tmp = Path(tempfile.mkdtemp(prefix="paridade_"))
    try:
        pacote = tmp / "an.mjs"
        # caminho ABSOLUTO: com cwd="frontend" o executável é resolvido
        # depois do chdir, e o relativo deixa de existir
        r = subprocess.run([str(esbuild.resolve()), "src/lib/animacao.ts", "--bundle",
                            "--format=esm", f"--outfile={pacote}",
                            "--log-level=error"],
                           cwd="frontend", capture_output=True, text=True)
        check(r.returncode == 0,
              f"o módulo de animação da prévia compila ({r.stderr.strip()[:120]})")
        if r.returncode != 0:
            return

        pedido = [{"marcos": m, "chave": c, "repouso": rep, "instantes": instantes}
                  for _n, m, c, rep in casos]
        roteiro = tmp / "roda.mjs"
        roteiro.write_text(
            "import * as A from " + json.dumps(str(pacote)) + ";\n"
            "const casos = " + json.dumps(pedido) + ";\n"
            "const fora = casos.map((c) => c.instantes.map("
            "(t) => A.valorEm(c.marcos, c.chave, t, c.repouso)));\n"
            "console.log(JSON.stringify(fora));\n",
            encoding="utf-8")
        saida = subprocess.run([node, str(roteiro)], capture_output=True, text=True)
        check(saida.returncode == 0,
              f"e roda no Node ({saida.stderr.strip()[:140]})")
        if saida.returncode != 0:
            return
        do_ts = json.loads(saida.stdout.strip().split("\n")[-1])

        piores = []
        for (nome, marcos, chave, repouso), linha_ts in zip(casos, do_ts):
            pior = 0.0
            onde = 0.0
            for t, v_ts in zip(instantes, linha_ts):
                v_py = A.valor_em(marcos, chave, t, repouso=repouso)
                d = abs(float(v_ts) - float(v_py))
                if d > pior:
                    pior, onde = d, t
            piores.append((nome, pior, onde))
        ruins = [(n, d, t) for n, d, t in piores if d > 1e-9]
        check(not ruins,
              f"a prévia e o render dão O MESMO número nas quatro curvas "
              f"({len(instantes)} instantes x {len(casos)} casos; "
              f"pior diferença {max(d for _n, d, _t in piores):.2e})"
              if not ruins else
              f"a prévia e o render DIVERGEM: {ruins}")

        # e o "segura fora dos extremos" vale nos dois
        primeiro = do_ts[0][0]
        check(abs(primeiro - 0.2) < 1e-12,
              f"antes do primeiro marco os dois seguram o valor ({primeiro})")

        # TER A CONTA NÃO É USAR A CONTA. O teste acima prova que a função da
        # prévia dá o número certo; estes provam que a prévia CHAMA ela para
        # desenhar — e que o que o render aplica na imagem (máscara, efeito,
        # giro, faixa) a prévia aplica também.
        player = Path("frontend/src/components/Player.tsx").read_text(encoding="utf-8")
        check("valorEm(kfs, 'x', playhead, o.x)" in player
              and "valorEm(kfs, 'scale', playhead, o.scale)" in player,
              "a geometria da prévia sai do valor ANIMADO no instante, não do "
              "valor de repouso")
        check("valorEm(kfs, 'opacity'" in player
              and "valorEm(kfs, 'rotation'" in player,
              "opacidade e giro também")
        check("estiloDaMascara(o.mask)" in player
              and "filtroDosEfeitos(o.effects" in player,
              "a máscara e os efeitos são desenhados, não só gravados")
        check("tremorEm(o.effects" in player
              and "playhead - o.out_start" in player,
              "o tremor da prévia conta do primeiro quadro da janela, como o "
              "render passou a contar")
        check("zIndex: 10 + (Number(o.track) || 0)" in player,
              "a faixa decide o empilhamento na prévia como no render")
        check("temChroma(o.effects)" in player,
              "e o chroma key, que o CSS não sabe fazer, é AVISADO na tela em "
              "vez de virar surpresa na exportação")
        # arrastar uma janela animada move a animação inteira
        check("mexeuPos = temAnimacao(kfs, 'x')" in player
              and "keyframes: kfs.map(" in player,
              "arrastar uma janela animada desloca os MARCOS dela; gravar só o "
              "x/y faria o arrasto não ter efeito no arquivo")
        # o gesto que faz a animação existir sem digitar número nenhum
        check("marcar aqui" in player,
              "existe o botão 'marcar aqui': posiciona, clica, anda, posiciona, "
              "clica — sem ele marco só existia por comando")
        check("Math.abs(Number(k.t) - t) > 0.02" in player,
              "e marcar duas vezes no mesmo instante SUBSTITUI em vez de "
              "empilhar (dois marcos no mesmo t dariam um salto de duração zero)")
        check("limpar marcos" in player,
              "e dá para tirar todos os marcos de volta")

        trilha = Path("frontend/src/components/Timeline.tsx").read_text(encoding="utf-8")
        check("const [ripple, setRipple] = useState(false)" in trilha,
              "o modo 'empurrar junto' começa DESLIGADO")
        check("empurrar junto" in trilha
              and "props.onMoveItem(seg.kind, seg.id, seg.side, alvoOut - bordaOut, ripple)"
              in trilha,
              "e tem botão próprio, passando o modo nos dois gestos")
    finally:
        _shutil.rmtree(tmp, ignore_errors=True)


def testar_ripple_fecha_o_buraco() -> None:
    """"Empurrar junto": mover empurra os seguintes, apagar fecha o buraco.

    A metade difícil disto já existia e não se chamava ripple: toda edição que
    muda a duração passa por ``remap_output_items``, que reancora cutaway,
    sobreposição, desfoque e trilha pela FONTE. Só que aquilo é involuntário —
    conserta o que o corte deslocou. O que faltava era a INTENÇÃO: abrir espaço
    e fechar buraco por gesto.

    Três coisas que este teste garante e que são o que separa ripple de
    "empurra tudo":
    - desligado (o padrão) NADA além do item pego se move;
    - só os itens DEPOIS do que foi mexido andam, nunca os de antes;
    - só a MESMA faixa anda — mover um cartão não arrasta uma janela de outra
      faixa.
    """
    import subprocess
    import tempfile
    from pathlib import Path

    from editor import projects as svc
    from editor.config import FFMPEG
    from editor.models import Clip
    from editor.server import app

    tmp = Path(tempfile.mkdtemp(prefix="ripple_"))
    projeto = None
    try:
        fonte = tmp / "fonte.mp4"
        subprocess.run([FFMPEG, "-y", "-v", "error",
                        "-f", "lavfi", "-i", "color=c=0x303030:s=320x240:r=30:d=30",
                        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                        "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", str(fonte)], check=True)
        selo = tmp / "selo.png"
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=yellow:s=40x40:d=1", "-frames:v", "1",
                        str(selo)], check=True)
        projeto = svc.create(str(fonte), "ripple", "VSL")
        projeto.plan.clips = [Clip(src_start=0.0, src_end=30.0)]
        projeto.save_plan()
        midia = svc.add_media(projeto.id, str(selo), "image", "selo")
        cliente = TestClient(app)

        def por_janelas(janelas: list[tuple[float, float, int]]) -> list[str]:
            """Recria as sobreposições e devolve os ids, em ordem."""
            p = svc.load(projeto.id)
            p.plan.overlays = []
            p.save_plan()
            ids = []
            for a, b, faixa in janelas:
                r = cliente.post(f"/api/projects/{projeto.id}/overlays",
                                 json={"media_id": midia["id"], "out_start": a,
                                       "out_end": b, "track": faixa})
                ids.append(r.json()["overlay"]["id"])
            return ids

        def janelas() -> list[tuple]:
            plano = cliente.get(f"/api/projects/{projeto.id}").json()["plan"]
            return [(round(o["out_start"], 2), round(o["out_end"], 2),
                     o.get("track", 0))
                    for o in plano["overlays"]]

        # ---- apagar com ripple fecha o buraco -------------------------
        ids = por_janelas([(1.0, 3.0, 0), (5.0, 7.0, 0), (9.0, 11.0, 0)])
        cliente.post(f"/api/projects/{projeto.id}/ops/item",
                     json={"kind": "overlay", "id": ids[1], "action": "delete"})
        check(janelas() == [(1.0, 3.0, 0), (9.0, 11.0, 0)],
              f"sem ripple, apagar o do meio deixa o buraco ({janelas()})")

        ids = por_janelas([(1.0, 3.0, 0), (5.0, 7.0, 0), (9.0, 11.0, 0)])
        r = cliente.post(f"/api/projects/{projeto.id}/ops/item",
                         json={"kind": "overlay", "id": ids[1],
                               "action": "delete", "ripple": True}).json()
        check(janelas() == [(1.0, 3.0, 0), (7.0, 9.0, 0)],
              f"COM ripple, o terceiro vem 2 s para trás e fecha o buraco "
              f"({janelas()})")
        check("fechando o buraco" in (r.get("aviso") or ""),
              f"e a tela é avisada do que andou ({r.get('aviso')})")

        # o de ANTES nunca se move
        ids = por_janelas([(1.0, 3.0, 0), (5.0, 7.0, 0), (9.0, 11.0, 0)])
        cliente.post(f"/api/projects/{projeto.id}/ops/item",
                     json={"kind": "overlay", "id": ids[2],
                           "action": "delete", "ripple": True})
        check(janelas() == [(1.0, 3.0, 0), (5.0, 7.0, 0)],
              f"apagar o último não mexe em quem vem antes ({janelas()})")

        # ---- mover com ripple empurra os seguintes --------------------
        ids = por_janelas([(1.0, 3.0, 0), (5.0, 7.0, 0), (9.0, 11.0, 0)])
        r = cliente.post(f"/api/projects/{projeto.id}/ops/item",
                         json={"kind": "overlay", "id": ids[0],
                               "action": "move", "delta": 2.0,
                               "ripple": True}).json()
        check(janelas() == [(3.0, 5.0, 0), (7.0, 9.0, 0), (11.0, 13.0, 0)],
              f"mover o primeiro 2 s empurra os dois seguintes ({janelas()})")
        check("andaram" in (r.get("aviso") or ""),
              f"e diz quantos andaram ({r.get('aviso')})")

        ids = por_janelas([(1.0, 3.0, 0), (5.0, 7.0, 0)])
        cliente.post(f"/api/projects/{projeto.id}/ops/item",
                     json={"kind": "overlay", "id": ids[0],
                           "action": "move", "delta": 2.0})
        check(janelas() == [(3.0, 5.0, 0), (5.0, 7.0, 0)],
              f"sem ripple, só o item pego anda ({janelas()})")

        # ---- a faixa importa -----------------------------------------
        ids = por_janelas([(1.0, 3.0, 0), (5.0, 7.0, 1), (9.0, 11.0, 0)])
        cliente.post(f"/api/projects/{projeto.id}/ops/item",
                     json={"kind": "overlay", "id": ids[0],
                           "action": "move", "delta": 2.0, "ripple": True})
        depois = janelas()
        na_outra = [j for j in depois if j[2] == 1]
        check(na_outra == [(5.0, 7.0, 1)],
              f"o item da OUTRA faixa não é arrastado ({na_outra})")
        check((11.0, 13.0, 0) in depois,
              f"e o da mesma faixa é ({depois})")

        # ---- os marcos andam com a janela ----------------------------
        ids = por_janelas([(1.0, 3.0, 0), (5.0, 7.0, 0)])
        cliente.put(f"/api/projects/{projeto.id}/overlays/{ids[1]}",
                    json={"keyframes": [{"t": 5.0, "x": 0.2},
                                        {"t": 7.0, "x": 0.8}]})
        cliente.post(f"/api/projects/{projeto.id}/ops/item",
                     json={"kind": "overlay", "id": ids[0],
                           "action": "move", "delta": 2.0, "ripple": True})
        plano = cliente.get(f"/api/projects/{projeto.id}").json()["plan"]
        empurrada = next(o for o in plano["overlays"] if o["id"] == ids[1])
        ts = [round(float(k["t"]), 2) for k in empurrada["keyframes"]]
        check(ts == [7.0, 9.0],
              f"os MARCOS da janela empurrada andam junto ({ts}) — sem isso a "
              f"janela muda de lugar e o movimento dela fica onde estava")
    finally:
        if projeto is not None:
            try:
                svc.delete_project(projeto.id)
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(tmp, ignore_errors=True)


def testar_gravar_dentro_do_app() -> None:
    """A tomada gravada no app chega ao disco com duração — e some quando ele apaga.

    O MediaRecorder do navegador escreve um FLUXO, não um arquivo pronto: o
    cabeçalho WebM sai sem duração e sem índice de busca, porque no momento em
    que ele começa ninguém sabe quando vai terminar. Guardar como veio dá o
    sintoma conhecido: duração desconhecida, agulha que não anda, e o corte de
    silêncio recebendo duração zero e devolvendo vídeo vazio.

    O conserto é REMUX — os mesmos quadros comprimidos copiados para um
    recipiente novo (``-c copy``), que aí sai com duração. A regra 2 fica de
    pé: a gravação chega ao corte na primeira e única geração de compressão
    que ela tem.
    """
    import subprocess
    import tempfile
    from pathlib import Path

    from editor import gravacoes
    from editor.config import FFMPEG
    from editor.server import app

    tmp = Path(tempfile.mkdtemp(prefix="gravar_"))
    guardadas: list[str] = []
    try:
        # o que o Chrome manda: VP8 + Opus num WebM de fluxo
        bruto = tmp / "fluxo.webm"
        subprocess.run([FFMPEG, "-y", "-v", "error",
                        "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=3",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                        "-c:v", "libvpx", "-b:v", "300k", "-c:a", "libopus",
                        "-f", "webm", str(bruto)], check=True)
        dados = bruto.read_bytes()
        check(len(dados) > 1000, f"o 'navegador' produziu {len(dados)} bytes")

        cliente = TestClient(app)
        r = cliente.post("/api/gravacoes",
                         files={"arquivo": ("tomada 1.webm", dados, "video/webm")},
                         data={"nome": "tomada 1.webm", "mime": "video/webm"})
        check(r.status_code == 200, f"a rota aceita a gravação ({r.status_code})")
        g = r.json()
        guardadas.append(g["nome"])
        check(g.get("remuxado") is True,
              f"e a reempacota ({g.get('aviso') or 'sem ressalva'})")
        check(abs(g["duracao"] - 3.0) < 0.3,
              f"a duração aparece no arquivo guardado ({g['duracao']} s)")
        check(g["largura"] == 320 and g["altura"] == 240,
              f"com o tamanho certo ({g['largura']}x{g['altura']})")
        check(g["tem_audio"] is True, "e com o áudio dentro")
        check(Path(g["path"]).exists() and Path(g["path"]).stat().st_size > 0,
              "o arquivo está no disco")
        check(not list(gravacoes.pasta().glob("*(cru)*")),
              "e o arquivo cru foi apagado — guardar os dois dobra o disco por "
              "tomada, e o cru só servia para o remux")

        # sem áudio a tomada continua entrando, mas dizendo que não tem
        muda = tmp / "muda.webm"
        subprocess.run([FFMPEG, "-y", "-v", "error",
                        "-f", "lavfi", "-i", "testsrc2=s=160x120:r=30:d=1",
                        "-c:v", "libvpx", "-b:v", "150k", "-f", "webm",
                        str(muda)], check=True)
        r2 = cliente.post("/api/gravacoes",
                          files={"arquivo": ("muda.webm", muda.read_bytes(),
                                             "video/webm")},
                          data={"nome": "muda.webm", "mime": "video/webm"})
        g2 = r2.json()
        guardadas.append(g2["nome"])
        check(g2["tem_audio"] is False,
              "uma tomada sem áudio entra, e a lista diz que não tem — sem "
              "áudio não há fala para transcrever nem silêncio para cortar")

        lista = cliente.get("/api/gravacoes").json()
        nomes = [x["nome"] for x in lista]
        check(all(n in nomes for n in guardadas),
              f"as duas aparecem na lista ({nomes})")
        check(lista[0]["criado_em"] >= lista[-1]["criado_em"],
              "da mais recente para a mais antiga")

        # MICROFONE ALTO DEMAIS: a onda achatada no teto não tem conserto
        # depois — o limitador da exportação impede o editor de estourar, não
        # desfaz o que nasceu estourado. O aviso tem que vir NA HORA.
        import numpy as np
        from editor.ffmpeg_utils import write_wav
        t_ = np.arange(48000 * 2) / 48000
        alto = tmp / "alto.wav"
        write_wav(alto, np.clip(2.5 * np.sin(2 * np.pi * 300 * t_), -1, 1)
                  .astype(np.float32), 48000)
        estourada = tmp / "estourada.webm"
        subprocess.run([FFMPEG, "-y", "-v", "error",
                        "-f", "lavfi", "-i", "testsrc2=s=160x120:r=30:d=2",
                        "-i", str(alto), "-c:v", "libvpx", "-b:v", "150k",
                        "-c:a", "libopus", "-shortest", "-f", "webm",
                        str(estourada)], check=True)
        r5 = cliente.post("/api/gravacoes",
                          files={"arquivo": ("estourada.webm",
                                             estourada.read_bytes(), "video/webm")},
                          data={"nome": "estourada.webm", "mime": "video/webm"})
        g5 = r5.json()
        guardadas.append(g5["nome"])
        check("ESTOUROU" in (g5.get("aviso") or "")
              and "microfone" in g5["aviso"],
              f"gravação com o microfone alto demais volta AVISANDO, com o "
              f"remédio — que só é barato agora, com a pessoa na frente da "
              f"câmera ({(g5.get('aviso') or 'sem aviso')[:60]}…)")
        check(not g.get("aviso"),
              "e a gravação normal não gera aviso nenhum (alarme falso ensina "
              "a ignorar o alarme)")

        # gravação vazia é recusada, não guardada
        r3 = cliente.post("/api/gravacoes",
                          files={"arquivo": ("nada.webm", b"", "video/webm")},
                          data={"nome": "nada.webm", "mime": "video/webm"})
        check(r3.status_code == 400,
              f"gravação vazia é recusada ({r3.status_code})")

        # APAGAR SÓ DE DENTRO DA PASTA. O nome vem da tela, e tela é entrada de
        # fora: um ".." no meio dele apagaria arquivo do usuário em qualquer
        # lugar do disco.
        check(not gravacoes.apagar("../../etc/passwd")
              and not gravacoes.apagar("..\\qualquer")
              and not gravacoes.apagar("sub/arquivo"),
              "travessia de caminho no nome não apaga nada fora da pasta")
        antes = len(cliente.get("/api/gravacoes").json())
        r4 = cliente.delete(f"/api/gravacoes/{guardadas[0]}")
        check(r4.status_code == 200, f"apagar a que não prestou funciona ({r4.status_code})")
        check(len(cliente.get("/api/gravacoes").json()) == antes - 1,
              "e ela sai da lista")
        check(cliente.delete("/api/gravacoes/nao-existe.mp4").status_code == 404,
              "apagar o que não existe dá 404, não erro cabeludo")
    finally:
        for n in guardadas:
            try:
                gravacoes.apagar(n)
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(tmp, ignore_errors=True)


def testar_pacote_numa_esteira_so() -> None:
    """Vários arquivos, UMA esteira, um vídeo — pela rota que a tela usa.

    Isto é o pedido inteiro: soltar três tomadas na primeira tela, escolher a
    receita uma vez e receber um vídeo só, com cada uma cortada no silêncio
    DELA e todas montadas na ordem escolhida.

    O único passo trocado por um substituto é o RENDER DA PRÉVIA, que é o mais
    caro do clique único (um passe de encode sobre a edição inteira) e já tem
    teste próprio. Tudo o mais roda de verdade: as duas análises, os dois
    cortes, a montagem e a legenda. Trocar o passo caro é o que mantém a suíte
    em um minuto; trocar o passo que está sendo testado seria trapaça.
    """
    import subprocess
    import tempfile
    import time
    from pathlib import Path

    from editor import projects as svc
    from editor.config import FFMPEG
    from editor.server import app
    from tests.speech import build_track, make_video

    tmp = Path(tempfile.mkdtemp(prefix="pacote_"))
    pid = None
    previa_real = svc.previa_da_edicao
    try:
        a_s, _a_m, a_d = build_track([("primeira tomada aqui", 0.7)])
        b_s, _b_m, b_d = build_track([("segunda tomada agora", 0.7)])
        v1 = make_video(tmp / "t1.mp4", a_s, a_d, 320, 180, 30)
        v2 = make_video(tmp / "t2.mp4", b_s, b_d, 320, 180, 30)
        install(["primeira tomada aqui", "segunda tomada agora"])

        svc.previa_da_edicao = lambda *a, **k: {"ok": True, "substituida": True}
        cliente = TestClient(app)
        r = cliente.post("/api/projects/pacote", json={
            "paths": [str(v1), str(v2), str(tmp / "nao_existe.mp4")],
            "preset": "VSL",
            "receita": {"export": {"scale": "240", "burn_subtitles": False,
                                   "preset": "ultrafast", "crf": 30}}})
        check(r.status_code == 200, f"a rota do pacote aceita a lista ({r.status_code})")
        d = r.json()
        pid = d["project"]["id"]
        check(d["fontes"] == 2,
              f"duas gravações entraram ({d['fontes']})")
        check(len(d["recusados"]) == 1
              and "nao_existe" in d["recusados"][0]["path"],
              f"e a que não existe é RECUSADA COM NOME, não engolida "
              f"({d['recusados']})")

        # espera a esteira
        limite = time.monotonic() + 240
        estado = {}
        while time.monotonic() < limite:
            js = [j for j in cliente.get("/api/jobs",
                                         params={"project_id": pid}).json()
                  if j["id"] == d["job"]["id"]]
            if js:
                estado = js[0]
                if estado["status"] in ("ok", "erro", "cancelado"):
                    break
            time.sleep(0.4)
        check(estado.get("status") == "ok",
              f"a esteira terminou bem ({estado.get('status')}: "
              f"{(estado.get('error') or '')[:160]})")

        p = svc.load(pid)
        fontes = {c.source for c in p.plan.active_clips}
        check(len(fontes) == 2,
              f"a linha do tempo tem blocos das DUAS gravações ({len(fontes)})")
        ordem = [c.source for c in p.plan.active_clips]
        check(ordem[0] == "main",
              f"o primeiro arquivo da lista abre o vídeo ({ordem})")
        check(len(p.plan.subtitles) >= 2,
              f"a legenda cobre o pacote ({len(p.plan.subtitles)} legendas)")
        check(p.plan.active_clips and all(c.src_duration > 0.01
                                          for c in p.plan.active_clips),
              "e todo bloco tem conteúdo")
        # O DEFEITO QUE ESTE TESTE DEIXOU PASSAR. Ele conferia que as duas
        # fontes estavam na linha do tempo e nunca conferia que NÃO havia nada
        # por cima: a segunda tomada entrava na sequência E era posta como
        # janela sobre a primeira, muda. O usuário viu "um vídeo por cima do
        # outro, sem áudio" e o teste estava verde.
        fontes_ids = {c.source for c in p.plan.active_clips if c.source != "main"}
        por_cima = [o for o in p.plan.overlays if o.media_id in fontes_ids]
        check(not por_cima,
              f"NENHUMA gravação do pacote é posta como janela por cima de "
              f"outra ({len(por_cima)} janela(s) apontando para uma fonte)")
        cobrindo = [c for c in p.plan.cutaways if c.media_id in fontes_ids]
        check(not cobrindo,
              f"nem como cobertura ({len(cobrindo)})")
        # o silêncio saiu: a soma dos blocos é menor que os dois arquivos juntos
        somado = sum(c.src_duration for c in p.plan.active_clips)
        check(somado < (a_d + b_d) - 0.4,
              f"o silêncio das duas saiu ({somado:.1f} s de "
              f"{a_d + b_d:.1f} s de gravação)")
    finally:
        svc.previa_da_edicao = previa_real
        if pid:
            try:
                svc.delete_project(pid)
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(tmp, ignore_errors=True)


def testar_gravacao_e_teleprompter_na_tela() -> None:
    """A tela de gravação e o teleprompter existem e estão ligados.

    Estas são as coisas que só o navegador faz — câmera, microfone, texto
    rolando — e a suíte não tem navegador. O que dá para afirmar daqui é o que
    quebra na prática quando falta: o pedido de câmera, o formato que o
    MediaRecorder aceita, o desligamento da câmera ao sair (senão a luz ao lado
    da lente fica acesa e a pessoa acha que está sendo gravada), o laço que rola
    o texto, e o caminho de volta das tomadas para a esteira.
    """
    from pathlib import Path

    frente = Path("frontend/src")
    g = (frente / "components/Gravar.tsx").read_text(encoding="utf-8")
    home = (frente / "components/Home.tsx").read_text(encoding="utf-8")
    apis = (frente / "lib/api.ts").read_text(encoding="utf-8")

    # ---- gravar ---------------------------------------------------
    check("navigator.mediaDevices.getUserMedia" in g,
          "a tela pede câmera e microfone pelo navegador")
    check("MediaRecorder.isTypeSupported" in g
          and "video/mp4;codecs=avc1" in g and "video/webm" in g,
          "e escolhe o formato que ESTE navegador aceita, com mp4 primeiro e "
          "webm como recuo — não um formato chutado")
    check("stream.current?.getTracks().forEach((t) => t.stop())" in g
          and "return () => {" in g.replace("useEffect(() => () => {", "return () => {"),
          "a câmera é DESLIGADA ao sair da tela (senão a luz da lente fica "
          "acesa e a pessoa acha que continua sendo gravada)")
    check("r.start(1000)" in g,
          "grava em pedaços de um segundo: se a aba cair no meio, o que já "
          "passou está na mão em vez de se perder inteiro")
    check("setContagem(3)" in g,
          "tem contagem antes de começar — gravar no instante do clique põe o "
          "clique no vídeo")
    check("somenteAudio" in g and "video: somenteAudio ? false" in g,
          "dá para gravar só o áudio, com a câmera desligada")
    check("api.gravar(blob" in g and "apagarGravacao" in g,
          "a tomada vai para o disco e a que não prestou se apaga")
    check("window.isSecureContext" in g,
          "e o endereço de rede, onde o navegador NÃO libera câmera, é "
          "explicado em vez de virar um erro cru que parece permissão negada")

    # ---- teleprompter ---------------------------------------------
    check("requestAnimationFrame(passo)" in g and "el.scrollTop += velocidade * dt" in g,
          "o texto rola por tempo (px/s), não por quadro — em máquina lenta "
          "rolar por quadro muda a velocidade da leitura")
    check("setRolando(false)" in g and "el.scrollHeight - 2" in g,
          "e para sozinho no fim, em vez de ficar raspando o fundo")
    check("espelhado" in g and "scaleX(-1)" in g,
          "tem espelhamento, para vidro de teleprompter")
    check("if (texto.trim()) setRolando(true)" in g,
          "o texto começa a rolar quando a gravação começa")
    check("velocidade" in g and "corpo" in g,
          "com velocidade e tamanho de letra na mão")

    # ---- o caminho de volta ---------------------------------------
    check("onUsar={(paths)" in home and "juntar(paths)" in home,
          "as tomadas escolhidas voltam para a esteira da primeira tela")
    check("api.escolher('video', 'Escolher os vídeos para editar',\n" in home
          or "'Escolher os vídeos para editar'," in home,
          "a janela do sistema abre aceitando VÁRIOS arquivos")
    check("Array.from(ev.dataTransfer.files ?? [])" in home,
          "e soltar três arquivos pega os três (pegar só o primeiro fazia "
          "parecer que funcionou e editar um)")
    check("pacote de {1 + maisFontes.length} gravações" in home,
          "a tela mostra o pacote montado, com a ordem")
    check("fontes_extras: fontesExtras" in apis,
          "e o clique único recebe as outras gravações como FONTE, não como "
          "anexo — elas são continuação da montagem, não janela por cima")


def testar_olhar_na_lente() -> None:
    """O desvio do olhar em graus — e a conta conferida contra medida física.

    O problema do teleprompter é geométrico antes de ser tecnológico: você lê o
    texto, o texto está num lugar da tela, a lente está em outro, e a diferença
    é um ÂNGULO. Quem assiste não vê o texto; vê o desvio.

    A tecnologia que redesenha o olho (NVIDIA Broadcast, Apple) conserta o
    sintoma e é boa — e como ela expõe uma CÂMERA VIRTUAL, ela já funciona nesta
    tela, que lista todas as câmeras. O que o editor acrescenta é a causa: o
    ângulo, que se resolve movendo o texto, de graça e sem GPU.

    Este teste confere a conta contra a realidade, não contra ela mesma: a
    altura de uma tela de 24 polegadas em 16:9 é 29,9 cm — isso é medida de
    régua, não opinião. Se a fórmula errar isso, o número de graus que a tela
    mostra é decoração.
    """
    import json
    import shutil as _shutil
    import subprocess
    import tempfile
    from pathlib import Path

    node = _shutil.which("node")
    esbuild = Path("frontend/node_modules/.bin/esbuild")
    if not node or not esbuild.exists():
        print("  --    olhar na lente: sem Node/esbuild aqui, pulado")
        return

    tmp = Path(tempfile.mkdtemp(prefix="olhar_"))
    try:
        pacote = tmp / "olhar.mjs"
        r = subprocess.run([str(esbuild.resolve()), "src/lib/olhar.ts", "--bundle",
                            "--format=esm", f"--outfile={pacote}",
                            "--log-level=error"],
                           cwd="frontend", capture_output=True, text=True)
        check(r.returncode == 0,
              f"o módulo do olhar compila ({r.stderr.strip()[:120]})")
        if r.returncode != 0:
            return

        roteiro = tmp / "roda.mjs"
        roteiro.write_text(
            "import * as O from " + json.dumps(str(pacote)) + ";\n"
            "const base = {lenteY:0, alturaJanelaPx:1080, diagonalPolegadas:24,"
            " proporcaoTela:16/9, distanciaCm:60};\n"
            "const fora = {\n"
            "  altura_24_16x9: O.alturaDaTelaCm(24, 16/9),\n"
            "  altura_24_4x3: O.alturaDaTelaCm(24, 4/3),\n"
            "  altura_15_16x10: O.alturaDaTelaCm(15.6, 16/10),\n"
            "  na_lente: O.desvioDoOlhar({...base, linhaY:0.02}),\n"
            "  perto: O.desvioDoOlhar({...base, linhaY:0.14}),\n"
            "  meio: O.desvioDoOlhar({...base, linhaY:0.5}),\n"
            "  embaixo: O.desvioDoOlhar({...base, linhaY:0.8}),\n"
            "  // a MESMA altura numa tela grande desvia mais\n"
            "  meio_tela_grande: O.desvioDoOlhar({...base, linhaY:0.5,"
            " diagonalPolegadas:32}),\n"
            "  // e mais longe da tela desvia menos\n"
            "  meio_longe: O.desvioDoOlhar({...base, linhaY:0.5, distanciaCm:120}),\n"
            "  colado: O.linhaParaOAngulo(base),\n"
            "  colado_8: O.linhaParaOAngulo({...base, grausAlvo:8}),\n"
            "  virtual: ['NVIDIA Broadcast Camera','OBS Virtual Camera',"
            "'Integrated Webcam','Logitech C920'].map(O.ehCameraVirtual),\n"
            "};\n"
            "console.log(JSON.stringify(fora));\n",
            encoding="utf-8")
        saida = subprocess.run([node, str(roteiro)], capture_output=True, text=True)
        check(saida.returncode == 0, f"e roda ({saida.stderr.strip()[:140]})")
        if saida.returncode != 0:
            return
        d = json.loads(saida.stdout.strip().split("\n")[-1])

        # ---- a conta contra a régua ---------------------------------
        check(abs(d["altura_24_16x9"] - 29.9) < 0.1,
              f"uma tela de 24\" em 16:9 tem 29,9 cm de altura "
              f"({d['altura_24_16x9']:.1f})")
        check(abs(d["altura_24_4x3"] - 36.6) < 0.1,
              f"a MESMA diagonal em 4:3 tem 36,6 cm — a proporção entra na "
              f"conta, e é por isso que a diagonal sozinha não serve "
              f"({d['altura_24_4x3']:.1f})")
        check(abs(d["altura_15_16x10"] - 21.0) < 0.2,
              f"e um notebook de 15,6\" em 16:10 tem 21 cm "
              f"({d['altura_15_16x10']:.1f})")

        # ---- o ângulo, e o que ele significa ------------------------
        check(d["na_lente"]["graus"] < 2 and d["na_lente"]["veredito"] == "imperceptivel",
              f"texto colado na lente: {d['na_lente']['graus']}° — "
              f"{d['na_lente']['veredito']}")
        check(d["meio"]["graus"] > 12 and d["meio"]["veredito"] == "aparece",
              f"texto no MEIO da tela: {d['meio']['graus']}° — aparece que "
              f"está lendo (era exatamente onde o teleprompter estava antes)")
        check(d["embaixo"]["graus"] > d["meio"]["graus"] > d["perto"]["graus"]
              > d["na_lente"]["graus"],
              f"o desvio cresce conforme o texto desce "
              f"({d['na_lente']['graus']}° → {d['perto']['graus']}° → "
              f"{d['meio']['graus']}° → {d['embaixo']['graus']}°)")

        # ---- o que faz a tela e a distância importarem ---------------
        check(d["meio_tela_grande"]["graus"] > d["meio"]["graus"] + 2,
              f"a MESMA altura numa tela de 32\" desvia mais "
              f"({d['meio']['graus']}° → {d['meio_tela_grande']['graus']}°) — "
              f"é por isso que a conta pede o tamanho da tela")
        check(d["meio_longe"]["graus"] < d["meio"]["graus"] / 1.6,
              f"e sentar mais longe desvia menos "
              f"({d['meio']['graus']}° a 60 cm → {d['meio_longe']['graus']}° "
              f"a 120 cm)")

        # ---- "colar na lente" põe o texto onde cabe ------------------
        colado = d["colado"]
        check(0.02 < colado < 0.35,
              f"'colar na lente' põe o texto a {colado * 100:.0f}% da tela — "
              f"nem na borda (ilegível) nem no meio (aparece)")
        check(d["colado_8"] > colado,
              f"e aceitar 8° de desvio deixa o texto descer mais, porque texto "
              f"mais baixo é mais fácil de ler ({colado * 100:.0f}% → "
              f"{d['colado_8'] * 100:.0f}%)")

        # ---- a câmera virtual do corretor de olhar -------------------
        check(d["virtual"] == [True, True, False, False],
              f"reconhece a câmera virtual do NVIDIA Broadcast e do OBS, e não "
              f"confunde com webcam comum ({d['virtual']})")

        # ---- e a tela usa isso -------------------------------------
        g = Path("frontend/src/components/Gravar.tsx").read_text(encoding="utf-8")
        check("desvioDoOlhar({" in g and "olhar.graus" in g,
              "a tela MOSTRA o desvio em graus, em vez de deixar ele adivinhar")
        check("linhaParaOAngulo({" in g,
              "e tem o botão que cola o texto na lente")
        check("lente" in g and "top: `max(2px, ${lenteY * 100}%)`" in g,
              "com a marca de onde a câmera está — 'olhe para a câmera' sem "
              "endereço não ajuda ninguém")
        check("ehCameraVirtual(d.label)" in g and "NVIDIA Broadcast" in g,
              "e avisa quando existe uma câmera com correção de olhar na "
              "máquina, que é a tecnologia que redesenha o olho")
        check("width: 'min(46%, 620px)'" in g,
              "o texto virou COLUNA ESTREITA: linha curta mantém o olho perto "
              "do centro em vez de varrer a tela de ponta a ponta")
        check("top: `${linhaY * 100}%`" in g and "top-1/4" not in g,
              "e não é mais o bloco que cobria os três quartos de baixo — ler "
              "ali é olhar para baixo, o desvio mais visível que existe")
    finally:
        _shutil.rmtree(tmp, ignore_errors=True)


def testar_tomadas_em_sequencia_nunca_por_cima() -> None:
    """Três tomadas saem EM SEQUÊNCIA, com som — nunca uma por cima da outra.

    O relato foi: "gravei três e mandei, e o vídeo saiu um por cima do outro,
    sem áudio". A causa: o posicionador de anexos (enriquecer) pegava TODA
    mídia de vídeo do projeto, e uma segunda gravação do pacote também é mídia
    de vídeo. Ela entrava na sequência (certo) e ALÉM DISSO era posta como
    janela por cima — e janela entra sem áudio, por regra.

    Agora a mídia tem PAPEL: 'fonte' (continuação da montagem) ou 'anexo'
    (janela, cobertura, foto por cima). Este teste percorre o caminho que a
    TELA faz — cria com a primeira, sobe as outras como mídia, dispara o clique
    único com as extras — e o caminho do projeto ANTIGO, criado antes de o
    papel existir, que já está no disco dele com a janela-fantasma.
    """
    import tempfile
    from pathlib import Path

    from editor import projects as svc
    from editor.models import Overlay
    from tests.e2e import Ctx
    from tests.speech import build_track, make_video

    tmp = Path(tempfile.mkdtemp(prefix="sequencia_"))
    pids: list[str] = []
    previa_real = svc.previa_da_edicao
    svc.previa_da_edicao = lambda *a, **k: {"ok": True, "substituida": True}
    try:
        frases = ["primeira tomada aqui", "segunda tomada agora",
                  "terceira e ultima"]
        tomadas = []
        for i, frase in enumerate(frases):
            amostras, _m, dur = build_track([(frase, 0.7)])
            tomadas.append(make_video(tmp / f"t{i}.mp4", amostras, dur,
                                      320, 180, 30))
        install(frases)

        # ---- o caminho da TELA ----------------------------------------
        p = svc.create(str(tomadas[0]), "sequencia", "VSL")
        pids.append(p.id)
        extras = [svc.add_media(p.id, str(t), "video", papel="fonte")["id"]
                  for t in tomadas[1:]]
        # uma mídia que É anexo de verdade, para provar que o filtro não
        # derrubou o anexo junto com o defeito
        selo = svc.add_media(p.id, str(tomadas[2]), "video", "selo de verdade",
                             papel="anexo")
        svc.one_click(svc.load(p.id), Ctx(quiet=True), fontes_extras=extras)
        q = svc.load(p.id)
        ordem = []
        for c in q.plan.active_clips:
            if not ordem or ordem[-1] != c.source:
                ordem.append(c.source)
        check(ordem == ["main", *extras],
              f"as três tomadas saem EM SEQUÊNCIA, na ordem do pacote "
              f"({len(ordem)} trechos de gravação)")
        por_cima = [o for o in q.plan.overlays if o.media_id in extras]
        check(not por_cima,
              f"nenhuma tomada é posta como janela por cima de outra "
              f"({len(por_cima)})")
        check(all(c.audio != "mute" for c in q.plan.active_clips),
              "e todas com o som delas")
        anexas = {m["id"] for m in svc.midias_anexas(q)}
        check(selo["id"] in anexas and not (anexas & set(extras)),
              "o filtro separa o anexo DE VERDADE das fontes — o selo "
              "continua sendo anexo, as tomadas não")

        # ---- o projeto ANTIGO, que já está no disco com o defeito ------
        # criado antes do papel existir: as tomadas extras ficaram com
        # papel 'anexo' e uma delas virou janela por cima
        velho = svc.create(str(tomadas[0]), "antigo", "VSL")
        pids.append(velho.id)
        extras_velhas = [svc.add_media(velho.id, str(t), "video")["id"]
                         for t in tomadas[1:]]
        for mid in extras_velhas:
            svc.analisar_midia(svc.load(velho.id), mid, Ctx(quiet=True))
        from editor import db
        db.ex("UPDATE media SET papel='anexo' WHERE project_id=?", (velho.id,))
        v = svc.load(velho.id)
        v.plan.overlays = [Overlay(media_id=extras_velhas[0], out_start=1.0,
                                   out_end=4.0)]
        v.save_plan()
        check(not ({m["id"] for m in svc.midias_anexas(svc.load(velho.id))}
                   & set(extras_velhas)),
              "no projeto antigo, uma mídia com análise de fonte é fonte — "
              "seja lá o que o banco diga")
        svc.analyze(svc.load(velho.id), Ctx(quiet=True))
        # o defeito que este teste achou no caminho: analyze() reconstruía a
        # análise do zero e jogava fora a das outras gravações
        check(set(svc.load(velho.id).fontes) == set(extras_velhas),
              "refazer a análise do vídeo principal NÃO apaga a análise das "
              "outras tomadas (antes apagava, e elas sumiam da montagem)")
        svc.auto_edit(svc.load(velho.id), Ctx(quiet=True))
        v2 = svc.load(velho.id)
        check(not [o for o in v2.plan.overlays if o.media_id in extras_velhas],
              "e refazer a edição LIMPA a janela-fantasma que ele já tem no "
              "disco — não é edição de ninguém, é o defeito")
    finally:
        svc.previa_da_edicao = previa_real
        for pid in pids:
            try:
                svc.delete_project(pid)
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(tmp, ignore_errors=True)


def testar_nenhum_campo_branco_no_branco() -> None:
    """Todo campo de texto do app tem fundo escuro.

    O app pinta a letra de claro. Um campo sem a classe `field` fica com o
    fundo BRANCO padrão do navegador, e a letra clara some em cima dele. Foi o
    que aconteceu no teleprompter: ele colava o roteiro e não via o que tinha
    colado. A varredura achou um segundo, no campo da chave do Gemini, que
    usava a classe `input` — que não existe no app.

    Este teste varre TODOS os componentes, para o próximo campo não nascer
    assim sem ninguém perceber até alguém tentar escrever nele.
    """
    import re
    from pathlib import Path

    css = Path("frontend/src/index.css").read_text(encoding="utf-8")
    check(".field {" in css and "bg-ink-700" in css,
          "a classe `field` existe e dá fundo escuro")
    sem_estilo = []
    for arquivo in sorted(Path("frontend/src/components").glob("*.tsx")):
        texto = arquivo.read_text(encoding="utf-8")
        for m in re.finditer(r"<(textarea|select|input)\b([^>]*?)/?>", texto, re.S):
            tag, attrs = m.group(1), m.group(2)
            tipo = re.search(r'type="([^"]+)"', attrs)
            tipo = tipo.group(1) if tipo else ("text" if tag == "input" else tag)
            if tipo in ("checkbox", "radio", "range", "file", "hidden", "color"):
                continue
            if "field" in attrs:
                continue
            linha = texto[:m.start()].count("\n") + 1
            sem_estilo.append(f"{arquivo.name}:{linha} <{tag}>")
    check(not sem_estilo,
          f"nenhum campo de texto sem fundo escuro no app inteiro "
          f"({sem_estilo or 'todos com `field`'})")


def testar_nenhuma_cor_fora_da_paleta() -> None:
    """Toda cor usada nas telas existe na paleta.

    O Tailwind não reclama de cor que não existe: ele só não gera a classe, e
    o elemento fica sem cor nenhuma. A tela de gravação usava `bg-ink-950/95`,
    e a paleta vai só até o 900. A tela abria TRANSPARENTE, e a primeira tela
    aparecia por baixo das tomadas e do painel de controles: letras e botões
    fantasmas.

    Este teste confere cada tom usado contra o tailwind.config.js, e que a
    tela de gravação tapa a de baixo por inteiro.
    """
    import re
    from pathlib import Path

    config = Path("frontend/tailwind.config.js").read_text(encoding="utf-8")
    cores = re.search(r"colors:\s*\{(.*?)\n\s*\},", config, re.S).group(1)
    paleta = {}
    for nome, valor in re.findall(r"(\w+):\s*(\{[^}]*\}|'[^']*')", cores):
        paleta[nome] = (set(re.findall(r"(\w+):", valor))
                        if valor.startswith("{") else {"DEFAULT"})
    check({"ink", "line", "accent"} <= set(paleta),
          f"a paleta do app foi lida do tailwind.config.js ({sorted(paleta)})")

    uso = re.compile(r"-(%s)(?:-(\w+))?\b" % "|".join(paleta))
    fora = []
    for arquivo in sorted(Path("frontend/src").rglob("*")):
        if arquivo.suffix not in (".tsx", ".ts", ".css"):
            continue
        texto = arquivo.read_text(encoding="utf-8")
        for m in uso.finditer(texto):
            if (m.group(2) or "DEFAULT") not in paleta[m.group(1)]:
                linha = texto[:m.start()].count("\n") + 1
                fora.append(f"{arquivo.name}:{linha} {m.group(0)[1:]}")
    check(not fora,
          f"nenhuma cor fora da paleta no app inteiro "
          f"({fora or 'todas existem'})")

    gravar = Path("frontend/src/components/Gravar.tsx").read_text(encoding="utf-8")
    raiz = re.search(r'className="fixed inset-0[^"]*"', gravar)
    fundo = re.search(r"\bbg-ink-(\d+)(/\d+)?", raiz.group(0)) if raiz else None
    check(bool(fundo) and fundo.group(1) in paleta["ink"] and not fundo.group(2),
          "a tela de gravação tem fundo opaco: nada da primeira tela aparece "
          "por baixo")


def testar_som_nao_estoura_com_trilha() -> None:
    """Voz quente mais trilha masterizada não pode sair distorcida.

    A mistura de voz e trilha era gravada em 16 bits antes de o volume ser
    ajustado. As duas somadas passam do teto — medido, voz a -0,9 dBFS com a
    trilha a -6 dB dava +2,9 dBFS — e em 16 bits isso virava 54.960 amostras
    CLIPADAS numa faixa de 6 s. O loudnorm abaixava tudo DEPOIS, e a saída
    parecia boa (pico -2 dB): a distorção estava gravada dentro, só que mais
    baixa. Olhar o pico da saída não pegava isso; por isso este teste reproduz
    a etapa de mistura e conta amostra por amostra.
    """
    import subprocess
    import tempfile
    from pathlib import Path

    import numpy as np

    from editor.config import FFMPEG, AudioParams
    from editor.ffmpeg_utils import read_wav_mono, write_wav
    from editor.models import EditPlan
    from editor.render import renderer as R

    tmp = Path(tempfile.mkdtemp(prefix="som_"))
    try:
        sr = R.AUDIO_SR
        dur = 6.0
        t = np.arange(int(sr * dur)) / sr
        voz = tmp / "voz.wav"
        write_wav(voz, (0.9 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), sr)
        trilha = tmp / "trilha.wav"
        write_wav(trilha, (0.98 * np.sin(2 * np.pi * 330 * t)).astype(np.float32), sr)

        # o que ACONTECIA: a mesma mistura, em 16 bits
        from editor.render import filters as F
        g = F.music_chain(-6.0, False, 12, 0, 0, dur, 0.0, None, None)
        mix16 = tmp / "mix16.wav"
        subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(voz),
                        "-stream_loop", "-1", "-i", str(trilha),
                        "-filter_complex", g, "-map", "[aout]", "-ac", "1",
                        "-ar", str(sr), "-c:a", "pcm_s16le", "-t", f"{dur}",
                        str(mix16)], check=True)
        a16, _ = read_wav_mono(mix16)
        clip16 = int(np.sum(np.abs(a16) >= 0.9999))
        check(clip16 > 1000,
              f"a mistura em 16 bits CLIPA ({clip16} amostras) — é o defeito "
              f"que existia, reproduzido para o teste valer")

        # o que ACONTECE agora, pela função de verdade
        plano = EditPlan()
        plano.music = {"media_id": "m", "enabled": True, "gain_db": -6.0,
                       "ducking": False, "duck_amount": 12, "fade_in": 0,
                       "fade_out": 0, "out_start": 0}
        saida = tmp / "saida.wav"
        R.process_audio(voz, saida, AudioParams(), plano,
                        {"m": {"path": str(trilha)}}, dur)
        a, _ = read_wav_mono(saida)
        clip = int(np.sum(np.abs(a) >= 0.9999))
        pico = 20 * np.log10(float(np.max(np.abs(a))) + 1e-12)
        check(clip == 0, f"e agora a saída não tem amostra clipada ({clip})")
        check(pico <= AudioParams().true_peak + 0.1,
              f"com o pico abaixo do teto ({pico:.2f} dBFS, teto "
              f"{AudioParams().true_peak} dBFS)")
        check(abs(len(a) / sr - dur) < 0.002,
              f"e a duração exata ({len(a) / sr:.4f} s) — o limitador compensa "
              f"o próprio atraso, senão a boca sai fora de sincronia")

        # o limitador não pode reamplificar: o volume-alvo tem que sobreviver
        lufs = subprocess.run(
            [FFMPEG, "-v", "info", "-nostdin", "-i", str(saida),
             "-af", "ebur128=peak=true", "-f", "null", "-"],
            capture_output=True, text=True).stderr
        import re
        m = re.findall(r"I:\s*(-?[\d.]+) LUFS", lufs)
        integrado = float(m[-1]) if m else 0.0
        check(abs(integrado - AudioParams().target_lufs) < 2.0,
              f"e o volume-alvo sobrevive ao limitador ({integrado:.1f} LUFS, "
              f"alvo {AudioParams().target_lufs}) — com o ganho automático "
              f"dele ligado, que é o padrão, ele reamplificaria tudo")

        codigo = Path("editor/render/renderer.py").read_text(encoding="utf-8")
        check('"pcm_f32le"' in codigo and "level=0:latency=1" in codigo,
              "a mistura é em ponto flutuante e o limitador tem ganho "
              "automático desligado e atraso compensado")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def testar_formato_de_feed_e_sem_legenda() -> None:
    """4:5 para o feed, e a opção de sair sem legenda — pelo caminho da tela.

    O 4:5 é o formato que o feed do Instagram e do Facebook mostra MAIOR sem
    cortar. A armadilha dele é a legenda: com proporção 0,8 ele caía na faixa
    do vertical, com a legenda a 21,5% da base — altura calculada para passar
    por cima da interface do Reels, que no feed quase não existe. A legenda
    ficava alta no meio do peito.
    """
    import re
    import subprocess
    import tempfile
    from pathlib import Path

    from editor import projects as svc
    from editor.config import FFMPEG
    from editor.models import Clip
    from editor.render.renderer import PROPORCOES, tamanho_derivado
    from editor.server import app

    # ---- o formato --------------------------------------------------
    check("4:5" in PROPORCOES, "o 4:5 existe como formato")
    w, h = tamanho_derivado(1080, 1920, "4:5")
    check((w, h) == (1080, 1350),
          f"de uma gravação vertical 1080x1920, o 4:5 sai 1080x1350 — o "
          f"tamanho que o feed exibe ({w}x{h})")
    check(w % 2 == 0 and h % 2 == 0,
          "com as duas dimensões pares, que o encoder exige")

    # ---- a legenda do feed, e a paridade das duas tabelas -------------
    f45, m45, _c, ch45 = svc.padrao_de_legenda(1080, 1350)
    f916, m916, _c2, ch916 = svc.padrao_de_legenda(1080, 1920)
    check(m45 / 1350 < m916 / 1920 - 0.05,
          f"a legenda do feed fica mais BAIXA que a do vertical "
          f"({m45 / 1350:.1%} contra {m916 / 1920:.1%} da altura) — no feed "
          f"não há a interface do Reels para desviar")
    check(ch45 > ch916, f"e cabe mais texto por linha ({ch45} contra {ch916})")

    tabela_py = [tuple(round(x, 4) for x in linha)
                 for linha in svc.PADROES_DE_LEGENDA]
    ts = Path("frontend/src/lib/formato.ts").read_text(encoding="utf-8")
    # depois do "= [": antes dele está a anotação de tipo
    # `[number, number, ...][]`, que também é colchete e viraria uma faixa
    # vazia — foi o que fez este teste acusar divergência onde não havia
    bloco = ts.split("const PADROES")[1].split("= [", 1)[1].split("]\n")[0]
    tabela_ts = [tuple(round(float(x), 4) for x in re.findall(r"[\d.]+", linha))
                 for linha in re.findall(r"\[([^\[\]]+)\]", bloco)]
    check(tabela_py == tabela_ts,
          f"a tabela de legenda da PRÉVIA é idêntica à do RENDER "
          f"({len(tabela_py)} faixas) — são duas cópias, e uma mexida só "
          f"numa faria a prévia mostrar uma legenda e o arquivo outra")
    check("'4:5': 4 / 5" in ts and "'4:5': [1350, 1080, 900]" in ts,
          "e a prévia conhece o 4:5 com os mesmos tamanhos")

    # ---- o vídeo de origem 4:5 é chamado de 4:5 ----------------------
    tmp = Path(tempfile.mkdtemp(prefix="feed_"))
    projeto = None
    try:
        fonte = tmp / "feed.mp4"
        subprocess.run([FFMPEG, "-y", "-v", "error",
                        "-f", "lavfi", "-i", "color=c=0x303030:s=432x540:r=30:d=2",
                        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                        "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", str(fonte)],
                       check=True)
        cliente = TestClient(app)
        r = cliente.post("/api/probe", json={"path": str(fonte)}).json()
        check(r.get("formato") == "4:5",
              f"um vídeo gravado em 4:5 é reconhecido como 4:5, e não como "
              f"vertical ({r.get('formato')})")

        # ---- sem legenda, pela receita da primeira tela ---------------
        projeto = svc.create(str(fonte), "sem-legenda", "VSL")
        projeto.plan.clips = [Clip(src_start=0.0, src_end=2.0)]
        projeto.save_plan()
        cliente.post(f"/api/projects/{projeto.id}/params",
                     json={"style": {"fontsize_scale": 1},
                           "export": {"burn_subtitles": False}})
        plano = svc.load(projeto.id).plan
        check(plano.export.burn_subtitles is False,
              "'sem legenda' na primeira tela chega ao plano")
        check(plano.style.fontsize > 0,
              f"e o tamanho da letra NÃO vai a zero ({plano.style.fontsize}) — "
              f"fonte zero escreveria um ASS inválido; quem desliga é o "
              f"burn_subtitles")
    finally:
        if projeto is not None:
            try:
                svc.delete_project(projeto.id)
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(tmp, ignore_errors=True)

    home = Path("frontend/src/components/Home.tsx").read_text(encoding="utf-8")
    check("<option value={0}>sem legenda</option>" in home
          and "burn_subtitles: legenda > 0" in home,
          "a primeira tela tem 'sem legenda' e manda isso na receita")
    player = Path("frontend/src/components/Player.tsx").read_text(encoding="utf-8")
    check("cue && legendaNoVideo" in player,
          "e a prévia NÃO desenha legenda quando o arquivo sai sem — senão "
          "ela mentiria sobre o que vai baixar")


def testar_previa_mostra_o_que_baixa() -> None:
    """A prévia mostra o FORMATO e o FILTRO que vão para a pasta.

    Dois defeitos que davam a mesma sensação — "não é isso que eu vou
    receber": a prévia abria sempre no formato da gravação (o usuário pedia
    9:16, baixava 9:16 e via horizontal), e o filtro de cinema escolhido na
    primeira tela só aparecia depois de o arquivo ficar pronto, porque a
    prévia ao vivo não passa por ffmpeg nenhum.
    """
    from pathlib import Path

    frente = Path("frontend/src")
    editor = (frente / "components/Editor.tsx").read_text(encoding="utf-8")
    player = (frente / "components/Player.tsx").read_text(encoding="utf-8")
    formato = (frente / "lib/formato.ts").read_text(encoding="utf-8")

    check("formatoEntregue" in editor and "export?.extras" in editor,
          "a prévia nasce no formato que vai para a pasta, não no da gravação")
    check("formato={formatoAtual}" in editor,
          "e é esse formato que o player recebe")
    check("look={project.plan?.look}" in editor and "look?: string | null" in player,
          "o filtro escolhido na primeira tela chega ao player")
    check("filtroCss = linear ? undefined : filtroDoLook(look)" in player,
          "e só é aplicado na prévia AO VIVO — a renderizada já vem queimada")
    for look in ("pb", "quente", "frio", "teal_orange", "vintage", "nitido"):
        check(f"{look}:" in formato, f"o look '{look}' tem aproximação em CSS")
    check("grayscale(1)" in formato, "preto e branco vira grayscale de verdade")

    # a legenda se mexe com o mouse, e o elemento arrastado não passa pelo React
    check("iniciarArrastoLegenda" in player and "onStyleChange" in player,
          "a legenda é arrastável e redimensionável na prévia")
    check("margin_v: Math.max(0, Math.round(d.m0 + dm))" in player,
          "arrastar a legenda mexe na margem (sobe e desce)")
    check("fontsize: Math.max(8, Math.min(400, Math.round(d.f0 * fator)))" in player,
          "e a quina mexe no tamanho da letra")
    check("requestAnimationFrame(pintar)" in player and "ovNode" in player,
          "o elemento arrastado é pintado direto no DOM, um quadro por vez — "
          "era o setState a cada mousemove que deixava a caixa para trás do mouse")
    check("const folgaX = Math.max(0.02, fw * 0.2)" in player,
          "o limite do arrasto é a BORDA da janela, não o centro dela — preso "
          "no centro, a caixa parava de seguir o mouse no meio do quadro")
    check("const fator = Math.min(4, Math.max(0.25, 1 + dd / (d.largura / 2)))" in player,
          "a quina da legenda é proporcional à faixa: em pixels de estilo, um "
          "arrasto de 60 px levava o fontsize de 25 para 386")
    check("estiloParaFonte" in player,
          "e o que se mexe num formato derivado é escrito na régua da fonte")


def testar_relogio_e_aviso_de_pronto() -> None:
    """O tempo correndo na tela de processamento e o aviso de que ficou pronto."""
    from pathlib import Path

    frente = Path("frontend/src")
    proc = (frente / "components/ProcessingView.tsx").read_text(encoding="utf-8")
    editor = (frente / "components/Editor.tsx").read_text(encoding="utf-8")

    check("relogio" in proc and "setInterval" in proc,
          "a tela de processamento tem cronômetro correndo")
    check("created_at" in proc, "que conta do instante em que o job entrou na fila")
    check("gravação de ${minutos}" in proc,
          "e diz quantos minutos tem a gravação")
    check("falta ${faltando}" in proc, "com uma estimativa do que falta")
    check("Seu vídeo está pronto" in editor,
          "e o editor abre com o aviso de que ficou pronto")
    check("levou ${Math.floor(pronto.segundos / 60)} min" in editor,
          "dizendo quanto tempo demorou")
    check("bytes(pronto.bytes)" in editor, "e o tamanho do arquivo")
    check("size_bytes" in editor, "que vem do resultado da exportação")

    # e o servidor manda esses números
    from editor.projects import export as _export  # noqa: F401
    fonte = Path("editor/projects.py").read_text(encoding="utf-8")
    check('payload["size_bytes"] = dest.stat().st_size' in fonte,
          "a exportação devolve o tamanho do arquivo")
    check('"duracao": round(duracao_de_saida(project), 2)' in fonte,
          "e o clique único devolve a duração do vídeo montado")


def testar_trilha_toca_do_comeco_ao_fim() -> None:
    """A música de fundo toca no vídeo inteiro — e na prévia também.

    A primeira tela gravava ``out_end: 0`` (o usuário só escolheu o MP3, não
    pediu janela nenhuma). A linha do tempo já lia esse zero como "a duração
    inteira"; só o render o levava a ferro e fogo — ``fim = min(0, total)`` —
    e a trilha saía com 0,1 s. A música que ele anexou não tocava nem na
    prévia nem no arquivo, e o trilho mostrava a faixa inteira: o sintoma era
    "a música não toca", sem nenhuma pista do motivo.
    """
    import subprocess
    import tempfile
    from pathlib import Path

    import numpy as np

    from editor.render.filters import music_chain

    # 1) a conta: zero é "até o fim", não "termina em zero"
    cheio = music_chain(-18, False, 12, 0.0, 0.0, 30.0, 0.0, None)
    zero = music_chain(-18, False, 12, 0.0, 0.0, 30.0, 0.0, 0)
    curto = music_chain(-18, False, 12, 0.0, 0.0, 30.0, 0.0, 10.0)
    check("atrim=0:30.000" in cheio, "sem out_end a trilha cobre o vídeo inteiro")
    check("atrim=0:30.000" in zero,
          f"out_end=0 TAMBÉM cobre o vídeo inteiro (era atrim=0:0.100)")
    check("atrim=0:10.000" in curto,
          "e uma janela de verdade continua sendo respeitada")

    # 2) no ÁUDIO EXPORTADO: a música está lá, do começo ao fim
    tmp = Path(tempfile.mkdtemp(prefix="trilha_"))
    from editor.config import FFMPEG
    voz = tmp / "voz.mp4"
    subprocess.run([FFMPEG, "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30:d=6",
                    "-f", "lavfi", "-i", "sine=frequency=200:duration=6",
                    "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(voz)], check=True)
    # a trilha é um tom AGUDO (3 kHz): dá para achá-la no espectro sem
    # confundir com a "voz" de 200 Hz
    trilha = tmp / "trilha.mp3"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                    "-i", "sine=frequency=3000:duration=6",
                    "-c:a", "libmp3lame", str(trilha)], check=True)

    from editor import projects as svc
    from editor.models import Clip

    projeto = svc.create(str(voz), "trilha", "VSL")
    try:
        projeto.plan.clips = [Clip(src_start=0.0, src_end=6.0)]
        m = svc.add_media(projeto.id, str(trilha), "audio")
        # EXATAMENTE o que a primeira tela gravava: out_end zero
        projeto.plan.music = {"media_id": m["id"], "gain_db": -6, "ducking": False,
                              "duck_amount": 12, "fade_in": 0.0, "fade_out": 0.0,
                              "muted": False, "enabled": True,
                              "out_start": 0, "out_end": 0}
        projeto.plan.export.burn_subtitles = False
        projeto.save_plan()
        ctx = Ctx(quiet=True)
        r = svc.export(svc.load(projeto.id), ctx,
                       {"filename": "com-trilha.mp4", "overwrite": True,
                        "output_dir": str(tmp)})
        saida = Path(r["output"])
        check(saida.exists(), f"o vídeo com trilha foi exportado ({saida.name})")

        # mede a energia em 3 kHz em três instantes: começo, meio e fim
        def energia_3k(t0: float, t1: float) -> float:
            cru = subprocess.run(
                [FFMPEG, "-v", "error", "-ss", f"{t0}", "-to", f"{t1}",
                 "-i", str(saida), "-vn", "-ac", "1", "-ar", "16000",
                 "-f", "f32le", "-"], capture_output=True, check=True).stdout
            x = np.frombuffer(cru, np.float32)
            if x.size < 512:
                return 0.0
            espectro = np.abs(np.fft.rfft(x * np.hanning(x.size)))
            freq = np.fft.rfftfreq(x.size, 1 / 16000)
            faixa = (freq > 2700) & (freq < 3300)
            return float(espectro[faixa].max() / max(espectro.max(), 1e-9))

        medidas = [round(energia_3k(a, b), 3)
                   for a, b in ((0.3, 1.0), (2.5, 3.2), (4.8, 5.5))]
        check(all(v > 0.05 for v in medidas),
              f"a trilha está no áudio no começo, no meio E no fim "
              f"(energia em 3 kHz: {medidas})")

        # e desligar a trilha realmente a tira
        projeto = svc.load(projeto.id)
        projeto.plan.music["muted"] = True
        projeto.save_plan()
        r2 = svc.export(svc.load(projeto.id), ctx,
                        {"filename": "sem-trilha.mp4", "overwrite": True,
                         "output_dir": str(tmp)})
        saida = Path(r2["output"])
        muda = [round(energia_3k(a, b), 3) for a, b in ((0.3, 1.0), (4.8, 5.5))]
        check(all(v < 0.05 for v in muda),
              f"e com a trilha muda o tom de 3 kHz não aparece ({muda})")
    finally:
        try:
            svc.delete_project(projeto.id)
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(tmp, ignore_errors=True)

    # 3) e a prévia AO VIVO toca a trilha, com o volume ajustável
    frente = Path("frontend/src")
    player = (frente / "components/Player.tsx").read_text(encoding="utf-8")
    trilha_tsx = (frente / "components/TrilhaPreview.tsx").read_text(encoding="utf-8")
    editor = (frente / "components/Editor.tsx").read_text(encoding="utf-8")
    check("<TrilhaPreview" in player and "!linear && music?.media_id" in player,
          "a trilha toca na prévia AO VIVO (na renderizada ela já está no arquivo)")
    check("onMusicChange({ gain_db: +e.target.value })" in player,
          "com um controle de volume embaixo do player, ouvindo na hora")
    check("music={project.plan?.music}" in editor and "ajustarMusica" in editor,
          "e o que se ajusta ali é gravado no plano")
    check("Math.pow(10, v / 20)" in trilha_tsx,
          "o volume da prévia usa a mesma conta em dB do render")
    check("faixa?.db" in trilha_tsx and "duck_amount" in trilha_tsx,
          "e respeita a curva da IA e o abaixamento na fala")



def testar_broll_depois_da_edicao() -> None:
    """B-roll DEPOIS da edição: vários vídeos por cima da fala, em sequência.

    O pedido foi "tem que ser possível inserir b-roll se quiser, pós edição".
    O caminho existia (cutaway), mas escondido: o trilho se chamava
    "Sobreposição", o botão "+ sobreposição", e soltar dois vídeos no mesmo
    ponto dava erro de colisão no segundo. Agora o trilho é B-roll, aceita
    vários de uma vez e cada um entra no primeiro vão livre depois do outro.

    O teste vai até o ARQUIVO: exporta e confere a cor do quadro no meio de
    cada b-roll, e que a duração do vídeo e a fala não mudaram.
    """
    import subprocess
    import tempfile
    from pathlib import Path

    from editor import projects as svc
    from editor.config import FFMPEG
    from editor.mcp import ferramentas as F
    from editor.server import app
    from tests.e2e import Ctx
    from tests.speech import build_track, make_video

    tmp = Path(tempfile.mkdtemp(prefix="broll_"))
    projeto = None
    previa_real = svc.previa_da_edicao
    svc.previa_da_edicao = lambda *a, **k: {"ok": True, "substituida": True}
    try:
        frases = ["esse produto mudou a minha rotina",
                  "olha como ele funciona na pratica",
                  "e o resultado aparece rapido",
                  "eu uso todos os dias de manha",
                  "clica no link e garante o seu"]
        install(frases)
        amostras, _m, dur = build_track([(f, 0.7) for f in frases])
        fonte = make_video(tmp / "fala.mp4", amostras, dur, 320, 180, 30)
        cores = {"vermelho": ("0xff0000", 2.0), "verde": ("0x00ff00", 8.0),
                 "azul": ("0x0000ff", 3.0)}
        brolls = {}
        for nome, (cor, d) in cores.items():
            brolls[nome] = tmp / f"{nome}.mp4"
            subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                            f"color=c={cor}:s=320x180:r=30:d={d}",
                            "-c:v", "libx264", "-preset", "ultrafast",
                            "-pix_fmt", "yuv420p", str(brolls[nome])], check=True)
        foto = tmp / "foto.png"
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                        "color=c=white:s=64x64:d=0.1", "-frames:v", "1",
                        str(foto)], check=True)

        projeto = svc.create(str(fonte), "broll", "VSL")
        svc.one_click(svc.load(projeto.id), Ctx(quiet=True))
        q = svc.load(projeto.id)
        duracao = svc.duracao_de_saida(q)
        palavras_antes = [w for w in svc.timeline_summary(q)["subtitles"]]
        check(duracao > 5, f"o vídeo editado existe ({duracao:.1f} s)")

        # ---- o trilho se chama B-roll e o botão diz "+ b-roll" ----------
        cliente = TestClient(app)
        tl = cliente.get(f"/api/projects/{projeto.id}").json()["timeline"]
        v2 = next(t for t in tl["tracks"] if t["id"] == "V2")
        check(v2["label"] == "B-roll" and v2.get("acao") == "b-roll",
              f"o trilho se chama B-roll e o botão dele diz '+ b-roll' "
              f"({v2['label']!r}, {v2.get('acao')!r})")
        v1 = next(t for t in tl["tracks"] if t["id"] == "V1")
        check(v1.get("acao") == "",
              "o trilho de vídeo não oferece um segundo botão para a mesma coisa")

        # ---- uma cobertura que já existe no meio do caminho -------------
        # os b-rolls têm que CONTORNAR, não recusar nem sobrepor
        r = cliente.post(f"/api/projects/{projeto.id}/brolls",
                         json={"paths": [str(brolls["azul"])], "at": 3.2,
                               "duracao": 1.5})
        check(r.status_code == 200 and len(r.json()["postos"]) == 1,
              f"um b-roll sozinho entra no cursor ({r.status_code})")
        ja = r.json()["postos"][0]

        # ---- três de uma vez, a partir de 0,5 s -------------------------
        r = cliente.post(f"/api/projects/{projeto.id}/brolls", json={
            "paths": [str(brolls["vermelho"]), str(brolls["verde"]),
                      str(foto), str(brolls["azul"])],
            "at": 0.5, "duracao": 3})
        corpo = r.json()
        postos = corpo["postos"]
        check(r.status_code == 200 and len(postos) == 3,
              f"três vídeos de b-roll entram de uma vez ({len(postos)})")
        check([Path(x["name"]).stem for x in postos]
              == ["vermelho", "verde", "azul"],
              "na ordem em que foram escolhidos")
        check(abs(postos[0]["out_start"] - 0.5) < 0.01,
              f"o primeiro entra no cursor ({postos[0]['out_start']:.2f} s)")
        check(postos[0]["out_end"] - postos[0]["out_start"] <= 2.0 + 0.02,
              f"um b-roll de 2 s cobre só 2 s — nunca mais do que a mídia tem "
              f"({postos[0]['out_end'] - postos[0]['out_start']:.2f} s)")
        todas = sorted([(c.out_start, c.out_end)
                        for c in svc.load(projeto.id).plan.cutaways])
        sobrepostas = [(a, b) for (a, b), (c, d) in zip(todas, todas[1:])
                       if c < b - 0.02]
        check(not sobrepostas,
              f"nenhum b-roll em cima de outro — o do meio contornou a "
              f"cobertura que já estava lá ({len(todas)} coberturas)")
        check(all(b <= duracao + 0.01 for _a, b in todas),
              "nenhum passa do fim do vídeo")
        check(len(postos) == 3
              and postos[1]["out_start"] >= postos[0]["out_end"] - 0.01
              and postos[2]["out_start"] >= postos[1]["out_end"] - 0.01,
              "cada um começa onde o anterior terminou (ou no vão seguinte)")
        check(len(postos) == 3 and postos[1]["out_start"] >= ja["out_end"] - 0.01,
              "o vão de 0,7 s antes da cobertura que já existia é pulado — "
              "b-roll de meio segundo pisca, não ilustra")
        check(any(Path(x["path"]).name == "foto.png"
                  and "imagem" in x["motivo"] for x in corpo["recusados"]),
              "a imagem é recusada COM O MOTIVO (b-roll é vídeo; imagem vai "
              "como janela ou foto)")
        check(all(c.audio != "mute"
                  for c in svc.load(projeto.id).plan.active_clips),
              "a fala não perde o som")

        # ---- sem vão nenhum: recusa com nome, sem 500 --------------------
        r = cliente.post(f"/api/projects/{projeto.id}/brolls",
                         json={"paths": [str(brolls["azul"])],
                               "at": duracao - 0.3})
        check(r.status_code == 400 and "não coube" in r.json()["detail"],
              f"no fim do vídeo não cabe, e a recusa diz por quê "
              f"({r.status_code})")

        # ---- o MCP também põe b-roll -------------------------------------
        class _Cliente:
            pedidos: list = []

            def post(self, rota, corpo):
                self.pedidos.append((rota, corpo))
                return {"postos": [{"name": "x.mp4", "out_start": 1,
                                    "out_end": 3}], "recusados": []}

        falso = _Cliente()
        ferramenta = next((f for f in F.FERRAMENTAS if f["name"] == "broll"), None)
        texto = ferramenta["_fn"](falso, {"projeto": projeto.id,
                                          "caminhos": ["C:/b/x.mp4"], "em": 1})\
            if ferramenta else ""
        check(ferramenta is not None
              and falso.pedidos == [(f"/api/projects/{projeto.id}/brolls",
                                     {"paths": ["C:/b/x.mp4"], "at": 1.0})]
              and "b-roll x.mp4" in texto,
              "o Claude na máquina dele também põe b-roll (ferramenta 'broll' "
              "do MCP, mesma rota)")

        # ---- no ARQUIVO -------------------------------------------------
        q = svc.load(projeto.id)
        check([w for w in svc.timeline_summary(q)["subtitles"]] == palavras_antes,
              "as legendas (a fala) são as mesmas de antes do b-roll")
        saida = Path(svc.export(q, Ctx(quiet=True),
                                {"filename": "com-broll.mp4", "overwrite": True,
                                 "output_dir": str(tmp)})["output"])

        def cor_em(t: float) -> tuple[float, float, float]:
            cru = subprocess.run(
                [FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", str(saida),
                 "-frames:v", "1", "-vf", "scale=32:18", "-f", "rawvideo",
                 "-pix_fmt", "rgb24", "-"], capture_output=True,
                check=True).stdout
            px = np.frombuffer(cru, np.uint8).reshape(-1, 3).astype(float)
            return tuple(round(float(v)) for v in px.mean(axis=0))

        esperado = {"vermelho": 0, "verde": 1, "azul": 2}
        for x in postos:
            meio = (x["out_start"] + x["out_end"]) / 2
            rgb = cor_em(meio)
            canal = esperado[Path(x["name"]).stem]
            outros = [v for i, v in enumerate(rgb) if i != canal]
            check(rgb[canal] > 180 and max(outros) < 80,
                  f"no arquivo, em {meio:.1f} s está o b-roll "
                  f"{Path(x['name']).stem} (RGB médio {rgb})")
        from editor.ffmpeg_utils import probe
        d_arquivo = float(probe(saida).duration)
        check(abs(d_arquivo - duracao) < 0.15,
              f"e o vídeo tem a MESMA duração do editado — b-roll cobre a "
              f"imagem, não empurra nada ({d_arquivo:.2f} s contra "
              f"{duracao:.2f} s)")
    finally:
        svc.previa_da_edicao = previa_real
        if projeto is not None:
            try:
                svc.delete_project(projeto.id)
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- as telas usam o caminho novo -----------------------------------
    frente = Path("frontend/src")
    editor = (frente / "components/Editor.tsx").read_text(encoding="utf-8")
    midia = (frente / "components/MediaPanel.tsx").read_text(encoding="utf-8")
    timeline = (frente / "components/Timeline.tsx").read_text(encoding="utf-8")
    check("'Escolher o b-roll (pode marcar vários)', true" in editor
          and "porBrolls(paths" in editor,
          "o '+ b-roll' do trilho abre a janela com seleção de VÁRIOS")
    check("await porBrolls([caminho])" in editor,
          "soltar um vídeo no trilho usa o mesmo caminho (acha o vão livre)")
    check("+ b-roll</button>" in midia and "api.brolls(" in midia,
          "e a aba Mídia tem o '+ b-roll' também")
    check("t.acao ?? t.label.toLowerCase()" in timeline,
          "o botão do trilho mostra a ação dele ('+ b-roll')")
    check("t.acao !== ''" in timeline,
          "e o trilho de vídeo não tem um '+ vídeo' que faria o mesmo que o "
          "'+ b-roll' com outro nome")


def testar_trocar_a_musica() -> None:
    """A música de fundo se troca em qualquer ponto — antes só se tirava.

    O relato: "coloquei e depois não consegui mais trocar". Na primeira
    tela, com uma música escolhida, a lista de guardadas e o botão sumiam e
    sobrava um "tirar" de 10 px no canto do rótulo. No editor, a aba Áudio
    só tinha volume e "remover trilha". E se a música falhasse ao entrar, a
    primeira tela engolia o erro — o vídeo saía sem trilha, calado.
    """
    import tempfile
    from pathlib import Path

    from editor import projects as svc
    from editor.server import app
    from tests.e2e import Ctx

    frente = Path("frontend/src/components")
    home = (frente / "Home.tsx").read_text(encoding="utf-8")
    audio = (frente / "AudioPanel.tsx").read_text(encoding="utf-8")
    editor = (frente / "Editor.tsx").read_text(encoding="utf-8")
    check("{musica ? 'trocar MP3…' : 'escolher MP3…'}" in home,
          "primeira tela: com música escolhida, o botão vira 'trocar MP3…' "
          "e continua à vista")
    check("'trocar por uma guardada…'" in home,
          "e a lista de guardadas continua lá, para trocar por outra")
    check("catch { /* sem trilha o vídeo sai igual */ }" not in home
          and "'A música não entrou'" in home,
          "se a música falhar ao entrar, ele é AVISADO (antes era engolido)")
    check("{trilha ? 'trocar música…' : 'pôr música…'}" in audio
          and 'data-secao-trilha="1"' in audio,
          "aba Áudio: pôr, trocar e tirar a música no mesmo lugar")
    check("<div key={trilha.media_id}>" in audio,
          "os campos de volume remontam ao trocar (não mostram os números "
          "da música anterior)")
    check("antes.media_id ? 'Música trocada'" in editor,
          "o '+ trilha' da linha do tempo troca e diz que trocou")

    # a troca pelo servidor mantém o que ele ajustou, só o arquivo muda
    tmp = Path(tempfile.mkdtemp(prefix="troca_"))
    projeto = None
    try:
        import subprocess

        from editor.config import FFMPEG
        from tests.speech import build_track, make_video

        install(["a musica troca"])
        amostras, _m, dur = build_track([("a musica troca", 0.6)])
        fonte = make_video(tmp / "v.mp4", amostras, dur, 320, 180, 30)
        musicas = []
        for i, f in enumerate((440, 660)):
            musicas.append(tmp / f"m{i}.wav")
            subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                            f"sine=frequency={f}:duration=4", str(musicas[-1])],
                           check=True)
        projeto = svc.create(str(fonte), "troca", "VSL")
        svc.analyze(svc.load(projeto.id), Ctx(quiet=True))
        svc.auto_edit(svc.load(projeto.id), Ctx(quiet=True))
        c = TestClient(app)
        m1 = c.post(f"/api/projects/{projeto.id}/media",
                    json={"path": str(musicas[0]), "kind": "audio"}).json()
        c.post(f"/api/projects/{projeto.id}/music",
               json={"media_id": m1["id"], "gain_db": -9, "ducking": False,
                     "duck_amount": 12, "fade_in": 1, "fade_out": 2,
                     "enabled": True, "out_start": 0.4})
        antes = svc.load(projeto.id).plan.music
        # o que a aba Áudio manda ao trocar: o objeto anterior + a mídia nova
        m2 = c.post(f"/api/projects/{projeto.id}/media",
                    json={"path": str(musicas[1]), "kind": "audio"}).json()
        c.post(f"/api/projects/{projeto.id}/music",
               json={**antes, "media_id": m2["id"], "enabled": True,
                     "muted": False})
        depois = svc.load(projeto.id).plan.music
        check(depois["media_id"] == m2["id"],
              "trocar põe a música NOVA no plano")
        check(depois["gain_db"] == -9 and depois["ducking"] is False
              and abs(depois["out_start"] - 0.4) < 1e-6,
              "e mantém o volume, o ducking e onde ela começa")
        c.post(f"/api/projects/{projeto.id}/music", json={})
        check(not svc.load(projeto.id).plan.music,
              "tirar a música tira de verdade")
    finally:
        if projeto is not None:
            try:
                svc.delete_project(projeto.id)
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(tmp, ignore_errors=True)


def _banco_falso(pasta: Path, clipe: Path) -> tuple[str, dict]:
    """Um Pexels e um Pixabay de mentira em 127.0.0.1, com o formato da API.

    Devolve o endereço e um registro do que chegou: cabeçalhos, parâmetros e
    quantos arquivos foram baixados — é assim que o teste prova que a chave
    foi no lugar certo e que a biblioteca reaproveita sem rede.
    """
    import http.server
    import threading
    from urllib.parse import parse_qs, urlparse

    registro: dict = {"pedidos": [], "downloads": 0, "falhar_pixabay": False}
    base_holder: dict = {}

    def arquivos(n: int) -> list[dict]:
        b = base_holder["base"]
        return [
            {"id": n * 10 + 1, "quality": "sd", "file_type": "video/mp4",
             "width": 360, "height": 640, "fps": 25, "link": f"{b}/arquivos/{n}_sd.mp4"},
            {"id": n * 10 + 2, "quality": "hd", "file_type": "video/mp4",
             "width": 1080, "height": 1920, "fps": 25, "link": f"{b}/arquivos/{n}_hd.mp4"},
            {"id": n * 10 + 3, "quality": "uhd", "file_type": "video/mp4",
             "width": 2160, "height": 3840, "fps": 25, "link": f"{b}/arquivos/{n}_4k.mp4"},
            {"id": n * 10 + 4, "quality": "hls", "file_type": "video/mp4",
             "width": None, "height": None, "fps": None,
             "link": f"{b}/arquivos/{n}.m3u8"},
        ]

    def video_pexels(n: int) -> dict:
        b = base_holder["base"]
        v = {"id": n, "width": 1080, "height": 1920, "duration": 7,
             "url": f"https://www.pexels.com/video/{n}/",
             "image": f"{b}/mini/{n}.jpg", "full_res": None, "tags": [],
             "user": {"id": 5, "name": f"Autora {n}",
                      "url": "https://www.pexels.com/@autora"},
             "video_files": arquivos(n),
             "video_pictures": [{"id": 1, "picture": f"{b}/mini/{n}.jpg", "nr": 0}]}
        if n == 666:
            # o banco mandando baixar de fora dele: tem que ser recusado
            for f in v["video_files"]:
                f["link"] = "https://golpe.example.com/x.mp4"
        return v

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: D401
            pass

        def _json(self, dados, codigo=200):
            corpo = json.dumps(dados).encode()
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            registro["pedidos"].append({"caminho": u.path, "params": q,
                                        "auth": self.headers.get("Authorization")})
            if u.path == "/videos/search":
                if self.headers.get("Authorization") != "chave-pexels-9999":
                    return self._json({"error": "unauthorized"}, 401)
                return self._json({"page": 1, "per_page": 15, "total_results": 3,
                                   "videos": [video_pexels(n) for n in (1, 2, 666)]})
            if u.path.startswith("/videos/videos/"):
                return self._json(video_pexels(int(u.path.rsplit("/", 1)[1])))
            if u.path == "/api/videos/":
                if q.get("key") != "chave-pixabay-8888":
                    return self._json({"erro": "[ERROR 400] Invalid or missing API key"}, 400)
                if registro["falhar_pixabay"]:
                    return self._json({"erro": "interno"}, 500)
                b = base_holder["base"]
                hits = [{"id": 70 + k, "pageURL": f"https://pixabay.com/videos/x-{70 + k}/",
                         "type": "film", "tags": "academia, treino", "duration": 9,
                         "user_id": 3, "user": "fotografo",
                         "videos": {
                             "large": {"url": "", "width": 0, "height": 0, "size": 0,
                                       "thumbnail": ""},
                             "medium": {"url": f"{b}/arquivos/p{k}_m.mp4",
                                        "width": w, "height": h, "size": 1000,
                                        "thumbnail": f"{b}/mini/p{k}.jpg"},
                             "small": {"url": f"{b}/arquivos/p{k}_s.mp4",
                                       "width": w // 2, "height": h // 2, "size": 500,
                                       "thumbnail": f"{b}/mini/p{k}.jpg"}}}
                        for k, (w, h) in enumerate([(1920, 1080), (1080, 1920)])]
                if q.get("id"):
                    hits = [x for x in hits if str(x["id"]) == q["id"]]
                return self._json({"total": 2, "totalHits": 2, "hits": hits})
            if u.path.startswith("/arquivos/"):
                registro["downloads"] += 1
                corpo = clipe.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)
                return
            self._json({"erro": "rota"}, 404)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    base_holder["base"] = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    registro["_srv"] = srv
    return base_holder["base"], registro


def testar_banco_de_broll() -> None:
    """O banco de b-roll grátis (Pexels e Pixabay), contra um banco falso.

    A rede daqui não alcança os dois, então o teste sobe um servidor com o
    MESMO formato da API deles e prova o que importa: a chave vai no lugar
    certo e nunca volta para a tela nem aparece em mensagem de erro, só a
    palavra da busca sai, o arquivo é escolhido no servidor (nunca por URL
    vinda da tela), baixa para a biblioteca local, é reaproveitado sem rede
    e entra como b-roll no vídeo. E o Claude pelo MCP faz o mesmo caminho.
    """
    import json as _json
    import subprocess
    import tempfile
    import time as _time
    from pathlib import Path

    from editor import banco, db
    from editor import projects as svc
    from editor.config import FFMPEG
    from editor.jobs import get_queue
    from editor.mcp import ferramentas as F
    from editor.mcp.cliente import Cliente
    from editor.server import app
    from tests.e2e import Ctx
    from tests.speech import build_track, make_video

    # ---- sem rede nenhuma ------------------------------------------------
    termos = banco.sugerir_termos(
        "Olha, eu comecei a academia e a academia mudou a minha rotina de treino")
    check(termos[:1] == ["academia"] and "rotina" in termos and "olha" not in termos,
          f"as palavras de busca saem da fala, sem as vazias ({termos})")
    item = {"arquivos": [{"url": "a", "largura": 2160, "altura": 3840, "tamanho": 0},
                         {"url": "b", "largura": 1080, "altura": 1920, "tamanho": 0},
                         {"url": "c", "largura": 540, "altura": 960, "tamanho": 0}]}
    check(banco.escolher_arquivo(item, 1080, 1920)["url"] == "b",
          "baixa o menor arquivo que ainda tem a nitidez do quadro — não o 4K")
    check(banco.escolher_arquivo(item, 2160, 3840)["url"] == "a"
          and banco.escolher_arquivo({"arquivos": item["arquivos"][2:]},
                                     1080, 1920)["url"] == "c",
          "e o maior que houver quando nenhum chega lá")

    tmp = Path(tempfile.mkdtemp(prefix="banco_"))
    projeto = None
    # chave de ambiente na máquina de quem roda o teste passaria por cima
    ambiente = {v: os.environ.pop(v, None) for v in banco.AMBIENTE.values()}
    base_antiga = (banco.URL_PEXELS, banco.URL_PIXABAY)
    chaves_antigas = {f: db.get_setting(banco.CHAVES[f], "") for f in banco.FONTES}
    previa_real = svc.previa_da_edicao
    svc.previa_da_edicao = lambda *a, **k: {"ok": True, "substituida": True}
    srv = None
    try:
        for f in banco.FONTES:
            db.set_setting(banco.CHAVES[f], "")
        cliente = TestClient(app)
        e = cliente.get("/api/banco/estado").json()
        check(e["alguma"] is False, "sem chave, a tela sabe que falta a chave")
        r = cliente.get("/api/banco/buscar", params={"q": "academia"})
        check(r.status_code == 400 and "chave" in r.json()["detail"],
              "e a busca sem chave diz o que falta, sem erro 500")

        clipe = tmp / "clipe.mp4"
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                        "color=c=0xff00ff:s=180x320:r=25:d=4", "-c:v", "libx264",
                        "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(clipe)],
                       check=True)
        base, reg = _banco_falso(tmp, clipe)
        srv = reg["_srv"]
        banco.URL_PEXELS = banco.URL_PIXABAY = base
        banco._cache_busca.clear()
        banco._itens.clear()

        e = cliente.post("/api/banco/chaves",
                         json={"pexels": "chave-pexels-9999",
                               "pixabay": "chave-pixabay-8888"}).json()
        texto_estado = _json.dumps(e)
        check(e["pexels"]["tem_chave"] and e["pixabay"]["tem_chave"]
              and e["pexels"]["final"] == "9999",
              "as chaves ficam guardadas, e a tela vê só o final")
        check("chave-pexels" not in texto_estado and "chave-pixabay" not in texto_estado,
              "a chave NUNCA volta por rota nenhuma")

        # ---- um projeto vertical com fala ---------------------------------
        frases = ["eu comecei a academia esse ano",
                  "a academia mudou a minha rotina",
                  "e o treino ficou facil de manter"]
        install(frases)
        amostras, _m, dur = build_track([(f, 0.7) for f in frases])
        fonte = make_video(tmp / "fala.mp4", amostras, dur, 180, 320, 30)
        projeto = svc.create(str(fonte), "banco", "VSL")
        svc.one_click(svc.load(projeto.id), Ctx(quiet=True))
        pid = projeto.id

        sug = cliente.get(f"/api/projects/{pid}/banco/sugestao",
                          params={"t": 1.0}).json()
        check(sug["orientacao"] == "portrait" and "academia" in sug["termos"],
              f"no cursor, a sugestão vem da FALA e na orientação do vídeo "
              f"({sug['termos']}, {sug['orientacao']})")

        r = cliente.get("/api/banco/buscar", params={"q": "academia", "pid": pid})
        corpo = r.json()
        itens = corpo["itens"]
        # a busca de "academia" (guardar a chave já fez uma busca de teste antes)
        ped_pexels = next(x for x in reg["pedidos"] if x["caminho"] == "/videos/search"
                          and x["params"].get("query") == "academia")
        ped_pixabay = next(x for x in reg["pedidos"] if x["caminho"] == "/api/videos/"
                           and x["params"].get("q") == "academia")
        check(r.status_code == 200 and len(itens) == 5,
              f"a busca junta os dois bancos ({len(itens)} vídeos)")
        check(ped_pexels["auth"] == "chave-pexels-9999"
              and ped_pexels["params"].get("orientation") == "portrait"
              and ped_pexels["params"].get("locale") == "pt-BR",
              "Pexels: chave no cabeçalho, orientação do vídeo e busca em português")
        check(ped_pixabay["params"].get("key") == "chave-pixabay-8888"
              and ped_pixabay["params"].get("lang") == "pt"
              and ped_pixabay["params"].get("q") == "academia",
              "Pixabay: chave na consulta (o único jeito que ele aceita), em português")
        check({x["fonte"] for x in itens[:3]} == {"pexels", "pixabay"},
              "os dois bancos aparecem já no começo da lista (intercalados)")
        check(itens[-1]["id"] == "pixabay:70",
              "o vídeo deitado vai para o FIM num projeto em pé")
        check(all("arquivos" not in x and x.get("previa") for x in itens),
              "a tela recebe a prévia, mas NUNCA a lista de onde baixar")
        check(all(x.get("autor") for x in itens),
              "e o nome do autor, para o crédito")
        n_pedidos = len(reg["pedidos"])
        cliente.get("/api/banco/buscar", params={"q": "academia", "pid": pid})
        check(len(reg["pedidos"]) == n_pedidos,
              "a mesma busca não vai à rede de novo (o Pixabay pede 24 h de cache)")

        # ---- a chave nunca aparece num erro --------------------------------
        banco._cache_busca.clear()
        reg["falhar_pixabay"] = True
        r = cliente.get("/api/banco/buscar", params={"q": "treino"})
        check(r.status_code == 200 and r.json()["itens"]
              and any("Pixabay" in a for a in r.json()["avisos"]),
              "um banco fora do ar não derruba o outro — vem o aviso")
        check("chave-pixabay" not in r.text, "e o aviso não carrega a chave")
        reg["falhar_pixabay"] = False
        banco.URL_PIXABAY = "http://127.0.0.1:9"
        banco._cache_busca.clear()
        r = cliente.get("/api/banco/buscar", params={"q": "corrida"})
        check("chave-pixabay" not in r.text and "sem conexão" in r.text,
              "sem conexão: a mensagem diz isso, sem a URL (que tem a chave)")
        banco.URL_PIXABAY = base

        # ---- usar: baixa e põe no vídeo -----------------------------------
        r = cliente.post(f"/api/projects/{pid}/banco/usar",
                         json={"ids": ["https://golpe.example.com/x.mp4"], "at": 0})
        check(r.status_code == 400, "id que não é do banco é recusado na porta")
        antes_dl = reg["downloads"]
        job = cliente.post(f"/api/projects/{pid}/banco/usar",
                           json={"ids": ["pexels:1", "pixabay:71", "pexels:666"],
                                 "at": 0.4, "duracao": 1.5,
                                 "termo": "academia"}).json()
        fim = _time.time() + 120
        while _time.time() < fim:
            j = next((x for x in cliente.get("/api/jobs",
                                             params={"project_id": pid}).json()
                      if x["id"] == job["id"]), {})
            if j.get("status") in ("ok", "erro", "cancelado"):
                break
            _time.sleep(0.3)
        check(j.get("status") == "ok", f"o download roda como trabalho de fundo "
                                       f"({j.get('status')}: {j.get('error')})")
        res = j.get("result") or {}
        postos = res.get("postos") or []
        check(len(postos) == 2, f"dois b-rolls do banco entraram no vídeo ({len(postos)})")
        check(any("fora dele" in x["motivo"] for x in res.get("recusados") or []),
              "o vídeo que mandava baixar de outro endereço foi RECUSADO")
        check(reg["downloads"] - antes_dl == 2, "e nada foi baixado dele")
        nomes = sorted(x["name"] for x in postos)
        check(any("_360x640" in n for n in nomes),
              f"do Pexels veio o menor arquivo que dá o quadro, não o 4K ({nomes})")
        biblioteca = cliente.get("/api/banco/baixados").json()
        check(len(biblioteca) == 2 and all(Path(b["path"]).exists() for b in biblioteca)
              and all(b["autor"] for b in biblioteca),
              "os dois ficam na biblioteca local, com o crédito do autor")
        check(all(Path(b["path"]).parent == banco.pasta() for b in biblioteca),
              "na pasta de dados, não no projeto — servem para os próximos vídeos")
        q = svc.load(pid)
        check(len(q.plan.cutaways) == 2 and all(c.audio != "mute"
                                                 for c in q.plan.active_clips),
              "como b-roll (cobre a imagem), com a fala por baixo")

        # ---- o Claude pelo MCP --------------------------------------------
        c = Cliente(transporte=cliente)
        texto = F.chamar(c, "buscar_broll", {"projeto": pid, "em": 1.0})
        check("academia" in texto and "pexels:1" in texto,
              "pelo MCP, a busca sugere pela fala e devolve os ids")
        antes_dl = reg["downloads"]
        t0 = _time.time()
        texto = F.chamar(c, "broll_do_banco",
                         {"projeto": pid, "ids": ["pexels:1"], "em": 4.0})
        levou = _time.time() - t0
        check("b-roll" in texto and "Autora 1" in texto,
              f"e põe no vídeo, com o crédito ({texto[:80]!r})")
        check(levou < 60, f"a ferramenta volta quando o trabalho acaba "
                          f"({levou:.1f} s) — antes ela esperava a hora inteira")
        check(reg["downloads"] == antes_dl,
              "o que já foi baixado é reaproveitado sem rede")

        # e uma falha de trabalho é contada como falha, não como sucesso
        job_ruim = get_queue().submit("teste-falha", pid,
                                      lambda ctx: (_ for _ in ()).throw(RuntimeError("quebrou")))
        fimj = c.esperar_job(pid, job_ruim.id, limite=30, passo=0.2)
        check(F._falhou(fimj) and "quebrou" in F._falhou(fimj),
              "um trabalho que dá erro é contado como ERRO pelo MCP")
    finally:
        svc.previa_da_edicao = previa_real
        for k, v in ambiente.items():
            if v is not None:
                os.environ[k] = v
        banco.URL_PEXELS, banco.URL_PIXABAY = base_antiga
        for f, v in chaves_antigas.items():
            db.set_setting(banco.CHAVES[f], v)
        if srv is not None:
            srv.shutdown()
        if projeto is not None:
            try:
                svc.delete_project(projeto.id)
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(banco.pasta(), ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- a tela ----------------------------------------------------------
    frente = Path("frontend/src/components")
    tela = (frente / "BancoBroll.tsx").read_text(encoding="utf-8")
    midia = (frente / "MediaPanel.tsx").read_text(encoding="utf-8")
    check("<BancoBroll" in midia, "o banco aparece na aba Mídia")
    check("api.bancoUsar(" in tela and "url" not in
          tela.split("api.bancoUsar(")[1].split(")")[0],
          "a tela manda IDs para baixar, nunca uma URL")
    check("Pexels" in tela and "Pixabay" in tela and "autor" in tela,
          "e mostra de onde vem e de quem é cada vídeo")


def testar_linha_do_tempo_com_varias_gravacoes() -> None:
    """Três gravações: todas aparecem e todas se editam na linha do tempo.

    O relato: "tem três vídeos nesse editor, só dá para editar o primeiro; os
    dois últimos não aparecem na timeline, mas eles geram". A linha do tempo
    desenhava o eixo do primeiro arquivo só. E por baixo dela as rotas de
    corte também só conheciam o primeiro: cortar no segundo encaixava a
    borda no envelope do primeiro (vale errado, em cima de palavra),
    arrastar a borda de um corte apagava o vermelho dos outros vídeos, e
    remover uma palavra do segundo vídeo dava erro 500.
    """
    import tempfile
    from pathlib import Path

    from editor import projects as svc
    from tests.e2e import Ctx
    from tests.speech import build_track, make_video

    tmp = Path(tempfile.mkdtemp(prefix="eixo_"))
    pid = None
    previa_real = svc.previa_da_edicao
    svc.previa_da_edicao = lambda *a, **k: {"ok": True, "substituida": True}
    try:
        frases = [["primeira tomada aqui", "com mais uma frase"],
                  ["segunda tomada agora", "e outra frase dela"],
                  ["terceira e ultima", "fechando o video"]]
        install([f for grupo in frases for f in grupo])
        tomadas = []
        for k, grupo in enumerate(frases):
            amostras, _m, dur = build_track([(f, 0.9) for f in grupo], seed=k + 3)
            tomadas.append(make_video(tmp / f"t{k}.mp4", amostras, dur, 320, 180, 30))
        p = svc.create(str(tomadas[0]), "eixo", "VSL")
        pid = p.id
        extras = [svc.add_media(pid, str(t), "video", papel="fonte")["id"]
                  for t in tomadas[1:]]
        svc.one_click(svc.load(pid), Ctx(quiet=True), fontes_extras=extras)
        c = TestClient(app)

        # ---- o eixo -------------------------------------------------------
        tl = c.get(f"/api/projects/{pid}").json()["timeline"]
        mont = tl.get("montagem") or []
        check([t["source"] for t in mont] == ["main", *extras],
              f"a linha do tempo tem as TRÊS gravações, na ordem da montagem "
              f"({len(mont)})")
        continuo = all(abs(mont[k + 1]["offset"] - (mont[k]["offset"] + mont[k]["duracao"]))
                       < 1e-3 for k in range(len(mont) - 1))
        check(continuo and mont[0]["offset"] == 0,
              "uma depois da outra, sem buraco nem sobreposição")
        total = sum(t["duracao"] for t in mont)
        check(abs(tl["duracao_gravada"] - total) < 0.01
              and tl["duracao_gravada"] > tl["source_duration"] + 1,
              f"o total gravado é a soma das três ({tl['duracao_gravada']:.1f} s), "
              f"não só a primeira ({tl['source_duration']:.1f} s) — era isso que "
              f"dava '-951% mais curto'")
        env = c.get(f"/api/projects/{pid}/envelope").json()
        check(len(env.get("trechos") or []) == 3
              and abs(env["duration"] - total) < 0.05,
              f"a onda é das três gravações emendadas ({env['duration']:.2f} s "
              f"de {total:.2f} s)")
        cenas_fontes = {z.get("source") for z in tl["zoom_scenes"]}
        check(set(extras) <= cenas_fontes,
              "os enquadramentos das outras gravações também vão para a linha do tempo")

        def blocos(tl_, fonte):
            return [b for b in tl_["blocks"] if b["source"] == fonte]

        def ordem(tl_):
            vistas: list[str] = []
            for b in tl_["blocks"]:
                if not vistas or vistas[-1] != b["source"]:
                    vistas.append(b["source"])
            return vistas

        # ---- cortar DENTRO da segunda gravação ------------------------------
        b2 = blocos(tl, extras[0])
        alvo = max(b2, key=lambda b: b["out_end"] - b["out_start"])
        meio = (alvo["out_start"] + alvo["out_end"]) / 2
        cortes_c_antes = [r for r in tl["removed"] if r.get("source") == extras[1]]
        r = c.post(f"/api/projects/{pid}/ops/delete-range",
                   json={"start": meio - 0.15, "end": meio + 0.15})
        corpo = r.json()
        check(r.status_code == 200 and corpo.get("source") == extras[0],
              f"cortar no segundo vídeo corta o SEGUNDO vídeo ({r.status_code})")
        novos = [x for x in corpo["timeline"]["removed"]
                 if x.get("source") == extras[0] and x.get("reason") == "manual"]
        check(len(novos) == 1, "e o vermelho do corte diz de qual gravação é")
        # o encaixe foi no envelope DELE: a borda cai num ponto de silêncio
        # daquele áudio (ou numa borda de palavra dele), nunca dentro de uma
        # palavra dele
        palavras_b = svc.load(pid).words_de(extras[0])
        dentro = [w["text"] for w in palavras_b
                  for borda in (novos[0]["start"], novos[0]["end"])
                  if w["start"] + 0.02 < borda < w["end"] - 0.02]
        check(not dentro, f"a borda do corte não cai no meio de palavra do "
                          f"segundo vídeo ({dentro})")
        check(ordem(corpo["timeline"]) == ["main", *extras],
              "e a ordem das gravações continua a mesma")

        # ---- arrastar a borda de um corte não apaga o vermelho dos outros ----
        reg = novos[0]
        r = c.post(f"/api/projects/{pid}/ops/resize-removed",
                   json={"start": reg["start"], "end": reg["end"],
                         "new_start": reg["start"] - 0.05, "new_end": reg["end"],
                         "source": extras[0]})
        tl2 = r.json().get("timeline") or {}
        cortes_c_depois = [x for x in tl2.get("removed", [])
                           if x.get("source") == extras[1]]
        check(r.status_code == 200 and len(cortes_c_depois) == len(cortes_c_antes),
              f"arrastar a borda de um corte no vídeo 2 mantém os cortes do vídeo 3 "
              f"({len(cortes_c_depois)} de {len(cortes_c_antes)})")
        principais = [x for x in tl2.get("removed", []) if x.get("source", "main") == "main"]
        check(len(principais) >= 1, "e os do vídeo 1 também")

        # ---- devolver o trecho ----------------------------------------------
        reg2 = next(x for x in tl2["removed"] if x.get("source") == extras[0]
                    and abs(x["end"] - reg["end"]) < 0.05)
        r = c.post(f"/api/projects/{pid}/ops/restore-range",
                   json={"start": reg2["start"], "end": reg2["end"],
                         "source": extras[0]})
        check(r.status_code == 200 and not any(
            x.get("source") == extras[0] and abs(x["end"] - reg["end"]) < 0.05
            for x in r.json()["timeline"]["removed"]),
            "devolver o trecho devolve NO vídeo 2")

        # ---- seleção atravessando a emenda ----------------------------------
        tl3 = r.json()["timeline"]
        fim_1 = max(b["out_end"] for b in blocos(tl3, "main"))
        r = c.post(f"/api/projects/{pid}/ops/delete-range",
                   json={"start": fim_1 - 0.25, "end": fim_1 + 0.25})
        partes = r.json().get("partes") or []
        check(r.status_code == 200
              and [x["source"] for x in partes] == ["main", extras[0]],
              f"uma seleção que atravessa a emenda corta um pedaço de CADA vídeo "
              f"({[x['source'] for x in partes]})")

        # ---- remover uma palavra do terceiro vídeo pelo texto ----------------
        q = svc.load(pid)
        tirado = set(q.analysis.get("removed_word_ids", []))
        w3 = next(w for w in q.words_de(extras[1]) if w["i"] not in tirado)
        r = c.post(f"/api/projects/{pid}/ops/remove-words", json={"word_ids": [w3["i"]]})
        check(r.status_code == 200 and r.json().get("ok"),
              f"apagar uma palavra do vídeo 3 pelo texto funciona "
              f"(antes: erro 500) ({r.status_code})")
        check(any(x.get("source") == extras[1] and x.get("reason") == "texto"
                  for x in r.json()["timeline"]["removed"]),
              "e o corte é no vídeo 3")
        r = c.post(f"/api/projects/{pid}/ops/restore-words", json={"word_ids": [w3["i"]]})
        check(r.status_code == 200
              and w3["i"] not in svc.load(pid).analysis.get("removed_word_ids", []),
              "e recuperar a palavra também")
        check(ordem(r.json()["timeline"]) == ["main", *extras],
              "depois de tudo isso, as três gravações seguem na mesma ordem")
    finally:
        svc.previa_da_edicao = previa_real
        if pid:
            try:
                svc.delete_project(pid)
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- a tela -----------------------------------------------------------
    frente = Path("frontend/src")
    tl_tsx = (frente / "components/Timeline.tsx").read_text(encoding="utf-8")
    texto = (frente / "components/TextEditor.tsx").read_text(encoding="utf-8")
    check("montarEixo(view" in tl_tsx and "b.source !== 'main'" not in tl_tsx,
          "a linha do tempo desenha no eixo das gravações, sem filtrar só o vídeo 1")
    check("palavrasDaMontagem" in (frente / "components/Editor.tsx").read_text(encoding="utf-8")
          and "words.slice(Math.min(a, b)" in texto,
          "o painel Texto tem as palavras das três gravações e seleciona por posição")


def testar_broll_automatico() -> None:
    """B-roll automático, a biblioteca de b-roll dele e o "substituir".

    Pedido: "quero que a IA sugira onde dá para colocar b-roll e o tamanho, e
    coloque sozinho o b-roll grátis; antes de gerar, escolher se quero
    automático ou não e a frequência; quando gerar, o vídeo pronto; e poder
    subir b-rolls meus para a biblioteca." E: "se eu quiser substituir, é só
    clicar em cima dele e escolher outro".
    """
    import subprocess
    import tempfile
    from pathlib import Path

    from editor import banco, broll_auto, db
    from editor import projects as svc
    from editor.ai import gemini
    from editor.config import FFMPEG
    from editor.mcp import ferramentas as F
    from editor.mcp.cliente import Cliente
    from tests.e2e import Ctx
    from tests.speech import build_track, make_video

    tmp = Path(tempfile.mkdtemp(prefix="brollauto_"))
    pid = None
    srv = None
    base_antiga = (banco.URL_PEXELS, banco.URL_PIXABAY)
    chaves_antigas = {f: db.get_setting(banco.CHAVES[f], "") for f in banco.FONTES}
    gemini_antiga = db.get_setting("gemini_api_key", "")
    ambiente = {v: os.environ.pop(v, None)
                for v in (*banco.AMBIENTE.values(), "EDITOR_GEMINI_KEY")}
    previa_real = svc.previa_da_edicao
    svc.previa_da_edicao = lambda *a, **k: {"ok": True, "substituida": True}
    try:
        db.set_setting("gemini_api_key", "")
        c = TestClient(app)

        # ---- a biblioteca dele ----------------------------------------------
        meu = tmp / "meu treino.mp4"
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                        "color=c=0x00aa44:s=320x568:r=25:d=6", "-c:v", "libx264",
                        "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(meu)], check=True)
        r = c.post("/api/banco/enviar", json={"paths": [str(meu), str(tmp / "nao.mp4")],
                                              "palavras": "academia treino halteres"})
        corpo = r.json()
        check(r.status_code == 200 and len(corpo["guardados"]) == 1
              and corpo["recusados"],
              "mandar um vídeo meu para a biblioteca funciona (e o que não existe "
              "volta com o motivo)")
        item = corpo["guardados"][0]
        check(Path(item["path"]).parent == banco.pasta() and Path(item["path"]).exists()
              and meu.exists(),
              "a biblioteca guarda uma CÓPIA na pasta de dados; o original fica onde está")
        bib = c.get("/api/banco/baixados").json()
        meus = [b for b in bib if b["fonte"] == "meu"]
        check(len(meus) == 1 and meus[0]["miniatura"].startswith("/api/banco/arquivo/")
              and c.get(meus[0]["miniatura"]).status_code == 200,
              "com miniatura LOCAL, que abre sem internet")
        check(c.get(meus[0]["video"], headers={"Range": "bytes=0-99"}).status_code in (200, 206),
              "e o vídeo da biblioteca toca no navegador (para escolher o trecho)")
        check(c.get("/api/banco/arquivo/..%2F..%2Fsegredo").status_code in (400, 404),
              "a rota da biblioteca não sai da pasta dela")
        r = c.post("/api/banco/enviar", json={"paths": [str(meu)]})
        check(r.json()["guardados"][0].get("repetido")
              and len([b for b in c.get("/api/banco/baixados").json()
                       if b["fonte"] == "meu"]) == 1,
              "mandar o mesmo vídeo de novo não duplica")

        # ---- a regra do programa (sem Gemini) --------------------------------
        falas = [{"start": t, "end": t + 2.0, "text": f"a academia mudou meu treino {t}"}
                 for t in (0.5, 3.0, 6.0, 9.0, 12.0, 15.0, 18.0)]
        slots = broll_auto.pela_regra(falas, 21.0, "muito")
        check(slots and slots[0]["inicio"] >= broll_auto.LIVRE_NO_COMECO
              and all(s["fim"] <= 21.0 - broll_auto.LIVRE_NO_FIM + 1e-6 for s in slots),
              f"sem IA, a regra deixa o gancho e o fim livres ({[(s['inicio'], s['fim']) for s in slots]})")
        check(all(b["inicio"] - a["inicio"] >= broll_auto.FREQUENCIAS["muito"] - 0.01
                  for a, b in zip(slots, slots[1:])),
              "e respeita a frequência pedida")
        check(len(broll_auto.pela_regra(falas, 21.0, "pouco")) < len(slots),
              "'pouco' põe menos b-roll que 'muito'")
        check(slots[0]["busca"] == "academia",
              f"a busca sai das palavras da fala ({slots[0]['busca']})")
        t = banco.sugerir_termos
        check(t("a porta de casa ficou aberta")[0] == "porta"
              and t("o celular tocou de madrugada")[0] == "celular"
              and t("eu comecei a academia esse ano")[0] == "academia",
              f"a busca é a COISA da frase, não o verbo nem o adjetivo "
              f"({t('a porta de casa ficou aberta')[:2]}, "
              f"{t('eu comecei a academia esse ano')[:2]})")

        # ---- a IA: o que ela devolve passa pela trava -------------------------
        real = (gemini.chave_guardada, gemini.escolher_modelo, gemini.gerar_json)
        try:
            gemini.escolher_modelo = lambda chave, m: {"id": "falso", "saida": 4096}
            gemini.gerar_json = lambda *a, **k: {"brolls": [
                {"inicio": 0.5, "fim": 3.0, "busca": "casa"},           # no gancho
                {"inicio": 4.0, "fim": 7.0, "busca": "casa de praia"},
                {"inicio": 5.0, "fim": 8.0, "busca": "ladrão"},          # sobrepõe
                {"inicio": 10.0, "fim": 20.0, "busca": "cadeado"},       # longo demais
                {"inicio": 14.0, "fim": 16.0, "busca": ""},              # sem busca
            ]}
            got = broll_auto.pela_ia("x", "", falas, 21.0, "muito")
        finally:
            gemini.chave_guardada, gemini.escolher_modelo, gemini.gerar_json = real
        check(all(g["inicio"] >= 2.0 for g in got)
              and all(b["inicio"] >= a["fim"] for a, b in zip(got, got[1:]))
              and all(g["fim"] - g["inicio"] <= broll_auto.MAX_DUR + 1e-6 for g in got)
              and all(g["busca"] for g in got),
              f"o que a IA sugere passa pela trava: sem gancho, sem sobrepor, "
              f"no máximo {broll_auto.MAX_DUR:.0f} s, sempre com busca "
              f"({[(g['inicio'], g['fim'], g['busca']) for g in got]})")

        # ---- o clique único com b-roll automático -----------------------------
        clipe = tmp / "banco.mp4"
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                        "color=c=0xff00ff:s=180x320:r=25:d=8", "-c:v", "libx264",
                        "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(clipe)], check=True)
        base, reg = _banco_falso(tmp, clipe)
        srv = reg["_srv"]
        banco.URL_PEXELS = banco.URL_PIXABAY = base
        banco._cache_busca.clear()
        banco._itens.clear()
        # ---- as chaves são TESTADAS ao guardar --------------------------------
        e = c.post("/api/banco/chaves", json={"pexels": "chave-errada-1234"}).json()
        check(e["pexels"]["funciona"] is False and "recusou a chave" in e["pexels"]["aviso"],
              f"uma chave errada é apontada NA HORA de guardar, não no vídeo pronto "
              f"({e['pexels']['aviso']!r})")
        e = c.post("/api/banco/chaves", json={"pexels": "chave-pexels-9999",
                                              "pixabay": "chave-pixabay-8888"}).json()
        check(e["pexels"]["funciona"] is True and e["pixabay"]["funciona"] is True,
              "e as certas aparecem como funcionando (Pexels ✓, Pixabay ✓)")
        check("chave-pexels" not in json.dumps(e) and "chave-pixabay" not in json.dumps(e),
              "o teste também não devolve a chave")

        frases = ["olha so o que aconteceu comigo", "presta atencao nisso",
                  "eu comecei a academia esse ano",
                  "a porta de casa ficou aberta", "o celular tocou de madrugada",
                  "a academia virou rotina", "o carro ficou na garagem",
                  "meu cachorro latiu a noite toda", "a cozinha estava uma bagunca",
                  "e foi assim que mudou tudo"]
        install(frases)
        amostras, _m, dur = build_track([(f, 0.8) for f in frases])
        fonte = make_video(tmp / "fala.mp4", amostras, dur, 180, 320, 30)
        projeto = svc.create(str(fonte), "brollauto", "VSL")
        pid = projeto.id
        r = c.post(f"/api/projects/{pid}/params",
                   json={"broll": {"auto": True, "frequencia": "muito", "fonte": "banco"}})
        check(r.json()["plan"]["broll"] == {"auto": True, "frequencia": "muito",
                                             "fonte": "banco"},
              "a primeira tela grava 'b-roll automático, muito, do Pexels e Pixabay' no plano")
        res = svc.one_click(svc.load(pid), Ctx(quiet=True))
        q = svc.load(pid)
        autos = [k for k in q.plan.cutaways if k.origem == "auto"]
        dur_saida = svc.duracao_de_saida(q)
        check(len(autos) >= 2 and (res.get("broll") or {}).get("postos"),
              f"o clique único já entrega o vídeo com b-roll ({len(autos)} postos "
              f"num vídeo de {dur_saida:.1f} s)")
        check(all(k.out_start >= broll_auto.LIVRE_NO_COMECO - 1e-6 for k in autos)
              and all(k.out_end <= dur_saida - broll_auto.LIVRE_NO_FIM + 1e-3 for k in autos),
              "sem cobrir o gancho nem o fim")
        ordenados = sorted(autos, key=lambda k: k.out_start)
        check(all(b.out_start >= a.out_end - 1e-6 for a, b in zip(ordenados, ordenados[1:])),
              "nenhum em cima do outro")
        nomes = {m["id"]: m["path"] for m in svc.list_media(pid)}
        check(all(Path(nomes[k.media_id]).name.startswith(("pexels", "pixabay"))
                  for k in autos),
              "o vídeo automático pega o b-roll do PEXELS e do PIXABAY (as chaves "
              "grátis dele) — mesmo com um vídeo da biblioteca que casaria com a fala")
        por_fonte = (q.plan.broll.get("ultima") or {}).get("por_fonte") or {}
        check(sum(por_fonte.values()) == len(autos) and set(por_fonte) <= {"pexels", "pixabay"},
              f"e o plano conta de onde veio cada um ({por_fonte})")
        pedidos_busca = [x for x in reg["pedidos"] if x["caminho"] in ("/videos/search", "/api/videos/")
                         and not x["params"].get("id")]
        check(any(x["caminho"] == "/videos/search" for x in pedidos_busca)
              and any(x["caminho"] == "/api/videos/" for x in pedidos_busca),
              "busca nos DOIS bancos")

        # ---- vídeo novo antes do repetido --------------------------------------
        ja = banco.ids_baixados()
        novo_item = broll_auto.do_banco([("qualquer", "pt")], "portrait", set(), 1.0, 180, 320)
        check(novo_item is not None and novo_item["id"] not in ja,
              f"entre os achados, o que ainda não foi usado em outro vídeo vem antes "
              f"({novo_item and novo_item['id']} fora de {sorted(ja)})")

        # ---- com a IA: a busca vai em inglês primeiro --------------------------
        real = (gemini.escolher_modelo, gemini.gerar_json)
        db.set_setting("gemini_api_key", "chave-gemini-falsa")
        try:
            gemini.escolher_modelo = lambda chave, m: {"id": "falso", "saida": 4096}
            gemini.gerar_json = lambda *a, **k: {"brolls": [
                {"inicio": 3.0, "fim": 6.0, "busca": "casa de praia",
                 "busca_en": "beach house", "alternativas": ["praia"]}]}
            banco._cache_busca.clear()
            antes = len(reg["pedidos"])
            r_ia = broll_auto.aplicar(svc.load(pid), Ctx(quiet=True), "muito")
        finally:
            gemini.escolher_modelo, gemini.gerar_json = real
            db.set_setting("gemini_api_key", "")
        primeira = next((x for x in reg["pedidos"][antes:] if x["caminho"] == "/videos/search"), {})
        check(r_ia["quem"] == "ia" and primeira.get("params", {}).get("query") == "beach house"
              and primeira["params"].get("locale") == "en-US",
              f"com a IA, o banco é buscado EM INGLÊS primeiro, que é onde ele acha mais "
              f"({primeira.get('params')})")

        # ---- "minha biblioteca primeiro" -----------------------------------------
        c.post(f"/api/projects/{pid}/params", json={"broll": {"fonte": "biblioteca"}})
        r_bib = broll_auto.aplicar(svc.load(pid), Ctx(quiet=True), "muito")
        q = svc.load(pid)
        nomes = {m["id"]: m["path"] for m in svc.list_media(pid)}
        da_bib = [k for k in q.plan.cutaways if k.origem == "auto"
                  and Path(nomes[k.media_id]).name.startswith("meu_")]
        check(r_bib["fonte"] == "biblioteca" and bool(da_bib) and da_bib[0].termo == "academia",
              "com 'minha biblioteca primeiro', onde a fala diz 'academia' entra o vídeo "
              "MEU (pela palavra-chave), e o banco completa")
        c.post(f"/api/projects/{pid}/params", json={"broll": {"fonte": "banco"}})
        check(all(c2.audio != "mute" for c2 in q.plan.active_clips),
              "a fala continua com som por baixo")
        check((svc.load(pid).plan.broll.get("ultima") or {}).get("quem") == "regra",
              "sem chave do Gemini, quem escolheu os pontos foi a regra (e o plano diz isso)")

        # ---- um posto à mão sobrevive ao "refazer" ----------------------------
        livre = next((t for t in (dur_saida - 1.2,) if t > 0), 0)
        manual_mid = svc.add_media(pid, str(clipe), "video")["id"]
        c.post(f"/api/projects/{pid}/cutaways",
               json={"media_id": manual_mid, "out_start": livre - 0.8, "out_end": livre})
        n_manual = len([k for k in svc.load(pid).plan.cutaways if k.origem != "auto"])
        job = c.post(f"/api/projects/{pid}/broll-auto", json={"frequencia": "pouco"}).json()
        import time as _t
        fim = _t.time() + 120
        while _t.time() < fim:
            j = next(x for x in c.get("/api/jobs", params={"project_id": pid}).json()
                     if x["id"] == job["id"])
            if j["status"] in ("ok", "erro", "cancelado"):
                break
            _t.sleep(0.3)
        q = svc.load(pid)
        check(j["status"] == "ok"
              and len([k for k in q.plan.cutaways if k.origem != "auto"]) == n_manual,
              f"refazer o automático troca só os automáticos; o posto à mão fica "
              f"({j['status']}: {j.get('error')})")
        check(len([k for k in q.plan.cutaways if k.origem == "auto"]) <= len(autos),
              "e 'pouco' põe menos que 'muito'")

        # ---- substituir: outro vídeo no mesmo lugar ---------------------------
        alvo = next(k for k in q.plan.cutaways if k.origem == "auto")
        r = c.put(f"/api/projects/{pid}/cutaways/{alvo.id}",
                  json={"media_id": manual_mid, "media_start": 1.5})
        novo = r.json().get("cutaway") or {}
        check(r.status_code == 200 and novo.get("media_id") == manual_mid
              and abs(novo["out_start"] - alvo.out_start) < 1e-6
              and abs(novo["media_start"] - 1.5) < 1e-6 and novo.get("origem") == "",
              "substituir põe OUTRO vídeo no mesmo lugar, do trecho escolhido, e "
              "ele deixa de ser 'automático'")

        # ---- tirar os automáticos ----------------------------------------------
        r = c.post(f"/api/projects/{pid}/broll-auto/tirar")
        q = svc.load(pid)
        check(r.status_code == 200 and not [k for k in q.plan.cutaways if k.origem == "auto"]
              and len(q.plan.cutaways) == n_manual + 1,
              "'tirar os automáticos' tira só eles")

        # ---- pelo MCP ---------------------------------------------------------
        texto = F.chamar(Cliente(transporte=c), "broll_automatico",
                         {"projeto": pid, "frequencia": "medio"})
        check("b-roll" in texto and "regra do programa" in texto,
              f"o Claude na máquina dele também põe b-roll automático ({texto[:60]!r})")

        # ---- a tela ------------------------------------------------------------
        frente = Path("frontend/src/components")
        tl_tsx = (frente / "Timeline.tsx").read_text(encoding="utf-8")
        insp = (frente / "BrollInspector.tsx").read_text(encoding="utf-8")
        home = (frente / "Home.tsx").read_text(encoding="utf-8")
        check("props.onSelectItem?.(seg.kind, seg.id)" in tl_tsx
              and "<BrollInspector" in (frente / "Editor.tsx").read_text(encoding="utf-8"),
              "clicar (sem arrastar) num b-roll no trilho abre o painel dele")
        check(all(k in insp for k in ('data-campo="inicio"', 'data-campo="dura"',
                                      'data-campo="entra"', "substituir…", "do computador…",
                                      "banco grátis", "api.bancoSubstituir(")),
              "o painel tem começo, duração, o trecho do b-roll, e substituir pela "
              "biblioteca, pelo banco ou por um arquivo do computador")
        check("broll: { auto: brollAuto !== 'nao'" in home
              and "sem b-roll automático" in home and "muito (1 a cada ~7 s)" in home,
              "a primeira tela escolhe b-roll automático ou não, e a frequência")
        check("fonte: brollFonte" in home and "vídeos do Pexels e Pixabay (grátis)" in home
              and 'data-chaves-estado="1"' in home,
              "e de onde vem o vídeo (Pexels e Pixabay por padrão), com o estado de cada chave")
        check("+ enviar vídeos meus" in (frente / "BancoBroll.tsx").read_text(encoding="utf-8")
              and "+ meus b-rolls na biblioteca" in home,
              "e dá para mandar os b-rolls dele para a biblioteca (no editor e na primeira tela)")
    finally:
        svc.previa_da_edicao = previa_real
        banco.URL_PEXELS, banco.URL_PIXABAY = base_antiga
        for f, v in chaves_antigas.items():
            db.set_setting(banco.CHAVES[f], v)
        db.set_setting("gemini_api_key", gemini_antiga)
        for k, v in ambiente.items():
            if v is not None:
                os.environ[k] = v
        if srv is not None:
            srv.shutdown()
        if pid:
            try:
                svc.delete_project(pid)
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(banco.pasta(), ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    install(["frase %d" % i for i in range(20)])
    sys.exit(main())
