"""VOZ DE ESTÚDIO: tira o chiado, o ruído e o eco da voz, nesta máquina.

"Ele sai alto, mas não sai com aquela qualidade de microfone que isola o
som." A cadeia antiga (highpass -> compressor -> loudnorm) só deixava ALTO:
o compressor e o loudnorm levantam a voz e, junto com ela, o chiado, o
ar-condicionado e o eco da sala — é o som "de celular" que ele ouvia.

QUEM LIMPA: o DeepFilterNet 3 (Rikorose/DeepFilterNet, MIT/Apache), uma rede
treinada para separar voz de ruído, em tempo real, no PROCESSADOR. O programa
dele é um executável só, de 27 MB, com o modelo embutido — nada de Python,
nada de placa de vídeo, nada de nuvem. O arquivo NUNCA sai da máquina: o que
se baixa, uma vez, é o executável (com o SHA-256 conferido); a voz é lida e
escrita aqui dentro.

COMO ENTRA NO VÍDEO: a gravação inteira é limpa UMA vez (em pedaços, em
paralelo nos núcleos), guardada, e o render lê a voz limpa no lugar da
original, com os mesmos tempos. O corte, a velocidade e o encaixe continuam
exatamente iguais — só o som de cada bloco é outro. A limpeza começa no
instante em que o arquivo chega (junto com a transcrição) e, na hora de
exportar, em geral já está pronta.

A FORÇA: o DeepFilterNet deixa misturar de volta um pouco do original
(``--atten-lim-db``): "forte" tira até 40 dB de ruído (some para quem ouve)
e ainda guarda um fio do original, que é o que evita o som "metalizado" de
redutor no talo; "total" não guarda nada.
"""
from __future__ import annotations

import hashlib
import math
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from ..config import DATA_DIR, FFMPEG

VERSAO = "0.5.6"
_BASE = ("https://github.com/Rikorose/DeepFilterNet/releases/download/"
         f"v{VERSAO}/")
# (sistema, arquitetura) -> (arquivo no GitHub, bytes, SHA-256) — medidos no
# release v0.5.6. Qualquer byte diferente e o arquivo é jogado fora.
BINARIOS = {
    ("windows", "x86_64"): (
        "deep-filter-0.5.6-x86_64-pc-windows-msvc.exe", 26_912_256,
        "75e11fa16445f560cb6b021521ddb89e89270d13b83089705d98776f58fd7915"),
    ("darwin", "arm64"): (
        "deep-filter-0.5.6-aarch64-apple-darwin", 27_877_081,
        "4601e7f4e4c03e59a4c5b5000216ef3add3e808799cfccd95e14e83ea4611081"),
    ("darwin", "x86_64"): (
        "deep-filter-0.5.6-x86_64-apple-darwin", 29_933_512,
        "d3be84003acb7c23e738ad7f70a158ec779a8d233a82e7fa3e717d112eb5b50f"),
    ("linux", "x86_64"): (
        "deep-filter-0.5.6-x86_64-unknown-linux-musl", 36_417_296,
        "70775e251eee44c0f2451a1e833326cf8bcbbe304d3e7cd12851e6fce72ef7da"),
}

# o quanto de ruído sai, em dB (o resto é o original misturado de volta)
LIMPEZA = {"leve": 12, "media": 24, "forte": 40, "total": 100}
LIMPEZA_PADRAO = "forte"

SR = 48000                 # o DeepFilterNet trabalha em 48 kHz
PEDACO = 30.0              # segundos por pedaço (cada um num processo)
AQUECE = 1.5               # s de voz antes do pedaço: a rede "pega o ritmo"
SOBRA = 0.5                # s depois do pedaço (a saída encurta ~30 ms)
EMENDA = 0.05              # s de cruzamento entre dois pedaços
_REVISAO = "1"             # muda quando o jeito de limpar muda (invalida o cache)

_trava_geral = threading.Lock()
# a limpeza que começa quando o arquivo chega e a exportação podem pedir o
# download ao mesmo tempo: um baixa, o outro espera e usa o mesmo arquivo
_trava_baixar = threading.Lock()
_travas: dict[str, threading.Lock] = {}
_andamento: dict[str, float] = {}
_falhas: dict[str, str] = {}


