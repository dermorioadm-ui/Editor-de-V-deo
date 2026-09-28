"""A MARCA PELO PDF: solta o manual da identidade visual e o kit fica salvo.

"Quero na primeira tela poder jogar em pdf a identidade visual da marca e daí
fica salvo." Lê o PDF NESTA máquina com o pypdfium2 (PDFium, Apache-2.0/BSD-3,
wheel pronta para Windows, sem programa externo) e tira:

- texto por página, montado em parágrafos e seções (Cor, Tipografia, Voz…);
- cores: os hex/RGB/CMYK escritos no manual com o rótulo ao lado ("#FF385C ·
  marca, título") viram os papéis do kit (marca, confirma, alerta, texto…);
  sem rótulo, a cor viva de maior área é a marca;
- fonte: a citada no texto ("DM Sans em dois pesos"); o arquivo vem junto
  quando está embutido inteiro no PDF (subconjunto não serve para escrever);
- logos: os LOGOS EM VETOR, agrupando os caminhos da página por contenção,
  descascando os cartões de apresentação e renderizando SÓ os objetos do
  logo com fundo transparente; cada um ganha um papel (simbolo,
  simbolo_negativo, assinatura, assinatura_branca…);
- regras, voz e frases da marca, pelas seções do manual.

Nada aqui sai da máquina e não há IA: o kit sai do que está escrito e
desenhado no PDF. O que o kit já tinha (um kit que veio com o programa ou foi
ajustado à mão) fica — o PDF completa o que falta.
"""
from __future__ import annotations

import colorsys
import ctypes
import json
import re
import struct
import unicodedata
import zlib
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as raw

T_TEXTO, T_CAMINHO, T_IMAGEM, T_SOMBREADO, T_FORM = 1, 2, 3, 4, 5
MAX_PAGINAS = 40                 # manual de marca grande: o essencial está no começo
MAX_BYTES = 80 * 1024 * 1024


# ------------------------------------------------------------------ PNG
def escrever_png(caminho, arr) -> None:
    """HxWx3 (RGB) ou HxWx4 (RGBA) uint8 → PNG. Sem Pillow."""
    arr = np.ascontiguousarray(arr, dtype=np.uint8)
    h, w, c = arr.shape
    cru = np.zeros((h, w * c + 1), np.uint8)
    cru[:, 1:] = arr.reshape(h, -1)

    def ch(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    Path(caminho).write_bytes(b"\x89PNG\r\n\x1a\n"
                              + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, {3: 2, 4: 6}[c], 0, 0, 0))
                              + ch(b"IDAT", zlib.compress(cru.tobytes(), 6)) + ch(b"IEND", b""))


def _rgb(bm) -> np.ndarray:
    """PdfBitmap (BGR/BGRA) → RGB/RGBA."""
    a = bm.to_numpy()
    if bm.mode in ("BGR", "BGRA", "BGRX"):
        a = a[..., [2, 1, 0] + ([3] if a.shape[2] == 4 else [])]
    return a[..., :4] if bm.mode != "BGRX" else a[..., :3]


# ----------------------------------------------------------------- cores
def _hex(r, g, b) -> str:
    return "#%02X%02X%02X" % (int(r), int(g), int(b))


def _cor_do_obj(h, traco=False):
    R, G, B, A = (ctypes.c_uint() for _ in range(4))
    f = raw.FPDFPageObj_GetStrokeColor if traco else raw.FPDFPageObj_GetFillColor
    if not f(h, R, G, B, A):
        return None, 0
    return _hex(R.value, G.value, B.value), A.value


def neutra(hx: str) -> bool:
    r, g, b = (int(hx[i:i + 2], 16) / 255 for i in (1, 3, 5))
    _, l, s = colorsys.rgb_to_hls(r, g, b)
    return s < 0.18 or l > 0.95 or l < 0.06


_RX_HEX = re.compile(r"#([0-9A-Fa-f]{6})\b")
_RX_RGB = re.compile(r"\bRGB\s*[:(]?\s*(\d{1,3})[\s,/]+(\d{1,3})[\s,/]+(\d{1,3})", re.I)
_RX_CMYK = re.compile(r"\bCMYK\s*[:(]?\s*(\d{1,3})[\s,/%]+(\d{1,3})[\s,/%]+(\d{1,3})[\s,/%]+(\d{1,3})", re.I)


def cores_do_texto(paginas: list[str]) -> list[dict]:
    """Os códigos de cor escritos no manual, com o rótulo em volta.

    O rótulo é o trecho até o próximo código (``#000000 texto #484848 corpo``)
    ou, se vazio, a linha de cima (``Coral`` / ``#FF385C · marca, título``).
    """
    achadas: dict[str, dict] = {}
    conv = ((_RX_HEX, lambda m: "#" + m.group(1).upper(), "hex"),
            (_RX_RGB, lambda m: _hex(*(min(255, int(x)) for x in m.groups())), "rgb"),
            (_RX_CMYK, lambda m: _hex(*_cmyk(*(int(x) / 100 for x in m.groups()))), "cmyk"))
    for n, texto in enumerate(paginas, 1):
        linhas = [ln.strip() for ln in texto.splitlines()]
        for i, ln in enumerate(linhas):
            achados = sorted(((m, f, tipo) for rx, f, tipo in conv for m in rx.finditer(ln)),
                             key=lambda x: x[0].start())
            for j, (m, f, tipo) in enumerate(achados):
                hx = f(m)
                fim = achados[j + 1][0].start() if j + 1 < len(achados) else len(ln)
                depois = ln[m.end():fim].strip(" ·—-:|,")
                antes = ln[achados[j - 1][0].end() if j else 0:m.start()].strip(" ·—-:|,") if j == 0 else ""
                cima = linhas[i - 1] if i and not any(rx.search(linhas[i - 1]) for rx, _, _ in conv) else ""
                partes = [p for p in (antes or cima, depois) if p]
                rotulo = " · ".join(partes)[:80]
                d = achadas.setdefault(hx, {"hex": hx, "rotulo": rotulo, "pagina": n, "de": tipo, "vezes": 0})
                d["vezes"] += 1
    return list(achadas.values())


