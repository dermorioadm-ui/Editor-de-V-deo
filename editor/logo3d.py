"""LOGO 3D a partir do PNG — o Airbnb, o Booking, qualquer logo da biblioteca.

"Eu quero também o 3D da logo do Airbnb e Booking, animado, flutuante, que
esconde atrás de mim." Os logos de plataforma NÃO vêm com o Sharkcut (são
marcas dos outros): ele põe o PNG na biblioteca (aba Pós → pôr logo) e daqui
sai a peça 3D de verdade, sem ninguém desenhar nada:

1. o PNG é lido (com a transparência) e separado nas suas cores principais —
   o Booking tem o azul e o branco, o Airbnb o coral;
2. cada cor vira CONTORNOS (marching squares na borda da cor, simplificados),
   com os buracos no lugar (o "o" do Booking, o vão do símbolo do Airbnb);
3. o Blender extruda: a silhueta inteira na cor dominante, e as outras cores
   em relevo por cima — de frente é o logo exato; de lado, uma peça sólida.

Tudo nesta máquina: o PNG não sai daqui, e o render fica em cache global
(o mesmo logo com a mesma animação serve para todos os vídeos).
"""
from __future__ import annotations

import math

import numpy as np

LADO = 720           # o PNG é trabalhado nesta resolução (maior lado)
MAX_PONTOS = 9000    # somando todos os contornos de todas as camadas
MAX_CAMADAS = 3