# ------------------------------------------------------------------ binário
def _plataforma() -> tuple[str, str]:
    s = sys.platform
    so = ("windows" if s.startswith("win") else "darwin" if s == "darwin"
          else "linux" if s.startswith("linux") else s)
    m = platform.machine().lower()
    arq = ("x86_64" if m in ("x86_64", "amd64", "x64")
           else "arm64" if m in ("arm64", "aarch64") else m)
    return so, arq


def _dados() -> tuple[str, int, str] | None:
    return BINARIOS.get(_plataforma())


def suportado() -> bool:
    # SHARKCUT_VOZ_IA=0 desliga o redutor na máquina inteira (os testes usam:
    # sem isso, cada exportação de teste baixaria e rodaria a rede)
    if os.environ.get("SHARKCUT_VOZ_IA", "1") == "0":
        return False
    return _dados() is not None


def pasta() -> Path:
    return Path(DATA_DIR) / "modelos"


def caminho_do_binario() -> Path:
    ext = ".exe" if sys.platform.startswith("win") else ""
    return pasta() / f"deep-filter-{VERSAO}{ext}"


def instalado() -> bool:
    d = _dados()
    p = caminho_do_binario()
    return bool(d) and p.exists() and p.stat().st_size == d[1]


def estado() -> dict:
    d = _dados()
    return {"suportado": bool(d), "instalado": instalado(),
            "tamanho_mb": round(d[1] / 1e6, 1) if d else 0,
            "limpezas": list(LIMPEZA), "padrao": LIMPEZA_PADRAO}


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def _runtime_do_windows(alvo: Path) -> None:
    """O executável do Windows pede o VCRUNTIME140.dll (runtime do Visual C++).
    Toda instalação do Python para Windows traz essa DLL ao lado do
    python.exe; uma cópia ao lado do deep-filter garante que ele abre mesmo
    numa máquina sem o pacote do Visual C++ instalado."""
    if not sys.platform.startswith("win"):
        return
    for nome in ("vcruntime140.dll", "vcruntime140_1.dll"):
        if (alvo.parent / nome).exists():
            continue
        for base in {sys.base_prefix, sys.exec_prefix, sys.prefix,
                     os.path.dirname(sys.executable)}:
            origem = Path(base) / nome
            if origem.exists():
                try:
                    shutil.copy2(origem, alvo.parent / nome)
                except OSError:
                    pass
                break


def baixar(progresso: Callable[[float], None] | None = None) -> Path:
    """Baixa o executável UMA vez e confere o SHA-256. Levanta RuntimeError."""
    with _trava_baixar:
        return _baixar(progresso)


def _baixar(progresso: Callable[[float], None] | None) -> Path:
    d = _dados()
    if not d:
        raise RuntimeError("o redutor de ruído não tem versão para este sistema")
    alvo = caminho_do_binario()
    if instalado():
        _runtime_do_windows(alvo)
        return alvo
    import httpx

    nome, tamanho, sha = d
    alvo.parent.mkdir(parents=True, exist_ok=True)
    parcial = alvo.with_name(alvo.name + ".parcial")
    try:
        with httpx.stream("GET", _BASE + nome, follow_redirects=True,
                          timeout=httpx.Timeout(30.0, read=120.0)) as r:
            if r.status_code != 200:
                raise RuntimeError(f"o GitHub respondeu {r.status_code}")
            feito = 0
            with open(parcial, "wb") as f:
                for pedaco in r.iter_bytes(1 << 16):
                    f.write(pedaco)
                    feito += len(pedaco)
                    if feito > tamanho * 2:
                        raise RuntimeError("o arquivo veio maior que o esperado")
                    if progresso:
                        progresso(min(1.0, feito / tamanho))
    except RuntimeError:
        parcial.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001
        parcial.unlink(missing_ok=True)
        raise RuntimeError(f"não consegui baixar o redutor de ruído: {exc}") from exc
    if parcial.stat().st_size != tamanho or _sha256(parcial) != sha:
        parcial.unlink(missing_ok=True)
        raise RuntimeError("o redutor baixado não confere (SHA-256 diferente); "
                           "nada foi usado")
    if not sys.platform.startswith("win"):
        parcial.chmod(0o755)
    parcial.replace(alvo)
    _runtime_do_windows(alvo)
    return alvo