def _cmyk(c, m, y, k):
    """Conversão ingênua (sem perfil ICC) — a mesma ordem de grandeza do PDFium."""
    return tuple(round(255 * (1 - v) * (1 - k)) for v in (c, m, y))


# ------------------------------------------------------------------ fontes
def _info_fonte(h) -> dict | None:
    f = raw.FPDFTextObj_GetFont(h)
    if not f:
        return None
    buf = ctypes.create_string_buffer(256)
    raw.FPDFFont_GetBaseFontName(f, buf, 256)
    base = buf.value.decode("latin-1")
    raw.FPDFFont_GetFamilyName(f, buf, 256)
    fam = buf.value.decode("latin-1")
    emb = bool(raw.FPDFFont_GetIsEmbedded(f))
    if not emb:
        fam = base.split(",")[0]          # a família do PDFium seria a fonte substituta dele
    return {"base": base, "familia": fam, "embutida": emb,
            "subconjunto": bool(re.match(r"^[A-Z]{6}\+", base)),
            "type3": not base and not fam, "_h": f}


def _dados_da_fonte(f) -> bytes:
    n = ctypes.c_size_t()
    raw.FPDFFont_GetFontData(f, None, 0, ctypes.byref(n))
    if not n.value:
        return b""
    buf = (ctypes.c_uint8 * n.value)()
    raw.FPDFFont_GetFontData(f, buf, n.value, ctypes.byref(n))
    return bytes(buf)


_RX_FONTE_CITADA = re.compile(
    r"(?i:tipografia|fonte|typeface|font|família)\s*(?i:institucional|principal|da marca|oficial)?\s*[:\-–—]?\s*\n?\s*"
    r"([A-Z][A-Za-z0-9]*(?: [A-Z][A-Za-z0-9]+){0,2})")
_RX_FONTE_PESOS = re.compile(r"\b([A-Z][A-Za-z0-9]+(?: [A-Z][A-Za-z0-9]+){0,2})\s+(?:em \w+ pesos|Regular|Medium|Bold|Light|SemiBold)\b")


_KW_FONTE = re.compile(r"tipografia|fonte|typeface|font\b|família tipográfica", re.I)
_NOME_FONTE = re.compile(r"\b([A-Z][A-Za-z0-9]*(?: [A-Z][A-Za-z0-9]+){0,2})")
_NAO_E_FONTE = {"fonte", "tipografia", "font", "typeface", "regular", "medium", "bold", "light",
                "semibold", "títulos", "titulos", "texto", "nada", "a", "o", "em", "sem", "use"}


def fontes_citadas(paginas: list[str]) -> list[str]:
    """Nomes de fonte escritos no manual: depois de "Tipografia"/"Fonte", ou antes de um peso."""
    vistos: list[str] = []

    def por(nome):
        nome = re.sub(r"\s+(Regular|Medium|Bold|Light|SemiBold|Black|Thin)$", "", nome.strip())
        if nome and nome.lower() not in _NAO_E_FONTE and nome not in vistos:
            vistos.append(nome)
    for texto in paginas:
        linhas = [ln.strip() for ln in texto.splitlines() if ln.strip()]
        for i, ln in enumerate(linhas):
            m = _KW_FONTE.search(ln)
            if m:
                trecho = ln[m.end():] + " \n " + (linhas[i + 1] if i + 1 < len(linhas) else "")
                for n in _NOME_FONTE.findall(trecho):
                    if n.lower() not in _NAO_E_FONTE and n.split()[0].lower() not in _NAO_E_FONTE:
                        por(n)
                        break
            for n in re.findall(r"\b([A-Z][A-Za-z0-9]*(?: [A-Z][A-Za-z0-9]+){0,2})\s+(?:em \w+ pesos|Regular|Medium|Bold|SemiBold)\b", ln):
                por(n)
    return vistos


