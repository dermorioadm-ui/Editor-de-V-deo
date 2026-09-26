"""O Claude Code como editor, disparado pelo clique único — de ponta a ponta.

Pedido: "tirar a edição do Gemini e usar o Claude Code de ponta a ponta nos
cortes de silêncio, aceleração e legenda, além de tudo o que o Gemini faz —
dar o comando lá na primeira tela e entregar tudo junto".

O Claude Code de verdade não roda num teste (é a assinatura de alguém). No
lugar dele entra um "Claude falso" que faz exatamente o que a CLI faz com o
que o Sharkcut passa: lê o pedido pela entrada padrão, abre o servidor MCP
pelo arquivo de configuração, chama as ferramentas e escreve o progresso em
stream-json. O que se prova:

- a linha de comando tem as travas (sem ferramentas internas, só o MCP do
  Sharkcut, só as ferramentas liberadas, sem pergunta, e SEM --bare);
- o pedido leva o que ele escreveu na primeira tela;
- nada trava: o Claude dispara um trabalho (refazer o corte) enquanto o
  clique único espera por ele;
- o que o Claude fez está no vídeo (corte, legenda, ritmo, gráfico) e a tela
  recebe o relatório e o passo a passo;
- sem login, o vídeo sai do mesmo jeito, pela regra, com o motivo escrito.

    python -m tests.claude_editor
"""
from __future__ import annotations

import json
import os
import socket
import stat
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

os.environ.setdefault("EDITOR_DATA_DIR", tempfile.mkdtemp(prefix="editor-claude-"))

from editor import projects as svc                              # noqa: E402
from tests.fake_whisper import install                          # noqa: E402
from tests.speech import build_track, make_video               # noqa: E402

FALHAS: list[str] = []
FRASES = [
    "Presta atenção nisso aqui que é rápido",
    "O problema é que você perde cliente todo santo dia",
    "Então eu montei um jeito de cortar sozinho",
    "E você tem garantia de trinta dias clica no link",
]


def check(cond: bool, label: str, extra: str = "") -> None:
    print(("  OK    " if cond else "  FALHA ") + label + (f"  {extra}" if extra else ""))
    if not cond:
        FALHAS.append(label)


