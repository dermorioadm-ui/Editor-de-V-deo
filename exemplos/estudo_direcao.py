"""Estudo de motion reproduzível, sem IA nem imagens externas."""
import argparse
from pathlib import Path
import subprocess

from editor.config import FFMPEG
from editor.models import Grafico
from editor.render.motion import ass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--saida", required=True)
    op = parser.parse_args()
    pasta = Path(op.saida).resolve()
    pasta.mkdir(parents=True, exist_ok=True)
    comum = dict(estilo="editorial", cor="#75E0D1", entrada="cinema", saida="fade")
    gs = [
        Grafico(**comum, tipo="texto", texto="SHARKCUT  /  ESTUDO DE MOTION", tamanho=0.55,
                y=0.12, out_start=0, out_end=14),
        Grafico(**{**comum, "entrada": "linhas"}, tipo="titulo", texto="EDIÇÃO\nCOM INTENÇÃO.",
                subtexto="A fala determina o movimento.", tamanho=1.2,
                y=0.45, out_start=0.4, out_end=4),
        Grafico(**comum, tipo="comparacao", texto="O QUE GUIA A EDIÇÃO?",
                rotulos=["EXCESSO", "DIREÇÃO"], itens=[
                    {"texto": "Efeito em\ncada corte", "em": 0.4},
                    {"texto": "Ênfase na\nideia certa", "em": 1.8}],
                y=0.45, out_start=4.3, out_end=9.4),
        Grafico(**{**comum, "entrada": "linhas"}, tipo="titulo", texto="UMA IDEIA.\nUM MOMENTO.",
                subtexto="Composição, ritmo e espaço para entender.", tamanho=1.1,
                y=0.45, out_start=9.7, out_end=13.7),
        Grafico(**comum, tipo="texto", texto="Renderizado pelo motor do editor · sem gravação",
                tamanho=0.5, y=0.86, out_start=0, out_end=14),
    ]
    (pasta / "estudo.ass").write_text(ass(gs, 720, 1280, 0, 14), encoding="utf-8-sig")
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
           "color=c=0x0b141c:s=720x1280:r=30:d=14", "-vf",
           "drawgrid=w=180:h=160:t=1:c=0x324757@0.18,ass=estudo.ass",
           "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", "estudo-direcao.mp4"]
    subprocess.run(cmd, cwd=pasta, check=True)
    subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", "7.5", "-i", "estudo-direcao.mp4",
                    "-frames:v", "1", "comparacao.png"], cwd=pasta, check=True)
    print(pasta / "estudo-direcao.mp4")


if __name__ == "__main__":
    main()