# -------------------------------------------------------------- a leitura
def ler(caminho: str | Path, saida: str | Path, dpi_pagina: int = 100,
        paginas: bool = True, max_paginas: int = MAX_PAGINAS) -> dict:
    """Extrai tudo do PDF e grava as páginas/imagens/fontes/logos em ``saida``."""
    saida = Path(saida)
    (saida / "paginas").mkdir(parents=True, exist_ok=True)
    # em memória: no Windows o arquivo aberto não deixaria o usuário mover/apagar o PDF
    pdf = pdfium.PdfDocument(Path(caminho).read_bytes())
    out = {"arquivo": Path(caminho).name, "paginas": [], "cores_objetos": {},
           "cores_texto_obj": {}, "fontes": {}, "imagens": [], "logos": []}
    area_cor: dict[str, float] = {}
    fontes: dict[str, dict] = {}
    for i in range(min(len(pdf), max_paginas)):
        pg = pdf[i]
        W, H = pg.get_size()
        texto = pg.get_textpage().get_text_range()
        out["paginas"].append(texto)
        if paginas:
            png = saida / "paginas" / f"pagina_{i + 1:02d}.png"
            escrever_png(png, _rgb(pg.render(scale=dpi_pagina / 72))[..., :3])
        for o in pg.get_objects(max_depth=15):
            h, t = o.raw, raw.FPDFPageObj_GetType(o.raw)
            L, B, R, T = o.get_bounds()
            area = max(0.0, R - L) * max(0.0, T - B)
            if t == T_CAMINHO:
                fm, st = ctypes.c_int(), ctypes.c_int()
                raw.FPDFPath_GetDrawMode(h, ctypes.byref(fm), ctypes.byref(st))
                if fm.value:
                    hx, a = _cor_do_obj(h)
                    if hx and a >= 128 and area < 0.9 * W * H:
                        area_cor[hx] = area_cor.get(hx, 0) + area
            elif t == T_TEXTO:
                hx, _ = _cor_do_obj(h)
                if hx:
                    out["cores_texto_obj"][hx] = out["cores_texto_obj"].get(hx, 0) + 1
                inf = _info_fonte(h)
                if inf:
                    chave = inf["base"] or "(Type3 sem nome)"
                    d = fontes.setdefault(chave, {**{k: v for k, v in inf.items() if k != "_h"},
                                                  "objetos": 0, "arquivo": ""})
                    d["objetos"] += 1
                    if inf["embutida"] and not inf["type3"] and not d["arquivo"]:
                        dados = _dados_da_fonte(inf["_h"])
                        if dados:
                            nome = re.sub(r"[^A-Za-z0-9_-]", "_", inf["base"].split("+")[-1])
                            ext = ".otf" if dados[:4] == b"OTTO" else ".ttf"
                            (saida / "fontes").mkdir(exist_ok=True)
                            alvo = saida / "fontes" / f"{nome}{ext}"
                            alvo.write_bytes(dados)
                            d["arquivo"] = str(alvo)
                            d["bytes"] = len(dados)
            elif t == T_IMAGEM and o.level == 0:
                img = o
                bm = img.get_bitmap(render=True)       # aplica o SMask → alfa real
                arr = _rgb(bm)
                (saida / "imagens").mkdir(exist_ok=True)
                alvo = saida / "imagens" / f"p{i + 1}_img{len(out['imagens']) + 1}.png"
                escrever_png(alvo, arr)
                transp = float((arr[..., 3] < 8).mean()) if arr.shape[2] == 4 else 0.0
                out["imagens"].append({"pagina": i + 1, "arquivo": str(alvo),
                                       "px": list(img.get_px_size()), "filtros": img.get_filters(),
                                       "transparente": round(transp, 3)})
        try:
            out["logos"] += logos_vetoriais(pdf, i, saida)
        except Exception:  # noqa: BLE001 — página esquisita: fica sem logo, o resto do kit sai
            pass
    out["cores_objetos"] = dict(sorted(((k, round(v)) for k, v in area_cor.items()),
                                       key=lambda kv: -kv[1]))
    out["fontes"] = fontes
    out["cores_texto"] = cores_do_texto(out["paginas"])
    out["fontes_citadas"] = fontes_citadas(out["paginas"])
    out["escaneado"] = sum(len(p.strip()) for p in out["paginas"]) < 40 * len(out["paginas"])
    return out


# ------------------------------------------------------------ logo vetorial
def _contem(a, b, tol=0.8) -> bool:
    return a[0] - tol <= b[0] and a[1] - tol <= b[1] and a[2] + tol >= b[2] and a[3] + tol >= b[3] and a != b