CLAUDE_FALSO = r'''#!__PY__
"""Um Claude Code de mentira: faz com o que recebe o que a CLI faria."""
import json, os, re, subprocess, sys

args = sys.argv[1:]
if "--version" in args:
    print("9.9.9 (Claude Code falso)"); sys.exit(0)
if "--help" in args:
    print("  --tools <tools...>  Use \"\" to disable all tools\n"
          "  --permission-mode <mode>  (choices: \"acceptEdits\", \"dontAsk\")\n"
          "  --disallowedTools <tools...>\n"
          "Commands:\n"
          "  auth                                  Manage authentication")
    sys.exit(0)
if args[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": os.environ.get("CLAUDE_FALSO_LOGADO", "sim") == "sim",
                      "authMethod": "claude.ai"}, indent=2))
    sys.exit(0)
modo = os.environ.get("CLAUDE_FALSO_MODO", "ok")
prompt = sys.stdin.read()
json.dump({"argv": args, "prompt": prompt, "cwd": os.getcwd()},
          open(os.environ["CLAUDE_FALSO_LOG"], "w", encoding="utf-8"))

def emit(o):
    print(json.dumps(o, ensure_ascii=False), flush=True)

if modo == "sem_login":
    sys.stderr.write("Invalid API key · Please run /login\n")
    emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
          "result": "Invalid API key · Please run /login"})
    sys.exit(1)

import urllib.request
cfg = json.load(open(args[args.index("--mcp-config") + 1], encoding="utf-8"))
srv_cfg = cfg["mcpServers"]["sharkcut"]
# como o Claude Code de verdade: MCP pela porta HTTP, com o cabeçalho da chave
assert srv_cfg.get("type") == "http", srv_cfg
n = [0]
def post(corpo):
    req = urllib.request.Request(srv_cfg["url"], data=json.dumps(corpo).encode(), method="POST",
                                 headers={**srv_cfg.get("headers", {}),
                                          "Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=600) as r:
        return r.status, r.read().decode("utf-8")
def rpc(metodo, params=None):
    n[0] += 1
    status, corpo = post({"jsonrpc": "2.0", "id": n[0], "method": metodo, "params": params or {}})
    return json.loads(corpo)["result"]

if modo == "sem_ferramentas":
    emit({"type": "system", "subtype": "init", "model": "claude-falso",
          "mcp_servers": [{"name": "sharkcut", "status": "failed"}]})
    import time; time.sleep(30)          # o de verdade seguiria sem ferramenta
    sys.exit(0)
rpc("initialize", {"protocolVersion": "2025-06-18"})
assert post({"jsonrpc": "2.0", "method": "notifications/initialized"})[0] == 202
nomes = {t["name"] for t in rpc("tools/list")["tools"]}
permitidas = set(args[args.index("--allowedTools") + 1].split(","))
emit({"type": "system", "subtype": "init", "model": "claude-falso",
      "mcp_servers": [{"name": "sharkcut", "status": "connected"}]})
pid = re.search(r"projeto (\S+) \(", prompt).group(1)
import time
time.sleep(float(os.environ.get("CLAUDE_FALSO_PENSA", "0")))

def chamar(nome, a):
    emit({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": f"t{n[0]}", "name": f"mcp__sharkcut__{nome}",
         "input": a}]}})
    if f"mcp__sharkcut__{nome}" not in permitidas or nome not in nomes:
        emit({"type": "user", "message": {"content": [
            {"type": "tool_result", "is_error": True,
             "content": f"permissão negada: {nome}"}]}})
        return None
    r = rpc("tools/call", {"name": nome, "arguments": a})
    texto = " ".join(c.get("text", "") for c in r["content"] if c["type"] == "text")
    emit({"type": "user", "message": {"content": [
        {"type": "tool_result", "content": texto[:2000]}]}})
    return r

blocos = lambda: re.findall(r"\n  (c_[0-9a-f]+)  ", chamar("pos_contexto", {"projeto": pid})["content"][0]["text"])
blocos()
chamar("transcricao", {"projeto": pid})
chamar("respiro", {"projeto": pid, "corte": 0.2})
b = blocos()
chamar("ritmo", {"projeto": pid, "blocos": [{"bloco": b[0], "velocidade": 1.12,
                                              "zoom": 1.1, "etapa": "gancho"}]})
chamar("legendas", {"projeto": pid, "acao": "corrigir", "errado": "cliente",
                    "certo": "CLIENTE"})
chamar("grafico", {"projeto": pid, "tipo": "titulo", "inicio": 0.2, "fim": 2.0,
                   "texto": "TÍTULO AMARELO", "cor": "#FFD400"})
r = chamar("ver_quadros", {"projeto": pid, "tempos": [1.0], "lado": 240})
viu = r and any(c["type"] == "image" for c in r["content"])
chamar("exportar", {"projeto": pid})
emit({"type": "assistant", "message": {"content": [
    {"type": "text", "text": "Terminei."}]}})
emit({"type": "result", "subtype": "success", "is_error": False, "num_turns": 9,
      "total_cost_usd": 0.0,
      "result": "- pus o título amarelo no gancho\n- corrigi 'cliente'\n"
                + ("- conferi o quadro" if viu else "- NÃO consegui ver o quadro")})
'''


def porta_livre() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def http(metodo: str, rota: str, corpo: dict | None = None, base: str = "") -> dict:
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(base + rota, data=dados, method=metodo,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode() or "{}")


def mcp_post(base: str, auth: str, corpo: dict, origem: str = "",
             tipo: str = "application/json") -> tuple[int, dict]:
    cab = {"Content-Type": tipo, "Accept": "application/json, text/event-stream"}
    if auth:
        cab["Authorization"] = auth
    if origem:
        cab["Origin"] = origem
    req = urllib.request.Request(base + "/mcp", data=json.dumps(corpo).encode(), method="POST",
                                 headers=cab)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            bruto = r.read().decode()
            return r.status, (json.loads(bruto) if bruto.strip() else {})
    except urllib.error.HTTPError as exc:
        return exc.code, {}


