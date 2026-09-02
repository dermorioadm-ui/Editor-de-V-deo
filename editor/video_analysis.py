"""Análises sobre o vídeo (zona segura de legenda queimada — Parte 8)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from .config import FFMPEG
from .ffmpeg_utils import MediaInfo, probe

SAMPLE_WIDTH = 240


def detect_subtitle_band(path: str | Path, info: MediaInfo | None = None,
                         frames: int = 40) -> dict:
    """Acha a faixa que a legenda queimada ocupa.

    Procura linhas da metade inferior com muitos pixels quase brancos. Serve
    para impedir que um elemento seja posicionado em cima dela.
    """
    info = info or probe(path)
    w, h = info.display_size
    if not w or not h:
        return {"available": False, "reason": "vídeo sem dimensões"}
    sw = SAMPLE_WIDTH
    sh = max(2, int(round(h * sw / w)))
    sh -= sh % 2
    # SÓ OS QUADROS-CHAVE. `select` sozinho obrigava o ffmpeg a DECODIFICAR o
    # vídeo inteiro para jogar fora 33 mil quadros e ficar com 40 — medido:
    # 11,9 s por minuto de 1080p60, ou ~111 s de CPU cheia num vídeo de 9 min,
    # disparados no instante em que o editor abre, em cima da exportação de
    # fundo. Com -skip_frame nokey o decodificador pula tudo que não é
    # quadro-chave (um a cada ~2 s): decodifica 1 em 120 e a amostra é a mesma.
    chaves = max(1, int(info.duration / 2.0))          # GOP típico de 2 s
    step = max(1, chaves // max(frames, 1))
    proc = subprocess.run(
        [FFMPEG, "-v", "error", "-nostdin", "-skip_frame", "nokey",
         "-i", str(path),
         "-vf", f"select='not(mod(n\\,{step}))',scale={sw}:{sh}",
         "-vsync", "vfr", "-frames:v", str(frames),
         "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"],
        capture_output=True,
    )
    data = np.frombuffer(proc.stdout, dtype=np.uint8)
    n = len(data) // (sw * sh)
    if n == 0:
        return {"available": False, "reason": "não foi possível ler quadros"}
    stack = data[: n * sw * sh].reshape(n, sh, sw)
    bright = (stack > 205).mean(axis=2)          # fração de pixels quase brancos
    profile = bright.mean(axis=0)
    half = sh // 2
    lower = profile.copy()
    lower[:half] = 0.0
    threshold = max(0.045, float(lower.max()) * 0.35)
    rows = np.flatnonzero(lower >= threshold)
    if not rows.size:
        return {"available": True, "found": False,
                "profile": [round(float(v), 4) for v in profile],
                "message": "nenhuma faixa de legenda queimada detectada"}
    top = int(rows.min())
    bottom = int(rows.max())
    return {
        "available": True, "found": True,
        "top": round(top / sh, 4), "bottom": round((bottom + 1) / sh, 4),
        "top_px": int(round(top / sh * h)),
        "bottom_px": int(round((bottom + 1) / sh * h)),
        "coverage": round(float(lower[rows].mean()), 4),
        "profile": [round(float(v), 4) for v in profile],
        "message": (f"legenda queimada detectada entre {top/sh*100:.0f}% e "
                    f"{(bottom+1)/sh*100:.0f}% da altura — essa faixa fica bloqueada"),
    }


def suggest_anchor(band: dict, info: MediaInfo) -> dict:
    """Uma âncora única no topo, reaproveitada por todos os elementos.

    Consistência vale mais que variedade.
    """
    w, h = info.display_size
    y = 0.14
    if band.get("found") and band.get("top", 1.0) < 0.35:
        y = max(0.06, band["top"] - 0.12)
    return {"x": 0.5, "y": round(y, 4),
            "x_px": int(w / 2), "y_px": int(round(y * h)),
            "reason": "âncora única no topo, fora da faixa de legenda"}


def frame_jpeg(path: str | Path, time: float, filters: str = "",
               width: int = 360) -> tuple[bytes, float]:
    """Um quadro em JPEG (para comparação lado a lado) e o brilho médio dele.

    Se o instante pedido não existir — imagem parada, ou tempo além do fim —
    cai para o começo do arquivo em vez de falhar.
    """
    chain = ",".join(x for x in (filters, f"scale={width}:-2") if x)

    def shot(seek: float | None, out_args: list[str]) -> bytes:
        cmd = [FFMPEG, "-v", "error", "-nostdin"]
        if seek is not None:
            cmd += ["-ss", f"{max(0.0, seek):.3f}"]
        cmd += ["-i", str(path), "-vf", chain, "-frames:v", "1", *out_args, "pipe:1"]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0 or not proc.stdout:
            detail = proc.stderr.decode("utf-8", "replace").strip()[-300:]
            raise RuntimeError(detail or "o ffmpeg não devolveu quadro nenhum")
        return proc.stdout

    jpeg_args = ["-f", "image2", "-vcodec", "mjpeg", "-q:v", "3"]
    gray_args = ["-pix_fmt", "gray", "-f", "rawvideo"]
    try:
        data = shot(time, jpeg_args)
        raw = shot(time, gray_args)
    except RuntimeError:
        data = shot(None, jpeg_args)
        raw = shot(None, gray_args)
    arr = np.frombuffer(raw, dtype=np.uint8)
    return data, (float(arr.mean()) if arr.size else 0.0)


# ------------------------------------------------------------------- rosto
FACE_FALLBACK = (0.50, 0.44)   # um pouco acima do centro geométrico
FACE_SAMPLES = 26
FACE_GRID = 64                 # largura da grade de análise


def _weighted_median(peso: "np.ndarray") -> float:
    """Mediana ponderada de um perfil 1D, em fração de 0 a 1."""
    total = float(peso.sum())
    if total <= 1e-9:
        return 0.5
    acum = np.cumsum(peso) / total
    i = int(np.searchsorted(acum, 0.5))
    return float(min(max(i, 0), len(peso) - 1)) / max(len(peso) - 1, 1)


def face_center(path: str | Path, duration: float,
                samples: int = FACE_SAMPLES) -> dict:
    """Onde está o rosto, em fração da largura e da altura.

    Sem detector de rosto: numa gravação de câmera parada, o rosto é onde
    está o MOVIMENTO — a boca abre e fecha, a cabeça oscila, e o fundo fica
    parado. Amostramos quadros ao longo do vídeo, medimos a diferença entre
    quadros vizinhos e tiramos a MEDIANA das posições (não a média: um quadro
    com detecção errada estraga a média, a mediana ignora).

    Se o usuário tiver OpenCV instalado, o haarcascade entra na frente — é
    mais preciso quando existe. Mas o editor não depende dele.
    """
    por_cv = _face_center_opencv(path, duration, samples)
    if por_cv:
        return por_cv

    amostras = _motion_centers(path, duration, samples)
    if not amostras:
        return {"x": FACE_FALLBACK[0], "y": FACE_FALLBACK[1],
                "method": "padrao", "samples": 0,
                "detail": "não deu para medir movimento; usando o padrão"}
    xs = sorted(a[0] for a in amostras)
    ys = sorted(a[1] for a in amostras)
    n = len(xs)
    x = xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2
    y = ys[n // 2] if n % 2 else (ys[n // 2 - 1] + ys[n // 2]) / 2
    # espalhamento: se as amostras discordam muito, a medida não vale nada
    espalha = float(np.median([abs(a[1] - y) for a in amostras]))
    if espalha > 0.22:
        return {"x": FACE_FALLBACK[0], "y": FACE_FALLBACK[1],
                "method": "padrao", "samples": n,
                "detail": f"movimento espalhado demais (±{espalha:.2f}); "
                          f"usando o padrão"}
    return {"x": round(float(x), 4), "y": round(float(y), 4),
            "method": "movimento", "samples": n,
            "detail": f"mediana de {n} amostras de movimento (±{espalha:.2f})"}


def _motion_centers(path: str | Path, duration: float,
                    samples: int) -> list[tuple[float, float]]:
    """Centro do movimento em pares de quadros VIZINHOS, espalhados no vídeo.

    Comparar dois quadros distantes não mede movimento de boca: mede que a
    pessoa mudou de pose. Pior, amostrar em intervalo fixo contra um
    movimento periódico dá aliasing — no teste, quadros a 0,77 s de distância
    contra uma boca a 2,6 Hz saíram IDÊNTICOS. Por isso cada amostra é um par
    de quadros consecutivos: a diferença entre eles é a boca, e mais nada.
    """
    info = probe(path)
    dw, dh = info.display_size
    largura = FACE_GRID
    alt = max(2, int(round(largura * (dh or 1) / max(dw or 1, 1))))
    alt -= alt % 2
    quadro = largura * alt
    centros: list[tuple[float, float]] = []
    for k in range(samples):
        t = duration * (k + 0.5) / max(samples, 1)
        cmd = [FFMPEG, "-v", "error", "-nostdin",
               "-ss", f"{max(0.0, t):.3f}", "-i", str(path),
               "-frames:v", "2", "-vf", f"scale={largura}:-2,format=gray",
               "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0 or len(proc.stdout) < quadro * 2:
            continue
        par = np.frombuffer(proc.stdout[: quadro * 2],
                            dtype=np.uint8).reshape(2, alt, largura).astype(np.float32)
        dif = np.abs(par[1] - par[0])
        pico = float(dif.max())
        if pico < 5.0:
            continue                     # quadro parado: não diz nada
        # Só o que se mexeu DE VERDADE. Cortar por percentil deixava passar o
        # ruído de compressão do fundo inteiro — e a boca, que é o sinal, tem
        # menos de 1% dos pixels. O corte relativo ao pico isola o movimento.
        dif = np.where(dif > pico * 0.45, dif, 0.0)
        if float(dif.sum()) <= 0.0:
            continue
        centros.append((_weighted_median(dif.sum(axis=0)),
                        _weighted_median(dif.sum(axis=1))))
    return centros


def _face_center_opencv(path: str | Path, duration: float,
                        samples: int) -> dict | None:
    """Haarcascade, se o usuário tiver OpenCV. Opcional de propósito."""
    try:
        import cv2  # noqa: PLC0415
    except ImportError:
        return None
    try:
        cascata = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        cap = cv2.VideoCapture(str(path))
        achados: list[tuple[float, float]] = []
        for k in range(samples):
            cap.set(cv2.CAP_PROP_POS_MSEC, (duration * k / max(samples, 1)) * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            h, w = frame.shape[:2]
            cinza = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascata.detectMultiScale(cinza, 1.15, 5, minSize=(w // 12, w // 12))
            if len(faces):
                fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
                achados.append(((fx + fw / 2) / w, (fy + fh / 2) / h))
        cap.release()
        if len(achados) < 3:
            return None
        xs = sorted(a[0] for a in achados)
        ys = sorted(a[1] for a in achados)
        m = len(xs) // 2
        return {"x": round(float(xs[m]), 4), "y": round(float(ys[m]), 4),
                "method": "opencv", "samples": len(achados),
                "detail": f"haarcascade em {len(achados)} quadros"}
    except Exception:  # noqa: BLE001
        return None