def logos_vetoriais(pdf, indice: int, saida: Path, lado_px: int = 1000) -> list[dict]:
    """Acha os logos em vetor e os renderiza sozinhos, com fundo transparente.

    1. Objetos gráficos do nível de cima (caminho, imagem, form), sem sombra
       (alfa < 50%) e sem o fundo da página (> 50% da área).
    2. Árvore de contenção pela caixa de cada objeto.
    3. DESCASCAR: um objeto que contém outro que contém outro é o cartão de
       apresentação ("Símbolo, positivo" sobre cinza) — sai, e os filhos viram
       candidatos. O símbolo em si (quadrado ⊃ fechadura) fica.
    4. O nome escrito do lado (ou embaixo) do símbolo, dentro do mesmo cartão,
       entra junto: é a assinatura.
    5. Renderiza a página só com esses objetos, fundo (0,0,0,0).
    """
    pg = pdf[indice]
    W, H = pg.get_size()
    objs = list(pg.get_objects(max_depth=0))
    graf, textos = [], []
    for k, o in enumerate(objs):
        t = raw.FPDFPageObj_GetType(o.raw)
        L, B, R, T = o.get_bounds()
        caixa = (L, B, R, T)
        area = (R - L) * (T - B)
        if t == T_TEXTO:
            txt = pg.get_textpage().get_text_bounded(L, B, R, T).strip()
            if txt:
                fs = ctypes.c_float()
                raw.FPDFTextObj_GetFontSize(o.raw, ctypes.byref(fs))
                textos.append({"k": k, "caixa": caixa, "txt": txt, "cor": _cor_do_obj(o.raw)[0]})
            continue
        if t not in (T_CAMINHO, T_IMAGEM, T_FORM) or area <= 1 or area > 0.5 * W * H:
            continue
        cor, alfa = _cor_do_obj(o.raw) if t == T_CAMINHO else (None, 255)
        if t == T_CAMINHO and alfa < 128:
            continue
        if L < 0 or B < 0 or R > W or T > H:
            continue
        graf.append({"k": k, "caixa": caixa, "area": area, "cor": cor, "tipo": t})
    # pai = o menor que contém
    for g in graf:
        pais = [p for p in graf if _contem(p["caixa"], g["caixa"])]
        g["pai"] = min(pais, key=lambda p: p["area"])["k"] if pais else None
    por_k = {g["k"]: g for g in graf}
    filhos = {g["k"]: [f for f in graf if f["pai"] == g["k"]] for g in graf}

    def prof(k):
        return 1 + max((prof(f["k"]) for f in filhos[k]), default=0)

    raizes = [g for g in graf if g["pai"] is None]
    cartoes = {}
    mudou = True
    while mudou:
        mudou = False
        for g in list(raizes):
            if prof(g["k"]) >= 3:                      # cartão ⊃ símbolo ⊃ fechadura
                raizes.remove(g)
                cartoes[g["k"]] = g
                raizes += filhos[g["k"]]
                mudou = True
    tp = pg.get_textpage()
    candidatos = []
    for g in raizes:
        membros = _descendentes(g["k"], filhos)
        if not (g["tipo"] == T_IMAGEM or len(membros) > 1):
            continue                                   # retângulo solto = amostra de cor
        if g["area"] > 0.04 * W * H:
            continue                                   # grande demais: é painel, não logo
        cx = list(g["caixa"])
        cartao = por_k.get(g["pai"]) if g["pai"] is not None else None
        h_simb = cx[3] - cx[1]
        ks = set(membros)

        def no_mesmo_cartao(t):
            if cartao:
                return _contem(cartao["caixa"], t["caixa"])
            return not any(_contem(c["caixa"], t["caixa"]) for c in cartoes.values())
        viz = [t for t in textos if no_mesmo_cartao(t)]
        # 1º ao lado (mesma faixa, à direita); só sem isso, a 1ª linha embaixo e centrada
        lado = [t for t in viz if 0 <= t["caixa"][0] - cx[2] <= 0.6 * h_simb
                and min(t["caixa"][3], cx[3]) - max(t["caixa"][1], cx[1]) >= 0.5 * min(t["caixa"][3] - t["caixa"][1], h_simb)]
        semente = min(lado, key=lambda t: t["caixa"][0]) if lado else None
        if semente is None:
            baixo = [t for t in viz if 0 <= cx[1] - t["caixa"][3] <= 0.6 * h_simb]
            if baixo:
                ref = max(baixo, key=lambda t: t["caixa"][3])["caixa"]
                faixa = [t for t in baixo if min(t["caixa"][3], ref[3]) - max(t["caixa"][1], ref[1])
                         >= 0.4 * min(t["caixa"][3] - t["caixa"][1], ref[3] - ref[1])]
                esq = min(t["caixa"][0] for t in faixa)
                dir_ = max(t["caixa"][2] for t in faixa)
                if abs((esq + dir_) / 2 - (cx[0] + cx[2]) / 2) < 0.15 * (dir_ - esq + h_simb):
                    semente = min(faixa, key=lambda t: t["caixa"][0])
        junto = []
        if semente:
            # encadeia a linha (o Chromium grava um objeto por glifo ou sílaba)
            s = semente["caixa"]
            h_txt = s[3] - s[1]
            linha = sorted([t for t in viz if t["cor"] == semente["cor"]
                            and min(t["caixa"][3], s[3]) - max(t["caixa"][1], s[1]) >= 0.4 * h_txt],
                           key=lambda t: t["caixa"][0])
            if semente["caixa"][1] < cx[1]:            # embaixo: a linha toda que começa perto
                linha = [t for t in linha if t["caixa"][0] >= cx[0] - 4 * h_simb]
            fimx = s[2]
            junto = [semente]
            for t in linha:
                if t is semente or t["caixa"][0] < s[0]:
                    continue
                if t["caixa"][0] - fimx > 0.45 * max(h_txt, 1):
                    break
                junto.append(t)
                fimx = max(fimx, t["caixa"][2])
            tx = [min(t["caixa"][0] for t in junto), min(t["caixa"][1] for t in junto),
                  max(t["caixa"][2] for t in junto), max(t["caixa"][3] for t in junto)]
            nome = tp.get_text_bounded(tx[0] + 0.5, tx[1] + 0.5, tx[2] - 0.5, tx[3] - 0.5)
            nome = re.sub(r"\s+", " ", nome).strip()
            if not nome or len(nome) > 30 or re.search(r"[.:,;!?]$", nome):
                junto = []                              # frase, não nome
            else:
                for t in junto:
                    ks.add(t["k"])
                cx = [min(cx[0], tx[0]), min(cx[1], tx[1]), max(cx[2], tx[2]), max(cx[3], tx[3])]
        cores = [por_k[m]["cor"] for m in membros if por_k[m]["cor"]]
        candidatos.append({"ks": ks, "caixa": cx, "cores": cores,
                           "nome_escrito": nome if junto else "",
                           "cor_nome": junto[0]["cor"] if junto else None})
    out = []
    for n, c in enumerate(candidatos):
        arr = _renderizar_so(pdf, indice, c["ks"], c["caixa"], lado_px)
        (saida / "logos").mkdir(exist_ok=True)
        alvo = saida / "logos" / f"p{indice + 1}_logo{n + 1}.png"
        escrever_png(alvo, arr)
        L, B, R, T = c["caixa"]
        c["cores"] = cores_dos_pixels(arr)
        out.append({"pagina": indice + 1, "arquivo": str(alvo), "caixa_pt": [round(x, 1) for x in c["caixa"]],
                    "px": [arr.shape[1], arr.shape[0]], "cores": c["cores"],
                    "nome_escrito": c["nome_escrito"], "cor_nome": c["cor_nome"],
                    "proporcao": round((R - L) / max(1e-6, T - B), 2),
                    "transparente": round(float((arr[..., 3] < 8).mean()), 3)})
    return out