# ------------------------------------------------------------------- limpar
def _limite(forca: str) -> int:
    return LIMPEZA.get(str(forca or ""), LIMPEZA[LIMPEZA_PADRAO])


def _ler(p: Path) -> np.ndarray:
    from ..ffmpeg_utils import read_wav_mono

    s, sr = read_wav_mono(p)
    if sr != SR:
        raise RuntimeError(f"o redutor devolveu {sr} Hz")
    return s.astype(np.float32)


def _extrair(fonte: Path, destino: Path) -> None:
    """A voz inteira, mono 48 kHz, NO TEMPO DO ARQUIVO (first_pts=0: se o
    áudio começa depois do vídeo, o vão vira silêncio — é o mesmo relógio do
    -ss que o render usa para achar cada bloco)."""
    from ..ffmpeg_utils import run

    run([FFMPEG, "-y", "-v", "error", "-nostdin", "-i", str(fonte), "-vn",
         "-af", "aresample=first_pts=0", "-ac", "1", "-ar", str(SR),
         "-c:a", "pcm_s16le", str(destino)])


def _pedacos(n: int, trabalhadores: int) -> list[tuple[int, int]]:
    """Faixas [início, fim) em amostras. Pedaços de ~30 s, mas nunca menos
    pedaços que núcleos quando a gravação é longa o bastante para dividir."""
    dur = n / SR
    k = max(1, math.ceil(dur / PEDACO))
    if dur > 2 * PEDACO / 3:
        k = max(k, min(trabalhadores, math.ceil(dur / 10.0)))
    passo = math.ceil(n / k)
    return [(i * passo, min(n, (i + 1) * passo)) for i in range(k)
            if i * passo < n]


