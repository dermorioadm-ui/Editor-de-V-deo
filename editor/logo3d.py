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
    return np.frombuffer(r.stdout, np.uint8).reshape(h, w, 4).copy()


def _suavizar(arr: np.ndarray) -> np.ndarray:
    """A borda do alfa, levemente suavizada: contorno sem escadinha."""
    k = np.array([1, 4, 6, 4, 1], float) / 16
    a = np.pad(arr[..., 3].astype(float), 2, mode="edge")
    a = np.apply_along_axis(lambda v: np.convolve(v, k, mode="same"), 1, a)
    a = np.apply_along_axis(lambda v: np.convolve(v, k, mode="same"), 0, a)
    out = arr.copy()
    out[..., 3] = np.clip(a[2:-2, 2:-2], 0, 255).astype(np.uint8)
    return out


def _dilatar(m: np.ndarray) -> np.ndarray:
    d = m.copy()
    d[1:] |= m[:-1]
    d[:-1] |= m[1:]
    d[:, 1:] |= m[:, :-1]
    d[:, :-1] |= m[:, 1:]
    return d


def sem_fundo(arr: np.ndarray) -> tuple[np.ndarray, bool]:
    """Print ou JPG, sem transparência: o fundo liso (branco, cinza, a cor que
    estiver na borda) sai — só o que encosta na borda e tem a cor dela, para
    o branco DENTRO do logo (o "B" do Booking) ficar. E a poeira em volta (o
    ícone da busca de imagem no canto do print) sai junto."""
    if arr[..., 3].min() < 250:
        return arr, False                        # já tem transparência: é dele
    rgb = arr[..., :3].astype(int)
    borda = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    fundo = np.median(borda, axis=0)
    if np.abs(borda - fundo).sum(1).mean() > 60:
        return arr, False                        # a borda não é um fundo liso
    parecido = np.abs(rgb - fundo).sum(-1) < 60
    fora = np.zeros_like(parecido)
    fora[0], fora[-1], fora[:, 0], fora[:, -1] = parecido[0], parecido[-1], parecido[:, 0], parecido[:, -1]
    while True:
        novo = _dilatar(fora) & parecido
        if (novo == fora).all():
            break
        fora = novo
    logo = ~fora
    logo = _so_o_logo(logo)
    out = arr.copy()
    out[..., 3] = np.where(logo, 255, 0).astype(np.uint8)
    return out, True


def _so_o_logo(m: np.ndarray) -> np.ndarray:
    """Tira as ilhas pequenas e LONGE da peça principal (ícone de canto de
    print, sujeira). Letras de um logotipo ficam: são do tamanho da maior."""
    from collections import deque

    h, w = m.shape
    rotulo = np.zeros((h, w), np.int32)
    areas, caixas = [0], [None]
    n = 0
    for y0, x0 in zip(*np.nonzero(m)):
        if rotulo[y0, x0]:
            continue
        n += 1
        fila = deque([(y0, x0)])
        rotulo[y0, x0] = n
        cnt, y1, y2, x1, x2 = 0, y0, y0, x0, x0
        while fila:
            y, x = fila.popleft()
            cnt += 1
            y1, y2, x1, x2 = min(y1, y), max(y2, y), min(x1, x), max(x2, x)
            for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= yy < h and 0 <= xx < w and m[yy, xx] and not rotulo[yy, xx]:
                    rotulo[yy, xx] = n
                    fila.append((yy, xx))
        areas.append(cnt)
        caixas.append((y1, y2, x1, x2))
    if n <= 1:
        return m
    maior = int(np.argmax(areas))
    my1, my2, mx1, mx2 = caixas[maior]
    folga = 0.05 * max(my2 - my1, mx2 - mx1)
    manter = []
    for k in range(1, n + 1):
        y1, y2, x1, x2 = caixas[k]
        perto = not (x2 < mx1 - folga or x1 > mx2 + folga or y2 < my1 - folga or y1 > my2 + folga)
        # cortado pela borda do print (o ícone de busca do canto): não é do logo
        na_borda = y1 == 0 or x1 == 0 or y2 == h - 1 or x2 == w - 1
        if k == maior or (not na_borda and (areas[k] >= 0.25 * areas[maior]
                                            or (perto and areas[k] >= 12))):
            manter.append(k)
    return np.isin(rotulo, manter)


def _hsv(cor) -> tuple[float, float, float]:
    import colorsys

    return colorsys.rgb_to_hsv(*(float(v) / 255 for v in cor))


def _mesma_cor(a, b) -> bool:
    """Mesma cor do logo, mesmo com degradê (o azul do Booking vai do claro ao
    escuro; o coral do ícone do Airbnb também) — pelo matiz, não pelo brilho."""
    h1, s1, v1 = _hsv(a)
    h2, s2, v2 = _hsv(b)
    neutra1, neutra2 = s1 < 0.18, s2 < 0.18
    if neutra1 and neutra2:
        return abs(v1 - v2) < 0.3                # branco com sombra cinza = branco
    if neutra1 != neutra2:
        return False
    dh = min(abs(h1 - h2), 1 - abs(h1 - h2)) * 360
    return dh < 12 and abs(s1 - s2) < 0.3 and abs(v1 - v2) < 0.45


