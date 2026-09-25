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


if __name__ == "__main__":
    sys.exit(main())
