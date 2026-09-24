"""Máscara de forma da sobreposição, desenhada UMA vez num PNG cinza.

POR QUE EM ARQUIVO, E NÃO NO GRAFO. A forma é a mesma em todos os quadros, mas
``geq`` avalia a expressão POR PIXEL E POR QUADRO. Medido nesta máquina, numa
janela em tela cheia (1080x1920, 60 quadros): a sobreposição crua custa 1,2 s,
com ``geq`` de máscara custa 24,3 s — vinte vezes mais. Numa janela de 60 s
isso passa de dez minutos de espera, e o produto promete comportar-se como
impressora.

Com o PNG pronto, o mesmo trecho custa 2,5 s: o ``alphamerge`` é uma
multiplicação de canal em C, sem expressão nenhuma. O ``geq`` roda uma única
vez na vida do arquivo, e o arquivo é reaproveitado pelo nome, que é o hash do
conteúdo.

DOIS CAMINHOS MEDIDOS E DESCARTADOS, para ninguém tentar de novo:

- ``scale2ref`` para a máscara acompanhar o tamanho da janela: em tela cheia
  levou 75 s e o sistema MATOU o processo (rc=-9). Subir uma máscara pequena
  para 1080x1920 a cada quadro é pior que o geq.
- gerar a máscara dentro do próprio grafo (``color`` + ``geq`` + ``trim`` +
  ``loop``): funciona em quadro pequeno (0,23 s) e em tela cheia levou 85 s e
  também foi morto.

O que sobra é o que está aqui: PNG no TAMANHO NATIVO da mídia, aplicado ANTES
da escala. Antes da escala o tamanho é fixo, então a máscara casa exatamente
com a imagem sem nenhum redimensionamento — e continua casando quando a escala
é animada, porque a animação vem depois.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..config import FFMPEG
from ..ffmpeg_utils import probe, run
from . import animacao as A

# Limite do PNG de máscara. Uma gravação de celular em 4K daria uma máscara de
# 3840x2160 desenhada por expressão — caro para nada, porque a máscara é uma
# forma lisa e o alphamerge exige apenas que os dois lados tenham o mesmo
# tamanho. Acima disto a mídia é reduzida na mesma proporção e a máscara é
# desenhada no tamanho reduzido; quem redimensiona é o ffmpeg, em C.
TETO = 2048


def nome_da_mascara(mask: dict, largura: int, altura: int) -> str:
    """Nome estável pelo conteúdo: mesma máscara, mesmo arquivo, sem redesenhar."""
    partes = "|".join(f"{k}={mask[k]}" for k in sorted(mask))
    marca = hashlib.sha1(f"{partes}|{largura}x{altura}".encode()).hexdigest()[:12]
    return f"mascara_{marca}.png"


def dimensao_da_midia(caminho: str | Path) -> tuple[int, int]:
    """Tamanho em pixels da mídia anexada, ou (0, 0) se não der para saber."""
    try:
        info = probe(caminho)
    except Exception:  # noqa: BLE001 — sem probe, quem chama desiste da máscara
        return 0, 0
    w, h = info.display_size if getattr(info, "display_size", None) else (0, 0)
    try:
        return max(0, int(w)), max(0, int(h))
    except (TypeError, ValueError):
        return 0, 0


def preparar(mask: dict | None, midia: str | Path, destino: Path) -> str:
    """Garante o PNG da máscara e devolve o caminho. "" = não deu, siga sem ela.

    Nunca levanta: uma máscara que não pôde ser desenhada tem que deixar a
    sobreposição aparecer inteira, não derrubar o trecho.
    """
    if not isinstance(mask, dict):
        return ""
    fator = A.mascara_fator(mask)
    if not fator:
        return ""
    w, h = dimensao_da_midia(midia)
    if w <= 0 or h <= 0:
        return ""
    maior = max(w, h)
    if maior > TETO:
        k = TETO / float(maior)
        w = max(2, int(round(w * k)))
        h = max(2, int(round(h * k)))
    destino.mkdir(parents=True, exist_ok=True)
    alvo = destino / nome_da_mascara(mask, w, h)
    if alvo.exists() and alvo.stat().st_size > 0:
        return str(alvo)
    try:
        run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
             "-i", f"color=c=white:s={w}x{h}:d=1", "-frames:v", "1",
             "-vf", f"format=gray,geq=lum='255*({fator})'", str(alvo)])
    except Exception:  # noqa: BLE001
        return ""
    return str(alvo) if alvo.exists() and alvo.stat().st_size > 0 else ""
