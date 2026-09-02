"""A janela de escolher arquivo DO SISTEMA — a de verdade, não uma imitação.

O navegador entrega só nome e tamanho de um arquivo, nunca o caminho. Por isso
existia aqui um explorador próprio, escrito em HTML, que o usuário tinha de
aprender a usar. Ele estava errado por construção: ninguém quer aprender um
explorador de arquivos novo para abrir um MP3.

Só que este servidor roda NA MÁQUINA DO USUÁRIO. Ele pode abrir a janela do
próprio sistema — a mesma de qualquer programa — e receber o caminho de volta.
O arquivo continua sem sair do lugar: o que atravessa é uma string.

Como a janela é aberta, por sistema, em ordem de preferência:

  Windows   PowerShell + System.Windows.Forms.OpenFileDialog. Não depende de
            módulo Python nenhum: o PowerShell existe em toda instalação.
  macOS     osascript com `choose file`, que é o Finder de verdade.
  Linux     zenity ou kdialog, e tkinter como último recurso.

Tudo roda em SUBPROCESSO. Uma janela gráfica aberta dentro do processo do
servidor briga com o laço de eventos do uvicorn e, no Windows, exige a thread
principal — que é justamente a que está atendendo requisição. Em subprocesso,
o pior que pode acontecer é a janela não abrir.
"""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
from pathlib import Path

TIMEOUT = 300.0          # o usuário pode demorar procurando; 5 min é o teto

FILTROS = {
    "video": ("Vídeo", "*.mp4;*.mov;*.mkv;*.webm;*.m4v;*.avi;*.mpg;*.mpeg;*.wmv"),
    "texto": ("Texto", "*.txt"),
    "audio": ("Áudio", "*.mp3;*.wav;*.m4a;*.aac;*.flac;*.ogg;*.opus;*.wma"),
    "image": ("Imagem", "*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.gif;*.tif;*.tiff"),
    # material auxiliar: gravação de tela E print, na mesma janela — separar
    # em duas obrigaria a abrir a janela duas vezes para anexar um vídeo e uma
    # imagem, que é o caso normal
    "media": ("Vídeo ou imagem",
              "*.mp4;*.mov;*.mkv;*.webm;*.m4v;*.avi;*.png;*.jpg;*.jpeg;*.webp"),
}


class SemJanela(RuntimeError):
    """Não deu para abrir a janela do sistema nesta máquina."""


def _ps_script(rotulo: str, padroes: str, varios: bool, titulo: str) -> str:
    # -STA é obrigatório: OpenFileDialog é um controle COM apartment-threaded e
    # sem isso o PowerShell devolve erro em vez de abrir a janela.
    #
    # A PRIMEIRA linha é a que evita o bug que mais dói no Brasil: sem forçar
    # UTF-8 na saída, o PowerShell escreve no code page do console (850/1252) e
    # um caminho como C:\Users\João\Música\trilha.mp3 volta embaralhado — o
    # arquivo "não existe" e ninguém entende por quê.
    filtro = f"{rotulo}|{padroes}|Todos os arquivos|*.*"
    return (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;"
        "Add-Type -AssemblyName System.Windows.Forms | Out-Null;"
        "$d = New-Object System.Windows.Forms.OpenFileDialog;"
        f"$d.Filter = '{filtro}';"
        f"$d.Title = '{titulo}';"
        f"$d.Multiselect = ${'true' if varios else 'false'};"
        "$d.RestoreDirectory = $true;"
        # traz a janela para a frente do navegador em vez de nascer atrás dele
        "$t = New-Object System.Windows.Forms.Form;"
        "$t.TopMost = $true; $t.ShowInTaskbar = $false;"
        "if ($d.ShowDialog($t) -eq [System.Windows.Forms.DialogResult]::OK)"
        " { $d.FileNames | ForEach-Object { Write-Output $_ } }"
    )


def _windows(rotulo: str, padroes: str, varios: bool, titulo: str) -> list[str]:
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        raise SemJanela("não achei o PowerShell nesta máquina")
    # -EncodedCommand (UTF-16LE em base64) em vez de -Command: o comando viaja
    # sem passar pelo code page do console nem pelas regras de aspas do cmd,
    # então acento no título e no filtro chegam inteiros.
    codificado = base64.b64encode(
        _ps_script(rotulo, padroes, varios, titulo).encode("utf-16-le")).decode("ascii")
    r = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand", codificado],
        capture_output=True, timeout=TIMEOUT)
    if r.returncode != 0:
        erro = r.stderr.decode("utf-8", "replace").strip() or "o PowerShell falhou"
        raise SemJanela(erro[:200])
    saida = r.stdout.decode("utf-8", "replace")
    return [ln.strip() for ln in saida.splitlines() if ln.strip()]