def _rodar(binario: Path, entrada: Path, saida_dir: Path, limite: int,
           cancelar: Callable[[], bool] | None) -> Path:
    from ..ffmpeg_utils import _popen

    cmd = [str(binario), "-D", "-a", str(limite), "-o", str(saida_dir),
           str(entrada)]
    proc = _popen(cmd, stdout=subprocess.DEVNULL)
    while True:
        try:
            _, err = proc.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            if cancelar and cancelar():
                proc.kill()
                proc.communicate()
                raise RuntimeError("cancelado")
    saida = saida_dir / entrada.name
    if proc.returncode != 0 or not saida.exists():
        msg = (err or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
        codigo = proc.returncode
        if sys.platform.startswith("win") and codigo in (-1073741515, 3221225781):
            raise RuntimeError("o redutor não abriu: falta o runtime do Visual "
                               "C++ (VCRUNTIME140.dll) nesta máquina")
        raise RuntimeError(f"o redutor falhou (código {codigo}): "
                           f"{' '.join(msg)[:300]}")
    return saida


def limpar(fonte: str | Path, destino: str | Path, forca: str = LIMPEZA_PADRAO,
           progresso: Callable[[float], None] | None = None,
           cancelar: Callable[[], bool] | None = None,
           trabalhadores: int | None = None) -> Path:
    """Limpa a voz de ``fonte`` e grava em ``destino`` (WAV mono 48 kHz, o
    MESMO comprimento e o MESMO relógio da faixa original)."""
    from concurrent.futures import ThreadPoolExecutor

    from ..ffmpeg_utils import write_wav

    binario = caminho_do_binario()
    if not instalado():
        raise RuntimeError("o redutor de ruído não está instalado")
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_name(destino.stem + "_trab")
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "saida").mkdir(parents=True, exist_ok=True)
    try:
        cheio = tmp / "cheio.wav"
        _extrair(Path(fonte), cheio)
        voz = _ler(cheio)
        n = len(voz)
        if n == 0:
            raise RuntimeError("a gravação não tem áudio")
        nucleos = os.cpu_count() or 2
        trab = max(1, min(8, trabalhadores or max(1, nucleos - 1)))
        faixas = _pedacos(n, trab)
        aq, so = int(AQUECE * SR), int(SOBRA * SR)
        feitos = [0]
        trava = threading.Lock()

        def um(i: int) -> np.ndarray:
            a, b = faixas[i]
            ia, ib = max(0, a - aq), min(n, b + so)
            trecho = voz[ia:ib]
            if b >= n:  # o último: silêncio no fim, que a saída encurta
                trecho = np.concatenate([trecho, np.zeros(so, np.float32)])
            entrada = tmp / f"p{i:04d}.wav"
            write_wav(entrada, trecho, SR)
            saida = _rodar(binario, entrada, tmp / "saida", _limite(forca),
                           cancelar)
            y = _ler(saida)
            entrada.unlink(missing_ok=True)
            saida.unlink(missing_ok=True)
            alvo = ib - ia
            y = y[:alvo] if len(y) >= alvo else np.concatenate(
                [y, np.zeros(alvo - len(y), np.float32)])
            with trava:
                feitos[0] += 1
                if progresso:
                    progresso(feitos[0] / len(faixas))
            return y

        with ThreadPoolExecutor(max_workers=min(trab, len(faixas))) as ex:
            saidas = list(ex.map(um, range(len(faixas))))

        # a costura: cada pedaço vale no próprio miolo; na divisa, 50 ms de
        # cruzamento entre o fim de um e o começo do outro (os dois já
        # "aquecidos" ali, então a voz não pula)
        out = np.zeros(n, np.float32)
        h = int(EMENDA * SR / 2)
        for i, (a, b) in enumerate(faixas):
            ia = max(0, a - aq)
            out[a:b] = saidas[i][a - ia:b - ia]
        for i in range(1, len(faixas)):
            s = faixas[i][0]
            r0, r1 = max(0, s - h), min(n, s + h)
            if r1 - r0 < 2:
                continue
            ant_ia = max(0, faixas[i - 1][0] - aq)
            cur_ia = max(0, faixas[i][0] - aq)
            y0 = saidas[i - 1][r0 - ant_ia:r1 - ant_ia]
            y1 = saidas[i][r0 - cur_ia:r1 - cur_ia]
            w = np.linspace(0.0, 1.0, r1 - r0, dtype=np.float32)
            out[r0:r1] = y0 * (1 - w) + y1 * w
        parcial = destino.with_name(destino.name + ".parcial")
        write_wav(parcial, out, SR)
        # troca atômica: um cache pela metade (luz caiu) nunca vale
        os.replace(parcial, destino)
        return destino
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# -------------------------------------------------------------------- cache
def pasta_do_cache() -> Path:
    return Path(DATA_DIR) / "voz"


def chave(fonte: str | Path, forca: str) -> str:
    p = Path(fonte)
    try:
        st = p.stat()
        ident = f"{p.resolve()}|{st.st_size}|{st.st_mtime_ns}"
    except OSError:
        ident = str(p)
    base = f"{ident}|{VERSAO}|{_REVISAO}|{_limite(forca)}"
    return hashlib.sha1(base.encode("utf-8", "replace")).hexdigest()[:20]


def caminho_limpo(fonte: str | Path, forca: str) -> Path:
    return pasta_do_cache() / f"{chave(fonte, forca)}.wav"


def pronta(fonte: str | Path, forca: str) -> Path | None:
    p = caminho_limpo(fonte, forca)
    return p if p.exists() else None


def andamento(fonte: str | Path, forca: str) -> float | None:
    return _andamento.get(chave(fonte, forca))


def falha(fonte: str | Path, forca: str) -> str:
    return _falhas.get(chave(fonte, forca), "")