def cores_dos_pixels(arr: np.ndarray, n: int = 3) -> list[str]:
    """As cores opacas dominantes (quantizadas em 32 níveis), da mais para a menos usada."""
    if arr.shape[2] < 4:
        px = arr.reshape(-1, 3)
    else:
        px = arr.reshape(-1, 4)
        px = px[px[:, 3] > 230][:, :3]
    if not len(px):
        return []
    q = (px // 8).astype(np.int32)
    chave = q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2]
    vals, cont = np.unique(chave, return_counts=True)
    ordem = np.argsort(-cont)
    out = []
    for i in ordem[:n]:
        if cont[i] < 0.03 * len(px):
            break
        sel = px[chave == vals[i]]
        out.append(_hex(*np.median(sel, axis=0)))
    return out


def _descendentes(k, filhos) -> list[int]:
    out = [k]
    for f in filhos.get(k, []):
        out += _descendentes(f["k"], filhos)
    return out


def _renderizar_so(pdf, indice, manter: set[int], caixa, lado_px: int) -> np.ndarray:
    """Tira da página (em memória) tudo que não é o logo e renderiza com alfa."""
    pg = pdf[indice]                              # página nova: as remoções não vazam
    W, H = pg.get_size()
    objs = list(pg.get_objects(max_depth=0))
    for k, o in enumerate(objs):
        if k not in manter:
            pg.remove_obj(o)
    L, B, R, T = caixa
    pad = 0.04 * max(R - L, T - B)
    L, B, R, T = max(0, L - pad), max(0, B - pad), min(W, R + pad), min(H, T + pad)
    escala = lado_px / max(R - L, T - B)
    bm = pg.render(scale=escala, crop=(L, B, W - R, H - T), fill_color=(0, 0, 0, 0))
    return _rgb(bm)


# ------------------------------------------------------- o kit sem Claude
PAPEIS = [("marca", r"marca|principal|prim[aá]ria|brand|t[ií]tulo"),
          ("marca_escura", r"hover|pressionad|escur|fechad"),
          ("confirma", r"confirm|sucesso|online|assinad|ok\b|positiv"),
          ("alerta", r"alerta|aten[cç][aã]o|erro|recusa|aviso"),
          ("destaque", r"destaque|acento|accent"),
          ("texto", r"\btexto\b|t[ií]tulos?\b"),
          ("corpo", r"corpo|par[aá]grafo"),
          ("apoio", r"apoio|secund|legenda"),
          ("linha", r"linha|borda|divis"),
          ("fundo", r"fundo|background|base")]


def _slug(nome: str) -> str:
    n = unicodedata.normalize("NFKD", str(nome or "").lower())
    n = "".join(c for c in n if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9_-]+", "-", n).strip("-")[:40]


def nome_da_marca(ext: dict, nome_arquivo: str) -> str:
    """O nome escrito ao lado do logo; senão, a linha maior da capa; senão, o arquivo."""
    for lg in ext["logos"]:
        if lg["nome_escrito"] and len(lg["nome_escrito"]) >= 3:
            return lg["nome_escrito"]
    capa = [ln.strip() for ln in ext["paginas"][0].splitlines() if ln.strip()] if ext["paginas"] else []
    for ln in capa[:4]:
        m = re.match(r"(.+?)\s+[—–-]\s+(manual|identidade|brand)", ln, re.I)
        if m:
            return m.group(1).strip()
    base = re.sub(r"[_\-]+", " ", Path(nome_arquivo).stem)
    return re.sub(r"\b(identidade|visual|manual|marca|brand|book|guide|v\d+)\b", "", base, flags=re.I).strip() or base


def variantes(nome: str) -> list[str]:
    """Formas erradas prováveis (o que o Whisper e as pessoas escrevem)."""
    n = nome.strip()
    out = {n.lower(), n.upper(), n.title()}
    # quebra camelCase/juntos em duas palavras quando há sufixo conhecido
    m = re.match(r"^([a-zà-ú]+?)(pay|bank|tech|hub|app|flow|lab|go|now|house|home)$", n.lower())
    if m:
        a, b = m.groups()
        out |= {f"{a} {b}", f"{a}-{b}", f"{a.title()} {b.title()}", f"{a.title()}{b.title()}"}
    if " " in n:
        out |= {n.replace(" ", ""), n.replace(" ", "-"), n.lower().replace(" ", "")}
    out.discard(n)
    return sorted(out)[:40]


# ------------------------------------------------------- regras, voz, frases
_VOZ = re.compile(r"^(voz|tom de voz|tom|como (a marca )?fala(mos)?|linguagem|jeito de falar|"
                  r"personalidade)$", re.I)
_SIM = re.compile(r"^(assim|fa[cç]a|sim|use|exemplos?|certo|do)$", re.I)
_NAO = re.compile(r"^(nunca|n[aã]o|evite|errado|n[aã]o fa[cç]a|don'?t)$", re.I)
_PROIBIDO = re.compile(r"^(n[aã]o fa[cç]a|n[aã]o fazer|evite|proibido|erros?( comuns)?|"
                       r"usos? indevidos?|o que n[aã]o fazer)$", re.I)
_REGRA = re.compile(r"\b(nunca|sempre|s[oó](?!\w)|somente|jamais|nada de|nada entra|nada encosta|"
                    r"sem \w|n[aã]o \w|evite|m[ií]nimo|proibid)", re.I)
_ESPECIME = re.compile(r"^\d{1,3}\s*/\s*[\d.,]+\s+(.+)$")
_FIM = (".", "!", "?", ":")