def _ler(caminho: str) -> np.ndarray:
    """O PNG (ou WebP) em RGBA, com o maior lado em LADO px — pelo ffmpeg, que
    o Sharkcut já tem (sem depender de biblioteca de imagem)."""
    import json
    import subprocess

    from .config import FFMPEG, FFPROBE

    info = json.loads(subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "json", str(caminho)],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout or "{}")
    st = (info.get("streams") or [{}])[0]
    w0, h0 = int(st.get("width") or 0), int(st.get("height") or 0)
    if w0 < 2 or h0 < 2:
        raise ValueError("não consegui ler o PNG do logo")
    esc = LADO / max(w0, h0)
    w, h = max(8, round(w0 * esc)) // 2 * 2, max(8, round(h0 * esc)) // 2 * 2
    r = subprocess.run([FFMPEG, "-v", "error", "-i", str(caminho), "-frames:v", "1",
                        "-vf", f"scale={w}:{h}:flags=lanczos", "-f", "rawvideo",
                        "-pix_fmt", "rgba", "-"], capture_output=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode or len(r.stdout) != w * h * 4:
        raise ValueError("não consegui ler o PNG do logo")
    arr = np.frombuffer(r.stdout, np.uint8).reshape(h, w, 4).copy()
    # a borda do alfa, levemente suavizada: contorno sem escadinha
    k = np.array([1, 4, 6, 4, 1], float) / 16
    a = arr[..., 3].astype(float)
    a = np.apply_along_axis(lambda v: np.convolve(v, k, mode="same"), 1, a)
    a = np.apply_along_axis(lambda v: np.convolve(v, k, mode="same"), 0, a)
    arr[..., 3] = np.clip(a, 0, 255).astype(np.uint8)
    return arr


def _cores(arr: np.ndarray) -> list[tuple[np.ndarray, int]]:
    """As cores principais do logo (até 3), da de maior área para a menor."""
    px = arr[arr[..., 3] > 200][:, :3].astype(int)
    if not len(px):
        return []
    q = px // 24
    chave = q[:, 0] * 10000 + q[:, 1] * 100 + q[:, 2]
    vals, cont = np.unique(chave, return_counts=True)
    ordem = np.argsort(-cont)
    escolhidas: list[tuple[np.ndarray, int]] = []
    for i in ordem[:12]:
        if cont[i] < 0.03 * len(px):
            break
        cor = np.median(px[chave == vals[i]], axis=0)
        # uma cor quase igual a outra já escolhida é a mesma (antialias, degradê leve)
        if any(np.abs(cor - c).sum() < 70 for c, _ in escolhidas):
            continue
        escolhidas.append((cor, int(cont[i])))
        if len(escolhidas) >= MAX_CAMADAS:
            break
    return escolhidas


def _hex(cor) -> str:
    return "#%02X%02X%02X" % tuple(int(max(0, min(255, round(v)))) for v in cor)


# -------------------------------------------------------------- contornos
# marching squares: para cada célula 2x2, as arestas por onde a borda passa.
# Arestas: 0 = topo, 1 = direita, 2 = baixo, 3 = esquerda.
_TABELA = {1: [(3, 2)], 2: [(2, 1)], 3: [(3, 1)], 4: [(0, 1)], 5: [(3, 0), (2, 1)],
           6: [(0, 2)], 7: [(3, 0)], 8: [(0, 3)], 9: [(0, 2)], 10: [(0, 1), (3, 2)],
           11: [(0, 1)], 12: [(3, 1)], 13: [(2, 1)], 14: [(3, 2)]}


def _ponto(i: int, j: int, aresta: int) -> tuple[int, int]:
    """O meio da aresta, em coordenadas dobradas (inteiras): (x2, y2)."""
    if aresta == 0:
        return (2 * j + 1, 2 * i)
    if aresta == 1:
        return (2 * j + 2, 2 * i + 1)
    if aresta == 2:
        return (2 * j + 1, 2 * i + 2)
    return (2 * j, 2 * i + 1)


def contornos(mascara: np.ndarray) -> list[list[tuple[float, float]]]:
    """Os laços fechados da borda de uma máscara booleana (em pixels)."""
    m = np.pad(mascara.astype(np.uint8), 1)
    idx = (m[:-1, :-1] * 8 + m[:-1, 1:] * 4 + m[1:, 1:] * 2 + m[1:, :-1])
    vizinhos: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for i, j in zip(*np.nonzero((idx > 0) & (idx < 15))):
        for a, b in _TABELA[int(idx[i, j])]:
            p, q = _ponto(i, j, a), _ponto(i, j, b)
            vizinhos.setdefault(p, []).append(q)
            vizinhos.setdefault(q, []).append(p)
    lacos, vistos = [], set()
    for inicio in vizinhos:
        if inicio in vistos:
            continue
        laco, ant, atual = [], None, inicio
        while atual not in vistos:
            vistos.add(atual)
            laco.append(atual)
            prox = [v for v in vizinhos[atual] if v != ant and v not in vistos]
            if not prox:
                break
            ant, atual = atual, prox[0]
        if len(laco) >= 8:
            # -1: o pad; /2: as coordenadas dobradas
            lacos.append([((x / 2) - 1, (y / 2) - 1) for x, y in laco])
    return lacos


def _simplificar(pts: list, eps: float) -> list:
    """Ramer–Douglas–Peucker num laço fechado."""
    if len(pts) < 4:
        return pts
    arr = np.asarray(pts, float)
    # corta o laço no ponto mais distante do primeiro: vira duas linhas abertas
    k = int(np.argmax(((arr - arr[0]) ** 2).sum(1)))

    def rdp(seg: np.ndarray) -> list:
        manter = [0, len(seg) - 1]
        pilha = [(0, len(seg) - 1)]
        while pilha:
            a, b = pilha.pop()
            if b <= a + 1:
                continue
            p, q = seg[a], seg[b]
            d = q - p
            n = math.hypot(*d) or 1e-9
            dist = np.abs(d[0] * (seg[a + 1:b, 1] - p[1]) - d[1] * (seg[a + 1:b, 0] - p[0])) / n
            m = int(np.argmax(dist))
            if dist[m] > eps:
                c = a + 1 + m
                manter.append(c)
                pilha += [(a, c), (c, b)]
        return [tuple(seg[i]) for i in sorted(set(manter))]
    ida = rdp(arr[:k + 1])
    volta = rdp(np.vstack([arr[k:], arr[:1]]))
    return ida[:-1] + volta[:-1]


def _area(laco) -> float:
    a = np.asarray(laco)
    return 0.5 * abs(np.dot(a[:, 0], np.roll(a[:, 1], 1)) - np.dot(a[:, 1], np.roll(a[:, 0], 1)))


def camadas(caminho: str) -> dict:
    """O logo em camadas de contornos, prontas para o Blender extrudar.

    Devolve ``{"proporcao", "camadas": [{"cor", "lacos": [[[x, y], ...]]}]}``
    com x, y normalizados: o maior lado mede 2 unidades, centro na origem,
    y para cima. A primeira camada é a silhueta inteira na cor dominante.
    """
    arr = _ler(caminho)
    h, w = arr.shape[:2]
    alfa = arr[..., 3] > 128
    if alfa.mean() < 0.002:
        raise ValueError("o PNG do logo está vazio (sem nada opaco)")
    cores = _cores(arr)
    if not cores:
        raise ValueError("não achei as cores do logo")
    rgb = arr[..., :3].astype(int)
    paleta = np.array([c for c, _ in cores])
    dist = np.abs(rgb[:, :, None, :] - paleta[None, None, :, :]).sum(-1)
    dono = np.argmin(dist, axis=-1)
    ys, xs = np.nonzero(alfa)
    cx, cy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
    esc = 2.0 / max(xs.max() - xs.min() + 1, ys.max() - ys.min() + 1)
    saida, total = [], 0
    mascaras = [alfa] + [alfa & (dono == k) for k in range(1, len(cores))]
    for k, masc in enumerate(mascaras):
        if k and masc.mean() < 0.001:
            continue
        lacos = []
        for laco in contornos(masc):
            if _area(laco) < 30:
                continue                 # poeira de antialias
            simples = _simplificar(laco, 0.9)
            if len(simples) >= 3:
                lacos.append([[round((x - cx) * esc, 4), round((cy - y) * esc, 4)]
                              for x, y in simples])
        if lacos:
            total += sum(len(lc) for lc in lacos)
            saida.append({"cor": _hex(cores[k][0]), "lacos": lacos})
    if not saida:
        raise ValueError("não consegui tirar o contorno do logo")
    if total > MAX_PONTOS:
        raise ValueError("o logo tem detalhe demais para virar 3D — use um PNG mais simples")
    larg = (xs.max() - xs.min() + 1) / (ys.max() - ys.min() + 1)
    return {"proporcao": round(float(larg), 3), "camadas": saida}