def fonte_limpa(fonte: str | Path, forca: str = LIMPEZA_PADRAO,
                progresso: Callable[[float], None] | None = None,
                cancelar: Callable[[], bool] | None = None,
                baixar_se_faltar: bool = True,
                trabalhadores: int | None = None) -> Path:
    """A voz limpa desta gravação: do cache, ou limpa agora. Se outra
    chamada já está limpando a mesma gravação (a que começou quando o arquivo
    chegou), espera por ela em vez de fazer de novo. Levanta RuntimeError."""
    k = chave(fonte, forca)
    feito = pronta(fonte, forca)
    if feito:
        return feito
    if not instalado():
        if not baixar_se_faltar:
            raise RuntimeError("o redutor de ruído não está instalado")
        baixar()
    with _trava_geral:
        trava = _travas.setdefault(k, threading.Lock())
    # quem chega com a limpeza em andamento acompanha o número dela
    while not trava.acquire(timeout=0.5):
        if progresso and k in _andamento:
            progresso(_andamento[k])
        if cancelar and cancelar():
            raise RuntimeError("cancelado")
    try:
        feito = pronta(fonte, forca)
        if feito:
            return feito

        def andou(f: float) -> None:
            _andamento[k] = f
            if progresso:
                progresso(f)

        _andamento[k] = 0.0
        _falhas.pop(k, None)
        try:
            return limpar(fonte, caminho_limpo(fonte, forca), forca, andou,
                          cancelar, trabalhadores)
        except Exception as exc:  # noqa: BLE001
            _falhas[k] = str(exc)
            raise
    finally:
        _andamento.pop(k, None)
        trava.release()


def aquecer(fontes: list[str | Path], forca: str = LIMPEZA_PADRAO) -> None:
    """Começa a limpar JÁ, em segundo plano — é o "na hora que envia o
    arquivo". Usa metade dos núcleos (a transcrição está rodando junto) e
    prioridade baixa. Qualquer falha fica guardada e a exportação tenta de
    novo; nada aqui pode travar ou derrubar a análise."""
    if not suportado():
        return
    alvos = [str(f) for f in fontes if f and Path(f).exists()
             and not pronta(f, forca)]
    if not alvos:
        return

    def vai() -> None:
        _faxina()
        for f in alvos:
            try:
                fonte_limpa(f, forca,
                            trabalhadores=max(1, (os.cpu_count() or 2) // 2))
            except Exception:  # noqa: BLE001 — a exportação tenta de novo
                pass

    threading.Thread(target=vai, name="voz-de-estudio", daemon=True).start()


def _faxina(dias: float = 30.0) -> None:
    """Voz limpa que ninguém usou há um mês vai embora (sai do disco, não
    da gravação: dá para limpar de novo a qualquer hora)."""
    p = pasta_do_cache()
    if not p.exists():
        return
    agora = time.time()
    for f in p.glob("*.wav"):
        try:
            if agora - max(f.stat().st_mtime, f.stat().st_atime) > dias * 86400:
                f.unlink(missing_ok=True)
        except OSError:
            pass
    for f in p.glob("*_trab"):
        try:
            if agora - f.stat().st_mtime > 86400:
                shutil.rmtree(f, ignore_errors=True)
        except OSError:
            pass


# ----------------------------------------------------------------- cadeia
def cadeia_de_estudio(limpa: bool) -> list[str]:
    """O tratamento de microfone de estúdio, depois da limpeza.

    - corpo (140 Hz, +2 dB): o "grave de proximidade" de um microfone perto
      da boca, que o celular a um braço de distância não pega;
    - lama (350 Hz, -2,5 dB): o som de caixa/sala que embola a voz;
    - presença (5 kHz, +1,5 dB): as consoantes na frente, sem estridência;
    - ar (10 kHz, prateleira +2 dB): o brilho de estúdio. SÓ com a voz limpa
      — sem o redutor, esse agudo levantaria o chiado junto.
    """
    etapas = ["equalizer=f=140:t=q:w=0.9:g=2",
              "equalizer=f=350:t=q:w=1.1:g=-2.5",
              "equalizer=f=5000:t=q:w=1.0:g=1.5"]
    if limpa:
        etapas.append("treble=g=2:f=10000:t=q:w=0.7")
    return etapas