def _eh_titulo(ln: str) -> bool:
    """"Cor", "Tipografia", "Não faça", "Área de respiro" — o nome de uma seção."""
    if not ln or len(ln) > 28 or ln.endswith(_FIM + (",", ";")) or re.search(r"[#·|/\d]", ln):
        return False
    pal = ln.split()
    return 1 <= len(pal) <= 4 and ln[0].isupper() and not ln.isupper()


def _secoes(paginas: list[str]) -> list[tuple[str, list[str]]]:
    """O manual em [(título, [parágrafos])]: junta as linhas quebradas."""
    out: list[tuple[str, list[str]]] = [("", [])]
    for texto in paginas:
        atual: str = ""
        linhas = [x.strip() for x in texto.replace("\r", "\n").splitlines() if x.strip()]
        for i, ln in enumerate(linhas):
            fechado = not atual or atual.endswith(_FIM)
            # "Solução completa de gestão" / "inteligente." não é título: a
            # linha de baixo continua a frase
            segue = i + 1 < len(linhas) and linhas[i + 1][0].islower()
            if _eh_titulo(ln) and (fechado or ln[0].isupper()) and not segue:
                if atual:
                    out[-1][1].append(atual)
                atual = ""
                out.append((ln, []))
                continue
            # continua o parágrafo: a linha de cima não fechou a frase e esta
            # começa minúscula (ou a de cima é longa, de texto corrido); um
            # rótulo curto ("hospedepay", "Coral · v1") fica sozinho
            junta = (atual and not fechado and not _ESPECIME.match(ln) and "·" not in atual
                     and (ln[0].islower() or len(atual) >= 30))
            if junta:
                atual = f"{atual} {ln}"
            else:
                if atual:
                    out[-1][1].append(atual)
                atual = ln
        if atual:
            out[-1][1].append(atual)
    return out


def _curto(t: str, n: int = 300) -> str:
    return re.sub(r"\s+", " ", t).strip()[:n]


def textos_da_marca(paginas: list[str], nome: str) -> dict:
    """Descrição, regras, voz e frases, pelas seções do manual (sem IA).

    - descrição: o primeiro parágrafo de verdade da capa;
    - regras: nas seções de cor, fonte, forma, logo… as frases que mandam
      ("nunca", "sempre", "só", "sem", "nada de", "mínimo"), e tudo que está
      em "Não faça";
    - voz: o texto da seção "Voz"/"Tom de voz", com os exemplos de "Assim" e
      "Nunca" logo abaixo;
    - frases: os exemplos das amostras de tipografia ("44 / 1.15 Três
      travas antes da chave").
    """
    secoes = _secoes(paginas)
    descricao, regras, voz, frases = "", [], [], []
    sim: list[str] = []
    nao: list[str] = []
    modo = ""
    for titulo, pars in secoes:
        if _VOZ.match(titulo):
            modo = "voz"
        elif modo in ("voz", "sim", "nao") and _SIM.match(titulo):
            modo = "sim"
        elif modo in ("voz", "sim", "nao") and _NAO.match(titulo):
            modo = "nao"
        elif _PROIBIDO.match(titulo):
            modo = "proibido"
        else:
            modo = ""
        for par in pars:
            par = _curto(par, 400)
            m = _ESPECIME.match(par)
            if m:
                ex = _curto(m.group(1), 90)
                if ex and not any(len(w) >= 15 for w in ex.split()):
                    frases.append(ex)
                continue
            if "#" in par or len(par) < 8:
                continue
            if not descricao and len(par) >= 40 and "·" not in par and not titulo:
                descricao = _curto(par, 400)
                continue
            if modo == "voz":
                voz.append(_curto(par))
            elif modo == "sim":
                sim.append(_curto(par, 120))
            elif modo == "nao":
                nao.append(_curto(par, 120))
            elif modo == "proibido":
                if len(par) >= 20 and par.endswith(_FIM):
                    regras.append(_curto(par))
            elif 20 <= len(par) <= 300 and _REGRA.search(par) and par.endswith(_FIM):
                regras.append(_curto(par))
    if sim:
        voz.append("Assim: " + " · ".join(f"'{x}'" for x in sim[:6]))
    if nao:
        voz.append("Nunca: " + " · ".join(f"'{x}'" for x in nao[:6]))
    grafia = f"O nome é sempre {nome}, exatamente assim"
    if nome and nome != nome.upper():
        grafia += f" — nunca {nome.upper()}"
    regras.insert(0, grafia + ".")

    def unicos(xs, n):
        vistos: list[str] = []
        for x in xs:
            if x and x not in vistos:
                vistos.append(x)
        return vistos[:n]
    return {"descricao": descricao, "regras": unicos(regras, 20), "voz": unicos(voz, 10),
            "frases": unicos(frases, 20)}


