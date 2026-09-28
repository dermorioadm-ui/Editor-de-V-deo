"""O MCP, exercitado de ponta a ponta contra o editor de verdade.

Não abre socket: o transporte do cliente cai no TestClient do FastAPI, que é o
mesmo app que a suíte inteira usa. O que se prova aqui é o que costuma faltar
num servidor MCP — que a ferramenta MUDE alguma coisa. Uma ferramenta que
responde bonito e não mexe no projeto é o defeito clássico, e ele não aparece
em nenhum teste de protocolo.

    python -m tests.mcp
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from editor import projects as svc
from editor.audio.envelope import compute_envelope
from editor.config import FFMPEG
from editor.ffmpeg_utils import extract_wav, read_wav_mono
from editor.mcp import ferramentas as F
from editor.mcp.__main__ import servir
from editor.mcp.cliente import Cliente
from editor.models import Clip
from editor.server import app
from tests.fake_whisper import install
from tests.synth import build, write_video

# a voz de estúdio (rede de IA) só roda no teste dela
os.environ.setdefault("SHARKCUT_VOZ_IA", "0")
# a prévia automática do servidor (threads por plano gravado) fica de fora
os.environ.setdefault("SHARKCUT_PREVIA_AUTO", "0")

FALHAS: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  OK    " if cond else "  FALHA ") + label)
    if not cond:
        FALHAS.append(label)


def semear(cliente: Cliente, tmp: Path) -> str:
    """Um projeto pronto para editar, sem passar pelo clique único (que é lento)."""
    spans, t = [], 0.5
    for k in range(10):
        spans.append((round(t, 3), round(t + 0.45, 3)))
        t += 0.45 + (0.8 if k % 3 == 2 else 0.15)
    dur = t + 0.6
    src = write_video(tmp / "fonte.mp4", build(spans, dur, noise=0.001), dur,
                      180, 320, 30)
    p = svc.create(str(src), "mcp", "VSL")
    extract_wav(src, p.wav, 16000, 1)
    amostras, sr = read_wav_mono(p.wav)
    env = compute_envelope(amostras, sr)
    np.save(p.envelope_file, env.db)
    svc._envelope_cache[p.id] = env
    texto = "alfa bravo charlie delta eco fox golf hotel india julia".split()
    palavras = [{"i": i, "start": a, "end": b, "text": texto[i], "prob": 0.95}
                for i, (a, b) in enumerate(spans)]
    p.analysis = {"duration": dur, "words": palavras, "claps": [], "takes": [],
                  "fillers": [], "manual_removed_word_ids": [],
                  "envelope": {"hop": env.hop, "sample_rate": sr,
                               "noise_floor": env.noise_floor,
                               "duration": env.duration}}
    p.save_analysis()
    p.plan.clips = [Clip(src_start=0.0, src_end=dur)]
    p.save_plan()
    return p.id


def _jpeg_para_rgb(dados: bytes) -> np.ndarray:
    r = subprocess.run([FFMPEG, "-v", "error", "-i", "-", "-f", "rawvideo",
                        "-pix_fmt", "rgb24", "-"], input=dados, capture_output=True)
    pr = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                         "stream=width,height", "-of", "csv=p=0", "-"],
                        input=dados, capture_output=True)
    w, h = (int(x) for x in pr.stdout.decode().strip().split(",")[:2])
    return np.frombuffer(r.stdout, np.uint8).reshape(h, w, 3)


def testar_pos_edicao(cliente: Cliente, tc: TestClient, pid: str) -> None:
    """O Claude como editor: roteiro, gráficos, transição, camada, olhos.

    Pedido: "quero colocar você como editor tirando o Gemini da frente... você
    entraria numa pós-edição entregando o que o Sharkcut não entrega": telas
    didáticas, separação por tópicos, transições, desfoques, profundidade.
    """
    import base64

    print("\n-- pós-edição pelo MCP")
    # garante duas emendas: um corte no meio parte o vídeo em blocos
    F.chamar(cliente, "cortar", {"projeto": pid, "palavras": [6]})
    texto = F.chamar(cliente, "pos_contexto", {"projeto": pid})
    check("FALA" in texto and "alfa" in texto and "BLOCOS" in texto,
          "pos_contexto dá o roteiro: fala com tempo do vídeo final e os blocos")
    check("legenda" in texto,
          "e diz onde a legenda mora (para o gráfico não cair em cima dela)")

    texto = F.chamar(cliente, "grafico", {
        "projeto": pid, "tipo": "titulo", "inicio": 0.3, "fim": 2.4,
        "texto": "O GANCHO", "estilo": "claro", "y": 0.2, "entrada": "3d"})
    plano = svc.load(pid).plan
    check(len(plano.graficos) == 1 and plano.graficos[0].origem == "claude"
          and plano.graficos[0].entrada == "3d",
          f"grafico põe um título de verdade no plano ({texto})")
    check(plano.graficos[0].estilo == "vidro" and "trocado por vidro" in texto,
          "o cartão 'claro' do Claude por cima da pessoa vira vidro, e ele é avisado")
    gid = plano.graficos[0].id
    F.chamar(cliente, "grafico", {"projeto": pid, "id": gid, "texto": "NOVO"})
    check(svc.load(pid).plan.graficos[0].texto == "NOVO"
          and svc.load(pid).plan.graficos[0].entrada == "3d",
          "com id, muda só o que veio (o resto fica)")
    F.chamar(cliente, "grafico", {
        "projeto": pid, "tipo": "lista", "inicio": 2.5, "fim": 5.0,
        "texto": "3 passos", "itens": ["um", "dois", "três"], "itens_em": [0.2, 0.9]})
    lista = svc.load(pid).plan.graficos[-1]
    check(lista.itens[0] == {"texto": "um", "em": 0.2} and lista.itens[2] == "três",
          "lista casa cada item com o segundo em que ele é falado")
    texto = F.chamar(cliente, "grafico", {"projeto": pid, "tipo": "numero",
                                          "inicio": 99999, "numero": 1500})
    ultimo = svc.load(pid).plan.graficos[-1]
    dur = svc.duracao_de_saida(svc.load(pid))
    check(ultimo.out_end <= dur + 1e-6 and ultimo.out_start < ultimo.out_end,
          "tempo fora do vídeo é puxado para dentro (não some, não quebra)")

    tl = svc.timeline_summary(svc.load(pid))
    blocos = tl["blocks"]
    if len(blocos) >= 2:
        texto = F.chamar(cliente, "transicao", {"projeto": pid, "tipo": "flash",
                                                "tempo": blocos[1]["out_start"] + 0.05})
        xs = svc.load(pid).plan.transicoes
        check(len(xs) == 1 and xs[0].clip_id == blocos[1]["id"],
              f"transição pelo tempo cai na emenda mais perto ({texto})")
        F.chamar(cliente, "transicao", {"projeto": pid, "tipo": "zoom",
                                        "bloco": blocos[1]["id"]})
        xs = svc.load(pid).plan.transicoes
        check(len(xs) == 1 and xs[0].tipo == "zoom",
              "uma transição por emenda: a nova substitui")
        emendas = [x for x in svc.timeline_summary(svc.load(pid))["transicoes"]]
        check(emendas and abs(emendas[0]["emenda"] - blocos[1]["out_start"]) < 1e-6,
              "o trilho recebe a transição já na posição da emenda")
        # um corte DENTRO do bloco que entra troca o id dele; a transição
        # reencontra o bloco pela âncora na fonte e continua na mesma emenda
        bloco = blocos[-1]
        F.chamar(cliente, "transicao", {"projeto": pid, "tipo": "glitch",
                                        "bloco": bloco["id"]})
        palavra = next((w for w in svc.load(pid).analysis["words"]
                        if bloco["src_start"] + 0.3 < w["start"] < bloco["src_end"]), None)
        check(palavra is not None, "há palavra no meio do último bloco para cortar")
        if palavra is not None:
            F.chamar(cliente, "cortar", {"projeto": pid, "palavras": [palavra["i"]]})
            depois = svc.timeline_summary(svc.load(pid))
            ids = {b["id"] for b in depois["blocks"]}
            x = next(t for t in depois["transicoes"] if t["tipo"] == "glitch")
            check(bloco["id"] not in ids and x["clip_id"] in ids
                  and x["emenda"] is not None
                  and abs(x["emenda"] - bloco["out_start"]) < 0.05,
                  "um corte dentro do bloco não perde a transição: ela reencontra "
                  "o bloco pela âncora na fonte")
    else:
        check(False, f"o corte devia ter criado emendas ({len(blocos)} bloco)")
    texto = F.chamar(cliente, "transicao", {"projeto": pid, "bloco": "nao_existe"})
    check("não está no vídeo" in texto, "bloco que não existe volta explicado")
    texto = F.chamar(cliente, "camada", {"projeto": pid, "efeito": "desfoque",
                                         "inicio": 0.5, "fim": 2.0, "forca": 0.7})
    check(len(svc.load(pid).plan.camadas) == 1 and "desfoque" in texto,
          "camada separa pessoa e fundo no intervalo")

    # ---- os olhos: o quadro de VERDADE, com o título
    r = F.chamar(cliente, "ver_quadros", {"projeto": pid, "tempos": [1.5, 6.0],
                                          "lado": 240})
    imagens = [x for x in r["content"] if x["type"] == "image"]
    check(len(imagens) == 2 and imagens[0]["mimeType"] == "image/jpeg",
          "ver_quadros devolve IMAGENS (conteúdo de imagem do MCP)")
    # o instante da emenda: o último quadro de cada bloco (o Claude de verdade
    # pediu 10.00 s bem ali, e o ffmpeg saía sem foto nenhuma)
    beiras = [round(b["out_end"] - 0.001, 3) for b in svc.timeline_summary(svc.load(pid))["blocks"]]
    r2 = F.chamar(cliente, "ver_quadros", {"projeto": pid, "tempos": beiras[:6], "lado": 160})
    fotos = [x for x in r2["content"] if x["type"] == "image"]
    check(len(fotos) == len(beiras[:6]),
          f"o quadro na beira de cada bloco sai (o último quadro antes da emenda): "
          f"{len(fotos)}/{len(beiras[:6])}")
    com = _jpeg_para_rgb(base64.b64decode(imagens[0]["data"]))
    sem = _jpeg_para_rgb(base64.b64decode(imagens[1]["data"]))
    h, w, _ = com.shape
    # o MESMO instante sem o gráfico: a diferença tem de estar no painel de
    # vidro do título (centro em 0,5; 0,2) e em nenhum outro lugar
    p_ = svc.load(pid)
    p_.plan.graficos[0].enabled = False
    p_.save_plan()
    r3 = F.chamar(cliente, "ver_quadros", {"projeto": pid, "tempos": [1.5], "lado": 240})
    sem = _jpeg_para_rgb(base64.b64decode(
        [x for x in r3["content"] if x["type"] == "image"][0]["data"]))
    p_ = svc.load(pid)
    p_.plan.graficos[0].enabled = True
    p_.save_plan()
    regiao = (slice(int(h * 0.1), int(h * 0.3)), slice(int(w * 0.3), int(w * 0.7)))
    fora = (slice(int(h * 0.6), h), slice(0, w))
    dif = float(np.abs(com[regiao].astype(int) - sem[regiao].astype(int)).mean())
    resto = float(np.abs(com[fora].astype(int) - sem[fora].astype(int)).mean())
    check(dif > 8 and resto < 3,
          f"no quadro do título, o painel de vidro e o texto estão lá (diferença "
          f"{dif:.0f} no título, {resto:.1f} no resto) — o quadro é o encode de verdade")
    check(min(w, h) == 180,
          f"no tamanho pedido, sem ampliar a fonte de 180 px ({w}x{h})")
    rota = tc.get(f"/api/projects/{pid}/pos/quadro.jpg", params={"t": 1.0, "lado": 240})
    check(rota.status_code == 200 and rota.content[:2] == b"\xff\xd8",
          "e a tela tem o mesmo quadro em JPEG direto")

    # pelo protocolo, a imagem chega como conteúdo de imagem
    saida = io.StringIO()
    servir(io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                   "params": {"name": "ver_quadros", "arguments": {
                                       "projeto": pid, "tempos": [1.0], "lado": 240}}})
                       + "\n"), saida, cliente)
    resp = json.loads(saida.getvalue())
    tipos = [x["type"] for x in resp["result"]["content"]]
    check("image" in tipos and not resp["result"]["isError"],
          "tools/call de ver_quadros responde com imagem pelo protocolo")

    texto = F.chamar(cliente, "analisar_cena", {"projeto": pid, "tempo": 1.0})
    check("cena em" in texto and "luz" in texto and "fala" in texto,
          f"analisar_cena descreve o quadro e a fala dali ({texto[:120]!r})")

    # ---- a revisão: ler em frases, cortar, reler só o que ficou
    from editor.mcp.ferramentas import _palavras_da_montagem
    montagem = _palavras_da_montagem({
        "words": [{"i": 0, "text": "a"}],
        "fontes": {"m2": {"ordem": 2, "media_id": "m2", "words": [{"i": 200000, "text": "c"}]},
                   "m1": {"ordem": 1, "media_id": "m1", "words": [{"i": 100000, "text": "b"}]}}})
    check([w["i"] for w in montagem] == [0, 100000, 200000]
          and [w["_n"] for w in montagem] == [1, 2, 3],
          "a transcrição que o Claude lê tem TODAS as gravações, na ordem da montagem")
    texto = F.chamar(cliente, "transcricao", {"projeto": pid})
    check("\n[" in texto and "~" in texto,
          "a transcrição vem em linhas (frase/pausa) e marca o que já saiu")
    ficou = F.chamar(cliente, "transcricao", {"projeto": pid, "restante": True})
    fora = svc.load(pid).analysis.get("removed_word_ids") or []
    check("FICOU" in ficou and fora and f"[{fora[0]}]" not in ficou,
          "restante=true mostra só o que ficou, para reler o texto como ele vai soar")
    vivas = [w["i"] for w in svc.load(pid).analysis["words"] if w["i"] not in fora]
    texto = F.chamar(cliente, "cortar", {"projeto": pid, "palavras": [vivas[-2]]})
    check("trecho(s)" in texto, f"cortar diz quantos trechos cortou ({texto.splitlines()[0]})")
    F.chamar(cliente, "devolver", {"projeto": pid, "palavras": [vivas[-2]]})

    # ---- o que o Gemini decidia, agora pelas ferramentas
    tl = svc.timeline_summary(svc.load(pid))
    b0 = tl["blocks"][0]["id"]
    texto = F.chamar(cliente, "ritmo", {"projeto": pid, "blocos": [
        {"bloco": b0, "velocidade": 1.15, "zoom": 1.1, "etapa": "explicacao"},
        {"bloco": "nao_existe", "velocidade": 1.1}]})
    c0 = next(c for c in svc.load(pid).plan.clips if c.id == b0)
    check(abs(c0.speed - 1.15) < 0.01 and abs(c0.zoom - 1.1) < 0.01
          and c0.section == "explicacao" and "recusado" in texto,
          f"ritmo muda velocidade, zoom e etapa do bloco; o bloco errado volta "
          f"recusado sem derrubar os outros ({texto.splitlines()[0]})")
    texto = F.chamar(cliente, "legendas", {"projeto": pid, "acao": "ver"})
    check("  " in texto and ("–" in texto or "nenhuma" in texto),
          "legendas acao=ver lista id, tempo e texto")
    F.chamar(cliente, "legendas", {"projeto": pid, "acao": "corrigir",
                                   "errado": "bravo", "certo": "BRAVO!"})
    check(any("BRAVO!" in s.text for s in svc.load(pid).plan.subtitles),
          "legendas acao=corrigir troca a palavra no vídeo inteiro")
    F.chamar(cliente, "legendas", {"projeto": pid, "acao": "estilo", "posicao": "alto",
                                   "maiusculas": True, "cor": "#FFD400"})
    st = svc.load(pid).plan.style
    check(st.align == 8 and st.uppercase and st.primary == "#FFD400",
          "legendas acao=estilo muda posição, maiúsculas e cor")
    removidas = svc.load(pid).analysis.get("removed_word_ids") or []
    if removidas:
        antes = svc.duracao_de_saida(svc.load(pid))
        F.chamar(cliente, "devolver", {"projeto": pid, "palavras": [removidas[0]]})
        check(svc.duracao_de_saida(svc.load(pid)) > antes
              and removidas[0] not in (svc.load(pid).analysis.get("removed_word_ids") or []),
              "devolver traz de volta a palavra que o corte levou")
    texto = F.chamar(cliente, "respiro", {"projeto": pid, "corte": 0.1})
    check("corte refeito" in texto, f"respiro refaz o corte com outro fôlego ({texto})")

    # a mão dele fica: tirar tudo só leva o que o Claude pôs
    p = svc.load(pid)
    from editor.models import Grafico
    p.plan.graficos.append(Grafico(tipo="texto", texto="meu", out_start=0, out_end=1))
    p.save_plan()
    texto = F.chamar(cliente, "tirar_da_pos", {"projeto": pid, "tudo": True})
    p = svc.load(pid)
    check([g.texto for g in p.plan.graficos] == ["meu"] and not p.plan.camadas
          and not p.plan.transicoes,
          f"tirar_da_pos tudo=true leva só o que o Claude pôs ({texto})")

    # sem Gemini: a receita marca o Claude como editor e o corte da IA pula
    from editor.server import aplicar_receita
    aplicar_receita(p, {"editor": "claude"})
    check(p.plan.editor == "claude", "a receita marca o Claude como editor")

    class _Ctx:
        def stage(self, *a, **k): pass
        def progress(self, *a, **k): pass
    rel = svc._cortes_da_ia(p, _Ctx(), [], [], [])
    check(rel and rel.get("pulada") and rel.get("erro") == "claude",
          "com o Claude editando, o Gemini não decide os cortes")
    p.save_plan()
    check(svc.load(pid).plan.editor == "claude", "e isso fica gravado no plano")


def main() -> int:
    install(["frase %d" % i for i in range(20)])
    tc = TestClient(app)
    cliente = Cliente(transporte=tc)
    tmp = Path(tempfile.mkdtemp(prefix="mcp_"))
    pid = None
    try:
        # ---- 1) o protocolo -------------------------------------------
        pedidos = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "nao/existe"},
        ]
        saida = io.StringIO()
        servir(io.StringIO("\n".join(json.dumps(p) for p in pedidos) + "\n"),
               saida, cliente)
        respostas = [json.loads(l) for l in saida.getvalue().strip().split("\n")]
        por_id = {r.get("id"): r for r in respostas}
        check(len(respostas) == 3,
              f"o aviso 'initialized' não gera resposta ({len(respostas)} de 4 "
              f"pedidos responderam)")
        check(por_id[1]["result"]["protocolVersion"] == "2024-11-05",
              "o initialize responde na versão que o cliente pediu")
        check(por_id[1]["result"]["serverInfo"]["name"] == "sharkcut",
              "e se identifica")
        check(por_id[3].get("error", {}).get("code") == -32601,
              "método desconhecido vira erro de protocolo, não silêncio")
        ferramentas = por_id[2]["result"]["tools"]
        check(len(ferramentas) >= 14,
              f"o catálogo tem {len(ferramentas)} ferramentas")
        sem_esquema = [f["name"] for f in ferramentas
                       if f["inputSchema"].get("type") != "object"]
        check(not sem_esquema, f"toda ferramenta tem esquema de objeto ({sem_esquema})")
        sem_descricao = [f["name"] for f in ferramentas
                         if len(f.get("description", "")) < 40]
        check(not sem_descricao,
              f"e descrição que explica o gesto, não só o nome ({sem_descricao})")
        obrigatorios = [f["name"] for f in ferramentas
                        if set(f["inputSchema"].get("required") or [])
                        - set(f["inputSchema"].get("properties") or {})]
        check(not obrigatorios,
              f"nenhum campo obrigatório fora das propriedades ({obrigatorios})")

        # as ferramentas perigosas NÃO existem
        nomes = {f["name"] for f in ferramentas}
        proibidas = {"apagar_projeto", "revelar", "abrir_pasta",
                     "mudar_pasta_de_saida", "executar"}
        check(not (nomes & proibidas),
              "não existe ferramenta de apagar, revelar pasta ou trocar a "
              "pasta de saída — isso é botão dele, na tela")

        # juntar gravações é um gesto DIFERENTE de anexar uma janela, e a
        # descrição tem que dizer isso: são as duas coisas que ele mais
        # confunde ao pedir, e o modelo escolhe pela descrição
        junta = next((f for f in ferramentas if f["name"] == "adicionar_video"),
                     None)
        check(junta is not None, "existe ferramenta de acrescentar gravação")
        check(junta and "janela" in junta["description"],
              "e a descrição dela distingue de anexar uma janela por cima")

        pacote = next((f for f in ferramentas if f["name"] == "juntar_videos"),
                      None)
        check(pacote is not None
              and "array" == (pacote["inputSchema"]["properties"]
                              .get("caminhos", {}).get("type")),
              "juntar_videos recebe a LISTA de caminhos, na ordem da montagem")
        check(pacote and "ORDEM DA MONTAGEM" in
              pacote["inputSchema"]["properties"]["caminhos"]["description"].upper(),
              "e a descrição diz que a ordem importa — é a única coisa que ele "
              "precisa decidir")
        check(any(f["name"] == "gravacoes" for f in ferramentas),
              "e dá para listar as tomadas gravadas no app, para ele poder "
              "dizer 'a que acabei de gravar' em vez de um caminho")
        texto = F.chamar(cliente, "juntar_videos", {"caminhos": ["/x.mp4"]})
        check("abrir_video" in texto,
              f"com um arquivo só, ela manda usar a ferramenta certa em vez de "
              f"criar um pacote de um ({texto[:70]}…)")

        # ---- 2) cada ferramenta MUDA alguma coisa ----------------------
        texto = F.chamar(cliente, "estado_do_editor", {})
        check("ffmpeg" in texto and "editor no ar" in texto,
              "estado_do_editor diz se o ffmpeg está bom")

        pid = semear(cliente, tmp)
        antes = tc.get(f"/api/projects/{pid}").json()["timeline"]["duration"]

        texto = F.chamar(cliente, "ver_projeto", {"projeto": pid})
        check("blocos" in texto and len(texto) < 800,
              f"ver_projeto responde em texto curto ({len(texto)} caracteres, "
              f"não os centenas de KB do JSON cru)")

        texto = F.chamar(cliente, "transcricao", {"projeto": pid, "de": 0, "ate": 4})
        check("[0]alfa" in texto,
              f"transcricao numera as palavras para o corte ({texto[:60]}…)")

        texto = F.chamar(cliente, "cortar", {"projeto": pid, "palavras": [3, 4]})
        depois = tc.get(f"/api/projects/{pid}").json()["timeline"]["duration"]
        check(depois < antes - 0.2,
              f"cortar TIRA vídeo de verdade ({antes:.2f} s → {depois:.2f} s)")
        removidas = set(tc.get(f"/api/projects/{pid}").json()["analysis"]
                        .get("manual_removed_word_ids") or [])
        check({3, 4} <= removidas,
              f"e fica registrado como remoção manual ({sorted(removidas)})")

        d0 = tc.get(f"/api/projects/{pid}").json()["timeline"]["duration"]
        F.chamar(cliente, "velocidade", {"projeto": pid, "fator": 1.3})
        d1 = tc.get(f"/api/projects/{pid}").json()["timeline"]["duration"]
        check(d1 < d0 - 0.1, f"velocidade encurta o vídeo ({d0:.2f} → {d1:.2f} s)")
        F.chamar(cliente, "velocidade", {"projeto": pid, "fator": 1.0})

        # anexar + animar + recortar + efeito, a corrente inteira
        selo = tmp / "selo.png"
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=yellow:s=60x60:d=1", "-frames:v", "1",
                        str(selo)], check=True)
        texto = F.chamar(cliente, "anexar",
                         {"projeto": pid, "caminho": str(selo), "em": 1.0,
                          "dura": 2.0, "descricao": "selo de garantia"})
        plano = tc.get(f"/api/projects/{pid}").json()["plan"]
        check(len(plano["overlays"]) == 1,
              f"anexar põe a janela no plano ({len(plano['overlays'])})")
        oid = plano["overlays"][0]["id"]
        check(oid in texto, "e devolve o id dela, que as outras pedem")

        F.chamar(cliente, "animar", {
            "projeto": pid, "sobreposicao": oid,
            "marcos": [{"t": 1.0, "x": 0.2, "scale": 0.5},
                       {"t": 3.0, "x": 0.8, "scale": 1.0, "easing": "suave"}]})
        ov = tc.get(f"/api/projects/{pid}").json()["plan"]["overlays"][0]
        check(len(ov["keyframes"]) == 2 and ov["keyframes"][1]["easing"] == "suave",
              f"animar grava os marcos com a curva ({ov['keyframes']})")

        texto = F.chamar(cliente, "animar", {
            "projeto": pid, "sobreposicao": oid,
            "marcos": [{"t": "isto nao e numero", "x": 0.5},
                       {"t": 2.0, "y": 0.4}]})
        ov = tc.get(f"/api/projects/{pid}").json()["plan"]["overlays"][0]
        check(len(ov["keyframes"]) == 1 and "recusado" in texto,
              f"marco inválido é recusado E DITO, não engolido calado ({texto[:80]}…)")

        F.chamar(cliente, "recortar_forma",
                 {"projeto": pid, "sobreposicao": oid, "forma": "elipse",
                  "suavizar": 0.08})
        ov = tc.get(f"/api/projects/{pid}").json()["plan"]["overlays"][0]
        check((ov.get("mask") or {}).get("shape") == "elipse",
              f"recortar_forma grava a máscara ({ov.get('mask')})")

        F.chamar(cliente, "efeito", {
            "projeto": pid, "alvo": "sobreposicao", "id": oid,
            "efeitos": [{"kind": "desfoque", "sigma": 6}]})
        ov = tc.get(f"/api/projects/{pid}").json()["plan"]["overlays"][0]
        check([e["kind"] for e in ov["effects"]] == ["desfoque"],
              f"efeito grava na sobreposição ({ov['effects']})")

        bloco = tc.get(f"/api/projects/{pid}").json()["timeline"]["blocks"][0]["id"]
        F.chamar(cliente, "efeito", {
            "projeto": pid, "alvo": "clipe", "id": bloco,
            "efeitos": [{"kind": "vinheta", "amount": 0.7}]})
        clipes = tc.get(f"/api/projects/{pid}").json()["plan"]["clips"]
        alvo = next(c for c in clipes if c["id"] == bloco)
        check([e["kind"] for e in alvo["effects"]] == ["vinheta"],
              f"e no bloco do vídeo ({alvo['effects']})")

        F.chamar(cliente, "formatos",
                 {"projeto": pid, "principal": "9:16", "extras": ["1:1"]})
        exp = tc.get(f"/api/projects/{pid}").json()["plan"]["export"]
        check(exp.get("aspect") == "9:16" and "1:1" in (exp.get("extras") or []),
              f"formatos muda o que vai ser entregue ({exp.get('aspect')}, "
              f"{exp.get('extras')})")

        # ---- 3) erro vira TEXTO, não explosão -------------------------
        texto = F.chamar(cliente, "ver_projeto", {"projeto": "nao_existe"})
        check("recusou" in texto and "nao_existe" in texto,
              f"projeto inexistente volta como TEXTO que o modelo lê e "
              f"conserta, não como exceção ({texto[:70]}…)")
        texto = F.chamar(cliente, "cortar", {"projeto": pid})
        check("diga as palavras" in texto,
              "cortar sem argumento explica o que falta em vez de estourar")
        testar_pos_edicao(cliente, tc, pid)
        testar_marca_e_cenas(cliente, tc, pid)

        texto = F.chamar(cliente, "ferramenta_que_nao_existe", {})
        check("não existe ferramenta" in texto and "cortar" in texto,
              "nome errado de ferramenta lista as que existem")
    finally:
        if pid:
            try:
                svc.delete_project(pid)
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FALHAS:
        print(f"{len(FALHAS)} FALHA(S):")
        for f in FALHAS:
            print("  -", f)
        return 1
    print("o MCP passa")
    return 0


def testar_marca_e_cenas(cliente: Cliente, tc: TestClient, pid: str) -> None:
    """A marca, o logo e as cenas pelas ferramentas do Claude."""
    print("\n-- marca, logo e cenas pelo MCP")
    texto = F.chamar(cliente, "marca", {"projeto": pid})
    check("hospedepay" in texto and "#FF385C" in texto and "simbolo" in texto
          and "NUNCA logo de plataforma" in texto,
          "marca: o Claude lê o nome exato, o coral, as regras e os logos")
    check("hospedepay" in F.chamar(cliente, "pos_contexto", {"projeto": pid})[:400],
          "e o roteiro da pós já começa pela marca")
    texto = F.chamar(cliente, "grafico", {"projeto": pid, "tipo": "logo", "logo": "simbolo",
                                          "inicio": 0.3, "fim": 3.0, "x": 0.92, "y": 0.12,
                                          "opacidade": 0.8})
    g = [x for x in svc.load(pid).plan.graficos if x.tipo == "logo"]
    check(len(g) == 1 and g[0].logo == "simbolo" and abs(g[0].opacidade - 0.8) < 1e-6
          and "logo" in texto,
          f"grafico tipo=logo põe o logo de lado, transparente ({texto[:60]})")
    texto = F.chamar(cliente, "grafico", {"projeto": pid, "tipo": "logo", "logo": "airbnb",
                                          "inicio": 1.0})
    check("recusou" in texto and "simbolo" in texto,
          "logo que não existe volta explicado, com os que existem")
    F.chamar(cliente, "grafico", {"projeto": pid, "tipo": "titulo", "texto": "HOSPEDE PAY",
                                  "inicio": 0.5, "fim": 2.0, "estilo": "vidro"})
    t = svc.load(pid).plan.graficos[-1]
    check(t.texto == "hospedepay" and t.estilo == "vidro",
          f"o nome da marca é gravado na grafia exata ({t.texto}), estilo vidro aceito")
    texto = F.chamar(cliente, "cena", {"projeto": pid, "tipo": "moldura", "inicio": 1.0,
                                       "fim": 4.0, "lado": "direita"})
    cenas = svc.load(pid).plan.cenas
    check(len(cenas) == 1 and cenas[0].tipo == "moldura" and cenas[0].origem == "claude"
          and "ver_quadros" in texto,
          f"cena moldura entra no plano ({texto[:70]})")
    texto = F.chamar(cliente, "cena", {"projeto": pid, "tipo": "vidro3d", "inicio": 2.0,
                                       "fim": 5.0})
    check("recusou" in texto and "uma cena por vez" in texto,
          "duas cenas ao mesmo tempo: recusado, explicado")
    texto = F.chamar(cliente, "cena", {"projeto": pid, "id": cenas[0].id, "tipo": "vidro3d",
                                       "logos": ["assinatura_branca"]})
    c = svc.load(pid).plan.cenas[0]
    check(c.tipo == "vidro3d" and c.logos == ["assinatura_branca"],
          "com id, a cena muda (vira camadas de vidro com o logo da marca)")
    ctx = F.chamar(cliente, "pos_contexto", {"projeto": pid})
    check(f"cena {c.id}: vidro3d" in ctx, "o que já está na pós lista a cena")
    r = F.chamar(cliente, "tirar_da_pos", {"projeto": pid, "tudo": True})
    check(not svc.load(pid).plan.cenas and not [x for x in svc.load(pid).plan.graficos
                                                if x.origem == "claude"],
          f"tirar_da_pos tudo=true tira também as cenas ({r})")


if __name__ == "__main__":
    sys.exit(main())