def _cores(arr: np.ndarray) -> list[tuple[np.ndarray, int]]:
    """As cores principais do logo (até 3), da de maior área para a menor.
    Um pontinho de cor (o ponto azul-claro do Booking) conta: 0,8% basta."""
    px = arr[arr[..., 3] > 200][:, :3].astype(int)
    if not len(px):
        return []
    q = px // 16
    chave = q[:, 0] * 10000 + q[:, 1] * 100 + q[:, 2]
    vals, cont = np.unique(chave, return_counts=True)
    ordem = np.argsort(-cont)
    grupos: list[list] = []                      # [cor representante, contagem]
    for i in ordem[:60]:
        cor = np.median(px[chave == vals[i]], axis=0)
        alvo = next((g for g in grupos if _mesma_cor(g[0], cor)), None)
        if alvo is not None:
            alvo[1] += int(cont[i])
        elif cont[i] >= 0.008 * len(px):
            grupos.append([cor, int(cont[i])])
    grupos.sort(key=lambda g: -g[1])
    return [(g[0], g[1]) for g in grupos[:MAX_CAMADAS]]


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
    arr, _ = sem_fundo(_ler(caminho))
    arr = _suavizar(arr)
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
    # as cores de cima só DENTRO da silhueta, longe da borda: no print (JPG),
    # a borda do círculo azul misturada com o fundo branco virava um anel
    # "branco" fino em volta do logo
    miolo = alfa.copy()
    for _ in range(3):
        miolo = ~_dilatar(~miolo)
    def abrir(m: np.ndarray, n: int = 2) -> np.ndarray:
        # abertura: some o que é mais fino que 2n px — o fio de antialias entre
        # o branco e o azul virava um contorno azul-claro em volta do "B"
        for _ in range(n):
            m = ~_dilatar(~m)
        for _ in range(n):
            m = _dilatar(m)
        return m
    mascaras = [alfa] + [abrir(miolo & (dono == k)) for k in range(1, len(cores))]
    area_total = float(alfa.sum())
    for k, masc in enumerate(mascaras):
        if k and masc.mean() < 0.001:
            continue
        lacos = []
        minimo = 30 if k == 0 else max(30.0, 0.002 * area_total)
        for laco in contornos(masc):
            if _area(laco) < minimo:
                continue                 # poeira de antialias / de JPG
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


def quantos_logos(alfa: np.ndarray) -> int:
    """Quantos logos SEPARADOS há na imagem (um print com o Booking e o
    Airbnb lado a lado = 2). Peças perto umas das outras (as letras de um
    logotipo, o ponto do "B.") contam como o mesmo logo."""
    from collections import deque

    pequeno = alfa[::4, ::4]
    h, w = pequeno.shape
    rot = np.zeros((h, w), np.int32)
    caixas = []
    for y0, x0 in zip(*np.nonzero(pequeno)):
        if rot[y0, x0]:
            continue
        rot[y0, x0] = len(caixas) + 1
        fila, cx = deque([(y0, x0)]), [y0, y0, x0, x0, 0]
        while fila:
            y, x = fila.popleft()
            cx[4] += 1
            cx[0], cx[1], cx[2], cx[3] = min(cx[0], y), max(cx[1], y), min(cx[2], x), max(cx[3], x)
            for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= yy < h and 0 <= xx < w and pequeno[yy, xx] and not rot[yy, xx]:
                    rot[yy, xx] = len(caixas) + 1
                    fila.append((yy, xx))
        caixas.append(cx)
    if len(caixas) <= 1:
        return len(caixas)
    # um logo GRANDE é uma peça que sozinha ocupa boa parte da imagem: dois
    # ícones de app lado a lado são dois; as letras de um logotipo (pequenas
    # perto da palavra inteira) e o símbolo ao lado do nome são um só
    ys, xs = np.nonzero(pequeno)
    lado_total = max(ys.max() - ys.min(), xs.max() - xs.min(), 1)
    area_total = int(pequeno.sum())
    grandes = [c for c in caixas
               if max(c[1] - c[0], c[3] - c[2]) >= 0.35 * lado_total and c[4] >= 0.1 * area_total]
    return max(1, len(grandes))


def limpar_para_biblioteca(origem: str, base: str) -> dict:
    """Um print ou JPG de logo vira PNG transparente para a biblioteca: o fundo
    liso sai, a poeira do canto sai. Com transparência de verdade, o arquivo
    dele é copiado como está. ``base``: o caminho SEM extensão. Devolve
    ``{"caminho", "fundo_tirado"}``."""
    import shutil
    import subprocess
    from pathlib import Path

    from .config import FFMPEG

    arr = _ler(origem)
    limpo, tirou = sem_fundo(arr)
    if not tirou:
        if arr[..., 3].min() >= 250:
            raise ValueError("não achei o fundo do logo para tirar (a borda da imagem não é "
                             "de uma cor só) — use um PNG com fundo transparente, ou um print "
                             "com o logo sobre fundo liso")
        destino = f"{base}{Path(origem).suffix.lower()}"
        shutil.copyfile(origem, destino)
        return {"caminho": destino, "fundo_tirado": False}
    n = quantos_logos(limpo[..., 3] > 128)
    if n > 1:
        raise ValueError(f"achei {n} logos nessa imagem — recorte um de cada vez "
                         "(no Windows: Win+Shift+S) e ponha cada um com o nome dele")
    limpo = _suavizar(limpo)
    h, w = limpo.shape[:2]
    destino = f"{base}.png"
    r = subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba",
                        "-s", f"{w}x{h}", "-i", "-", "-frames:v", "1", destino],
                       input=limpo.tobytes(), capture_output=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode:
        raise ValueError("não consegui gravar o PNG do logo")
    return {"caminho": destino, "fundo_tirado": True}
