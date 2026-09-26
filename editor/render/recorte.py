"""A pessoa separada do fundo — a "profundidade" da pós-edição.

Com o recorte da pessoa, o Sharkcut faz o que dá a sensação de 3D num vídeo
de uma câmera só: título ATRÁS da pessoa, fundo desfocado como lente aberta,
fundo escurecido com a pessoa acesa, a pessoa saltando para a frente do
fundo, a pessoa recortada sobre uma cor. Não é reconstrução 3D da sala: é o
vídeo em duas camadas, que é como os editores fazem isso no After Effects
(rotoscopia + camadas).

QUEM RECORTA: o Robust Video Matting (RVM, mobilenetv3), um modelo aberto de
15 MB que roda no processador pelo onnxruntime. É feito para VÍDEO: carrega
um estado de um quadro para o outro, então o recorte não treme. Roda NESTA
máquina — o vídeo não sai dela. A única coisa que vem da internet é o
próprio modelo, uma vez, do repositório oficial no GitHub, conferido pelo
SHA-256 antes de ser usado.

CUSTO, medido num processador de 4 núcleos: 40 ms por quadro com o quadro
reduzido para 960 no lado maior — perto de tempo real a 30 fps. O recorte só
é calculado nos quadros que têm efeito de camada (com meio segundo antes,
para o estado do modelo assentar), e fica guardado: mexer num título não
recalcula o recorte.

UMA GERAÇÃO DE ENCODE, AINDA. O recorte é uma MÁSCARA, não imagem: sai num
arquivo cinza sem perda (FFV1) e entra no encode do trecho como mais uma
entrada, onde separa a pessoa do fundo. A imagem que vai para o arquivo final
continua vindo da fonte original, encodada uma vez.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from pathlib import Path
from typing import Callable

import numpy as np

from ..config import DATA_DIR, FFMPEG

MODELO_URL = ("https://github.com/PeterL1n/RobustVideoMatting/releases/download/"
              "v1.0.0/rvm_mobilenetv3_fp32.onnx")
MODELO_NOME = "rvm_mobilenetv3_fp32.onnx"
MODELO_SHA256 = "88d4531297118f595bf2fd60f6f566aec2e559393802d1f436c380f0cbbd2828"
MODELO_BYTES = 14_975_696

# O recorte é calculado com o lado maior em até 960 px e ampliado no encode.
# A borda sai um pouco mais macia que a do quadro cheio — e é o que se quer
# numa máscara — por um terço do tempo (111 ms contra 40 ms por quadro em
# 1080x1920, medido).
LADO_MAX = 960
AQUECIMENTO = 0.5          # segundos de recorte antes do efeito começar

_trava = threading.Lock()
_sessao = None


def pasta_modelos() -> Path:
    return Path(DATA_DIR) / "modelos"


def caminho_do_modelo() -> Path:
    return pasta_modelos() / MODELO_NOME


def tem_runtime() -> bool:
    try:
        import onnxruntime  # noqa: F401
    except Exception:  # noqa: BLE001 — sem o pacote, sem recorte (e sem erro)
        return False
    return True


def tem_modelo() -> bool:
    p = caminho_do_modelo()
    return p.exists() and p.stat().st_size == MODELO_BYTES


def pronto() -> bool:
    return tem_runtime() and tem_modelo()


def estado() -> dict:
    return {"runtime": tem_runtime(), "modelo": tem_modelo(),
            "pronto": pronto(), "tamanho_mb": round(MODELO_BYTES / 1e6, 1)}


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def baixar_modelo(progresso: Callable[[float], None] | None = None) -> Path:
    """Baixa o modelo UMA vez e confere o SHA-256. Levanta RuntimeError."""
    alvo = caminho_do_modelo()
    if tem_modelo():
        return alvo
    import httpx

    alvo.parent.mkdir(parents=True, exist_ok=True)
    parcial = alvo.with_suffix(".parcial")
    try:
        with httpx.stream("GET", MODELO_URL, follow_redirects=True,
                          timeout=httpx.Timeout(30.0, read=120.0)) as r:
            if r.status_code != 200:
                raise RuntimeError(f"o GitHub respondeu {r.status_code}")
            feito = 0
            with open(parcial, "wb") as f:
                for pedaco in r.iter_bytes(1 << 16):
                    f.write(pedaco)
                    feito += len(pedaco)
                    if feito > MODELO_BYTES * 2:
                        raise RuntimeError("o arquivo veio maior que o modelo")
                    if progresso:
                        progresso(min(1.0, feito / MODELO_BYTES))
    except RuntimeError:
        parcial.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001
        parcial.unlink(missing_ok=True)
        raise RuntimeError(f"não consegui baixar o modelo de recorte: {exc}") from exc
    if _sha256(parcial) != MODELO_SHA256:
        parcial.unlink(missing_ok=True)
        raise RuntimeError("o modelo baixado não confere (SHA-256 diferente); "
                           "nada foi usado")
    parcial.replace(alvo)
    return alvo


def _abrir():
    global _sessao
    with _trava:
        if _sessao is None:
            import onnxruntime as ort

            opcoes = ort.SessionOptions()
            opcoes.log_severity_level = 3
            _sessao = ort.InferenceSession(str(caminho_do_modelo()), opcoes,
                                           providers=["CPUExecutionProvider"])
        return _sessao


def tamanho_do_recorte(W: int, H: int) -> tuple[int, int]:
    k = min(1.0, LADO_MAX / float(max(W, H)))
    mw, mh = int(round(W * k)), int(round(H * k))
    return max(16, mw - mw % 2), max(16, mh - mh % 2)


def gerar(cmd_base: list[str], mw: int, mh: int, fps: float,
          janelas: list[tuple[float, float]], destino: Path,
          cancelar: Callable[[], bool] | None = None) -> dict:
    """Roda o ffmpeg da BASE (quadros rgb24 mw x mh no stdout), recorta a
    pessoa quadro a quadro e grava a máscara em ``destino`` (FFV1 cinza).

    ``janelas``: os intervalos, em segundos do trecho, em que o recorte é
    usado; fora deles o quadro vai preto (sem pessoa) e o modelo nem roda.

    Devolve {"path", "centro": [x, y] (fração do quadro, onde a pessoa está
    em média), "quadros"}. Levanta RuntimeError se algo der errado — quem
    chama decide seguir sem recorte.
    """
    sessao = _abrir()
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.stem + ".parcial.mkv")
    tam = mw * mh * 3
    # o modelo trabalha por dentro numa versão reduzida do quadro; entre 256 e
    # 512 px é a faixa que o próprio RVM recomenda para gente de corpo inteiro
    # ou meio corpo, e 384 é o meio dela
    razao = np.array([min(1.0, 384.0 / max(mw, mh))], np.float32)
    zero = np.zeros((1, 1, 1, 1), np.float32)

    leitor = subprocess.Popen(cmd_base, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
    escritor = subprocess.Popen(
        [FFMPEG, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray",
         "-s", f"{mw}x{mh}", "-r", f"{fps:.6f}", "-i", "-",
         "-c:v", "ffv1", "-level", "3", str(parcial)],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    estado = [zero, zero, zero, zero]
    ativo_antes = False
    soma_x = soma_y = soma_p = 0.0
    xs = np.arange(mw, dtype=np.float32)[None, :]
    ys = np.arange(mh, dtype=np.float32)[:, None]
    n = 0
    vazio = bytes(mw * mh)
    try:
        while True:
            if cancelar and cancelar():
                raise KeyboardInterrupt("exportação cancelada")
            buf = leitor.stdout.read(tam)
            if not buf or len(buf) < tam:
                break
            t = n / fps
            ativo = any(a - AQUECIMENTO <= t <= b + 0.1 for a, b in janelas)
            if not ativo:
                escritor.stdin.write(vazio)
                estado = [zero, zero, zero, zero]
                ativo_antes = False
                n += 1
                continue
            if not ativo_antes:
                estado = [zero, zero, zero, zero]
            ativo_antes = True
            quadro = (np.frombuffer(buf, np.uint8).reshape(mh, mw, 3)
                      .transpose(2, 0, 1)[None].astype(np.float32) / 255.0)
            _fgr, pha, *estado = sessao.run(None, {
                "src": quadro, "r1i": estado[0], "r2i": estado[1],
                "r3i": estado[2], "r4i": estado[3], "downsample_ratio": razao})
            alfa = np.clip(pha[0, 0], 0.0, 1.0)
            if any(a <= t <= b for a, b in janelas):
                peso = float(alfa.sum())
                if peso > 1.0:
                    soma_x += float((alfa * xs).sum())
                    soma_y += float((alfa * ys).sum())
                    soma_p += peso
            escritor.stdin.write((alfa * 255.0 + 0.5).astype(np.uint8).tobytes())
            n += 1
        escritor.stdin.close()
        if escritor.wait() != 0:
            raise RuntimeError("o ffmpeg não gravou a máscara: "
                               + escritor.stderr.read().decode(errors="replace")[-300:])
        leitor.wait()
        if leitor.returncode != 0 or n == 0:
            raise RuntimeError("o ffmpeg da base falhou: "
                               + leitor.stderr.read().decode(errors="replace")[-300:])
    except BaseException:
        for p in (leitor, escritor):
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass
        parcial.unlink(missing_ok=True)
        raise
    parcial.replace(destino)
    centro = ([soma_x / soma_p / mw, soma_y / soma_p / mh] if soma_p > 0
              else [0.5, 0.5])
    info = {"path": str(destino), "centro": [round(c, 4) for c in centro],
            "quadros": n, "tem_pessoa": soma_p > 0}
    destino.with_suffix(".json").write_text(json.dumps(info), encoding="utf-8")
    return info


def ler_info(destino: Path) -> dict | None:
    destino = Path(destino)
    lado = destino.with_suffix(".json")
    if not destino.exists() or not lado.exists():
        return None
    try:
        return json.loads(lado.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def alfas(quadros: np.ndarray) -> list[np.ndarray]:
    """O recorte (0..1) de uma sequência curta de quadros rgb24 (N, h, w, 3).

    Para quem quer SABER onde a pessoa está (a análise de cena), não gravar
    uma máscara: os quadros anteriores servem só para o estado do modelo
    assentar, e o que importa costuma ser o último.
    """
    sessao = _abrir()
    zero = np.zeros((1, 1, 1, 1), np.float32)
    estado = [zero, zero, zero, zero]
    _n, mh, mw, _c = quadros.shape
    razao = np.array([min(1.0, 384.0 / max(mw, mh))], np.float32)
    out = []
    for q in quadros:
        src = q.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        _fgr, pha, *estado = sessao.run(None, {
            "src": src, "r1i": estado[0], "r2i": estado[1], "r3i": estado[2],
            "r4i": estado[3], "downsample_ratio": razao})
        out.append(np.clip(pha[0, 0], 0.0, 1.0))
    return out
