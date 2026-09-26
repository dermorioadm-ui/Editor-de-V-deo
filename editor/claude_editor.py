"""O Claude Code como EDITOR, disparado pelo próprio Sharkcut.

Na primeira tela ele escolhe "quem edita: Claude". O clique único faz o que
é mecânico — transcrever, cortar o silêncio pela regra, legendar — e então
chama o Claude Code que está instalado NESTA máquina, sem janela
(``claude -p``), com o login da assinatura dele: nenhuma chave paga, nenhum
servidor novo. O Claude recebe o projeto e as ferramentas do Sharkcut pelo
MCP — as mesmas que ele usa quando é chamado na conversa — e decide tudo o que
o Gemini decidia: o que sai do corte, o fôlego, a velocidade e a câmera de
cada bloco, a legenda, o b-roll, e a pós-edição inteira. Quando ele termina,
o Sharkcut gera a prévia e o arquivo final. Tudo junto, num clique.

TRÊS TRAVAS DE SEGURANÇA, na linha de comando:

- ``--tools ""``: nenhuma ferramenta interna do Claude Code (terminal, editar
  arquivo, internet). Ele só enxerga as do Sharkcut.
- ``--strict-mcp-config`` com um arquivo de MCP só nosso: nenhum outro
  servidor MCP que ele tenha configurado entra nesta edição.
- ``--allowedTools`` com a lista exata das ferramentas liberadas e
  ``--permission-mode dontAsk``: o que não está na lista é negado sem
  pergunta — e nunca há pergunta, porque ninguém está olhando para responder.
  Ficam de fora as que abririam outro projeto, exportariam ou refariam o
  clique único por cima deste.

NÃO usa ``--bare``: nesse modo o Claude Code só aceita chave de API e
ignora o login da assinatura (medido: é o que diz o ``claude --help``).

O QUE SAI DA MÁQUINA: o que o Claude lê vai para a Anthropic — o texto da
transcrição e, quando ele confere o resultado, quadros soltos do vídeo em
tamanho de conferência. O arquivo de vídeo não sai.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import db
from .config import DATA_DIR, PORT

# o que o Claude NÃO pode chamar dentro desta edição
EXCLUIDAS = {"editar_sozinho", "abrir_video", "juntar_videos", "adicionar_video",
             "exportar", "listar_projetos", "gravacoes"}
SERVIDOR = "sharkcut"
TETO_MINUTOS = 45
MODELOS = ("", "opus", "sonnet", "fable", "haiku")

_cache: dict = {}


# ------------------------------------------------------------- achar o claude
def _candidatos() -> list[Path]:
    casa = Path.home()
    out = [casa / ".local" / "bin" / "claude.exe", casa / ".local" / "bin" / "claude",
           casa / ".claude" / "local" / "claude.exe", casa / ".claude" / "local" / "claude",
           casa / ".npm-global" / "bin" / "claude", Path("/usr/local/bin/claude"),
           Path("/opt/homebrew/bin/claude")]
    appdata = os.environ.get("APPDATA")
    if appdata:
        out.append(Path(appdata) / "npm" / "claude.cmd")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(Path(local) / "Programs" / "claude" / "claude.exe")
    return out


def achar() -> str:
    """O executável do Claude Code: o que ele apontou, o instalador nativo, o
    do PATH, os de praxe. O nativo (claude.exe) vem ANTES do PATH: o PATH do
    Windows costuma achar primeiro o atalho claude.cmd do npm, e um .exe roda
    direto, sem o cmd.exe no meio."""
    guardado = str(db.get_setting("claude_caminho", "") or "").strip().strip('"')
    if guardado and Path(guardado).is_file():
        return guardado
    nativo = Path.home() / ".local" / "bin" / "claude.exe"
    if nativo.is_file():
        return str(nativo)
    achado = shutil.which("claude")
    if achado:
        return achado
    for c in _candidatos():
        if c.is_file():
            return str(c)
    return ""


# O ATALHO DO NPM. Instalado pelo npm, o Claude Code vira um claude.cmd — um
# arquivo de lote. Rodar um .cmd passa pelo cmd.exe, e o cmd.exe tem uma regra
# antiga com aspas: se a linha tem mais de duas, ele tira a PRIMEIRA e a
# ÚLTIMA. Com o usuário "C:\Users\Renato Fulano", o caminho do atalho perdia
# as aspas e quebrava no espaço — "'C:\Users\Renato' não é reconhecido como
# um comando interno ou externo" (visto na máquina dele). Então o atalho é
# LIDO, e o programa que ele chama (node + cli.js, ou um .exe) roda direto.
EH_WINDOWS = os.name == "nt"


def _binario_de_verdade(p: Path) -> bool:
    """Um .exe que é programa mesmo. O pacote do npm deixa um "claude.exe" de
    mentira (um texto de erro) até o pós-instalação trocar pelo nativo."""
    try:
        if not p.is_file():
            return False
        if not EH_WINDOWS:
            return True
        with open(p, "rb") as f:
            return f.read(2) == b"MZ"
    except OSError:
        return False


def _nativos_do_npm(pasta: Path) -> list[Path]:
    """Onde o npm põe o Claude Code NATIVO (versões novas: um .exe de verdade)."""
    base = pasta / "node_modules" / "@anthropic-ai"
    out = [base / "claude-code" / "bin" / "claude.exe"]
    for arq in ("win32-x64", "win32-arm64"):
        out += [base / f"claude-code-{arq}" / "claude.exe",
                base / "claude-code" / "node_modules" / "@anthropic-ai"
                / f"claude-code-{arq}" / "claude.exe"]
    return out


def _achar_node(pasta: Path) -> str:
    """O node.exe: ao lado do atalho, no PATH, ou nas pastas de praxe do Windows."""
    candidatos = [pasta / "node.exe"]
    achado = shutil.which("node")
    if achado:
        candidatos.append(Path(achado))
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        if os.environ.get(var):
            candidatos.append(Path(os.environ[var]) / "nodejs" / "node.exe")
    if os.environ.get("LOCALAPPDATA"):
        candidatos.append(Path(os.environ["LOCALAPPDATA"]) / "Programs" / "nodejs" / "node.exe")
    if os.environ.get("NVM_SYMLINK"):
        candidatos.append(Path(os.environ["NVM_SYMLINK"]) / "node.exe")
    for c in candidatos:
        if c.is_file():
            return str(c)
    return ""


def _alvo_do_atalho(atalho: Path) -> list[str] | None:
    """O programa por trás de um claude.cmd, sem o cmd.exe. None = não achei.

    Por ordem: o que o próprio atalho chama (lido do arquivo); o Claude nativo
    onde o npm o instala; o cli.js com o node. Um usuário com "&" no nome
    ("Renato&Cibele") não pode depender do atalho: o próprio claude.cmd do npm
    faz SET dp0=%~dp0 sem aspas, e o & corta o caminho ali — o atalho quebra
    até no terminal dele (visto na máquina dele).
    """
    try:
        texto = atalho.read_text(encoding="utf-8", errors="replace")
    except OSError:
        texto = ""
    pasta = atalho.parent
    relativos = re.findall(r'"%~?dp0%?\\?([^"%]+?\.(?:js|cjs|mjs|exe))"', texto, re.I)
    alvos: list[Path] = []
    for rel in relativos:
        partes = [x for x in re.split(r"[\\/]", rel) if x]
        if partes and partes[-1].lower() != "node.exe":   # o node dele não é o Claude
            alvos.append(pasta.joinpath(*partes))
    alvos += _nativos_do_npm(pasta)
    js = pasta / "node_modules" / "@anthropic-ai" / "claude-code"
    alvos += [js / "cli.js", js / "cli-wrapper.cjs"]
    for alvo in alvos:
        if alvo.suffix.lower() == ".exe":
            if _binario_de_verdade(alvo):
                return [str(alvo)]
            continue
        if alvo.is_file():
            node = _achar_node(pasta)
            if node:
                return [node, str(alvo)]
    return None


# o que o cmd.exe interpreta mesmo dentro de um .cmd — com isso no caminho, o
# atalho do npm não tem como funcionar, com aspas ou sem
_ESPECIAIS_DO_CMD = set("&^%!()")


def _base(caminho: str) -> list[str] | None:
    """Como chamar este executável sem o cmd.exe. None = só pelo cmd.exe."""
    if Path(caminho).suffix.lower() in (".cmd", ".bat"):
        return _alvo_do_atalho(Path(caminho))
    return [caminho]


class AtalhoQuebrado(OSError):
    """O único caminho é o claude.cmd, e o nome da pasta o quebra no cmd.exe."""


def _linha_do_cmd(cmd: list[str]) -> str:
    """A última saída, se o atalho não pôde ser lido: pelo cmd.exe, com /s.

    Com /s o cmd.exe tira só as aspas de FORA da linha, e as de dentro — as
    do caminho com espaço — sobrevivem.
    """
    return 'cmd.exe /d /s /c "' + subprocess.list2cmdline(cmd) + '"'


def _abrir(cmd: list[str], **kw) -> subprocess.Popen:
    kw.setdefault("creationflags", getattr(subprocess, "CREATE_NO_WINDOW", 0))
    base = _base(cmd[0])
    if base is not None:
        return subprocess.Popen([*base, *cmd[1:]], **kw)
    if _ESPECIAIS_DO_CMD & set(cmd[0]):
        raise AtalhoQuebrado(
            f"o Claude Code está instalado pelo npm em {Path(cmd[0]).parent}, e o "
            f"'{''.join(sorted(_ESPECIAIS_DO_CMD & set(cmd[0])))}' no nome da pasta quebra "
            f"o atalho claude.cmd no Windows. Instale o Claude Code pelo instalador "
            f"nativo (no PowerShell: irm https://claude.ai/install.ps1 | iex) e aperte "
            f"testar de novo — ou cole aqui o caminho de um claude.exe")
    return subprocess.Popen(_linha_do_cmd(cmd), **kw)


def _texto(b: bytes) -> str:
    """Saída de processo em texto: UTF-8 (o Claude Code) ou a página de código
    do console (as mensagens do próprio Windows, que saíam "n�o �")."""
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("oem" if os.name == "nt" else "latin-1", errors="replace")


def _rodar(args: list[str], timeout: float = 20.0,
           entrada: bytes | None = None) -> subprocess.CompletedProcess:
    p = _abrir(args, stdin=subprocess.PIPE if entrada is not None else subprocess.DEVNULL,
               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        out, err = p.communicate(entrada, timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        raise
    return subprocess.CompletedProcess(args, p.returncode, _texto(out), _texto(err))


def estado(forcar: bool = False) -> dict:
    """Instalado? Qual versão? (Barato: não gasta nada da assinatura.)"""
    agora = time.time()
    if not forcar and _cache.get("estado") and agora - _cache["estado_t"] < 60:
        return _cache["estado"]
    caminho = achar()
    info = {"instalado": False, "caminho": caminho, "versao": "",
            "modelo": str(db.get_setting("claude_modelo", "") or ""),
            "motivo": ""}
    if not caminho:
        info["motivo"] = ("não achei o Claude Code nesta máquina — instale "
                          "(claude.ai/code) e faça login uma vez")
    else:
        try:
            r = _rodar([caminho, "--version"])
            if r.returncode == 0 and r.stdout.strip():
                info["instalado"] = True
                info["versao"] = r.stdout.strip().splitlines()[0][:80]
            else:
                info["motivo"] = (f"achei em {caminho}, mas ele não respondeu: "
                                  + (r.stderr or r.stdout or "sem mensagem").strip())[:400]
        except AtalhoQuebrado as exc:
            info["motivo"] = str(exc)[:600]
        except (OSError, subprocess.SubprocessError) as exc:
            info["motivo"] = f"não consegui rodar o Claude Code: {exc}"[:300]
    _cache["estado"], _cache["estado_t"] = info, agora
    return info


def _ajuda(caminho: str) -> str:
    if _cache.get("ajuda_de") != caminho:
        try:
            _cache["ajuda"] = _rodar([caminho, "--help"]).stdout or ""
        except (OSError, subprocess.SubprocessError):
            _cache["ajuda"] = ""
        _cache["ajuda_de"] = caminho
    return _cache["ajuda"]


# ------------------------------------------------------------------- montar
# TRÊS MODOS, porque ele decide vídeo a vídeo o que entregar:
#   completo — a edição (o que o Gemini fazia) E a pós-edição (o "After
#              Effects": títulos, telas, transições, camadas);
#   edicao   — SÓ a edição: o vídeo sai cortado, no ritmo, legendado, com
#              b-roll, sem nenhum gráfico por cima;
#   pos      — SÓ a pós-edição, em cima de uma edição que já existe (do
#              Gemini, da regra, dele à mão ou do próprio Claude antes).
# A trava é na lista de ferramentas, não só no pedido: no modo "edicao" as
# ferramentas de gráfico não existem para ele; no "pos", as de corte não.
MODOS = ("completo", "edicao", "pos")
FERRAMENTAS_DA_POS = {"grafico", "camada", "transicao", "tirar_da_pos"}
FERRAMENTAS_DE_LEITURA = {"pos_contexto", "transcricao", "ver_projeto", "ver_quadros",
                          "analisar_cena", "estado_do_editor"}


def _nomes() -> list[str]:
    from .mcp.ferramentas import catalogo

    return [f["name"] for f in catalogo()]


def _liberadas(modo: str) -> set[str]:
    todas = set(_nomes()) - EXCLUIDAS
    if modo == "edicao":
        return todas - FERRAMENTAS_DA_POS
    if modo == "pos":
        return (FERRAMENTAS_DE_LEITURA | FERRAMENTAS_DA_POS) & todas
    return todas


def ferramentas_liberadas(modo: str = "completo") -> list[str]:
    return [f"mcp__{SERVIDOR}__{n}" for n in _nomes() if n in _liberadas(modo)]


def ferramentas_negadas(modo: str = "completo") -> list[str]:
    return [f"mcp__{SERVIDOR}__{n}" for n in _nomes() if n not in _liberadas(modo)]


def config_mcp(pasta: Path) -> Path:
    """O arquivo de MCP desta edição: só o servidor do Sharkcut."""
    raiz = str(Path(__file__).resolve().parent.parent)
    env = {"EDITOR_PORT": str(os.environ.get("EDITOR_PORT") or PORT),
           "PYTHONPATH": raiz, "PYTHONIOENCODING": "utf-8",
           "EDITOR_DATA_DIR": str(os.environ.get("EDITOR_DATA_DIR") or DATA_DIR)}
    cfg = {"mcpServers": {SERVIDOR: {"command": sys.executable,
                                     "args": ["-m", "editor.mcp"], "env": env}}}
    pasta.mkdir(parents=True, exist_ok=True)
    alvo = pasta / "mcp.json"
    alvo.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    return alvo


SISTEMA = (
    "Você está editando um vídeo SEM SUPERVISÃO dentro do Sharkcut, o editor "
    "que roda na máquina do dono do vídeo. Ninguém vai responder perguntas "
    "durante a edição: decida e siga. Use só as ferramentas do Sharkcut. "
    "Escreva em português do Brasil."
)

GUIA_DA_POS = (
    "PÓS-EDIÇÃO com grafico, transicao e camada: título forte no gancho; "
    "tela de tópico quando o assunto muda; lista quando ele enumera (cada item "
    "entrando quando é falado, com itens_em); número quando cita valor; nome "
    "no começo se ele se apresenta; destaque na palavra que carrega a frase; "
    "poucas transições (nas mudanças de assunto); camada desfoque/escurecer "
    "nos momentos de ênfase; texto atrás da pessoa em títulos grandes quando "
    "ela está no centro. Use analisar_cena para posicionar. Um gráfico por "
    "ideia, nunca em cima da legenda, nunca cobrindo o rosto.")


def _freq(f: str) -> str:
    return {"pouco": "pouco (um a cada ~20 s)", "medio": "médio (um a cada ~12 s)",
            "muito": "muito (um a cada ~7 s)"}.get(f, f)


def _dono(plan) -> str:
    return (getattr(plan, "pedido_claude", "") or "").strip()


def pedido_de_retoque(project, texto: str) -> str:
    """Um pedido NO MEIO da edição: faça só isto, sem refazer o resto."""
    from .projects import duracao_de_saida

    return "\n".join([
        f"Você é o editor do projeto {project.id} (\"{project.name}\") no Sharkcut. "
        f"O vídeo JÁ está editado ({duracao_de_saida(project):.1f} s) — por você "
        f"antes e/ou à mão pelo dono. O Gemini não participa.",
        "",
        "O DONO DO VÍDEO PEDIU AGORA: " + texto.strip(),
        "",
        "Faça SÓ o que ele pediu, sem refazer o resto nem desfazer o que ele "
        "mexeu à mão. Leia pos_contexto antes, confira o resultado com "
        "ver_quadros e termine com um relatório curto do que mudou. Você não "
        "exporta: o Sharkcut refaz a prévia e o arquivo sozinho.",
    ])


def pedido_da_pos(project) -> str:
    """SÓ a pós-edição, por cima de uma edição que já está pronta."""
    from .projects import duracao_de_saida

    dono = _dono(project.plan)
    return "\n".join([
        f"Você faz a PÓS-EDIÇÃO do projeto {project.id} (\"{project.name}\") no "
        f"Sharkcut. A edição (cortes, ritmo, câmera, legenda, b-roll) já está "
        f"pronta ({duracao_de_saida(project):.1f} s) e NÃO é sua para mexer: você "
        f"só acrescenta por cima.",
        "",
        "O QUE O DONO DO VÍDEO PEDIU: " + (dono or "nada específico — a pós que "
                                           "um editor de After Effects faria."),
        "",
        "ORDEM DE TRABALHO:",
        "1. pos_contexto (o roteiro com os tempos do vídeo final) e, se precisar, "
        "transcricao.",
        "2. " + GUIA_DA_POS,
        "3. CONFIRA com ver_quadros o começo, cada gráfico e cada transição, e "
        "corrija o que ficou ruim com grafico(id=...).",
        "4. Termine com um RELATÓRIO curto, em tópicos, do que você pôs e por quê. "
        "Você não exporta: o Sharkcut gera a prévia e o arquivo sozinho.",
    ])


REVISAO = [
    "REVISÃO — o seu trabalho principal. Leia a transcrição INTEIRA antes de "
    "cortar qualquer coisa: entenda a mensagem, o gancho, a promessa e a oferta. "
    "Depois corte, com cortar (pelos números das palavras), tudo o que tira a "
    "dinâmica da conversa:",
    "  • REDUNDÂNCIA: a mesma ideia dita duas vezes com outras palavras — fica a "
    "versão mais forte e mais curta.",
    "  • REPETIÇÃO e TAKE REFEITO: a frase começada, abandonada e dita de novo — "
    "fica a última versão completa.",
    "  • MULETA que não carrega sentido: 'né', 'tipo', 'então', 'assim', 'é...', "
    "'hã', 'basicamente', 'na verdade', 'vamos lá', 'beleza?', 'olha só', "
    "'sabe?', 'enfim', 'aí'.",
    "  • ENROLAÇÃO de abertura e de transição: 'então pessoal, hoje eu vou falar "
    "com vocês sobre...', 'antes de começar...', 'como eu falei antes...' — o "
    "vídeo começa no que importa.",
    "  • EXPLICAÇÃO DEMAIS: detalhe que não muda a decisão de quem assiste, "
    "exemplo em dobro, a mesma prova contada duas vezes.",
    "  • AUTOCORREÇÃO audível ('quer dizer...', 'digo...') — fica só a forma certa.",
    "  O QUE NUNCA SAI: o gancho, a promessa, o preço, a garantia, o CTA, números "
    "e provas. Nenhum corte pode deixar frase sem sentido ou com concordância "
    "quebrada: leia a frase como ela fica depois do corte. Prefira tirar a "
    "expressão inteira a picotar uma palavra no meio da fala corrida; se o "
    "cortar disser 'NÃO cortado', corte a expressão em volta ou deixe como está.",
    "  Depois, RELEIA o que ficou (transcricao com restante=true): o texto tem que "
    "fluir como se tivesse sido dito assim. Se algum corte truncou a fala, use "
    "devolver. Palavra mal transcrita (nome, marca, número) também é revisão: "
    "legendas acao=corrigir.",
]


def _o_que_o_sistema_fez(plan) -> str:
    feito = ["o corte de silêncio", "a aceleração", "o jogo de câmera (zoom)"]
    feito.append("a legenda" if plan.export.burn_subtitles else "sem legenda (pedido)")
    if plan.look and plan.look != "nenhum":
        feito.append(f"o filtro ({plan.look})")
    if (plan.music or {}).get("media_id"):
        feito.append("a música")
    return ", ".join(feito)


def pedido(project, modo: str = "completo") -> str:
    """O pedido da primeira edição: a REVISÃO é o trabalho do Claude.

    O mecânico — corte de silêncio, aceleração, câmera, legenda, filtro,
    música — o sistema já fez, do jeito que a primeira tela pediu. O que o
    sistema não sabe fazer é ler a fala como um editor lê um texto: tirar a
    redundância, a repetição, a muleta, a enrolação. É isso que o Claude faz;
    o resto, só se o dono pedir.
    """
    from .projects import duracao_de_saida

    if modo == "pos":
        return pedido_da_pos(project)
    plan = project.plan
    info = project.info
    dur = duracao_de_saida(project)
    formato = plan.export.aspect if plan.export.aspect != "fonte" else (
        "vertical" if info and info.display_size[1] > info.display_size[0] else "horizontal")
    dono = _dono(plan)
    linhas = [
        f"Você é o editor do projeto {project.id} (\"{project.name}\") no Sharkcut, "
        f"na PRIMEIRA edição deste vídeo ({dur:.1f} s, formato {formato}).",
        f"O Gemini NÃO participa: tudo o que ele decidiria, você decide.",
        f"O SISTEMA JÁ FEZ, do jeito que a primeira tela pediu: {_o_que_o_sistema_fez(plan)}. "
        f"Não refaça isso — respiro, ritmo e estilo de legenda só se o dono pedir "
        f"abaixo. Quando você terminar, o Sharkcut gera a prévia e o arquivo final "
        f"sozinho — você não exporta.",
        "",
        "O QUE O DONO DO VÍDEO PEDIU: " + (dono or "nada além da revisão."),
    ]
    if modo == "edicao":
        linhas.append("SEM PÓS-EDIÇÃO: nenhum título, tela, gráfico, transição ou "
                      "camada por cima do vídeo.")
    if plan.alvo_duracao > 0:
        linhas.append(f"DURAÇÃO: o vídeo final tem que caber em {plan.alvo_duracao:.0f} s — "
                      f"a revisão escolhe o que sai (nunca o gancho, o preço ou o CTA).")
    br = plan.broll or {}
    if br.get("auto"):
        linhas.append(f"B-ROLL: ele quer b-roll na frequência {_freq(br.get('frequencia', 'medio'))}"
                      + (f", assunto: {br['assunto']}" if br.get("assunto") else "")
                      + ". Depois da revisão, use buscar_broll com ver=true para escolher "
                        "OLHANDO e broll_do_banco para pôr — termos em português que tenham "
                        "a ver com o assunto do vídeo inteiro. Sem chave do banco, "
                        "broll_automatico.")
    passos = ["pos_contexto e transcricao (TODAS as palavras, de 400 em 400)."]
    passos.append("\n".join(REVISAO))
    passos.append("SÓ SE O DONO PEDIU: respiro (fôlego do corte de silêncio), ritmo "
                  "(velocidade, zoom e etapa de cada bloco), legendas acao=estilo.")
    if br.get("auto"):
        passos.append("B-ROLL.")
    if modo == "completo":
        passos += [GUIA_DA_POS,
                   "CONFIRA com ver_quadros o começo, cada gráfico e cada transição, "
                   "e corrija o que ficou ruim com grafico(id=...)."]
    passos.append("Termine com um RELATÓRIO curto, em tópicos, para o dono do vídeo: "
                  "o que você tirou na revisão (cite os trechos entre aspas) e por quê, "
                  "o que mais mudou, e quanto o vídeo encurtou (antes → depois).")
    linhas += ["", "ORDEM DE TRABALHO:"] + [f"{i}. {x}" for i, x in enumerate(passos, 1)]
    return "\n".join(linhas)


def comando(caminho: str, cfg: Path, modelo: str = "", modo: str = "completo") -> list[str]:
    ajuda = _ajuda(caminho)
    cmd = [caminho, "-p", "--output-format", "stream-json", "--verbose",
           "--mcp-config", str(cfg), "--strict-mcp-config",
           "--allowedTools", ",".join(ferramentas_liberadas(modo)),
           "--append-system-prompt", SISTEMA]
    # as travas que dependem da versão instalada entram quando ela conhece
    if "--tools" in ajuda:
        cmd += ["--tools", ""]
    if "dontAsk" in ajuda:
        cmd += ["--permission-mode", "dontAsk"]
    if "--disallowedTools" in ajuda:
        # as proibidas somem da lista que ele vê: sem isto ele gastava uma
        # volta tentando exportar e levando "negado" (medido com a CLI real)
        cmd += ["--disallowedTools", ",".join(ferramentas_negadas(modo))]
    if modelo and modelo in MODELOS:
        cmd += ["--model", modelo]
    return cmd


# --------------------------------------------------------------- progresso
def _frase(nome: str, a: dict) -> str:
    """O que o Claude está fazendo, em português, para a barra de progresso."""
    a = a or {}
    if nome == "pos_contexto":
        return "lendo o roteiro do vídeo"
    if nome == "transcricao":
        return "lendo a transcrição"
    if nome == "cortar":
        n = len(a.get("palavras") or [])
        return f"cortando {n} palavra(s)" if n else "cortando um trecho"
    if nome == "devolver":
        return f"devolvendo {len(a.get('palavras') or [])} palavra(s) que o corte levou"
    if nome == "respiro":
        return "ajustando o fôlego do corte de silêncio"
    if nome == "ritmo":
        return f"ajustando ritmo e câmera de {len(a.get('blocos') or [])} bloco(s)"
    if nome == "legendas":
        acao = a.get("acao")
        if acao == "corrigir":
            return f"corrigindo a legenda: '{a.get('errado', '')}' → '{a.get('certo', '')}'"
        return {"ver": "revisando as legendas", "editar": "reescrevendo uma legenda",
                "estilo": "ajustando o estilo da legenda"}.get(acao, "legendas")
    if nome == "buscar_broll":
        return f"procurando b-roll: {a.get('termo') or 'pela fala'}"
    if nome in ("broll_do_banco", "broll", "broll_automatico"):
        return "pondo b-roll"
    if nome == "grafico":
        tipo = a.get("tipo") or "gráfico"
        texto = str(a.get("texto") or "")[:40]
        return f"{'ajustando' if a.get('id') else 'pondo'} {tipo}" + (f": {texto}" if texto else "")
    if nome == "camada":
        return f"camada: {a.get('efeito') or 'ajuste'}"
    if nome == "transicao":
        return f"transição {a.get('tipo') or ''}".strip()
    if nome == "ver_quadros":
        return f"conferindo {len(a.get('tempos') or [])} quadro(s) do vídeo final"
    if nome == "analisar_cena":
        return f"olhando a cena em {float(a.get('tempo') or 0):.1f} s"
    if nome == "tirar_da_pos":
        return "tirando itens da pós-edição"
    return nome.replace("_", " ")


def _curva(n: int) -> float:
    """Progresso sem saber o total: anda rápido no começo e nunca chega a 1."""
    return 0.95 * (1 - math.exp(-n / 22.0))


# --------------------------------------------------------------------- rodar
def editar(pid: str, ctx, modelo: str | None = None, retoque: str = "",
           modo: str = "completo") -> dict:
    """Roda o Claude Code como editor do projeto. Nunca levanta (exceto cancelar).

    Devolve {"ok", "relatorio", "ferramentas", "erro", "segundos", ...}.
    """
    from . import projects as svc

    t0 = time.time()
    modo = modo if modo in MODOS else "completo"
    saida = {"ok": False, "relatorio": "", "ferramentas": 0, "erro": "",
             "passos": [], "modo": modo}
    ctx.stage("claude", "chamando o Claude Code nesta máquina")
    est = estado(forcar=True)
    if not est["instalado"]:
        saida["erro"] = est["motivo"] or "Claude Code não encontrado"
        return _gravar(pid, saida, t0)
    project = svc.load(pid)
    pasta = project.dir / "claude"
    cfg = config_mcp(pasta)
    modelo = modelo if modelo is not None else str(db.get_setting("claude_modelo", "") or "")
    cmd = comando(est["caminho"], cfg, modelo, modo)
    env = dict(os.environ)
    env.setdefault("MCP_TIMEOUT", "60000")
    env.setdefault("MCP_TOOL_TIMEOUT", "900000")
    env["PYTHONIOENCODING"] = "utf-8"
    diario = pasta / f"sessao_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
    try:
        proc = _abrir(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                      stderr=subprocess.PIPE, cwd=str(pasta), env=env)
    except OSError as exc:
        saida["erro"] = f"não consegui abrir o Claude Code: {exc}"
        return _gravar(pid, saida, t0)

    # o pedido vai pela entrada padrão: a linha de comando do Windows para em
    # 32767 caracteres, e o pedido não tem por que disputar esse espaço
    try:
        texto = pedido_de_retoque(project, retoque) if retoque.strip() else pedido(project, modo)
        proc.stdin.write(texto.encode("utf-8"))
        proc.stdin.close()
    except OSError:
        pass

    erro_bruto: list[bytes] = []

    def ler_erros() -> None:
        for linha in proc.stderr:
            erro_bruto.append(linha)
            del erro_bruto[:-40]

    threading.Thread(target=ler_erros, daemon=True).start()
    parar = threading.Event()
    cancelado = threading.Event()

    def vigia() -> None:
        limite = t0 + TETO_MINUTOS * 60
        while not parar.wait(1.0):
            if ctx.cancelled():
                cancelado.set()
            if cancelado.is_set() or time.time() > limite:
                try:
                    proc.kill()
                except OSError:
                    pass
                return

    threading.Thread(target=vigia, daemon=True).start()
    resultado: dict = {}
    ultimo_texto = ""
    try:
        with open(diario, "wb") as log:
            for bruto in proc.stdout:
                log.write(bruto)
                try:
                    ev = json.loads(bruto.decode("utf-8", "replace"))
                except ValueError:
                    continue
                tipo = ev.get("type")
                if tipo == "system" and ev.get("subtype") == "init":
                    servidores = {s.get("name"): s.get("status")
                                  for s in ev.get("mcp_servers") or []}
                    if servidores.get(SERVIDOR) not in (None, "connected"):
                        saida["erro"] = (f"o Claude não conseguiu ligar as ferramentas do "
                                         f"Sharkcut ({servidores.get(SERVIDOR)})")
                    ctx.progress(0.01, f"Claude conectado ({ev.get('model', '')})")
                elif tipo == "assistant":
                    msg = ev.get("message") or ev
                    for bloco in msg.get("content") or []:
                        if bloco.get("type") == "tool_use":
                            nome = str(bloco.get("name", "")).split("__")[-1]
                            saida["ferramentas"] += 1
                            frase = _frase(nome, bloco.get("input") or {})
                            saida["passos"].append(frase)
                            del saida["passos"][:-60]
                            try:
                                ctx.progress(_curva(saida["ferramentas"]),
                                             f"Claude: {frase}", "claude")
                            except KeyboardInterrupt:
                                cancelado.set()
                                raise
                        elif bloco.get("type") == "text" and bloco.get("text", "").strip():
                            ultimo_texto = bloco["text"].strip()
                elif tipo == "result":
                    resultado = ev
        proc.wait(timeout=30)
    except KeyboardInterrupt:
        cancelado.set()
    finally:
        parar.set()
        try:
            if proc.poll() is None:
                proc.kill()
        except OSError:
            pass
    if cancelado.is_set() and ctx.cancelled():
        raise KeyboardInterrupt("cancelado")

    saida["segundos"] = round(time.time() - t0, 1)
    saida["custo_usd"] = resultado.get("total_cost_usd")
    saida["turnos"] = resultado.get("num_turns")
    saida["diario"] = str(diario)
    if resultado and not resultado.get("is_error") and resultado.get("subtype", "success") == "success":
        saida["ok"] = not saida["erro"] or saida["ferramentas"] > 0
        saida["relatorio"] = str(resultado.get("result") or ultimo_texto).strip()[:4000]
    else:
        detalhe = str(resultado.get("result") or "") if resultado else ""
        erro = _texto(b"".join(erro_bruto)).strip()
        if time.time() - t0 > TETO_MINUTOS * 60 - 2:
            detalhe = f"passou de {TETO_MINUTOS} min e foi interrompido"
        saida["erro"] = saida["erro"] or _explicar(detalhe or erro or
                                                   f"o Claude Code saiu com código {proc.returncode}")
        # o que ele já tinha feito continua no projeto
        saida["relatorio"] = ultimo_texto[:2000]
    return _gravar(pid, saida, t0)


def _explicar(bruto: str) -> str:
    b = bruto.lower()
    if "login" in b or "not logged" in b or "authenticat" in b or "401" in b:
        return ("o Claude Code não está logado nesta máquina: abra um terminal, "
                "rode 'claude' e faça login uma vez")
    if "limit" in b and ("usage" in b or "rate" in b):
        return "o limite de uso da sua assinatura do Claude foi atingido — tente mais tarde"
    return bruto.strip().splitlines()[-1][:300] if bruto.strip() else "o Claude Code falhou"


def _gravar(pid: str, saida: dict, t0: float) -> dict:
    """O relatório fica no projeto: a tela do editor mostra o que ele fez."""
    from . import projects as svc

    saida.setdefault("segundos", round(time.time() - t0, 1))
    try:
        p = svc.load(pid)
        p.analysis["claude_edicao"] = {
            "ok": saida["ok"], "modo": saida.get("modo", "completo"), "relatorio": saida.get("relatorio", ""),
            "erro": saida.get("erro", ""), "ferramentas": saida.get("ferramentas", 0),
            "passos": saida.get("passos", [])[-30:], "segundos": saida["segundos"],
            "quando": time.time()}
        p.save_analysis()
    except Exception:  # noqa: BLE001 — o relatório não pode derrubar a edição
        pass
    return saida


def testar(modelo: str = "") -> dict:
    """Uma pergunta mínima, para saber se está instalado E logado."""
    est = estado(forcar=True)
    if not est["instalado"]:
        return {"ok": False, "motivo": est["motivo"]}
    cmd = [est["caminho"], "-p", "Responda só com a palavra OK.",
           "--output-format", "json"]
    if "--tools" in _ajuda(est["caminho"]):
        cmd += ["--tools", ""]
    if modelo and modelo in MODELOS:
        cmd += ["--model", modelo]
    try:
        r = _rodar(cmd, timeout=120)
    except subprocess.TimeoutExpired:
        return {"ok": False, "motivo": "o Claude Code não respondeu em 2 minutos"}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "motivo": str(exc)[:200]}
    try:
        dados = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
    except ValueError:
        dados = {}
    if r.returncode == 0 and not dados.get("is_error"):
        return {"ok": True, "versao": est["versao"],
                "resposta": str(dados.get("result") or "")[:80]}
    return {"ok": False, "motivo": _explicar(str(dados.get("result") or "")
                                             or r.stderr or r.stdout)}
