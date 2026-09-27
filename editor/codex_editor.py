"""Codex local, autenticado pela conta ChatGPT, editando pelo MCP do Sharkcut."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time

from . import claude_editor as C, db


def achar() -> str:
    guardado = str(db.get_setting("codex_caminho", "") or "").strip().strip('"')
    if guardado and Path(guardado).is_file():
        return guardado
    no_path = shutil.which("codex")
    if no_path:
        return no_path
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    candidatos = sorted((local / "OpenAI/Codex/bin").glob("*/codex.exe"),
                         key=lambda p: p.stat().st_mtime, reverse=True)
    candidatos += [Path.home() / ".local/bin/codex", Path.home() / ".local/bin/codex.exe"]
    return next((str(p) for p in candidatos if p.is_file()), "")


def _base(caminho: str) -> list[str]:
    if Path(caminho).suffix.lower() in (".cmd", ".bat"):
        js = Path(caminho).parent / "node_modules/@openai/codex/bin/codex.js"
        node = shutil.which("node")
        if node and js.is_file():
            return [node, str(js)]
        raise OSError("aponte o executável codex.exe ou reinstale o Codex CLI pelo npm")
    return [caminho]


def _abrir(cmd: list[str], **kw):
    return subprocess.Popen([*_base(cmd[0]), *cmd[1:]],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **kw)


def _consultar(caminho: str, args: list[str], timeout=15) -> tuple[int, str]:
    proc = _abrir([caminho, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, (out + err).decode("utf-8", "replace").strip()
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise OSError("o Codex demorou para responder") from None


def estado(forcar: bool = False) -> dict:
    caminho = achar()
    out = {"instalado": bool(caminho), "caminho": caminho, "logado": False,
           "versao": "", "motivo": "", "modelo": db.get_setting("codex_modelo", "") or ""}
    if not caminho:
        out["motivo"] = "Codex CLI não encontrado. Instale o Codex ou informe o caminho."
        return out
    try:
        codigo, versao = _consultar(caminho, ["--version"])
        out["versao"] = versao.splitlines()[-1] if versao else ""
        if codigo:
            raise OSError("o executável não respondeu como Codex CLI")
        codigo, auth = _consultar(caminho, ["login", "status"])
        out["logado"] = codigo == 0 and "chatgpt" in auth.lower()
        if not out["logado"]:
            out["motivo"] = "Faça login com sua conta do ChatGPT: codex login."
        _, ajuda = _consultar(caminho, ["exec", "--help"])
        out["compativel"] = "--ignore-user-config" in ajuda
        if not out["compativel"]:
            out["motivo"] = "Atualize o Codex CLI: esta integração exige --ignore-user-config."
    except OSError as exc:
        out["motivo"] = str(exc)
    return out


def comando(caminho: str, modo: str, modelo: str = "") -> list[str]:
    """A autenticação salva permanece; configurações e ferramentas alheias não entram."""
    cmd = [caminho, "exec", "--json", "--ephemeral", "--ignore-user-config",
           "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never"]
    valores = {"approval_policy": "on-request", "approvals_reviewer": "auto_review",
               "forced_login_method": "chatgpt",
               "features.shell_tool": False, "features.unified_exec": False,
               "features.apps": False, "agents.enabled": False, "web_search": "disabled",
               "mcp_servers.sharkcut.url": f"http://127.0.0.1:{C._porta()}/mcp",
               "mcp_servers.sharkcut.bearer_token_env_var": "SHARKCUT_MCP_TOKEN",
               "mcp_servers.sharkcut.required": True,
               "mcp_servers.sharkcut.startup_timeout_sec": 30,
               "mcp_servers.sharkcut.tool_timeout_sec": 900,
               "mcp_servers.sharkcut.enabled_tools": sorted(C._liberadas(modo)),
               "mcp_servers.sharkcut.default_tools_approval_mode": "auto"}
    for chave, valor in valores.items():
        cmd += ["-c", f"{chave}={json.dumps(valor, ensure_ascii=False)}"]
    if modelo:
        cmd += ["--model", modelo]
    return cmd + ["-"]


def _gravar(pid: str, saida: dict, t0: float) -> dict:
    from . import projects as svc
    saida["segundos"] = round(time.monotonic() - t0, 1)
    p = svc.load(pid)
    p.analysis["codex_edicao"] = {**saida, "quando": time.time()}
    p.save_analysis()
    return saida


def editar(pid: str, ctx, modelo: str | None = None, retoque: str = "",
           modo: str = "completo") -> dict:
    from . import diretor, projects as svc
    t0 = time.monotonic()
    saida = {"ok": False, "provedor": "codex", "modo": modo, "relatorio": "",
             "erro": "", "ferramentas": 0, "passos": []}
    ctx.stage("codex", "conectando o diretor à sua conta do ChatGPT")
    est = estado(True)
    if not est.get("logado") or not est.get("compativel"):
        saida["erro"] = est["motivo"] or "Codex indisponível"
        return _gravar(pid, saida, t0)
    project = svc.load(pid)
    pasta = project.dir / "codex"
    pasta.mkdir(parents=True, exist_ok=True)
    chave = C.abrir_chave("codex", pid, modo)
    env = dict(os.environ)
    env["SHARKCUT_MCP_TOKEN"] = chave
    env["PYTHONIOENCODING"] = "utf-8"
    # A escolha foi a assinatura ChatGPT. Não usar uma chave de API herdada.
    for k in ("CODEX_API_KEY", "OPENAI_API_KEY"):
        env.pop(k, None)
    for k in ("NO_PROXY", "no_proxy"):
        env[k] = ",".join(filter(None, ["127.0.0.1", "localhost", env.get(k, "")]))
    texto = C.pedido_de_retoque(project, retoque) if retoque.strip() else C.pedido(project, modo)
    texto = ("Você é o diretor do Sharkcut. O dono autorizou a edição desta etapa "
             "neste projeto. Use somente o MCP sharkcut para este projeto.\n"
             + texto + diretor.instrucoes(project))
    proc = None
    try:
        falha = C._conferir_porta(chave)
        if falha:
            raise OSError(falha)
        cmd = comando(est["caminho"], modo, modelo if modelo is not None else est["modelo"])
        proc = _abrir(cmd, cwd=str(pasta), env=env, stdin=subprocess.PIPE,
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        eventos: queue.Queue = queue.Queue()
        erros = []

        def ler_saida():
            try:
                for linha in proc.stdout:
                    eventos.put(linha)
            finally:
                eventos.put(None)

        def ler_erros():
            for linha in proc.stderr:
                erros.append(linha.decode("utf-8", "replace"))
                del erros[:-20]

        leitores = [threading.Thread(target=ler_saida, daemon=True),
                    threading.Thread(target=ler_erros, daemon=True)]
        for leitor in leitores:
            leitor.start()
        proc.stdin.write(texto.encode("utf-8"))
        proc.stdin.close()
        terminou = False
        chamadas = set()
        ultimo = time.monotonic()
        while True:
            if ctx.cancelled():
                raise KeyboardInterrupt("cancelado")
            if time.monotonic() - t0 > C.TETO_MINUTOS * 60:
                raise OSError(f"o Codex passou de {C.TETO_MINUTOS} minutos")
            try:
                linha = eventos.get(timeout=0.5)
            except queue.Empty:
                if time.monotonic() - ultimo > 10:
                    ctx.progress(C._curva(saida["ferramentas"]), "Codex está avaliando a edição", "codex")
                    ultimo = time.monotonic()
                continue
            if linha is None:
                break
            try:
                ev = json.loads(linha)
            except ValueError:
                continue
            tipo = ev.get("type")
            item = ev.get("item") or {}
            if tipo == "turn.completed":
                terminou = True
            elif tipo in ("turn.failed", "error"):
                err = ev.get("error") or ev.get("message") or "falha na execução"
                saida["erro"] = str(err.get("message", err) if isinstance(err, dict) else err)[:1000]
            elif item.get("type") == "agent_message" and tipo == "item.completed":
                saida["relatorio"] = str(item.get("text") or "")[-6000:]
            elif item.get("type") == "mcp_tool_call" and item.get("server") == "sharkcut":
                ident = item.get("id")
                if ident not in chamadas:
                    chamadas.add(ident)
                    nome = item.get("tool", "ferramenta")
                    saida["ferramentas"] += 1
                    argumentos = item.get("arguments") or {}
                    if isinstance(argumentos, str):
                        try:
                            argumentos = json.loads(argumentos)
                        except ValueError:
                            argumentos = {}
                    frase = C._frase(nome, argumentos if isinstance(argumentos, dict) else {})
                    saida["passos"].append(frase)
                    del saida["passos"][:-60]
                    ctx.progress(C._curva(saida["ferramentas"]), f"Codex: {frase}", "codex")
        proc.wait(timeout=10)
        saida["ok"] = terminou and proc.returncode == 0 and not saida["erro"] and bool(chamadas)
        if not saida["ok"] and not saida["erro"]:
            saida["erro"] = ("".join(erros)[-1000:] or
                "O Codex terminou sem usar as ferramentas; a edição não foi confirmada.")
    except (OSError, subprocess.TimeoutExpired) as exc:
        saida["erro"] = str(exc)[:1000]
    finally:
        if proc:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
            for leitor in locals().get("leitores", []):
                leitor.join(timeout=2)
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                if pipe and not pipe.closed:
                    pipe.close()
        C.fechar_chave(chave)
    return _gravar(pid, saida, t0)