def kit_deterministico(ext: dict, nome_arquivo: str) -> dict:
    nome = nome_da_marca(ext, nome_arquivo)
    cores: dict[str, str] = {}
    usadas = set()
    # 1) pelo rótulo escrito ao lado do código
    for papel, rx in PAPEIS:
        for c in ext["cores_texto"]:
            if c["hex"] not in usadas and re.search(rx, c["rotulo"], re.I):
                cores[papel] = c["hex"]
                usadas.add(c["hex"])
                break
    for c in ext["cores_texto"]:                    # rótulo sem papel conhecido: vira chave própria
        if c["hex"] not in usadas and c["rotulo"]:
            chave = _slug(c["rotulo"].split("·")[0]).replace("-", "_")[:24]
            if chave and chave not in cores:
                cores[chave] = c["hex"]
                usadas.add(c["hex"])
    # 2) sem rótulo: a cor não neutra de maior área é a marca
    if "marca" not in cores:
        vivas = [hx for hx in ext["cores_objetos"] if not neutra(hx)]
        escritas = [c["hex"] for c in ext["cores_texto"] if not neutra(c["hex"])]
        if escritas:
            cores["marca"] = escritas[0]
        elif vivas:
            cores["marca"] = vivas[0]
    if "texto" not in cores and ext["cores_texto_obj"]:
        escuras = sorted(ext["cores_texto_obj"], key=lambda hx: sum(int(hx[i:i + 2], 16) for i in (1, 3, 5)))
        cores["texto"] = escuras[0]
    cores.setdefault("fundo", "#FFFFFF")
    # fonte: a citada no texto ganha; a embutida confirma
    embutidas = [f for f in ext["fontes"].values() if not f["type3"]]
    familias = [f["familia"] for f in sorted(embutidas, key=lambda f: -f["objetos"]) if f["familia"]]
    citada = ext["fontes_citadas"][0] if ext["fontes_citadas"] else ""
    fonte_nome = citada or (familias[0] if familias else "")
    textos = textos_da_marca(ext["paginas"], nome)
    negrito = not re.search(r"(nada de|sem|nunca)\s+negrito", " ".join(ext["paginas"]), re.I)
    return {
        "nome": nome,
        "descricao": textos["descricao"],
        "grafia": {"exata": nome, "variantes": variantes(nome)},
        "cores": cores,
        "fonte": {"texto": fonte_nome, "titulo": "", "negrito": negrito, "arquivos": []},
        "forma": {},
        "logos": {},
        "regras": textos["regras"],
        "voz": textos["voz"],
        "frases": textos["frases"],
        "vocabulario": [nome],
    }


def papeis_dos_logos(logos: list[dict], cor_marca: str) -> dict[str, dict]:
    """Dá nome aos logos achados (simbolo, simbolo_negativo, assinatura…) e tira os repetidos."""
    def perto(a, b, tol=40):
        if not a or not b:
            return False
        return sum(abs(int(a[i:i + 2], 16) - int(b[i:i + 2], 16)) for i in (1, 3, 5)) < tol
    unicos: dict[tuple, dict] = {}
    for lg in logos:
        fora = lg["cores"][0] if lg["cores"] else None
        ok = lambda c: perto(c, cor_marca) or perto(c, "#FFFFFF") or perto(c, "#000000")
        if lg["cores"] and not all(ok(c) for c in lg["cores"]):
            continue          # cor fora da paleta do logo: exemplo de "não faça" ou ícone
        if lg["cor_nome"] and not ok(lg["cor_nome"]):
            continue
        forma = "sozinho" if not (lg["nome_escrito"] or lg["proporcao"] >= 2.5) else ("horizontal" if lg["proporcao"] >= 2 else "empilhada")
        chave = (tuple(lg["cores"][:3]), forma, lg["cor_nome"])
        if chave not in unicos or _area(lg) > _area(unicos[chave]):
            unicos[chave] = lg
    out: dict[str, dict] = {}
    for lg in unicos.values():
        fora = lg["cores"][0] if lg["cores"] else None
        larga = lg["proporcao"] >= 2.5
        if lg["nome_escrito"] or larga:
            if lg["cor_nome"]:                       # vetor: a cor do texto, direto do objeto
                branco = perto(lg["cor_nome"], "#FFFFFF")
            else:                                    # raster: a 2ª cor dos pixels
                branco = len(lg["cores"]) > 1 and perto(lg["cores"][1], "#FFFFFF") and not perto(fora, "#FFFFFF")
            forma = "" if lg["proporcao"] >= 2 else "_empilhada"
            papel = "assinatura" + forma + ("_branca" if branco else "")
            uso = ("símbolo + nome em branco — sobre a cor da marca ou imagem escura" if branco
                   else "símbolo + nome — sobre fundo claro")
        elif perto(fora, cor_marca):
            papel, uso = "simbolo", "o símbolo na cor da marca — o padrão"
        elif perto(fora, "#FFFFFF") and any(perto(c, cor_marca) for c in lg["cores"][1:]):
            papel, uso = "simbolo_negativo", "símbolo branco com o desenho na cor da marca — sobre a cor da marca ou fundo escuro"
        elif all(perto(c, "#FFFFFF") or perto(c, "#000000") for c in lg["cores"]):
            papel, uso = "simbolo_pb", "preto e branco"
        else:
            papel, uso = "logo", "logo achado no manual"
        base, n = papel, 2
        while papel in out:
            papel = f"{base}_{n}"
            n += 1
        out[papel] = {**lg, "uso": uso}
    return out


def _area(lg):
    c = lg["caixa_pt"]
    return (c[2] - c[0]) * (c[3] - c[1])


def fontes_conhecidas(familia: str) -> list[str]:
    """A fonte citada no manual, se outro kit já tem o arquivo (DM Sans → DMSans-*.ttf)."""
    from . import marca as MK

    alvo = re.sub(r"[^a-z0-9]", "", str(familia or "").lower())
    if not alvo:
        return []
    achados: dict[str, str] = {}
    for base in MK._pastas():
        if not base.is_dir():
            continue
        for f in sorted(base.glob("*/fontes/*")):
            if f.suffix.lower() in (".ttf", ".otf"):
                nome = re.sub(r"[^a-z0-9]", "", f.stem.lower())
                if nome.startswith(alvo) and f.name not in achados:
                    achados[f.name] = str(f)
    return list(achados.values())