def mcp_get(base: str, auth: str) -> int:
    req = urllib.request.Request(base + "/mcp", headers={"Authorization": auth,
                                                         "Accept": "text/event-stream"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code


def esperar(base: str, pid: str, job_id: str, limite: float = 900.0) -> tuple[dict, list]:
    fim = time.time() + limite
    mensagens: list[str] = []
    while time.time() < fim:
        for j in http("GET", f"/api/jobs?project_id={pid}", base=base):
            if j["id"] == job_id:
                if j.get("message") and (not mensagens or mensagens[-1] != j["message"]):
                    mensagens.append(j["message"])
                if j["status"] in ("ok", "erro", "cancelado"):
                    return j, mensagens
        time.sleep(0.3)
    return {"status": "estourou"}, mensagens


SHIM_DO_NPM = r"""@ECHO off
GOTO start
:find_dp0
SET dp0=%~dp0
EXIT /b
:start
SETLOCAL
CALL :find_dp0

IF EXIST "%dp0%\node.exe" (
  SET "_prog=%dp0%\node.exe"
) ELSE (
  SET "_prog=node"
  SET PATHEXT=%PATHEXT:;.JS;=;%
)

endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\node_modules\@anthropic-ai\claude-code\cli.js" %*
"""


def testar_atalho_do_windows(tmp: Path) -> None:
    """A máquina dele: usuário "C:\\Users\\Renato Fulano" e o Claude pelo npm.

    Rodar o claude.cmd passava pelo cmd.exe, que tirava a primeira e a última
    aspas da linha e quebrava o caminho no espaço: "'C:\\Users\\Renato' não é
    reconhecido como um comando interno ou externo". O atalho agora é LIDO e o
    programa dele roda direto, sem o cmd.exe.
    """
    from editor import claude_editor as C

    print("\n-- o Claude Code instalado pelo npm, numa pasta com espaço")
    npm = tmp / "Renato Fulano" / "AppData" / "Roaming" / "npm"
    cli = npm / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("// cli", encoding="utf-8")
    atalho = npm / "claude.cmd"
    atalho.write_text(SHIM_DO_NPM, encoding="utf-8")
    base = C._base(str(atalho))
    check(base is not None and base[-1] == str(cli) and len(base) == 2
          and Path(base[0]).name.lower().startswith("node"),
          "o claude.cmd do npm vira node + cli.js — sem o cmd.exe no meio",
          str(base))
    (npm / "node.exe").write_bytes(b"MZ")
    base = C._base(str(atalho))
    check(base == [str(npm / "node.exe"), str(cli)],
          "com o node.exe ao lado do atalho, é ele que roda (e não é confundido "
          "com o Claude)")
    exe = npm / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    atalho2 = npm / "claude2.cmd"
    atalho2.write_text('@"%~dp0\\node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe" %*',
                       encoding="utf-8")
    check(C._base(str(atalho2)) == [str(exe)],
          "o atalho que aponta para um claude.exe roda o .exe direto")
    vazio = tmp / "outro dir" / "claude.cmd"
    vazio.parent.mkdir(parents=True)
    vazio.write_text("@echo off\nnada aqui", encoding="utf-8")
    linha = C._linha_do_cmd([str(vazio), "-p", "--tools", "", "--model", "opus"])
    check(C._base(str(vazio)) is None and linha.startswith('cmd.exe /d /s /c ""')
          and f'"{vazio}"' in linha and linha.endswith('opus"'),
          "sem alvo legível, vai pelo cmd.exe com /s — que só tira as aspas de fora",
          linha)
    # ---- a máquina dele de verdade: "Renato&Cibele", Claude Code novo do npm
    npm2 = tmp / "Renato&Cibele" / "AppData" / "Roaming" / "npm"
    pacote = npm2 / "node_modules" / "@anthropic-ai" / "claude-code"
    (pacote / "bin").mkdir(parents=True)
    atalho3 = npm2 / "claude.CMD"
    atalho3.write_text('@ECHO off\nGOTO start\n:find_dp0\nSET dp0=%~dp0\nEXIT /b\n:start\n'
                       'SETLOCAL\nCALL :find_dp0\n'
                       '"%dp0%\\node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe"   %*\n',
                       encoding="utf-8")
    stub = pacote / "bin" / "claude.exe"
    stub.write_text('echo "Error: claude native binary not installed." >&2\nexit 1\n')
    nativo = npm2 / "node_modules" / "@anthropic-ai" / "claude-code-win32-x64" / "claude.exe"
    nativo.parent.mkdir(parents=True)
    nativo.write_bytes(b"MZ\x90\x00")
    C.EH_WINDOWS = True         # só aqui: o teste do "MZ" é coisa de Windows
    try:
        base = C._base(str(atalho3))
    finally:
        C.EH_WINDOWS = os.name == "nt"
    check(base == [str(nativo)],
          "com '&' no nome da pasta e o claude.exe do npm ainda de mentira, acha o "
          "nativo que o npm baixou — sem passar pelo atalho", str(base))
    stub.write_bytes(b"MZ\x90\x00")
    check(C._base(str(atalho3)) == [str(stub)],
          "com o pós-instalação feito, roda o bin\\claude.exe do pacote direto")

    # o pacote antigo (cli.js) com o node fora do PATH, na pasta padrão
    npm3 = tmp / "Renato&Cibele2" / "npm"
    js = npm3 / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
    js.parent.mkdir(parents=True)
    js.write_text("//", encoding="utf-8")
    (npm3 / "claude.cmd").write_text(SHIM_DO_NPM, encoding="utf-8")
    pf = tmp / "Program Files"
    (pf / "nodejs").mkdir(parents=True)
    (pf / "nodejs" / "node.exe").write_bytes(b"MZ")
    which_real, pf_real = C.shutil.which, os.environ.get("ProgramFiles")
    C.shutil.which = lambda *_a, **_k: None
    os.environ["ProgramFiles"] = str(pf)
    try:
        base = C._base(str(npm3 / "claude.cmd"))
    finally:
        C.shutil.which = which_real
        if pf_real is None:
            os.environ.pop("ProgramFiles", None)
        else:
            os.environ["ProgramFiles"] = pf_real
    check(base == [str(pf / "nodejs" / "node.exe"), str(js)],
          "sem node no PATH, acha o node na pasta padrão do Windows", str(base))

    # nada por trás do atalho e '&' no caminho: explica em vez de quebrar
    npm4 = tmp / "Renato&Cibele3" / "npm"
    npm4.mkdir(parents=True)
    (npm4 / "claude.cmd").write_text("@echo off\n", encoding="utf-8")
    try:
        C._abrir([str(npm4 / "claude.cmd"), "--version"])
        msg = ""
    except C.AtalhoQuebrado as exc:
        msg = str(exc)
    check("'&'" in msg and "instalador" in msg and "install.ps1" in msg,
          "se só sobrar o atalho e o nome da pasta o quebra, a tela diz o porquê e o "
          "que fazer", msg[:120])
    check("incompleto" in C._nao_respondeu(
              str(atalho3), "[@anthropic-ai/claude-code] Failed to execute native binary at "
              + str(nativo) + "\n  spawnSync " + str(nativo) + " ENOENT")
          and "app Claude" in C._nao_respondeu(str(atalho3), "Failed to execute native binary"),
          "o npm com o programa faltando: a tela diz que a instalação está incompleta e "
          "aponta o Claude Code do app")
    testar_app_claude(tmp)
    check(C._texto("n\u00e3o \u00e9 reconhecido".encode("latin-1")).startswith("n")
          and "\ufffd" not in C._texto("ok".encode()),
          "mensagem do Windows fora do UTF-8 não vira lixo nem derruba")


def testar_app_claude(tmp: Path) -> None:
    """Quem usa o app Claude (a janela, com a aba Code) já tem o Claude Code:
    o app guarda o motor em %APPDATA%\\Claude\\claude-code\\<versão>\\claude.exe.
    E o atalho que o app põe no PATH (WindowsApps\\Claude.exe) abre a JANELA —
    esse o Sharkcut nunca roda."""
    from editor import claude_editor as C

    print("\n-- o Claude Code que vem com o app Claude")
    casa = tmp / "casa do app"
    appdata = casa / "AppData" / "Roaming"
    local = casa / "AppData" / "Local"
    motor = appdata / "Claude" / "claude-code"
    for versao, conteudo in (("2.1.142", b"MZ\x90"), ("2.1.283", b"MZ\x90"),
                             ("2.1.9", b"MZ\x90"), ("2.2.0", b"texto")):
        (motor / versao).mkdir(parents=True)
        (motor / versao / "claude.exe").write_bytes(conteudo)
    janela = local / "Microsoft" / "WindowsApps"
    janela.mkdir(parents=True)
    npm = appdata / "npm"
    npm.mkdir(parents=True)
    for pasta in (janela, npm):
        (pasta / "claude").write_text("#!/bin/sh\nexit 9\n", encoding="utf-8")
        (pasta / "claude").chmod(0o755)
    antes = {k: os.environ.get(k) for k in ("APPDATA", "LOCALAPPDATA", "PATH", "HOME")}
    get_real = C.db.get_setting
    os.environ.update(APPDATA=str(appdata), LOCALAPPDATA=str(local), HOME=str(casa),
                      PATH=os.pathsep.join([str(janela), str(npm)]))
    C.db.get_setting = lambda k, d=None: d
    C.EH_WINDOWS = True
    try:
        check(C._do_app_claude()[0] == motor / "2.1.283" / "claude.exe"
              and all(x.parent.name != "2.2.0" for x in C._do_app_claude()),
              "acha o motor do app, na versão mais nova que é programa de verdade "
              "(a pasta velha que a atualização deixa não ganha pela ordem alfabética)",
              str(C._do_app_claude()[:1]))
        check(C.achar() == str(motor / "2.1.283" / "claude.exe") and C._origem(C.achar()) == "app",
              "com o app instalado, o Sharkcut usa o Claude Code dele — antes do atalho "
              "quebrado do npm")
        loja = (local / "Packages" / "Claude_pzs8sxrjxfjjc" / "LocalCache" / "Roaming"
                / "Claude" / "claude-code" / "2.1.300")
        loja.mkdir(parents=True)
        (loja / "claude.exe").write_bytes(b"MZ\x90")
        check(C.achar() == str(loja / "claude.exe"),
              "o app da Microsoft Store (pasta virtual em Packages) também é achado")
        for v in [*motor.iterdir(), loja]:
            (v / "claude.exe").unlink()
        check(C.achar() == str(npm / "claude"),
              "sem o app, o PATH pula o Claude.exe da JANELA (WindowsApps) e acha o de "
              "verdade", C.achar())
    finally:
        C.EH_WINDOWS = os.name == "nt"
        C.db.get_setting = get_real
        for k, v in antes.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    janelas = [r"C:\Users\R&C\AppData\Local\Microsoft\WindowsApps\Claude.exe",
               r"C:\Users\R&C\AppData\Local\AnthropicClaude\claude.exe",
               r"C:\Program Files\WindowsApps\Claude_1.0.0_x64__pzs8sxrjxfjjc\app\claude.exe",
               "/Applications/Claude.app/Contents/MacOS/Claude"]
    motores = [r"C:\Users\R&C\AppData\Roaming\Claude\claude-code\2.1.283\claude.exe",
               r"C:\Users\R&C\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache"
               r"\Roaming\Claude\claude-code\2.1.283\claude.exe",
               r"C:\Users\R&C\.local\bin\claude.exe",
               r"C:\Users\R&C\AppData\Roaming\npm\claude.cmd"]
    check(C._origem(motores[0]) == "app" and C._origem(motores[1]) == "app"
          and C._origem(r"C:\\U\\npm\\node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe") == "npm",
          "diz de onde o Claude Code veio (o do app, ou o do npm)")
    check(all(C._eh_a_janela(j) for j in janelas)
          and not any(C._eh_a_janela(m) for m in motores),
          "sabe a diferença entre a janela do app e o Claude Code")


def main() -> int:
    import uvicorn

    from editor.server import app

    tmp = Path(tempfile.mkdtemp(prefix="claude-editor-"))
    install(FRASES)
    amostras, _marcas, dur = build_track([(f, 0.8) for f in FRASES], noise=0.0011)
    fonte = make_video(tmp / "fonte.mp4", amostras, dur, 360, 640, 30)

    porta = porta_livre()
    os.environ["EDITOR_PORT"] = str(porta)
    servidor = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=porta,
                                             log_level="warning"))
    threading.Thread(target=servidor.run, daemon=True).start()
    base = f"http://127.0.0.1:{porta}"
    for _ in range(100):
        try:
            http("GET", "/api/health", base=base)
            break
        except OSError:
            time.sleep(0.1)

    testar_atalho_do_windows(tmp)

    falso = tmp / "claude_falso.py"
    falso.write_text(CLAUDE_FALSO.replace("__PY__", sys.executable), encoding="utf-8")
    falso.chmod(falso.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    registro = tmp / "argv.json"
    os.environ["CLAUDE_FALSO_LOG"] = str(registro)
    try:
        print("\n-- o Claude Code nesta máquina")
        e = http("POST", "/api/claude/config", {"caminho": str(falso), "editor_padrao": "claude"},
                 base=base)
        check(e["instalado"] and "falso" in e["versao"] and e["editor_padrao"] == "claude",
              "o Sharkcut acha o Claude Code, lê a versão e lembra quem edita",
              e.get("versao", ""))
        check(e["logado"] is True, "e sabe que ele entrou na conta (claude auth status)")
        os.environ["CLAUDE_FALSO_LOGADO"] = "nao"
        e = http("GET", "/api/claude/estado?forcar=true", base=base)
        entrar = http("POST", "/api/claude/entrar", {}, base=base)
        os.environ.pop("CLAUDE_FALSO_LOGADO")
        check(e["instalado"] and e["logado"] is False and "entrar na conta" in e["motivo"],
              "sem login, a primeira tela diz o que falta: entrar na conta", e["motivo"])
        check(entrar["ok"] is False and "auth login" in entrar["motivo"]
              if os.name != "nt" else entrar["ok"],
              "o botão de entrar abre o login do Claude Code (fora do Windows, diz o "
              "comando)", str(entrar)[:160])
        http("GET", "/api/claude/estado?forcar=true", base=base)

        print("\n-- clique único com o Claude como editor")
        p = http("POST", "/api/projects", {"source_path": str(fonte), "name": "claude",
                                           "preset": "VSL"}, base=base)
        pid = p["id"]
        job = http("POST", f"/api/projects/{pid}/oneclick", {"receita": {
            "editor": "claude", "pedido_claude": "título amarelo no começo, corte seco",
            "pos_claude": True,
            "broll": {"auto": False}}}, base=base)
        fim, msgs = esperar(base, pid, job["id"])
        check(fim["status"] == "ok", f"o clique único termina (não travou): {fim['status']}",
              fim.get("error") or "")
        res = fim.get("result") or {}
        cl = res.get("claude") or {}
        check(cl.get("ok") and "título amarelo" in cl.get("relatorio", ""),
              "o Claude editou e o relatório dele volta", cl.get("erro", ""))
        check("conferi o quadro" in cl.get("relatorio", ""),
              "e ele VIU o quadro do vídeo final (a imagem chegou pelo MCP)")
        check(any(m.startswith("Claude: ") for m in msgs),
              "a tela de processamento mostra o passo a passo do Claude",
              str([m for m in msgs if m.startswith("Claude")][:4]))

        a = json.loads(registro.read_text(encoding="utf-8"))
        argv = a["argv"]
        permitidas = argv[argv.index("--allowedTools") + 1].split(",")
        check("-p" in argv and "--bare" not in argv,
              "roda sem janela (-p) e SEM --bare — o --bare ignoraria o login da assinatura")
        check("--tools" in argv and argv[argv.index("--tools") + 1] == "",
              "nenhuma ferramenta interna do Claude Code (terminal, arquivos, internet)")
        check("--strict-mcp-config" in argv
              and argv[argv.index("--permission-mode") + 1] == "dontAsk",
              "só o MCP do Sharkcut, e nunca uma pergunta que ninguém vai responder")
        check("mcp__sharkcut__grafico" in permitidas
              and "mcp__sharkcut__exportar" not in permitidas
              and "mcp__sharkcut__editar_sozinho" not in permitidas,
              "libera as ferramentas de edição e não as que exportariam ou refariam tudo")
        negadas = argv[argv.index("--disallowedTools") + 1].split(",")
        check("mcp__sharkcut__exportar" in negadas and "mcp__sharkcut__grafico" not in negadas,
              "e as proibidas nem aparecem para ele")
        check(f"projeto {pid}" in a["prompt"] and "título amarelo no começo" in a["prompt"],
              "o pedido leva o projeto e o que ele escreveu na primeira tela")
        check("Gemini NÃO participa" in a["prompt"],
              "e diz que o Gemini está fora")
        check("REVISÃO — o seu trabalho principal" in a["prompt"]
              and "REDUNDÂNCIA" in a["prompt"] and "MULETA" in a["prompt"]
              and "Não refaça isso" in a["prompt"],
              "na primeira edição o trabalho dele é a REVISÃO da fala; o mecânico "
              "(silêncio, aceleração, legenda, filtro, música) o sistema já fez")

        proj = svc.load(pid)
        g = [x for x in proj.plan.graficos if x.origem == "claude"]
        check(len(g) == 1 and g[0].cor == "#FFD400", "o título do Claude está no plano")
        check(any("CLIENTE" in s.text for s in proj.plan.subtitles),
              "a correção de legenda do Claude está nas legendas")
        check(any(abs(c.speed - 1.12) < 0.01 and c.section == "gancho"
                  for c in proj.plan.clips),
              "o ritmo e a etapa que ele escolheu estão no bloco")
        jobs = http("GET", f"/api/jobs?project_id={pid}", base=base)
        check(any(j["kind"] == "edicao" and j["status"] == "ok" for j in jobs),
              "o trabalho que o Claude disparou (refazer o corte) rodou enquanto o "
              "clique único esperava — sem travar")
        check((res.get("previa") or {}).get("ok") is not False and res.get("final_job"),
              "depois do Claude: a prévia e o arquivo final saem juntos")
        check((proj.analysis.get("claude_edicao") or {}).get("ok"),
              "o relatório fica no projeto para a tela do editor")
        check(proj.plan.editor == "claude" and proj.plan.pedido_claude,
              "o projeto lembra que o Claude é o editor e o pedido")

        print("\n-- as ferramentas pela porta local (/mcp), com a chave da edição")
        cfg_mcp = json.loads((proj.dir / "claude" / "mcp.json").read_text(encoding="utf-8"))
        srv = cfg_mcp["mcpServers"]["sharkcut"]
        check(srv.get("type") == "http" and srv["url"].endswith("/mcp")
              and srv["headers"]["Authorization"].startswith("Bearer ")
              and "command" not in srv,
              "o Claude liga as ferramentas pela porta do Sharkcut que já está aberta — "
              "sem abrir um segundo programa (era onde o Windows dele falhava)")
        velha = srv["headers"]["Authorization"]
        check(mcp_post(base, velha, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})[0] == 401,
              "a chave morre quando a edição acaba")
        from editor import claude_editor as C
        chave = "Bearer " + C.abrir_chave()
        st, corpo = mcp_post(base, chave, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                           "params": {"protocolVersion": "2025-06-18"}})
        check(st == 200 and corpo["result"]["serverInfo"]["name"] == "sharkcut",
              "com a chave, a porta responde o MCP", str(corpo)[:100])
        st, corpo = mcp_post(base, chave, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        nomes = {t["name"] for t in corpo["result"]["tools"]}
        check(st == 200 and {"pos_contexto", "cortar", "ver_quadros"} <= nomes,
              "e lista as mesmas ferramentas do mcp.bat", str(len(nomes)))
        check(mcp_post(base, chave, {"jsonrpc": "2.0", "method": "notifications/initialized"})[0]
              == 202, "aviso sem id: 202, sem corpo")
        check(mcp_post(base, "", {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})[0] == 401
              and mcp_post(base, "Bearer inventada", {"jsonrpc": "2.0", "id": 3,
                                                       "method": "tools/list"})[0] == 401,
              "sem a chave (ou com uma inventada), nada")
        check(mcp_post(base, chave, {"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
                       origem="http://site-qualquer.com")[0] == 403
              and mcp_post(base, chave, {"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
                           tipo="text/plain")[0] == 415,
              "uma página aberta no navegador não chama as ferramentas (origem e tipo)")
        check(mcp_get(base, chave) == 405, "GET: 405 (sem fluxo do servidor), como manda a regra")
        C.fechar_chave(chave[7:])

        print("\n-- pedir mais ao Claude de dentro do editor")
        os.environ["CLAUDE_FALSO_PENSA"] = "7"
        job = http("POST", f"/api/projects/{pid}/claude", {"pedido": "aumenta a legenda"},
                   base=base)
        fim, _m = esperar(base, pid, job["id"])
        os.environ.pop("CLAUDE_FALSO_PENSA")
        check(any("pensando há" in m for m in _m),
              "enquanto ele pensa, a barra diz há quanto tempo — não parece travada",
              str([m for m in _m if "Claude" in m][:3]))
        a = json.loads(registro.read_text(encoding="utf-8"))
        check(fim["status"] == "ok" and (fim.get("result") or {}).get("claude", {}).get("ok"),
              "o pedido de retoque roda (em linha própria, sem travar)", fim.get("error") or "")
        check("PEDIU AGORA: aumenta a legenda" in a["prompt"]
              and "SÓ o que ele pediu" in a["prompt"],
              "e o Claude recebe só o pedido novo, para não refazer o resto")

        print("\n-- as ferramentas não ligam: para na hora e diz o porquê")
        os.environ["CLAUDE_FALSO_MODO"] = "sem_ferramentas"
        t_ini = time.time()
        job = http("POST", f"/api/projects/{pid}/claude", {"pedido": "qualquer coisa"},
                   base=base)
        fim, _m = esperar(base, pid, job["id"])
        os.environ.pop("CLAUDE_FALSO_MODO")
        cl = (fim.get("result") or {}).get("claude") or {}
        check(not cl.get("ok") and "ligar as ferramentas" in cl.get("erro", "")
              and time.time() - t_ini < 20,
              "sem as ferramentas, o Sharkcut encerra o Claude na hora (não deixa ele "
              "inventar texto) e escreve o motivo", f"{cl.get('erro', '')} {time.time() - t_ini:.0f}s")

        print("\n-- o Claude editando, SEM a pós-edição (só o que o Gemini fazia)")
        p3 = http("POST", "/api/projects", {"source_path": str(fonte), "name": "sem pós",
                                            "preset": "VSL"}, base=base)
        job = http("POST", f"/api/projects/{p3['id']}/oneclick", {"receita": {
            "editor": "claude", "pos_claude": False, "broll": {"auto": False}}}, base=base)
        fim, _m = esperar(base, p3["id"], job["id"])
        a = json.loads(registro.read_text(encoding="utf-8"))
        argv = a["argv"]
        negadas = argv[argv.index("--disallowedTools") + 1].split(",")
        proj = svc.load(p3["id"])
        check(fim["status"] == "ok" and "mcp__sharkcut__grafico" in negadas
              and "mcp__sharkcut__ritmo" not in negadas,
              "sem pós: as ferramentas de gráfico nem existem para ele; as de edição sim")
        check("SEM PÓS-EDIÇÃO" in a["prompt"] and not proj.plan.graficos
              and any(abs(c.speed - 1.12) < 0.01 for c in proj.plan.clips),
              "e o vídeo sai só com a edição dele — nenhum gráfico por cima")
        check((proj.analysis.get("claude_edicao") or {}).get("modo") == "edicao",
              "o relatório diz que foi edição sem pós")

        print("\n-- a regra (ou o Gemini) edita, e o Claude entra SÓ com a pós")
        p4 = http("POST", "/api/projects", {"source_path": str(fonte), "name": "só pós",
                                            "preset": "VSL"}, base=base)
        job = http("POST", f"/api/projects/{p4['id']}/oneclick", {"receita": {
            "editor": "", "pos_claude": True, "pedido_claude": "telas de tópico",
            "broll": {"auto": False}}}, base=base)
        fim, _m = esperar(base, p4["id"], job["id"])
        a = json.loads(registro.read_text(encoding="utf-8"))
        argv = a["argv"]
        permitidas = argv[argv.index("--allowedTools") + 1].split(",")
        proj = svc.load(p4["id"])
        res = fim.get("result") or {}
        check(fim["status"] == "ok" and (res.get("claude") or {}).get("ok"),
              "o clique único termina com a pós do Claude", fim.get("error") or "")
        check("Você faz a PÓS-EDIÇÃO" in a["prompt"] and "telas de tópico" in a["prompt"],
              "o Claude recebe o pedido de pós (não o de edição)")
        check("mcp__sharkcut__grafico" in permitidas
              and "mcp__sharkcut__ritmo" not in permitidas
              and "mcp__sharkcut__cortar" not in permitidas
              and "mcp__sharkcut__respiro" not in permitidas,
              "na pós ele NÃO pode mexer na edição: corte, ritmo e fôlego ficam de fora")
        check(len(proj.plan.graficos) == 1
              and not any(abs(c.speed - 1.12) < 0.01 for c in proj.plan.clips),
              "o gráfico entrou e a edição da regra ficou intacta")
        check(proj.plan.editor == "" and res.get("final_job")
              and (res.get("previa") or {}).get("ok") is not False,
              "quem editou continua sendo a regra; prévia e arquivo final saem depois da pós")

        print("\n-- o botão 'fazer a pós-edição' no editor")
        p5 = http("POST", "/api/projects", {"source_path": str(fonte), "name": "pós depois",
                                            "preset": "VSL"}, base=base)
        job = http("POST", f"/api/projects/{p5['id']}/oneclick",
                   {"receita": {"editor": "", "pos_claude": False, "broll": {"auto": False}}},
                   base=base)
        esperar(base, p5["id"], job["id"])
        check(not svc.load(p5["id"]).plan.graficos, "entregou só a edição")
        job = http("POST", f"/api/projects/{p5['id']}/claude", {"modo": "pos"}, base=base)
        fim, _m = esperar(base, p5["id"], job["id"])
        proj = svc.load(p5["id"])
        check(fim["status"] == "ok" and len(proj.plan.graficos) == 1
              and proj.plan.editor == "" and proj.plan.pos_claude,
              "um clique depois, a pós entra por cima — sem trocar quem editou")

        print("\n-- sem login: o vídeo sai do mesmo jeito, pela regra")
        os.environ["CLAUDE_FALSO_MODO"] = "sem_login"
        p2 = http("POST", "/api/projects", {"source_path": str(fonte), "name": "sem login",
                                            "preset": "VSL"}, base=base)
        job = http("POST", f"/api/projects/{p2['id']}/oneclick",
                   {"receita": {"editor": "claude"}}, base=base)
        fim, _m = esperar(base, p2["id"], job["id"])
        cl = (fim.get("result") or {}).get("claude") or {}
        check(fim["status"] == "ok" and not cl.get("ok") and "login" in cl.get("erro", ""),
              "o clique único termina, e diz que falta o login do Claude Code",
              cl.get("erro", ""))
        check((fim.get("result") or {}).get("final_job"),
              "e o arquivo final sai mesmo assim")
    finally:
        servidor.should_exit = True
        os.environ.pop("CLAUDE_FALSO_MODO", None)

    print()
    if FALHAS:
        print(f"{len(FALHAS)} FALHA(S):")
        for f in FALHAS:
            print("  -", f)
        return 1
    print("o Claude edita de ponta a ponta")
    return 0


if __name__ == "__main__":
    sys.exit(main())
