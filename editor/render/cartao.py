"""Cartões de tópico desenhados PELO PROGRAMA, não por modelo de imagem.

Modelo de imagem escreve texto mal: troca letra, come acento, desalinha
linha. Um cartão de "3 passos" ou um número em destaque é justamente texto —
então quem desenha é o programa, com a mesma máquina que já escreve as
legendas (libass, medido com 1,9% de erro de altura contra o ffmpeg). O que a
IA faz é escrever AS PALAVRAS e dizer ONDE o cartão entra.

O resultado é um PNG opaco do tamanho exato do painel. Ele entra na linha do
tempo como uma sobreposição comum — a mesma que já tem posição, escala,
opacidade e fade de entrada e saída. Nenhum caminho novo de render.

Sem Pillow de propósito: o ffmpeg já é dependência obrigatória e está em toda
máquina onde o editor roda; uma biblioteca a mais é uma instalação a mais
para dar errado no Windows de quem só quer editar vídeo.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from ..config import FFMPEG
from ..ffmpeg_utils import escape_filter_path

# O TIPO segue a ALTURA do quadro, igual à legenda — é a régua de leitura, e
# é o que faz o cartão ter o mesmo peso visual em 9:16, 1:1 e 16:9. Preso à
# largura do painel, o cartão de um 16:9 saía com letra gigante ocupando
# metade da tela.
LARGURA_PAINEL = 0.84         # do quadro
LARGURA_MAXIMA = 1.15         # ...mas nunca mais largo que isto em alturas
MARGEM = 0.032                # da altura
TITULO = 0.042                # corpo do título
TOPICO = 0.031                # corpo de cada tópico
ENTRELINHA = 1.62             # espaço entre tópicos, em corpos
DEPOIS_DO_TITULO = 1.60       # respiro abaixo do título
BARRA = 0.006                 # a barra de cor na lateral, em alturas
NUMERO = 0.155                # corpo do número em destaque

MAX_TOPICOS = 4
MAX_CHARS_TITULO = 42
MAX_CHARS_TOPICO = 38

FUNDO = "0x101828"
BARRA_COR = "0x38BDF8"
COR_TITULO = "&H00FFFFFF"
COR_TOPICO = "&H00E0E8F0"


def _ass_escape(texto: str) -> str:
    """O que o ASS trata como comando não pode vir do texto do usuário."""
    return (str(texto).replace("\\", "\\\\").replace("{", "(")
            .replace("}", ")").replace("\n", " ").strip())


def _estilo(nome: str, fonte: str, corpo: int, cor: str, negrito: bool) -> str:
    return (f"Style: {nome},{fonte},{corpo},{cor},{cor},&H00000000,&H00000000,"
            f"{-1 if negrito else 0},0,0,0,100,100,0,0,1,0,0,7,0,0,0,1")


def _quebrar(texto: str, largura_px: float, corpo: float) -> list[str]:
    """Quebra o texto no que CABE na largura do painel.

    O ASS do cartão usa ``WrapStyle: 2`` (sem quebra automática) e uma linha
    só com ``\pos`` fixo: um título que não coubesse saía cortado no meio da
    palavra, silenciosamente. É o caso do cartão de FRASE (o hook), que leva
    a frase inteira no título. Medido no libass com Arial negrito, o avanço
    médio de um caractere é ~0,52 do corpo; 0,55 dá a folga do contorno.
    """
    cabe = max(8, int(largura_px / max(1.0, corpo * 0.55)))
    palavras = str(texto).split()
    linhas: list[str] = []
    atual = ""
    for p in palavras:
        cand = f"{atual} {p}".strip()
        if len(cand) <= cabe or not atual:
            atual = cand
        else:
            linhas.append(atual)
            atual = p
    if atual:
        linhas.append(atual)
    return linhas or [""]


def _layout(largura_video: int, altura_video: int, titulo: str,
            topicos: list[str], numero: str) -> tuple[int, int, list[tuple]]:
    """A planta do cartão: painel e linhas saem do MESMO cálculo.

    Medir por uma fórmula e desenhar por outra é como o painel acabava com um
    palmo de vazio embaixo do último tópico.
    """
    pw = max(120, int(round(min(largura_video * LARGURA_PAINEL,
                                altura_video * LARGURA_MAXIMA))))
    h = altura_video
    margem = h * MARGEM
    c_titulo = max(12, int(round(h * TITULO)))
    c_topico = max(10, int(round(h * TOPICO)))
    c_numero = max(24, int(round(h * NUMERO)))

    linhas: list[tuple] = []
    y = margem
    if numero:
        linhas.append(("N", margem, y, numero))
        y += c_numero * 1.16
        if titulo:
            linhas.append(("B", margem + 4, y, titulo))
            y += c_topico * 1.25
    else:
        if titulo:
            partes = _quebrar(titulo, pw - 2 * margem, c_titulo)
            for k, parte in enumerate(partes):
                linhas.append(("T", margem, y, parte))
                y += c_titulo * (DEPOIS_DO_TITULO if k == len(partes) - 1 else 1.18)
        for k, t in enumerate(topicos):
            partes = _quebrar(t, pw - 2 * margem - c_topico * 0.35, c_topico)
            for j, parte in enumerate(partes):
                linhas.append(("B", margem + c_topico * 0.35, y, parte))
                ultimo = (k == len(topicos) - 1 and j == len(partes) - 1)
                y += c_topico * (1.25 if ultimo
                                 else (1.12 if j < len(partes) - 1 else ENTRELINHA))
    ph = int(round(min(y + margem, h * 0.45)))
    return pw - (pw % 2), max(80, ph - (ph % 2)), linhas


def medir(largura_video: int, altura_video: int, topicos: int,
          numero: bool = False) -> tuple[int, int]:
    """O tamanho do painel para este conteúdo, em pixels do quadro."""
    pw, ph, _l = _layout(largura_video, altura_video, "titulo",
                         ["x"] * max(0, topicos), "0" if numero else "")
    return pw, ph


def cabe_na_largura(largura_video: int, altura_video: int, texto: str,
                    corpo_do_titulo: bool = True) -> int:
    """Quantas linhas este texto ocupa no painel (1 = coube numa linha)."""
    pw, _ph, _l = _layout(largura_video, altura_video, "x", [], "")
    h = altura_video
    corpo = h * (TITULO if corpo_do_titulo else TOPICO)
    return len(_quebrar(texto, pw - 2 * (h * MARGEM), corpo))


def desenhar(dest: Path, largura_video: int, altura_video: int,
             titulo: str = "", topicos: list[str] | None = None,
             numero: str = "", fonte: str = "Arial",
             cor_barra: str = BARRA_COR, fundo: str = FUNDO) -> dict:
    """Desenha o cartão e devolve {path, width, height}.

    ``numero`` desenha o cartão de DESTAQUE (um número grande com uma linha
    embaixo); com ele, ``topicos`` é ignorado e ``titulo`` vira a legenda do
    número.
    """
    topicos = [_ass_escape(t)[:MAX_CHARS_TOPICO]
               for t in (topicos or []) if str(t).strip()][:MAX_TOPICOS]
    titulo = _ass_escape(titulo)[:MAX_CHARS_TITULO]
    numero = _ass_escape(numero)[:12]
    if not titulo and not topicos and not numero:
        raise ValueError("cartão sem texto nenhum")
    if numero:
        topicos = []

    pw, ph, linhas = _layout(largura_video, altura_video, titulo, topicos, numero)
    c_titulo = max(12, int(round(altura_video * TITULO)))
    c_topico = max(10, int(round(altura_video * TOPICO)))
    c_numero = max(24, int(round(altura_video * NUMERO)))

    eventos = [
        f"Dialogue: 0,0:00:00.00,0:00:30.00,{estilo},,0,0,0,,"
        f"{{\\pos({x:.0f},{y:.0f})}}{texto}"
        for estilo, x, y, texto in linhas
    ]

    ass = [
        "[Script Info]", "ScriptType: v4.00+",
        f"PlayResX: {pw}", f"PlayResY: {ph}",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        _estilo("T", fonte, c_titulo, COR_TITULO, True),
        _estilo("B", fonte, c_topico, COR_TOPICO, False),
        _estilo("N", fonte, c_numero, COR_TITULO, True), "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text",
        *eventos, "",
    ]
    dest.parent.mkdir(parents=True, exist_ok=True)
    caminho_ass = dest.with_suffix(".ass")
    caminho_ass.write_text("\n".join(ass), encoding="utf-8")

    barra = max(3, int(round(altura_video * BARRA)))
    subprocess.run([
        FFMPEG, "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c={fundo}:s={pw}x{ph}",
        "-vf", (f"drawbox=x=0:y=0:w={barra}:h={ph}:color={cor_barra}:t=fill,"
                f"ass='{escape_filter_path(caminho_ass)}'"),
        "-frames:v", "1", str(dest),
    ], check=True, capture_output=True)
    caminho_ass.unlink(missing_ok=True)
    return {"path": str(dest), "width": pw, "height": ph}


def nome_do_cartao(titulo: str, topicos: list[str] | None,
                   numero: str, largura: int, altura: int) -> str:
    """Nome estável pelo conteúdo: mesmo cartão, mesmo arquivo, sem redesenhar."""
    marca = hashlib.sha1(
        f"{titulo}|{'|'.join(topicos or [])}|{numero}|{largura}x{altura}"
        .encode()).hexdigest()[:12]
    return f"cartao_{marca}.png"
