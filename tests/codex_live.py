"""Teste opcional com conta real: python -m tests.codex_live --usar-conta.

Consome a conta ChatGPT conectada; usa somente um projeto sintético e um banco
temporário. Sem a opção explícita, não chama o modelo. --servir mantém a prévia
local aberta ao terminar para conferir a interface.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--usar-conta", action="store_true")
    args.add_argument("--servir", action="store_true")
    op = args.parse_args()
    if not op.usar_conta:
        args.error("este teste exige --usar-conta e consome sua conta ChatGPT")
    pasta = Path(tempfile.mkdtemp(prefix="sharkcut-codex-live-"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        porta = sock.getsockname()[1]
    os.environ.update(EDITOR_DATA_DIR=str(pasta / "dados"),
                      EDITOR_OUTPUT_DIR=str(pasta / "saida"), EDITOR_PORT=str(porta))
    import uvicorn
    from editor import projects as S, editores as E, claude_editor as C, diretor as D
    from editor.mcp.cliente import Cliente
    from editor.server import app
    from tests.mcp import semear

    servidor = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="warning"))
    thread = threading.Thread(target=servidor.run, daemon=True)
    thread.start()
    for _ in range(100):
        if servidor.started:
            break
        time.sleep(0.1)
    if not servidor.started:
        raise RuntimeError("servidor de teste não iniciou")
    pid = semear(Cliente(f"http://127.0.0.1:{porta}"), pasta)
    p = S.load(pid)
    p.plan.marca = "-"
    p.plan.editor = "codex"
    p.plan.pos_editor = "codex"
    p.plan.direcao = {"ativa": True, "perfil": "editorial"}
    p.save_plan()
    C.TETO_MINUTOS = 6

    class Contexto:
        def stage(self, stage, message=""): print(stage, message, flush=True)
        def progress(self, value, message="", stage=""): print(message, flush=True)
        def cancelled(self): return False

    print(json.dumps({"url": f"http://127.0.0.1:{porta}", "projeto": pid,
                      "artefatos": str(pasta)}, ensure_ascii=False), flush=True)
    try:
        resultado = E.executar(pid, Contexto(), "codex", "pos",
            "Teste técnico num vídeo SINTÉTICO, com palavras de teste e som de sinal. "
            "Não avalie o áudio nem use recorte da pessoa: não há pessoa. Leia pos_contexto. "
            "Planeje um único momento de 0 a 3 segundos com direcao planejar. "
            "Crie um gancho com compor, título ALFA BRAVO, y=0.22, de 0 a 3 segundos, "
            "estilo limpo. Confira ver_quadros em 0.3, 1.5 e 2.7 segundos, lado 360. "
            "Se a tipografia está legível e livre da legenda, use direcao revisar. "
            "Não altere cortes, não faça exportação. Finalize com relatório curto.")
        (pasta / "resultado.json").write_text(json.dumps(resultado, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({k: v for k, v in resultado.items() if k != "qualidade"},
                         ensure_ascii=False), flush=True)
        if not resultado["ok"] or not D.estado(S.load(pid))["aprovada"]:
            raise RuntimeError(resultado.get("erro") or "direção não aprovada")
        video = S.exportar_final(S.load(pid), Contexto())
        print(json.dumps({"exportacao": video}, ensure_ascii=False, default=str), flush=True)
        if op.servir:
            print("PRONTO_PARA_CONFERIR", flush=True)
            while not servidor.should_exit:
                time.sleep(1)
    finally:
        servidor.should_exit = True
        thread.join(timeout=10)


if __name__ == "__main__":
    main()
