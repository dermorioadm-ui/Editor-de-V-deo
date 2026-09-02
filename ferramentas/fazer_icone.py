"""Monta o sharkcut.ico a partir de PNGs já renderizados.

Um .ico é um contêiner: cabeçalho, uma entrada de diretório por tamanho, e
os bitmaps em seguida. Do Vista em diante cada entrada pode ser um PNG
inteiro, e é o que fazemos — nada de BMP com máscara AND, que é o formato
antigo e o motivo de tanto ícone com borda serrilhada.

Sem Pillow, pela mesma razão do resto do projeto: o formato é simples o
bastante para escrever à mão, e uma dependência a menos é uma instalação a
menos para dar errado no Windows de quem só quer editar vídeo.

Os PNGs vêm do SVG da marca, rasterizados pelo Chromium e reduzidos pelo
ffmpeg (lanczos). Nos tamanhos pequenos entra uma variante mais cheia — com
menos margem e a água mais grossa —, porque a arte "de tela grande"
encolhida para 16 px vira um borrão: a nadadeira some e a linha d'água
desaparece. Isso é ofício de ícone, não preciosismo: 16 px é o tamanho que
aparece na barra de título e na lista de arquivos.

Uso:
    python ferramentas/fazer_icone.py saida.ico 16=peq16.png 32=peq32.png ...
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

CABECALHO = "<HHH"        # reservado, tipo (1 = ícone), quantidade
ENTRADA = "<BBBBHHII"     # larg, alt, cores, reservado, planos, bits, bytes, offset


def montar(destino: Path, imagens: list[tuple[int, bytes]]) -> None:
    """Escreve o .ico com uma entrada PNG por tamanho."""
    imagens = sorted(imagens)
    inicio = struct.calcsize(CABECALHO) + struct.calcsize(ENTRADA) * len(imagens)
    partes = [struct.pack(CABECALHO, 0, 1, len(imagens))]
    offset = inicio
    for lado, dados in imagens:
        # 256 é gravado como 0: o campo tem UM byte, e 256 não cabe nele
        campo = 0 if lado >= 256 else lado
        partes.append(struct.pack(ENTRADA, campo, campo, 0, 0, 1, 32,
                                  len(dados), offset))
        offset += len(dados)
    partes.extend(d for _lado, d in imagens)
    destino.write_bytes(b"".join(partes))


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    destino = Path(argv[1])
    imagens = []
    for arg in argv[2:]:
        lado, _, caminho = arg.partition("=")
        imagens.append((int(lado), Path(caminho).read_bytes()))
    montar(destino, imagens)
    print(f"{destino}: {len(imagens)} tamanhos, {destino.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