def salvar_kit(kit: dict, logos: dict[str, dict], fontes: list[str], pasta_usuario: Path) -> Path:
    """Grava ``<pasta_usuario>/<slug>/marca.json`` + logos/ + fontes/ (formato do editor/marca.py).

    Kit que já existe com o mesmo nome e não veio de um PDF (o que veio com o
    programa, ou um ajustado à mão): o que ele tinha FICA, e o PDF só completa
    — cor sem papel, logo novo, fonte que faltava. Um kit que já tinha vindo de
    um PDF é refeito do zero (é a versão nova do manual).
    """
    import shutil

    from . import marca as MK

    slug = _slug(kit["nome"]) or "marca"
    pasta = Path(pasta_usuario) / slug
    antes = MK._pasta_do_kit(slug)
    base: dict = {}
    if antes and antes.resolve() != pasta.resolve():
        shutil.copytree(antes, pasta, dirs_exist_ok=True)   # o do programa: copia e completa
    if (pasta / "marca.json").is_file():
        try:
            base = json.loads((pasta / "marca.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            base = {}
    if base.get("origem") == "pdf":
        base = {}
    (pasta / "logos").mkdir(parents=True, exist_ok=True)
    novo = json.loads(json.dumps(kit))
    for papel, lg in logos.items():
        if papel in (base.get("logos") or {}):
            continue
        shutil.copyfile(lg["arquivo"], pasta / "logos" / f"{papel}.png")
        novo["logos"][papel] = {"arquivo": f"logos/{papel}.png", "uso": lg["uso"]}
    if fontes and not ((base.get("fonte") or {}).get("arquivos")):
        (pasta / "fontes").mkdir(exist_ok=True)
        for f in fontes:
            if Path(f).resolve() != (pasta / "fontes" / Path(f).name).resolve():
                shutil.copyfile(f, pasta / "fontes" / Path(f).name)
        novo["fonte"]["arquivos"] = [f"fontes/{Path(f).name}" for f in fontes]
        medio = [Path(f).stem for f in fontes if re.search(r"medium|semibold", Path(f).stem, re.I)]
        if medio and not novo["fonte"].get("titulo"):
            novo["fonte"]["titulo"] = f"{novo['fonte']['texto']} Medium"
    if base:
        final = dict(base)
        final["cores"] = {**novo["cores"], **(base.get("cores") or {})}
        final["logos"] = {**novo["logos"], **(base.get("logos") or {})}
        for campo in ("descricao", "regras", "voz", "frases", "vocabulario", "forma"):
            if not base.get(campo):
                final[campo] = novo[campo]
        if not (base.get("fonte") or {}).get("texto"):
            final["fonte"] = novo["fonte"]
    else:
        final = {**novo, "origem": "pdf"}
    (pasta / "marca.json").write_text(json.dumps(final, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    return pasta


def importar(caminho: str | Path, *, ativar: bool = True, pasta_usuario: Path | None = None) -> dict:
    """O PDF da identidade visual → kit salvo (e ligado). Tudo nesta máquina."""
    import shutil
    import tempfile

    from . import marca as MK

    arq = Path(str(caminho or ""))
    if not arq.is_file() or arq.suffix.lower() != ".pdf":
        raise ValueError("escolha o PDF da identidade visual da marca")
    if arq.stat().st_size > MAX_BYTES:
        raise ValueError("PDF grande demais (máx. 80 MB) — exporte o manual sem as fotos")
    with open(arq, "rb") as f:
        if b"%PDF" not in f.read(1024):
            raise ValueError("esse arquivo não é um PDF")
    tmp = Path(tempfile.mkdtemp(prefix="marca_pdf_"))
    try:
        try:
            ext = ler(arq, tmp, paginas=False)
        except pdfium.PdfiumError as exc:
            raise ValueError("não consegui abrir esse PDF (protegido por senha ou "
                             "corrompido)") from exc
        kit = kit_deterministico(ext, arq.name)
        if ext["escaneado"] and not ext["logos"] and not ext["cores_texto"]:
            raise ValueError("esse PDF é só imagem (escaneado): não dá para ler as cores "
                             "nem os logos. Exporte o manual direto do programa de design.")
        logos = papeis_dos_logos(ext["logos"], kit["cores"].get("marca", ""))
        fontes = [f["arquivo"] for f in ext["fontes"].values()
                  if f.get("arquivo") and not f["subconjunto"]
                  and re.sub(r"[^a-z0-9]", "", f["familia"].lower()).startswith(
                      re.sub(r"[^a-z0-9]", "", kit["fonte"]["texto"].lower()) or "#")]
        fontes = fontes or fontes_conhecidas(kit["fonte"]["texto"])
        pasta = salvar_kit(kit, logos, fontes, Path(pasta_usuario or MK.PASTA_DO_USUARIO))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        # os objetos do PDFium têm ciclo com o documento: sem coletar aqui, eles
        # só morrem na saída do programa, depois da biblioteca, e avisam vazamento
        import gc
        gc.collect()
    slug = pasta.name
    final = MK.carregar(slug)
    if not final:
        raise ValueError("o kit não ficou de pé — o PDF não tem o nome da marca")
    if ativar:
        MK.definir_ativa(slug)
    return {"slug": slug, "kit": MK.publico(final),
            "achado": {"cores": len(final["cores"]), "logos": len(final["logos"]),
                       "fonte": final["fonte"].get("texto", ""),
                       "fonte_arquivo": bool(final["fontes"]),
                       "regras": len(final["regras"]), "voz": len(final["voz"]),
                       "frases": len(final["frases"])}}