def _macos(padroes: str, varios: bool, titulo: str) -> list[str]:
    tipos = ",".join(f'"{p.lstrip("*.")}"' for p in padroes.split(";"))
    multi = " with multiple selections allowed" if varios else ""
    script = (f'set f to choose file with prompt "{titulo}" '
              f'of type {{{tipos}}}{multi}\n'
              'set o to ""\n'
              + ('repeat with x in f\nset o to o & POSIX path of x & linefeed\n'
                 'end repeat\n' if varios else 'set o to POSIX path of f\n')
              + 'return o')
    r = subprocess.run(["osascript", "-e", script],
                       capture_output=True, timeout=TIMEOUT)
    erro = r.stderr.decode("utf-8", "replace")
    if r.returncode != 0:
        if "User canceled" in erro:
            return []                      # cancelar não é erro
        raise SemJanela((erro or "o Finder falhou").strip()[:200])
    saida = r.stdout.decode("utf-8", "replace")
    return [ln.strip() for ln in saida.splitlines() if ln.strip()]


def _linux(rotulo: str, padroes: str, varios: bool, titulo: str) -> list[str]:
    zen = shutil.which("zenity")
    if zen:
        cmd = [zen, "--file-selection", f"--title={titulo}",
               f"--file-filter={rotulo} | {padroes.replace(';', ' ')}",
               "--file-filter=Todos | *"]
        if varios:
            cmd += ["--multiple", "--separator=\n"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
        if r.returncode == 1:
            return []                      # cancelou
        if r.returncode != 0:
            raise SemJanela((r.stderr or "o zenity falhou").strip()[:200])
        return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]

    kd = shutil.which("kdialog")
    if kd:
        cmd = [kd, "--getopenfilename", str(Path.home()),
               f"{padroes.replace(';', ' ')}|{rotulo}", "--title", titulo]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
        if r.returncode != 0:
            return []
        return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]

    # último recurso: tkinter, num processo à parte
    codigo = (
        "import json,sys\n"
        "import tkinter as tk\n"
        "from tkinter import filedialog\n"
        "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
        f"f = filedialog.askopenfilename{'s' if varios else ''}("
        f"title={titulo!r}, filetypes=[({rotulo!r}, {padroes.replace(';', ' ')!r}),"
        f" ('Todos', '*.*')])\n"
        "print(json.dumps(list(f) if isinstance(f, (list, tuple)) else ([f] if f else [])))\n"
    )
    r = subprocess.run([sys.executable, "-c", codigo],
                       capture_output=True, text=True, timeout=TIMEOUT)
    if r.returncode != 0:
        raise SemJanela("nenhuma janela de arquivo disponível (instale o zenity)")
    try:
        return [p for p in json.loads(r.stdout.strip() or "[]") if p]
    except json.JSONDecodeError as exc:
        raise SemJanela("não entendi a resposta da janela") from exc


def escolher(kind: str = "video", varios: bool = False,
             titulo: str = "") -> list[str]:
    """Abre a janela do sistema e devolve os caminhos escolhidos.

    Lista vazia = o usuário cancelou, que não é erro.
    """
    rotulo, padroes = FILTROS.get(kind, FILTROS["video"])
    titulo = titulo or f"Escolher {rotulo.lower()}"
    try:
        if sys.platform == "win32":
            achados = _windows(rotulo, padroes, varios, titulo)
        elif sys.platform == "darwin":
            achados = _macos(padroes, varios, titulo)
        else:
            achados = _linux(rotulo, padroes, varios, titulo)
    except subprocess.TimeoutExpired as exc:
        raise SemJanela("a janela ficou aberta tempo demais e foi fechada") from exc
    except FileNotFoundError as exc:
        raise SemJanela("não achei o programa que abre a janela") from exc
    # só o que existe de verdade volta: a janela pode devolver caminho de rede
    # que sumiu entre o clique e a resposta
    return [str(Path(p).resolve()) for p in achados if Path(p).is_file()]


def disponivel() -> bool:
    """A janela do sistema pode ser aberta nesta máquina?"""
    if sys.platform == "win32":
        return bool(shutil.which("powershell") or shutil.which("pwsh"))
    if sys.platform == "darwin":
        return bool(shutil.which("osascript"))
    if shutil.which("zenity") or shutil.which("kdialog"):
        return True
    try:
        import tkinter  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False
