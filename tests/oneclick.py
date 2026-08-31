"""Prova que o CLIQUE ÚNICO entrega o vídeo PRONTO PARA BAIXAR.

O pedido não foi "abra um editor": foi "quando carrega já deve estar pronto
pra baixar direto". Este teste solta um arquivo, roda `one_click` inteiro e
exige o MP4 final no fim — na resolução da fonte, com áudio, com legenda
queimada e com a receita da primeira tela aplicada. Se algum dia alguém
mover uma etapa para dentro do editor, este teste quebra.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

os.environ.setdefault("EDITOR_DATA_DIR", tempfile.mkdtemp(prefix="editor-oneclick-"))

from fastapi.testclient import TestClient                      # noqa: E402

from editor import projects as svc                              # noqa: E402
from editor.ffmpeg_utils import probe                           # noqa: E402
from editor.jobs import get_queue                               # noqa: E402
from editor.server import app                                   # noqa: E402
from tests.e2e import Ctx                                       # noqa: E402
from tests.fake_whisper import install                          # noqa: E402
from tests.speech import build_track, make_video               # noqa: E402

FALHAS: list[str] = []

FRASES = [
    "Presta atenção nisso aqui que é rápido",
    "O problema é que você perde cliente todo santo dia",
    "Então eu montei um jeito de cortar sozinho",
    "São trezentos e quarenta e sete clientes atendidos",
    "O investimento é de noventa e sete por mês",
    "E você tem garantia de trinta dias clica no link",
]


def check(cond: bool, label: str, extra: str = "") -> None:
    print(("  OK    " if cond else "  FALHA ") + label + (f"  {extra}" if extra else ""))
    if not cond:
        FALHAS.append(label)


def fonte(tmp: Path) -> tuple[Path, float]:
    """Seis frases faladas de verdade (espeak-ng), separadas por pausas, 1080x1920.

    Fala sintetizada e não sintética: o envelope precisa ter trechos contínuos
    de som, senão a detecção de fala não enxerga frase nenhuma e o teste
    mediria o fixture, não o produto.
    """
    samples, _marcas, duracao = build_track([(f, 0.9) for f in FRASES], noise=0.0011)
    video = make_video(tmp / "fonte.mp4", samples, duracao, 1080, 1920, 30)
    return video, duracao


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="oneclick-"))
    install(FRASES)

    print("1) a fonte que o usuário arrasta")
    src, duracao = fonte(tmp)
    info = probe(src)
    print(f"   {info.width}x{info.height} @ {info.fps:.2f} fps, {info.duration:.2f} s")

    print("2) a RECEITA da primeira tela, gravada ANTES do clique único")
    project = svc.create(str(src), "pronto", "VSL")
    legenda = project.plan.style.fontsize
    print(f"   legenda escalada sozinha para a altura {info.display_size[1]}: "
          f"fontsize {legenda}")
    check(60 <= legenda <= 72, "legenda já sai no tamanho do formato vertical",
          f"fontsize={legenda}")
    client = TestClient(app)
    looks = client.get("/api/looks").json()
    filtro = next((l["id"] for l in looks if l.get("id") not in ("nenhum", None)), "")
    RECEITA = {"speed": {"global_multiplier": 1.1},
               "zoom": {"intensity": 1.4},
               "style": {"fontsize_scale": 1.2},
               "export": {"scale": "720"},
               "look": filtro}
    print(f"   receita: velocidade +10%, zoom 1.4, legenda 1.2x, 720 de largura, "
          f"filtro '{filtro}'")

    print("3) o clique único COM a receita junto — quem aplica é o servidor")
    # É exatamente o que a primeira tela manda: preset + receita na MESMA
    # chamada. Gravar a receita antes e mandar só o preset era o furo — o
    # preset era reaplicado por cima e apagava tudo.
    r = client.post(f"/api/projects/{project.id}/oneclick",
                    json={"preset": "VSL", "receita": RECEITA})
    check(r.status_code == 200, "a rota do clique único aceitou a receita",
          f"HTTP {r.status_code}")
    project = svc.load(project.id)
    check(abs(project.plan.speed.global_multiplier - 1.1) < 1e-6,
          "velocidade da primeira tela SOBREVIVEU ao preset",
          f"{project.plan.speed.global_multiplier}")
    check(abs(getattr(project.plan.zoom, "intensity", 1.0) - 1.4) < 1e-6,
          "intensidade de zoom SOBREVIVEU ao preset",
          f"{getattr(project.plan.zoom, 'intensity', None)}")
    check(project.plan.look == filtro, "o filtro SOBREVIVEU ao preset",
          project.plan.look)
    check(project.plan.style.fontsize == round(legenda * 1.2),
          "a legenda 'maior' virou 1,2x o tamanho do formato",
          f"{project.plan.style.fontsize} (formato {legenda})")
    check(project.plan.export.scale == "720", "a resolução escolhida SOBREVIVEU",
          str(project.plan.export.scale))

    # o job roda numa thread do servidor, como na vida real: espera acabar
    jid = r.json()["id"]
    fila = get_queue()
    limite = 1800.0
    t0 = time.monotonic()
    while time.monotonic() - t0 < limite:
        job = fila.get(jid)
        if job and job.status in ("ok", "erro", "cancelado"):
            break
        time.sleep(0.5)
    job = fila.get(jid)
    check(job is not None and job.status == "ok",
          "o job do clique único terminou bem",
          f"{getattr(job, 'status', '?')} {str(getattr(job, 'error', ''))[:70]}")
    res = (job.result if job else None) or {}
    for etapa in ("analysis", "edit", "proxy", "previa", "final"):
        check(etapa in res, f"a etapa '{etapa}' rodou dentro do clique único")

    print("4) o que o usuário recebe no fim")
    final = res.get("final") or {}
    check(bool(final.get("download")), "o clique único devolve o link de download",
          str(final.get("download") or final.get("erro", ""))[:90])
    saida = Path(final.get("output", ""))
    check(saida.exists() and saida.stat().st_size > 100_000,
          "o MP4 final existe no disco",
          f"{saida.stat().st_size/1e6:.1f} MB" if saida.exists() else "sem arquivo")
    if saida.exists():
        f = probe(saida)
        print(f"   {f.width}x{f.height} @ {f.fps:.2f} fps | {f.duration:.2f} s | "
              f"v={f.v_codec} a={f.a_codec} | "
              f"{(f.v_bitrate or f.bitrate)/1e6:.2f} Mbps")
        check(f.width == 720, "o final sai NA RESOLUÇÃO PEDIDA na primeira tela",
              f"{f.width}x{f.height}")
        check(f.height > f.width, "e continua vertical (a proporção é a da fonte)")
        check(f.a_codec is not None, "o final tem áudio")
        check(f.duration < duracao, "o silêncio foi cortado sem ninguém pedir",
              f"{f.duration:.2f} s de {duracao:.2f} s")

    print("5) o botão de baixar do editor realmente baixa")
    link = final.get("download", "")
    check(link.startswith(f"/api/projects/{project.id}/download/"),
          "o link aponta para este projeto")
    r = client.get(link, headers={"Range": "bytes=0-1023"})
    check(r.status_code in (200, 206), "o link do download responde",
          f"HTTP {r.status_code}")
    check(len(r.content) > 0 and r.content[4:8] == b"ftyp",
          "o que sai do link é um MP4 de verdade",
          r.content[:12].hex() if r.content else "vazio")

    print("6) a prévia que abre no editor é a EDIÇÃO, não a fonte")
    previa = res.get("previa") or {}
    check(bool(previa.get("download")), "a prévia da edição foi gerada")
    if previa.get("download"):
        rp = client.get(previa["download"], headers={"Range": "bytes=0-1023"})
        check(rp.status_code in (200, 206), "a prévia também abre pelo link",
              f"HTTP {rp.status_code}")
    pv = Path(previa.get("output", ""))
    check(pv.exists() and pv.parent.name == "exports"
          and pv.parent.parent.name == project.id,
          "a prévia de 240p fica DENTRO do projeto, não na pasta de Vídeos",
          str(pv.parent))

    print("7) legenda pronta, sem ninguém abrir aba nenhuma")
    project = svc.load(project.id)
    check(len(project.plan.clips) > 0, "o corte já está no plano",
          f"{len(project.plan.clips)} blocos")
    subs = final.get("subtitles") or []
    check(len(subs) > 0, "as legendas foram queimadas na exportação",
          f"{len(subs)} legendas")

    print()
    if FALHAS:
        print(f"{len(FALHAS)} FALHA(S):")
        for f in FALHAS:
            print(f"  - {f}")
        return 1
    print("TUDO PRONTO: o arquivo final sai do clique único, sem tocar no editor.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
