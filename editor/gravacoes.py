"""As gravações feitas DENTRO do aplicativo.

O navegador grava pela câmera e pelo microfone e manda o arquivo para cá. Isso
NÃO fere a regra número um: o navegador é o daqui, o servidor é o daqui, e o
endereço é 127.0.0.1 — o arquivo atravessa a memória da mesma máquina e pousa
no disco dela. Nada sai.

O QUE O NAVEGADOR ENTREGA E POR QUE ELE PRECISA DE CONSERTO. O MediaRecorder
escreve um fluxo, não um arquivo pronto: o cabeçalho WebM sai SEM duração e
sem índice de busca, porque no momento em que ele começa a escrever ninguém
sabe quando vai terminar. O sintoma disso, se a gente guardasse como veio, é
conhecido: o vídeo "tem duração desconhecida", a agulha não anda, e o corte de
silêncio recebe uma duração zero e devolve um vídeo vazio.

O conserto é REMUX, não reencode: os mesmos quadros comprimidos são copiados
para um recipiente novo, que aí sim sai com duração e índice. `-c copy`. A
regra número dois continua de pé — a gravação chega ao corte na primeira (e
única) geração de compressão que ela vai ter.
"""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from .config import DATA_DIR, FFMPEG
from .ffmpeg_utils import probe

PASTA = DATA_DIR / "gravacoes"
# o que o MediaRecorder pode mandar. Tudo é remuxado; a extensão de origem só
# decide como o ffmpeg lê.
EXTENSOES = {"video/webm": ".webm", "video/mp4": ".mp4",
             "audio/webm": ".webm", "audio/mp4": ".m4a",
             "audio/ogg": ".ogg", "video/x-matroska": ".mkv"}
LIMPO = re.compile(r"[^A-Za-z0-9 ._-]+")


def pasta() -> Path:
    PASTA.mkdir(parents=True, exist_ok=True)
    return PASTA


def _nome_livre(base: str, sufixo: str) -> Path:
    destino = pasta() / f"{base}{sufixo}"
    n = 2
    while destino.exists():
        destino = pasta() / f"{base} ({n}){sufixo}"
        n += 1
    return destino


def _remux(origem: Path, destino: Path) -> bool:
    """Copia os fluxos para um recipiente novo. Nunca reencoda."""
    r = subprocess.run(
        [FFMPEG, "-y", "-v", "error",
         # genpts: o fluxo do navegador chega com o relógio começando em
         # qualquer lugar (e, quando a aba perde o foco, com buraco). Sem isto
         # o remux herda o relógio torto e a duração continua mentindo.
         "-fflags", "+genpts", "-i", str(origem),
         "-c", "copy", "-movflags", "+faststart", str(destino)],
        capture_output=True, text=True)
    return r.returncode == 0 and destino.exists() and destino.stat().st_size > 0


def guardar(dados: bytes, nome: str = "", mime: str = "video/webm") -> dict:
    """Grava o que veio do navegador e devolve o registro dela.

    O arquivo cru é apagado depois do remux: guardar os dois dobra o disco por
    gravação, e o cru só serve para o remux.
    """
    if not dados:
        raise ValueError("a gravação chegou vazia")
    base = LIMPO.sub("", (nome or "").rsplit(".", 1)[0]).strip()
    if not base:
        base = time.strftime("gravacao %Y-%m-%d %H-%M-%S")
    origem = _nome_livre(f"{base} (cru)", EXTENSOES.get(mime, ".webm"))
    origem.write_bytes(dados)

    # mp4 primeiro: é o que todo lugar abre. Quando o codec do navegador não
    # cabe num mp4 (VP8/VP9, que é o padrão do Chrome), cai para mkv, que
    # aceita qualquer fluxo — e o editor lê os dois.
    destino = _nome_livre(base, ".mp4")
    if not _remux(origem, destino):
        try:
            destino.unlink(missing_ok=True)
        except OSError:
            pass
        destino = _nome_livre(base, ".mkv")
        if not _remux(origem, destino):
            # não deu para consertar: fica o cru, que é melhor que nada, e o
            # aviso diz que a duração pode estar errada
            info = _medir(origem)
            return {**info, "remuxado": False,
                    "aviso": "não consegui reempacotar esta gravação; ela pode "
                             "abrir sem duração certa"}
    try:
        origem.unlink(missing_ok=True)
    except OSError:
        pass
    return {**_medir(destino), "remuxado": True, "aviso": ""}


def _medir(arquivo: Path) -> dict:
    duracao = 0.0
    largura = altura = 0
    tem_audio = False
    try:
        info = probe(arquivo)
        duracao = float(info.duration or 0.0)
        tem_audio = bool(info.has_audio)
        if getattr(info, "display_size", None):
            largura, altura = (int(info.display_size[0]), int(info.display_size[1]))
    except Exception:  # noqa: BLE001 — a lista não pode morrer por um arquivo
        pass
    return {"nome": arquivo.name, "path": str(arquivo.resolve()),
            "duracao": round(duracao, 2), "size_bytes": arquivo.stat().st_size,
            "largura": largura, "altura": altura, "tem_audio": tem_audio,
            "criado_em": arquivo.stat().st_mtime}


def listar() -> list[dict]:
    """As gravações, da mais recente para a mais antiga."""
    if not PASTA.exists():
        return []
    arquivos = [f for f in PASTA.iterdir()
                if f.is_file() and not f.name.endswith(("(cru).webm", "(cru).mp4"))]
    arquivos.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return [_medir(f) for f in arquivos]


def apagar(nome: str) -> bool:
    """Apaga UMA gravação, e só de dentro da pasta de gravações.

    O nome vem da tela, e tela é entrada de fora: um ".." no meio dele faria
    esta função apagar arquivo do usuário em qualquer lugar do disco. Por isso
    o caminho é resolvido e conferido contra a pasta — não basta filtrar o
    texto, porque link simbólico também escapa.
    """
    if not nome or "/" in nome or "\\" in nome:
        return False
    alvo = (pasta() / nome).resolve()
    try:
        alvo.relative_to(pasta().resolve())
    except ValueError:
        return False
    if not alvo.is_file():
        return False
    alvo.unlink()
    return True
