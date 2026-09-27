"""Gráficos animados da pós-edição, escritos em ASS e queimados pelo libass.

É o "After Effects" do Sharkcut: título com barra de destaque, tela de tópico
em tela cheia, lista que entra item a item, número que conta, nome com barra
lateral, seta, círculo, barra de progresso. Tudo desenhado pelo MESMO libass
que já queima a legenda — então entra no MESMO passe do encode, sem arquivo
intermediário e sem segunda geração. A regra do encode único continua.

POR QUE ASS E NÃO PNG. Um cartão em PNG (render/cartao.py) é uma foto parada:
para animar teria que passar pelo ``geq`` por quadro, que é o filtro caro da
cadeia. O ASS anima de graça: ``\\t`` interpola escala, rotação 3D (``\\frx``
e ``\\fry`` com perspectiva de verdade), recorte e cor; ``\\move`` desliza;
``\\fade`` acende e apaga. É o libass que desenha, em C, quadro a quadro.

O TEMPO É O DA SAÍDA, como o de todo item de pós. O vídeo é encodado em
TRECHOS, cada um com o seu ``-ss``; um gráfico que atravessa a emenda de dois
trechos aparece nos dois, e a animação tem que estar na MESMA fase dos dois
lados. Para isso cada elemento é fatiado em eventos (entrada, meio, saída, e
cada troca de texto — o número contando, a letra sendo digitada) e cada fatia
leva os tempos das animações relativos ao começo da FASE, não ao dela própria.
Medido no libass: ``\\t`` e ``\\move`` com tempo NEGATIVO funcionam — uma
fatia que começa no meio da entrada mostra a animação no ponto exato, não a
reinicia.

POSIÇÃO E TAMANHO SÃO FRAÇÕES DO QUADRO. O corpo do texto segue o lado MENOR
do quadro: é o que dá o mesmo peso visual no 9:16, no 4:5 e no 16:9.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

TIPOS = ("titulo", "tela", "lista", "destaque", "numero", "texto", "nome",
         "seta", "circulo", "barra", "logo")
ENTRADAS = ("pop", "slide", "subir", "3d", "digitar", "fade")
SAIDAS = ("fade", "slide", "pop", "corte")
ESTILOS = ("escuro", "claro", "neon", "marca", "limpo", "vidro")
CAMADAS = ("frente", "atras")

# Onde cada tipo nasce quando ninguém disse: longe da legenda, que mora
# embaixo. ``nome`` é ancorado pela borda ESQUERDA e ``seta`` pela PONTA.
POSICAO_PADRAO = {
    "titulo": (0.5, 0.2), "tela": (0.5, 0.5), "lista": (0.5, 0.42),
    "destaque": (0.5, 0.3), "numero": (0.5, 0.3), "texto": (0.5, 0.22),
    "nome": (0.06, 0.72), "seta": (0.5, 0.5), "circulo": (0.5, 0.5),
    "barra": (0.5, 0.14), "logo": (0.9, 0.16),
}

# Paletas. ``fundo_a`` é a transparência do painel no ASS (00 = opaco).
PALETAS = {
    "escuro": {"fundo": "#11141A", "fundo_a": 0x1A, "texto": "#FFFFFF",
               "sub": "#C7CED8", "acento": "#FFC400"},
    "claro":  {"fundo": "#FFFFFF", "fundo_a": 0x0C, "texto": "#12151B",
               "sub": "#4A5260", "acento": "#0A84FF"},
    "neon":   {"fundo": "#07070F", "fundo_a": 0x38, "texto": "#FFFFFF",
               "sub": "#BDF6FF", "acento": "#00E5FF", "brilho": True},
    "marca":  {"fundo": "#E11D48", "fundo_a": 0x00, "texto": "#FFFFFF",
               "sub": "#FFE4EA", "acento": "#FFFFFF"},
    "limpo":  {"fundo": "", "fundo_a": 0xFF, "texto": "#FFFFFF",
               "sub": "#F1F1F1", "acento": "#FFC400", "contorno": True},
    # VIDRO: o painel quase transparente, com um fio claro na borda — a
    # imagem continua aparecendo por trás. O texto ganha uma sombra macia
    # para não sumir em fundo claro.
    "vidro":  {"fundo": "#FFFFFF", "fundo_a": 0xC4, "texto": "#FFFFFF",
               "sub": "#F4F4F4", "acento": "#FFFFFF", "vidro": True},
}

# Duração de cada entrada e saída, em segundos. Encolhem em gráfico curto.
DUR_ENTRADA = {"pop": 0.45, "slide": 0.5, "subir": 0.45, "3d": 0.65,
               "fade": 0.4, "crescer": 0.5, "cortina": 0.55, "nenhuma": 0.0}
DUR_SAIDA = {"fade": 0.3, "slide": 0.4, "pop": 0.3, "corte": 0.0,
             "cortina": 0.45}

MAX_TEXTO = 120
MAX_ITENS = 6
MAX_ITEM = 80


# ------------------------------------------------------------ utilitários
def _v(g, k: str, padrao=None):
    if isinstance(g, dict):
        return g.get(k, padrao)
    return getattr(g, k, padrao)


def _cor(hexa: str) -> str:
    c = str(hexa or "").strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in c):
        c = "FFFFFF"
    return f"&H{c[4:6]}{c[2:4]}{c[0:2]}&".upper()


def _alfa(a: int) -> str:
    return f"&H{max(0, min(255, int(a))):02X}&"


def _claro_demais(hexa: str) -> bool:
    """Cor clara pede texto escuro por cima (luminância relativa)."""
    c = str(hexa or "").strip().lstrip("#")
    if len(c) != 6:
        return True
    try:
        r, g, b = (int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return True
    return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.6


def _esc(texto) -> str:
    """O que o ASS trata como comando não pode vir do texto de ninguém."""
    return (str(texto or "").replace("\\", "/").replace("{", "(")
            .replace("}", ")").replace("\r", " ").strip())


def _fonte(nome) -> str:
    """Nome de fonte seguro: a vírgula separa os campos da linha de estilo."""
    return _esc(nome).replace(",", " ").strip() or "Arial"


class _Fonte(str):
    """A fonte do texto, que sabe o que fazer com "negrito".

    Sem kit de marca, negrito é ``\b1`` na mesma fonte. Com o kit (a
    hospedepay: "nada de negrito"), o peso de destaque é OUTRA fonte — a de
    título, DM Sans Medium — e ``\b`` fica sempre em 0.
    """

    titulo: str = ""
    negrito: bool = True

    @classmethod
    def de(cls, texto: str, titulo: str = "", negrito: bool = True) -> "_Fonte":
        f = cls(_fonte(texto))
        f.titulo = _fonte(titulo) if titulo else str(f)
        f.negrito = bool(negrito)
        return f


def _esc_sem_aparar(texto) -> str:
    return (str(texto or "").replace("\\", "/").replace("{", "(")
            .replace("}", ")").replace("\r", " ").replace("\n", " "))


_ESTREITOS = set("ijlI.,;:!|'`íìïÍ")
_MEIO = set("ftr()[]-\"/ ")
_LARGOS = set("mwMW@%")


def _largura(texto: str, corpo: float, negrito: bool = True) -> float:
    """Largura estimada do texto em pixels (Arial; medida contra o libass)."""
    tot = 0.0
    for ch in texto:
        if ch in _ESTREITOS:
            k = 0.28
        elif ch in _MEIO:
            k = 0.34
        elif ch in _LARGOS:
            k = 0.86
        elif ch.isupper():
            k = 0.70
        else:
            k = 0.57
        tot += k
    return tot * corpo * (1.06 if negrito else 1.0)


def _quebrar(texto: str, corpo: float, largura_max: float,
             negrito: bool = True) -> list[str]:
    linhas: list[str] = []
    for bloco in str(texto or "").split("\n"):
        atual = ""
        for p in bloco.split():
            cand = f"{atual} {p}".strip()
            if not atual or _largura(cand, corpo, negrito) <= largura_max:
                atual = cand
            else:
                linhas.append(atual)
                atual = p
        if atual:
            linhas.append(atual)
    return linhas


def _ret(w: float, h: float, r: float = 0.0) -> str:
    """Retângulo (de cantos redondos) como desenho ASS, a partir de 0,0."""
    w, h = max(1, int(round(w))), max(1, int(round(h)))
    r = int(round(max(0.0, min(r, w / 2, h / 2))))
    if r <= 0:
        return f"m 0 0 l {w} 0 {w} {h} 0 {h}"
    k = int(round(r * 0.4477))       # 1 - 0,5523: o ponto de controle da curva
    return (f"m {r} 0 l {w - r} 0 b {w - k} 0 {w} {k} {w} {r} "
            f"l {w} {h - r} b {w} {h - k} {w - k} {h} {w - r} {h} "
            f"l {r} {h} b {k} {h} 0 {h - k} 0 {h - r} "
            f"l 0 {r} b 0 {k} {k} 0 {r} 0")


def _elipse(rx: float, ry: float) -> str:
    rx, ry = max(1, int(round(rx))), max(1, int(round(ry)))
    kx, ky = int(round(rx * 0.5523)), int(round(ry * 0.5523))
    cx, cy = rx, ry
    return (f"m {cx} 0 b {cx + kx} 0 {2 * rx} {cy - ky} {2 * rx} {cy} "
            f"b {2 * rx} {cy + ky} {cx + kx} {2 * ry} {cx} {2 * ry} "
            f"b {cx - kx} {2 * ry} 0 {cy + ky} 0 {cy} "
            f"b 0 {cy - ky} {cx - kx} 0 {cx} 0")


def _seta(comp: float, haste: float, cabeca: float) -> str:
    """Seta deitada apontando para a direita; a cauda no x=0, centro em y=0."""
    L, t, c = int(round(comp)), max(2, int(round(haste / 2))), int(round(cabeca))
    b = int(round(c * 0.62))
    return (f"m 0 {-t} l {L - c} {-t} {L - c} {-b} {L} 0 {L - c} {b} "
            f"{L - c} {t} 0 {t}")


def _numero(v: float, casas: int) -> str:
    s = f"{v:,.{casas}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _casas(n: float) -> int:
    if abs(n - round(n)) < 1e-9:
        return 0
    if abs(n * 10 - round(n * 10)) < 1e-6:
        return 1
    return 2


# -------------------------------------------------------------- elementos
@dataclass
class _El:
    """Uma peça do gráfico: um painel, uma linha de texto, uma barra."""

    camada: int
    x: float
    y: float
    an: int
    tags: str
    variantes: list = field(default_factory=list)   # [(t desde o início, conteúdo)]
    entrada: str = "fade"
    saida: str = "fade"
    aparece: float = 0.0
    org: tuple | None = None
    lado: int = -1                  # slide: -1 vem da esquerda, +1 da direita
    ed: float | None = None
    sd: float | None = None
    W: int = 0
    H: int = 0


def _fade_in(ms: float) -> str:
    return f"\\fade(255,0,0,§0§,§{int(round(ms))}§,99999999,99999999)"


def _fade_out(ms: float) -> str:
    return f"\\fade(0,0,255,§0§,§0§,§0§,§{int(round(ms))}§)"


def _tags_da_fase(el: _El, fase: str, ms: float) -> str:
    x, y = el.x, el.y
    pos = f"\\pos({x:.1f},{y:.1f})"
    org = f"\\org({el.org[0]:.1f},{el.org[1]:.1f})" if el.org else ""
    if fase == "M":
        return pos + org
    m = int(round(ms))
    if fase == "E":
        k = el.entrada
        if k == "slide":
            dx = 0.24 * el.W * el.lado
            return (f"\\move({x + dx:.1f},{y:.1f},{x:.1f},{y:.1f},§0§,§{m}§)"
                    + org + _fade_in(ms * 0.6))
        if k == "subir":
            dy = 0.045 * el.H
            return (f"\\move({x:.1f},{y + dy:.1f},{x:.1f},{y:.1f},§0§,§{m}§)"
                    + org + _fade_in(ms * 0.7))
        if k == "pop":
            a = int(round(ms * 0.62))
            return (pos + org + "\\fscx0\\fscy0"
                    f"\\t(§0§,§{a}§,0.7,\\fscx112\\fscy112)"
                    f"\\t(§{a}§,§{m}§,\\fscx100\\fscy100)" + _fade_in(ms * 0.3))
        if k == "3d":
            return (pos + org + "\\frx82\\fry-24"
                    f"\\t(§0§,§{m}§,0.45,\\frx0\\fry0)" + _fade_in(ms * 0.5))
        if k == "crescer":
            return (pos + org + f"\\fscx0\\t(§0§,§{m}§,0.5,\\fscx100)"
                    + _fade_in(ms * 0.25))
        if k == "cortina":
            W, H = el.W, el.H
            if el.lado > 0:
                ini = f"\\clip({W},0,{W},{H})"
            else:
                ini = f"\\clip(0,0,0,{H})"
            return (pos + org + ini
                    + f"\\t(§0§,§{m}§,0.45,\\clip(0,0,{W},{H}))")
        return pos + org + _fade_in(ms)
    # saída
    k = el.saida
    if k == "slide":
        dx = 0.24 * el.W * (-el.lado)
        return (f"\\move({x:.1f},{y:.1f},{x + dx:.1f},{y:.1f},§0§,§{m}§)"
                + org + _fade_out(ms))
    if k == "pop":
        return (pos + org + f"\\t(§0§,§{m}§,1.8,\\fscx0\\fscy0)"
                + _fade_out(ms))
    if k == "cortina":
        W, H = el.W, el.H
        return (pos + org + f"\\clip(0,0,{W},{H})"
                + f"\\t(§0§,§{m}§,1.6,\\clip({W},0,{W},{H}))")
    return pos + org + _fade_out(ms)


_MARCA = re.compile(r"§(-?\d+)§")


def _cs(t: float) -> int:
    return int(round(t * 100))


def _ts(cs: int) -> str:
    cs = max(0, cs)
    h, resto = divmod(cs, 360000)
    m, resto = divmod(resto, 6000)
    s, c = divmod(resto, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{c:02d}"


def _eventos_do_elemento(el: _El, gs: float, ge: float, t0: float,
                         t1: float, camada_base: int) -> list[str]:
    a = gs + max(0.0, el.aparece)
    if a >= ge - 0.05:
        return []
    total = ge - a
    ed = el.ed if el.ed is not None else DUR_ENTRADA.get(el.entrada, 0.4)
    sd = el.sd if el.sd is not None else DUR_SAIDA.get(el.saida, 0.3)
    ed = min(ed, total * 0.45) if el.entrada != "nenhuma" else 0.0
    sd = min(sd, total * 0.35)
    fases = []
    if ed > 0.02:
        fases.append(("E", a, a + ed))
    fases.append(("M", a + ed, ge - sd))
    if sd > 0.02:
        fases.append(("S", ge - sd, ge))

    variantes = sorted(el.variantes, key=lambda v: v[0]) or [(0.0, "")]
    pontos = {a, ge}
    for _f, f0, f1 in fases:
        pontos.update((f0, f1))
    for tv, _c in variantes:
        if a < gs + tv < ge:
            pontos.add(gs + tv)
    lo, hi = max(a, t0), min(ge, t1)
    if hi - lo <= 0.005:
        return []
    marcos = [lo] + sorted(p for p in pontos if lo < p < hi) + [hi]

    linhas = []
    for s0, s1 in zip(marcos, marcos[1:]):
        c0, c1 = _cs(s0 - t0), _cs(s1 - t0)
        if c1 <= c0:
            continue
        fase = next((f for f in fases if f[1] - 1e-6 <= s0 < f[2] - 1e-6),
                    fases[-1])
        conteudo = variantes[0][1]
        for tv, c in variantes:
            if gs + tv <= s0 + 1e-6:
                conteudo = c
        desloc = int(round((fase[1] - t0) * 1000 - c0 * 10))
        tags = el.tags + _tags_da_fase(el, fase[0], (fase[2] - fase[1]) * 1000)
        tags = _MARCA.sub(lambda m: str(int(m.group(1)) + desloc), tags)
        linhas.append(
            f"Dialogue: {camada_base + el.camada},{_ts(c0)},{_ts(c1)},G,,0,0,0,,"
            f"{{\\an{el.an}{tags}}}{conteudo}")
    return linhas


# ---------------------------------------------------------------- estilo
def _paleta(g, kit: dict | None = None) -> dict:
    estilo = str(_v(g, "estilo", "escuro") or "escuro")
    p = dict(PALETAS.get(estilo, PALETAS["escuro"]))
    if kit:
        # AS CORES DA MARCA. "marca" é o cartão coral de letra branca (o do
        # anúncio e do story); "claro" é o cartão branco com letra preta e
        # destaque coral (o padrão da identidade); nos outros, o destaque
        # passa a ser o coral.
        c = kit.get("cores") or {}
        marca = c.get("marca") or p["acento"]
        if estilo == "marca":
            p.update(fundo=marca, texto="#FFFFFF", sub="#FFFFFF", acento="#FFFFFF")
        elif estilo == "claro":
            p.update(fundo=c.get("fundo") or "#FFFFFF", fundo_a=0x00,
                     texto=c.get("texto") or "#000000", sub=c.get("corpo") or "#484848",
                     acento=marca)
        elif estilo != "neon":
            p["acento"] = marca
        p["kit"] = True
    cor = str(_v(g, "cor", "") or "").strip()
    if cor and re.fullmatch(r"#?[0-9a-fA-F]{6}", cor):
        cor = "#" + cor.lstrip("#")
        if estilo == "marca":
            p["fundo"] = cor
            if _claro_demais(cor):
                p["texto"], p["sub"], p["acento"] = "#12151B", "#2B303A", "#12151B"
        else:
            p["acento"] = cor
    return p


def _texto(p: dict, corpo: float, cor: str, fonte: str, negrito: bool = True,
           u: float = 1000) -> str:
    nome, b = fonte, 1 if negrito else 0
    if isinstance(fonte, _Fonte) and not fonte.negrito:
        nome, b = (fonte.titulo if negrito else str(fonte)), 0
    t = (f"\\fn{nome}\\fs{corpo:.1f}\\b{b}"
         f"\\1c{_cor(cor)}\\1a&H00&\\p0\\blur0")
    if p.get("vidro"):
        # no vidro o fundo pode ser claro: uma sombra macia segura a letra
        t += (f"\\bord0\\shad{max(1.0, corpo * 0.045):.1f}\\4c&H000000&\\4a&H9A&"
              f"\\blur{max(0.6, corpo * 0.02):.1f}")
        return t
    if p.get("contorno"):
        t += (f"\\bord{max(1.0, corpo * 0.07):.1f}\\3c&H000000&\\3a&H10&"
              f"\\shad{max(1.0, corpo * 0.05):.1f}\\4c&H000000&\\4a&H70&")
    else:
        t += "\\bord0\\shad0"
    return t


def _painel(cor: str, alfa: int) -> str:
    return f"\\p1\\1c{_cor(cor)}\\1a{_alfa(alfa)}\\bord0\\shad0\\blur0"


def _painel_de(p: dict, u: float) -> str:
    """O painel do estilo: no vidro, com o fio claro na borda."""
    if p.get("vidro"):
        return (f"\\p1\\1c{_cor(p['fundo'])}\\1a{_alfa(p['fundo_a'])}"
                f"\\bord{max(1.0, u * 0.0022):.1f}\\3c&HFFFFFF&\\3a&H6A&\\shad0\\blur0.6")
    return _painel(p["fundo"], p["fundo_a"])


def _sombra(u: float, longa: bool = False) -> str:
    if longa:
        # "sombra longa e quase invisível: o cartão flutua, não recorta"
        return (f"\\p1\\1c&H000000&\\1a&HDC&\\bord0\\shad0"
                f"\\blur{u * 0.05:.1f}")
    return (f"\\p1\\1c&H000000&\\1a&H8C&\\bord0\\shad0"
            f"\\blur{u * 0.018:.1f}")


def _brilho(corpo: float, acento: str) -> str:
    return (f"\\bord{corpo * 0.09:.1f}\\3c{_cor(acento)}\\3a&H30&"
            f"\\blur{corpo * 0.22:.1f}\\1a&H40&")


def _digitado(linhas: list[str], dur: float, inicio: float) -> list[tuple]:
    """As variantes da máquina de escrever: cada uma mostra mais letras.

    A letra ainda não digitada existe, só que transparente — o texto não
    anda para o lado a cada letra nova, e o painel não precisa crescer.
    """
    tokens: list[str] = []
    for i, linha in enumerate(linhas):
        if i:
            tokens.append("\\N")
        tokens.extend(list(linha))
    visiveis = [i for i, t in enumerate(tokens) if t not in (" ", "\\N")]
    if not visiveis:
        return [(inicio, "\\N".join(linhas))]
    passos = min(len(visiveis), 60)
    out = []
    for k in range(passos + 1):
        n = int(round(len(visiveis) * k / passos))
        corte = visiveis[n - 1] + 1 if n > 0 else 0
        feito = "".join(tokens[:corte])
        resto = "".join(tokens[corte:])
        conteudo = feito + ("{\\alpha&HFF&}" + resto if resto else "")
        out.append((inicio + dur * k / passos, conteudo))
    return out


# ---------------------------------------------------------------- tipos
@dataclass
class _Ctx:
    g: object
    W: int
    H: int
    u: float
    s: float
    p: dict
    fonte: str
    dur: float
    entrada: str
    saida: str
    kit: dict | None = None

    def canto(self, bw: float, bh: float, base: float) -> float:
        """Canto largo quando há marca ("canto largo em tudo")."""
        if not self.kit:
            return base
        return min(bh * 0.34, bw * 0.34, self.u * 0.05)

    @property
    def dy_sombra(self) -> float:
        return self.u * (0.028 if self.kit else 0.01)

    @property
    def sombra(self) -> str:
        return _sombra(self.u, longa=bool(self.kit))


def _encaixar(cx: float, cy: float, bw: float, bh: float, W: int, H: int,
              margem: float) -> tuple[float, float]:
    """Puxa o centro para dentro: o painel inteiro cabe no quadro."""
    cx = min(max(cx, bw / 2 + margem), W - bw / 2 - margem) if bw < W - 2 * margem else W / 2
    cy = min(max(cy, bh / 2 + margem), H - bh / 2 - margem) if bh < H - 2 * margem else H / 2
    return cx, cy


def _el(ctx: _Ctx, camada: int, x: float, y: float, an: int, tags: str,
        conteudo, entrada: str | None = None, saida: str | None = None,
        aparece: float = 0.0, org=None, lado: int = -1, ed=None) -> _El:
    variantes = conteudo if isinstance(conteudo, list) else [(0.0, conteudo)]
    return _El(camada=camada, x=x, y=y, an=an, tags=tags, variantes=variantes,
               entrada=entrada or ctx.entrada, saida=saida or ctx.saida,
               aparece=aparece, org=org, lado=lado, ed=ed, W=ctx.W, H=ctx.H)


def _texto_com_entrada(ctx: _Ctx, camada, x, y, an, tags, linhas, aparece,
                       org, lado) -> list[_El]:
    """Texto que entra com a entrada do gráfico — ou digitado."""
    if ctx.entrada == "digitar":
        dur = min(max(0.35, 0.05 * sum(len(li) for li in linhas)), 1.8,
                  ctx.dur * 0.45)
        variantes = _digitado(linhas, dur, aparece)
        return [_el(ctx, camada, x, y, an, tags, variantes, entrada="nenhuma",
                    aparece=aparece, org=org, lado=lado)]
    return [_el(ctx, camada, x, y, an, tags, "\\N".join(linhas),
                aparece=aparece, org=org, lado=lado)]


def _caixa_de_texto(ctx: _Ctx, principal: str, secundario: str,
                    corpo1: float, corpo2: float, largura_max: float,
                    barra: bool) -> list[_El]:
    """Título e texto: painel + linhas + (opcional) a barra de destaque."""
    W, H, u, p = ctx.W, ctx.H, ctx.u, ctx.p
    for _ in range(8):
        pad = corpo1 * 0.42
        l1 = _quebrar(principal, corpo1, largura_max - 2 * pad)
        l2 = _quebrar(secundario, corpo2, largura_max - 2 * pad, False) if secundario else []
        tw = max([_largura(li, corpo1) for li in l1]
                 + [_largura(li, corpo2, False) for li in l2] + [corpo1])
        if tw + 2 * pad <= W * 0.94:
            break
        corpo1 *= 0.88
        corpo2 *= 0.88
    lh1, lh2 = corpo1 * 1.16, corpo2 * 1.24
    fio_h = max(3.0, corpo1 * 0.085) if barra else 0.0
    fio_gap = corpo1 * 0.28 if barra else 0.0
    gap2 = corpo1 * 0.3 if l2 else 0.0
    bw = tw + 2 * pad
    bh = 2 * pad + len(l1) * lh1 + fio_gap + fio_h + gap2 + len(l2) * lh2
    cx, cy = _encaixar(float(_v(ctx.g, "x", 0.5)) * W, float(_v(ctx.g, "y", 0.5)) * H,
                       bw, bh, W, H, u * 0.03)
    org = (cx, cy)
    lado = -1 if cx <= W / 2 else 1
    topo = cy - bh / 2
    els: list[_El] = []
    if p.get("fundo"):
        r = ctx.canto(bw, bh, u * 0.02)
        els.append(_el(ctx, 0, cx, cy + ctx.dy_sombra, 5, ctx.sombra, _ret(bw, bh, r),
                       org=org, lado=lado))
        els.append(_el(ctx, 1, cx, cy, 5, _painel_de(p, u),
                       _ret(bw, bh, r), org=org, lado=lado))
    y1 = topo + pad + len(l1) * lh1 / 2
    tags1 = _texto(p, corpo1, p["texto"], ctx.fonte)
    if p.get("brilho"):
        els.append(_el(ctx, 2, cx, y1, 5, tags1 + _brilho(corpo1, p["acento"]),
                       "\\N".join(l1), org=org, lado=lado))
    els += _texto_com_entrada(ctx, 3, cx, y1, 5, tags1, l1, 0.0, org, lado)
    y = topo + pad + len(l1) * lh1
    if barra:
        fw = max(corpo1 * 1.4, min(tw, max(_largura(li, corpo1) for li in l1)) * 0.32)
        yb = y + fio_gap + fio_h / 2
        els.append(_el(ctx, 3, cx, yb, 5, _painel(p["acento"], 0),
                       _ret(fw, fio_h, fio_h / 2), entrada="crescer",
                       aparece=0.18, org=org, lado=lado))
        y += fio_gap + fio_h
    if l2:
        y2 = y + gap2 + len(l2) * lh2 / 2
        tags2 = _texto(p, corpo2, p["sub"], ctx.fonte, negrito=False)
        els.append(_el(ctx, 3, cx, y2, 5, tags2, "\\N".join(l2),
                       entrada=("fade" if ctx.entrada == "digitar" else None),
                       aparece=0.12 if ctx.entrada != "digitar" else 0.5,
                       org=org, lado=lado))
    return els


def _titulo(ctx: _Ctx) -> list[_El]:
    return _caixa_de_texto(ctx, _esc(_v(ctx.g, "texto", ""))[:MAX_TEXTO],
                           _esc(_v(ctx.g, "subtexto", ""))[:MAX_TEXTO],
                           ctx.u * 0.085 * ctx.s, ctx.u * 0.042 * ctx.s,
                           ctx.W * 0.86, barra=True)


def _texto_simples(ctx: _Ctx) -> list[_El]:
    return _caixa_de_texto(ctx, _esc(_v(ctx.g, "texto", ""))[:MAX_TEXTO],
                           _esc(_v(ctx.g, "subtexto", ""))[:MAX_TEXTO],
                           ctx.u * 0.052 * ctx.s, ctx.u * 0.038 * ctx.s,
                           ctx.W * 0.8, barra=False)


def _itens(g) -> list[tuple[str, float | None]]:
    out = []
    for it in list(_v(g, "itens", []) or [])[:MAX_ITENS]:
        if isinstance(it, dict):
            texto, em = it.get("texto", ""), it.get("em")
        else:
            texto, em = it, None
        texto = _esc(texto)[:MAX_ITEM]
        if not texto:
            continue
        try:
            em = None if em is None else max(0.0, float(em))
        except (TypeError, ValueError):
            em = None
        out.append((texto, em))
    return out


def _horarios(itens: list, dur: float, inicio: float) -> list[float]:
    """Quando cada item entra: o que veio marcado, ou em cascata."""
    n = len(itens)
    if not n:
        return []
    passo = min(0.9, max(0.3, (dur * 0.55) / n))
    return [em if em is not None and em < dur - 0.3 else inicio + k * passo
            for k, (_t, em) in enumerate(itens)]


def _entrada_do_item(entrada: str) -> str:
    return {"slide": "slide", "3d": "3d", "fade": "fade", "digitar": "digitar",
            "pop": "subir", "subir": "subir"}.get(entrada, "subir")


def _lista(ctx: _Ctx) -> list[_El]:
    W, H, u, p, s = ctx.W, ctx.H, ctx.u, ctx.p, ctx.s
    titulo = _esc(_v(ctx.g, "texto", ""))[:MAX_TEXTO]
    itens = _itens(ctx.g)
    ct, ci = u * 0.058 * s, u * 0.047 * s
    for _ in range(8):
        pad = ci * 0.8
        bol = ci * 1.2
        gap = ci * 0.45
        maxw = W * 0.88 - 2 * pad
        lt = _quebrar(titulo, ct, maxw) if titulo else []
        li = [_quebrar(t, ci, maxw - bol - gap, False) for t, _em in itens]
        tw = max([_largura(x, ct) for x in lt]
                 + [bol + gap + max(_largura(x, ci, False) for x in ls)
                    for ls in li if ls] + [ci * 4])
        if tw + 2 * pad <= W * 0.94:
            break
        ct *= 0.88
        ci *= 0.88
    lht, lhi = ct * 1.18, ci * 1.22
    entre = ci * 0.55
    alt_t = len(lt) * lht + (ct * 0.45 if lt and li else 0)
    alturas = [max(bol, len(ls) * lhi) for ls in li]
    bw = tw + 2 * pad
    bh = 2 * pad + alt_t + sum(alturas) + entre * max(0, len(li) - 1)
    cx, cy = _encaixar(float(_v(ctx.g, "x", 0.5)) * W, float(_v(ctx.g, "y", 0.5)) * H,
                       bw, bh, W, H, u * 0.03)
    org, lado = (cx, cy), (-1 if cx <= W / 2 else 1)
    esq, topo = cx - bw / 2, cy - bh / 2
    els: list[_El] = []
    if p.get("fundo"):
        r = ctx.canto(bw, bh, u * 0.02)
        els.append(_el(ctx, 0, cx, cy + ctx.dy_sombra, 5, ctx.sombra, _ret(bw, bh, r),
                       org=org, lado=lado))
        els.append(_el(ctx, 1, cx, cy, 5, _painel_de(p, u),
                       _ret(bw, bh, r), org=org, lado=lado))
    y = topo + pad
    if lt:
        els += _texto_com_entrada(ctx, 3, esq + pad, y, 7,
                                  _texto(p, ct, p["texto"], ctx.fonte), lt, 0.0,
                                  org, lado)
        y += alt_t
    horas = _horarios(itens, ctx.dur, 0.35 if lt or p.get("fundo") else 0.0)
    ent = _entrada_do_item(ctx.entrada)
    cor_num = "#12151B" if _claro_demais(p["acento"]) else "#FFFFFF"
    for k, (ls, alt, quando) in enumerate(zip(li, alturas, horas)):
        yc = y + alt / 2 if len(ls) <= 1 else y + lhi / 2
        bx = esq + pad + bol / 2
        els.append(_el(ctx, 2, bx, yc, 5, _painel(p["acento"], 0),
                       _ret(bol, bol, bol * 0.28),
                       entrada="pop" if ent != "fade" else "fade", aparece=quando,
                       org=org, lado=lado))
        els.append(_el(ctx, 3, bx, yc, 5,
                       _texto({}, ci * 0.72, cor_num, ctx.fonte),
                       str(k + 1), entrada="pop" if ent != "fade" else "fade",
                       aparece=quando, org=org, lado=lado))
        tags = _texto(p, ci, p["texto"], ctx.fonte, negrito=False)
        if ent == "digitar":
            dur = min(max(0.3, 0.045 * sum(len(x) for x in ls)), 1.4)
            els.append(_el(ctx, 3, esq + pad + bol + gap, y + (alt - len(ls) * lhi) / 2, 7,
                           tags, _digitado(ls, dur, quando), entrada="nenhuma",
                           aparece=quando, org=org, lado=lado))
        else:
            els.append(_el(ctx, 3, esq + pad + bol + gap, y + (alt - len(ls) * lhi) / 2, 7,
                           tags, "\\N".join(ls), entrada=ent, aparece=quando + 0.05,
                           org=org, lado=lado))
        y += alt + entre
    return els


def _tela(ctx: _Ctx) -> list[_El]:
    """A tela de tópico: cobre o quadro inteiro, como um capítulo."""
    W, H, u, p, s = ctx.W, ctx.H, ctx.u, ctx.p, ctx.s
    fundo = p.get("fundo") or "#11141A"
    titulo = _esc(_v(ctx.g, "texto", ""))[:MAX_TEXTO]
    sub = _esc(_v(ctx.g, "subtexto", ""))[:MAX_TEXTO]
    rotulo = _esc(_v(ctx.g, "prefixo", ""))[:30]
    itens = _itens(ctx.g)
    claro = _claro_demais(fundo) and not p.get("vidro")
    cor_texto = (p["texto"] if p.get("kit") else "#12151B") if claro else "#FFFFFF"
    cor_sub = (p["sub"] if p.get("kit") else "#4A5260") if claro else "#C7CED8"
    ct, cs_, cr, ci = u * 0.1 * s, u * 0.045 * s, u * 0.04 * s, u * 0.047 * s
    maxw = W * 0.84
    for _ in range(8):
        lt = _quebrar(titulo, ct, maxw)
        ls = _quebrar(sub, cs_, maxw, False) if sub else []
        li = [_quebrar(t, ci, maxw - ci * 1.15, False) for t, _e in itens]
        larg = max([_largura(x, ct) for x in lt] + [0])
        alt = (len(lt) * ct * 1.14 + (cr * 1.9 if rotulo else 0)
               + (ct * 0.5 + len(ls) * cs_ * 1.25 if ls else 0)
               + (ct * 0.6 + sum(max(1, len(x)) * ci * 1.3 + ci * 0.35 for x in li)
                  if li else 0) + ct * 0.4)
        if larg <= W * 0.9 and alt <= H * 0.86:
            break
        ct, cs_, cr, ci = ct * 0.88, cs_ * 0.88, cr * 0.88, ci * 0.88
    cx = W / 2
    topo = H / 2 - alt / 2
    lado = -1
    cortina = "fade" if ctx.entrada == "fade" else "cortina"
    saida_fundo = "cortina" if ctx.saida == "slide" else ("corte" if ctx.saida == "corte" else "fade")
    luzes = not ctx.kit and not p.get("vidro")
    els: list[_El] = [
        _el(ctx, 0, 0, 0, 7, _painel(fundo, p["fundo_a"] if p.get("vidro") else 0),
            _ret(W, H), entrada=cortina, saida=saida_fundo, lado=lado),
    ]
    if luzes:
        els += [
            # profundidade: duas luzes da cor de destaque, bem desfocadas. O
            # tamanho é contido de propósito: o filtro ass mistura cada bitmap
            # pixel a pixel em todo quadro, e duas luzes de meia tela custavam
            # 1,5 s de encode a cada 4 s de tela (medido em 1080p).
            _el(ctx, 1, W * 0.88, H * 0.1, 5,
                f"\\p1\\1c{_cor(p['acento'])}\\1a&HB4&\\bord0\\shad0\\blur{u * 0.1:.1f}",
                _elipse(u * 0.3, u * 0.3), entrada="fade", saida=saida_fundo,
                aparece=0.1, ed=0.8),
            _el(ctx, 1, W * 0.08, H * 0.94, 5,
                f"\\p1\\1c{_cor(p['acento'])}\\1a&HCC&\\bord0\\shad0\\blur{u * 0.09:.1f}",
                _elipse(u * 0.24, u * 0.24), entrada="fade", saida=saida_fundo,
                aparece=0.1, ed=0.8),
        ]
    ent_txt = "subir" if ctx.entrada in ("pop", "slide", "subir") else ctx.entrada
    sub_ctx = _Ctx(**{**ctx.__dict__, "entrada": ent_txt})
    y = topo + ct * 0.2
    if rotulo:
        cor_rot = p["acento"]
        if p.get("kit") and _v(ctx.g, "estilo", "") == "marca":
            cor_rot = "#FFFFFF"
        els.append(_el(sub_ctx, 3, cx, y + cr / 2, 5,
                       _texto(p if p.get("vidro") else {}, cr, cor_rot, ctx.fonte)
                       + ("" if p.get("kit") else f"\\fsp{cr * 0.12:.1f}"),
                       rotulo, aparece=0.25))
        y += cr * 1.9
    yt = y + len(lt) * ct * 1.14 / 2
    tags_t = _texto(p if p.get("vidro") else {}, ct, cor_texto, ctx.fonte)
    if p.get("brilho"):
        els.append(_el(sub_ctx, 2, cx, yt, 5, tags_t + _brilho(ct, p["acento"]),
                       "\\N".join(lt), aparece=0.3))
    els += _texto_com_entrada(sub_ctx, 3, cx, yt, 5, tags_t, lt, 0.3, None, lado)
    y += len(lt) * ct * 1.14
    fio_h = max(3.0, ct * 0.07)
    els.append(_el(sub_ctx, 3, cx, y + ct * 0.28, 5, _painel(p["acento"], 0),
                   _ret(max(ct * 1.6, u * 0.12), fio_h, fio_h / 2),
                   entrada="crescer", aparece=0.45))
    y += ct * 0.5
    if ls:
        y += cs_ * 0.2
        els.append(_el(sub_ctx, 3, cx, y + len(ls) * cs_ * 1.25 / 2, 5,
                       _texto({}, cs_, cor_sub, ctx.fonte, negrito=False),
                       "\\N".join(ls), entrada="fade", aparece=0.55))
        y += len(ls) * cs_ * 1.25
    if li:
        y += ct * 0.35
        larg_i = max(ci * 1.15 + max(_largura(x, ci, False) for x in ls_)
                     for ls_ in li if ls_)
        esq = cx - larg_i / 2
        horas = _horarios(itens, ctx.dur, 0.8)
        for k, (linhas_i, quando) in enumerate(zip(li, horas)):
            alt_i = max(1, len(linhas_i)) * ci * 1.3
            els.append(_el(sub_ctx, 2, esq + ci * 0.45, y + ci * 0.62, 5,
                           _painel(p["acento"], 0), _elipse(ci * 0.2, ci * 0.2),
                           entrada="pop", aparece=quando))
            els.append(_el(sub_ctx, 3, esq + ci * 1.15, y, 7,
                           _texto({}, ci, cor_texto, ctx.fonte, negrito=False),
                           "\\N".join(linhas_i), entrada=_entrada_do_item(ent_txt)
                           if ent_txt != "digitar" else "fade", aparece=quando + 0.05))
            y += alt_i + ci * 0.35
    return els


def _destaque(ctx: _Ctx) -> list[_El]:
    """A palavra-chave em adesivo: caixa da cor de destaque, levemente torta."""
    W, H, u, p, s = ctx.W, ctx.H, ctx.u, ctx.p, ctx.s
    texto = _esc(_v(ctx.g, "texto", ""))[:MAX_TEXTO]
    corpo = u * 0.1 * s
    for _ in range(8):
        linhas = _quebrar(texto, corpo, W * 0.8)
        tw = max([_largura(x, corpo) for x in linhas] + [corpo])
        if tw <= W * 0.86:
            break
        corpo *= 0.88
    pad = corpo * 0.38
    bw, bh = tw + 2 * pad, len(linhas) * corpo * 1.12 + pad * 1.3
    cx, cy = _encaixar(float(_v(ctx.g, "x", 0.5)) * W, float(_v(ctx.g, "y", 0.5)) * H,
                       bw, bh, W, H, u * 0.04)
    org, lado = (cx, cy), (-1 if cx <= W / 2 else 1)
    acento = p["fundo"] if _v(ctx.g, "estilo", "") == "marca" else p["acento"]
    cor_txt = "#12151B" if _claro_demais(acento) else "#FFFFFF"
    torto = "" if ctx.kit else "\\frz3"
    r = ctx.canto(bw, bh, u * 0.012)
    els = [
        _el(ctx, 0, cx + (0 if ctx.kit else u * 0.008), cy + (ctx.dy_sombra if ctx.kit else u * 0.012),
            5, ctx.sombra + torto, _ret(bw, bh, r), org=org, lado=lado),
        _el(ctx, 1, cx, cy, 5, _painel(acento, 0) + torto, _ret(bw, bh, r),
            org=org, lado=lado),
    ]
    els += _texto_com_entrada(ctx, 3, cx, cy, 5,
                              _texto({}, corpo, cor_txt, ctx.fonte) + torto,
                              linhas, 0.0, org, lado)
    return els


def _numero_el(ctx: _Ctx) -> list[_El]:
    """O número que conta de zero até o valor, com a legenda embaixo."""
    W, H, u, p, s = ctx.W, ctx.H, ctx.u, ctx.p, ctx.s
    try:
        alvo = float(_v(ctx.g, "numero", 0.0) or 0.0)
    except (TypeError, ValueError):
        alvo = 0.0
    casas = _casas(alvo)
    # o espaço de "R$ " e de " mil" é do valor: não pode ser aparado
    pre = _esc_sem_aparar(_v(ctx.g, "prefixo", ""))[:12]
    suf = _esc_sem_aparar(_v(ctx.g, "sufixo", ""))[:12]
    rotulo = _esc(_v(ctx.g, "texto", ""))[:MAX_TEXTO]
    final = f"{pre}{_numero(alvo, casas)}{suf}"
    cn, cr = u * 0.17 * s, u * 0.045 * s
    for _ in range(8):
        lr = _quebrar(rotulo, cr, W * 0.8, False) if rotulo else []
        tw = max([_largura(final, cn)] + [_largura(x, cr, False) for x in lr])
        if tw <= W * 0.86:
            break
        cn, cr = cn * 0.88, cr * 0.88
    pad = cr * 0.9
    bw = tw + 2 * pad
    bh = cn * 1.1 + (len(lr) * cr * 1.25 + cr * 0.2 if lr else 0) + pad * 1.4
    cx, cy = _encaixar(float(_v(ctx.g, "x", 0.5)) * W, float(_v(ctx.g, "y", 0.5)) * H,
                       bw, bh, W, H, u * 0.03)
    org, lado = (cx, cy), (-1 if cx <= W / 2 else 1)
    topo = cy - bh / 2
    els: list[_El] = []
    if p.get("fundo"):
        r = ctx.canto(bw, bh, u * 0.025)
        els.append(_el(ctx, 0, cx, cy + ctx.dy_sombra, 5, ctx.sombra, _ret(bw, bh, r),
                       org=org, lado=lado))
        els.append(_el(ctx, 1, cx, cy, 5, _painel_de(p, u),
                       _ret(bw, bh, r), org=org, lado=lado))
    cor_n = p["acento"] if _v(ctx.g, "estilo", "") != "marca" else p["texto"]
    # a contagem: rápida no começo, freando no fim (curva cúbica)
    conta = min(1.4, max(0.5, ctx.dur * 0.45))
    passos = max(2, int(conta * 16))
    variantes = []
    for k in range(passos + 1):
        f = 1 - (1 - k / passos) ** 3
        variantes.append((0.08 + conta * k / passos,
                          f"{pre}{_numero(alvo * f, casas)}{suf}"))
    variantes.insert(0, (0.0, f"{pre}{_numero(0.0, casas)}{suf}"))
    yn = topo + pad * 0.7 + cn * 0.55
    tags_n = _texto(p, cn, cor_n, ctx.fonte)
    ent = "pop" if ctx.entrada == "digitar" else ctx.entrada
    if p.get("brilho"):
        els.append(_el(ctx, 2, cx, yn, 5, tags_n + _brilho(cn, p["acento"]), variantes,
                       entrada=ent, org=org, lado=lado))
    els.append(_el(ctx, 3, cx, yn, 5, tags_n, variantes, entrada=ent, org=org, lado=lado))
    if lr:
        yr = topo + pad * 0.7 + cn * 1.1 + cr * 0.2 + len(lr) * cr * 1.25 / 2
        els.append(_el(ctx, 3, cx, yr, 5, _texto(p, cr, p["sub"], ctx.fonte, False),
                       "\\N".join(lr), entrada="fade" if ent == "pop" else ent,
                       aparece=0.15, org=org, lado=lado))
    return els


def _nome(ctx: _Ctx) -> list[_El]:
    """Lower third: barra de destaque na lateral, nome e função ao lado."""
    W, H, u, p, s = ctx.W, ctx.H, ctx.u, ctx.p, ctx.s
    nome = _esc(_v(ctx.g, "texto", ""))[:60]
    funcao = _esc(_v(ctx.g, "subtexto", ""))[:80]
    c1, c2 = u * 0.056 * s, u * 0.036 * s
    for _ in range(8):
        tw = max(_largura(nome, c1), _largura(funcao, c2, False) if funcao else 0)
        if tw + c1 * 1.4 <= W * 0.9:
            break
        c1, c2 = c1 * 0.88, c2 * 0.88
    pad = c1 * 0.42
    barra = max(4.0, c1 * 0.14)
    bw = tw + 2 * pad
    bh = pad * 1.6 + c1 * 1.12 + (c2 * 1.3 if funcao else 0)
    x0 = min(max(float(_v(ctx.g, "x", 0.06)) * W, u * 0.03), W - bw - barra - u * 0.03)
    yc = min(max(float(_v(ctx.g, "y", 0.72)) * H, bh / 2 + u * 0.03), H - bh / 2 - u * 0.03)
    org = (x0 + bw / 2, yc)
    ent_painel = "crescer" if ctx.entrada in ("slide", "pop", "subir") else ctx.entrada
    if ent_painel == "digitar":
        ent_painel = "fade"
    ent_txt = "slide" if ctx.entrada in ("slide", "pop") else ctx.entrada
    sub_ctx = _Ctx(**{**ctx.__dict__, "entrada": ent_txt})
    els: list[_El] = [
        _el(ctx, 2, x0, yc, 4, _painel(p["acento"], 0),
            _ret(barra, bh, barra / 2 if ctx.kit else 0.0),
            entrada="pop" if ctx.entrada != "fade" else "fade", org=org),
    ]
    if p.get("fundo"):
        r = ctx.canto(bw, bh, 0.0)
        els.append(_el(ctx, 0, x0 + barra, yc + ctx.dy_sombra, 4, ctx.sombra, _ret(bw, bh, r),
                       entrada=ent_painel, aparece=0.08, org=org))
        els.append(_el(ctx, 1, x0 + barra, yc, 4, _painel_de(p, u),
                       _ret(bw, bh, r), entrada=ent_painel, aparece=0.08, org=org))
    topo = yc - bh / 2 + pad * 0.8
    # o texto desliza DE DENTRO da barra: fora do painel ele não existe
    janela = (f"\\clip({x0 + barra:.0f},{yc - bh / 2 - u * 0.02:.0f},"
              f"{W:.0f},{yc + bh / 2 + u * 0.02:.0f})")
    els += _texto_com_entrada(sub_ctx, 3, x0 + barra + pad, topo, 7,
                              _texto(p, c1, p["texto"], ctx.fonte) + janela,
                              [nome], 0.22, org, -1)
    if funcao:
        els.append(_el(sub_ctx, 3, x0 + barra + pad, topo + c1 * 1.12, 7,
                       _texto(p, c2, p["acento"] if p.get("fundo") else p["sub"],
                              ctx.fonte, False) + janela,
                       funcao, entrada="fade" if ent_txt == "digitar" else None,
                       aparece=0.34, lado=-1, org=org))
    return els


def _seta_el(ctx: _Ctx) -> list[_El]:
    """Seta que cresce da cauda até a ponta, apontando para (x, y)."""
    W, H, u, p, s = ctx.W, ctx.H, ctx.u, ctx.p, ctx.s
    ang = float(_v(ctx.g, "angulo", 0.0) or 0.0)
    comp = u * 0.2 * s
    haste, cab = u * 0.02 * s, u * 0.065 * s
    px, py = float(_v(ctx.g, "x", 0.5)) * W, float(_v(ctx.g, "y", 0.5)) * H
    dx, dy = math.cos(math.radians(ang)), math.sin(math.radians(ang))
    tx, ty = px - dx * comp, py - dy * comp
    rot = f"\\frz{-ang:.1f}"
    cor = p["acento"]
    contorno = f"\\bord{u * 0.006 * s:.1f}\\3c&H000000&\\3a&H60&"
    corpo = _seta(comp, haste, cab)
    els = [
        _el(ctx, 1, tx, ty, 4,
            f"\\p1\\1c{_cor(cor)}\\1a&H00&{contorno}\\shad0\\blur0.6" + rot,
            corpo, entrada="crescer" if ctx.entrada != "fade" else "fade",
            org=(tx, ty), ed=0.4),
    ]
    rotulo = _esc(_v(ctx.g, "texto", ""))[:60]
    if rotulo:
        c = u * 0.045 * s
        lw = _largura(rotulo, c)
        lx = tx - dx * (u * 0.02 + lw / 2 * abs(dx) + c * 0.7 * abs(dy))
        ly = ty - dy * (u * 0.02 + c * 0.7 * abs(dy) + lw / 2 * abs(dx) * 0.2)
        lx, ly = _encaixar(lx, ly, lw + c, c * 1.4, W, H, u * 0.02)
        els.append(_el(ctx, 2, lx, ly, 5,
                       _texto(PALETAS["limpo"], c, "#FFFFFF", ctx.fonte), rotulo,
                       entrada="fade", aparece=0.25))
    return els


def _circulo(ctx: _Ctx) -> list[_El]:
    """Anel de marcação em volta de um ponto (o "olha aqui" do tutorial)."""
    W, H, u, p, s = ctx.W, ctx.H, ctx.u, ctx.p, ctx.s
    r = u * 0.11 * s
    cx, cy = float(_v(ctx.g, "x", 0.5)) * W, float(_v(ctx.g, "y", 0.5)) * H
    esp = max(3.0, u * 0.011 * s)
    tags = (f"\\p1\\1a&HFF&\\bord{esp:.1f}\\3c{_cor(p['acento'])}\\3a&H00&"
            f"\\shad0\\blur0.8\\frz-8")
    ent = "pop" if ctx.entrada not in ("fade", "3d") else ctx.entrada
    els = [_el(ctx, 1, cx, cy, 5, tags, _elipse(r * 1.18, r), entrada=ent,
               org=(cx, cy))]
    rotulo = _esc(_v(ctx.g, "texto", ""))[:60]
    if rotulo:
        c = u * 0.042 * s
        ly = cy + r + esp + c * 0.9 if cy < H * 0.7 else cy - r - esp - c * 0.9
        lx, ly = _encaixar(cx, ly, _largura(rotulo, c) + c, c * 1.4, W, H, u * 0.02)
        els.append(_el(ctx, 2, lx, ly, 5, _texto(PALETAS["limpo"], c, "#FFFFFF", ctx.fonte),
                       rotulo, entrada="fade", aparece=0.25))
    return els


def _barra(ctx: _Ctx) -> list[_El]:
    """Barra de progresso ("passo 2 de 5", "70% do caminho")."""
    W, H, u, p, s = ctx.W, ctx.H, ctx.u, ctx.p, ctx.s
    try:
        pct = max(0.0, min(100.0, float(_v(ctx.g, "numero", 0.0) or 0.0)))
    except (TypeError, ValueError):
        pct = 0.0
    bw, bh = min(W * 0.8, u * 0.75 * s), max(6.0, u * 0.026 * s)
    cx, cy = _encaixar(float(_v(ctx.g, "x", 0.5)) * W, float(_v(ctx.g, "y", 0.14)) * H,
                       bw, bh * 4, W, H, u * 0.03)
    esq = cx - bw / 2
    trilho = p["fundo"] or "#000000"
    els = [
        _el(ctx, 1, cx, cy, 5, _painel(trilho, 0x60 if p.get("fundo") else 0x80),
            _ret(bw, bh, bh / 2), entrada="fade" if ctx.entrada != "slide" else "slide"),
    ]
    if pct > 0.5:
        fw = max(bh, bw * pct / 100)
        els.append(_el(ctx, 2, esq, cy, 4, _painel(p["acento"], 0), _ret(fw, bh, bh / 2),
                       entrada="crescer", aparece=0.15, ed=min(1.2, ctx.dur * 0.4)))
    rotulo = _esc(_v(ctx.g, "texto", ""))[:60]
    c = u * 0.036 * s
    base_txt = PALETAS["limpo"]
    if rotulo:
        els.append(_el(ctx, 3, esq, cy - bh / 2 - c * 0.25, 1,
                       _texto(base_txt, c, "#FFFFFF", ctx.fonte), rotulo,
                       entrada="fade", aparece=0.1))
    direita = _esc(_v(ctx.g, "subtexto", ""))[:20] or f"{int(round(pct))}%"
    els.append(_el(ctx, 3, esq + bw, cy - bh / 2 - c * 0.25, 3,
                   _texto(base_txt, c, p["acento"], ctx.fonte), direita,
                   entrada="fade", aparece=0.3))
    return els


_MONTADORES = {
    "titulo": _titulo, "tela": _tela, "lista": _lista, "destaque": _destaque,
    "numero": _numero_el, "texto": _texto_simples, "nome": _nome,
    "seta": _seta_el, "circulo": _circulo, "barra": _barra,
}


def _com_grafia(g, kit: dict | None):
    """O gráfico com o nome da marca na grafia exata (HOSPEDEPAY → hospedepay)."""
    if not kit:
        return g
    from ..marca import corrigir_grafia

    d = dict(g) if isinstance(g, dict) else dict(getattr(g, "__dict__", {}))
    for k in ("texto", "subtexto", "prefixo", "sufixo"):
        if d.get(k):
            d[k] = corrigir_grafia(str(d[k]), kit)
    itens = []
    for it in d.get("itens") or []:
        if isinstance(it, dict):
            it = {**it, "texto": corrigir_grafia(str(it.get("texto") or ""), kit)}
        else:
            it = corrigir_grafia(str(it), kit)
        itens.append(it)
    d["itens"] = itens
    return d


def fonte_do_kit(kit: dict | None, padrao: str) -> "_Fonte":
    if not kit or not kit.get("fontes"):
        return _Fonte.de(padrao)
    f = kit.get("fonte") or {}
    return _Fonte.de(f.get("texto") or padrao, f.get("titulo") or "",
                     bool(f.get("negrito", True)))


def elementos(g, W: int, H: int, fonte: str = "Arial",
              kit: dict | None = None) -> list[_El]:
    """As peças de um gráfico, no quadro W x H."""
    tipo = str(_v(g, "tipo", "texto") or "texto")
    if tipo == "logo":
        return []            # o logo é imagem: vem por render/logos.py
    g = _com_grafia(g, kit)
    montar = _MONTADORES.get(tipo, _texto_simples)
    try:
        s = max(0.4, min(2.5, float(_v(g, "tamanho", 1.0) or 1.0)))
    except (TypeError, ValueError):
        s = 1.0
    entrada = str(_v(g, "entrada", "pop") or "pop")
    saida = str(_v(g, "saida", "fade") or "fade")
    ctx = _Ctx(g=g, W=int(W), H=int(H), u=float(min(W, H)), s=s, p=_paleta(g, kit),
               fonte=fonte if isinstance(fonte, _Fonte) else fonte_do_kit(kit, fonte),
               dur=max(0.1, float(_v(g, "out_end", 0)) - float(_v(g, "out_start", 0))),
               entrada=entrada if entrada in ENTRADAS else "pop",
               saida=saida if saida in SAIDAS else "fade", kit=kit)
    return montar(ctx)


# ------------------------------------------------------------------ saída
def no_trecho(graficos, t0: float, dur: float,
              camadas: tuple = ("frente",)) -> list:
    """Os gráficos ligados que tocam a janela [t0, t0 + dur)."""
    out = []
    for g in graficos or []:
        if not _v(g, "enabled", True):
            continue
        a, b = float(_v(g, "out_start", 0.0)), float(_v(g, "out_end", 0.0))
        if b - a < 0.1 or b <= t0 or a >= t0 + dur:
            continue
        if str(_v(g, "camada", "frente") or "frente") not in camadas:
            continue
        out.append(g)
    return out


def eventos(graficos, W: int, H: int, t0: float, dur: float,
            camadas: tuple = ("frente",), fonte: str = "Arial",
            kit: dict | None = None) -> list[str]:
    linhas: list[str] = []
    todos = sorted((g for g in no_trecho(graficos, t0, dur, camadas)
                    if _v(g, "tipo", "") != "logo"),
                   key=lambda g: float(_v(g, "out_start", 0.0)))
    for i, g in enumerate(todos):
        gs, ge = float(_v(g, "out_start", 0.0)), float(_v(g, "out_end", 0.0))
        base = 1 + 10 * i          # o gráfico que começa depois fica por cima
        for el in elementos(g, W, H, fonte, kit):
            linhas += _eventos_do_elemento(el, gs, ge, t0, t0 + dur + 0.05, base)
    return linhas


def ass(graficos, W: int, H: int, t0: float, dur: float,
        camadas: tuple = ("frente",), fonte: str = "Arial",
        kit: dict | None = None) -> str:
    """O ASS de um trecho: tempos relativos ao começo do trecho. "" = nada."""
    linhas = eventos(graficos, W, H, t0, dur, camadas, fonte, kit)
    if not linhas:
        return ""
    cabeca = [
        "[Script Info]", "ScriptType: v4.00+",
        f"PlayResX: {int(W)}", f"PlayResY: {int(H)}",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: G,{_fonte(fonte_do_kit(kit, fonte))},40,&H00FFFFFF,&H00FFFFFF,&H00000000,"
        "&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1", "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text",
    ]
    return "\n".join(cabeca + linhas) + "\n"


def escrever(caminho: Path, graficos, W: int, H: int, t0: float, dur: float,
             camadas: tuple = ("frente",), fonte: str = "Arial",
             kit: dict | None = None) -> bool:
    texto = ass(graficos, W, H, t0, dur, camadas, fonte, kit)
    if not texto:
        return False
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(texto, encoding="utf-8")
    return True


# ------------------------------------------------------------- validação
def _num(v, padrao: float, lo: float, hi: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return padrao
    if math.isnan(f) or math.isinf(f):
        return padrao
    return max(lo, min(hi, f))


def normalizar(d: dict, duracao: float | None = None) -> dict:
    """O pedido (da tela, do MCP) vira campos válidos de um Grafico.

    Nada aqui levanta: valor fora da lista cai no padrão, número fora da
    faixa é puxado para dentro. O que for texto passa pelo escape do ASS na
    hora de desenhar — aqui só o tamanho é limitado.
    """
    d = dict(d or {})
    tipo = str(d.get("tipo") or "texto")
    tipo = tipo if tipo in TIPOS else "texto"
    x0, y0 = POSICAO_PADRAO.get(tipo, (0.5, 0.5))
    fim_max = duracao if duracao and duracao > 0 else 1e9
    a = _num(d.get("out_start"), 0.0, 0.0, fim_max)
    b = min(fim_max, _num(d.get("out_end"), a + 3.0, 0.0, fim_max))
    if b - a < 0.3:
        b = min(fim_max, a + 3.0)
        if b - a < 0.3:
            # pedido no fim do vídeo (ou além): o gráfico recua para caber
            a = max(0.0, b - 3.0)
    itens = []
    for it in list(d.get("itens") or [])[:MAX_ITENS]:
        if isinstance(it, dict):
            texto = str(it.get("texto") or "")[:MAX_ITEM]
            if texto.strip():
                item = {"texto": texto}
                if it.get("em") is not None:
                    item["em"] = _num(it.get("em"), 0.0, 0.0, 600.0)
                itens.append(item)
        elif str(it).strip():
            itens.append(str(it)[:MAX_ITEM])
    cor = str(d.get("cor") or "").strip()
    if cor and not re.fullmatch(r"#?[0-9a-fA-F]{6}", cor):
        cor = ""
    if cor:
        cor = "#" + cor.lstrip("#").upper()
    out = {
        "tipo": tipo, "out_start": round(a, 3), "out_end": round(b, 3),
        "texto": str(d.get("texto") or "")[:MAX_TEXTO],
        "subtexto": str(d.get("subtexto") or "")[:MAX_TEXTO],
        "itens": itens,
        "x": _num(d.get("x"), x0, 0.0, 1.0),
        "y": _num(d.get("y"), y0, 0.0, 1.0),
        "tamanho": _num(d.get("tamanho"), 1.0, 0.4, 2.5),
        "estilo": d.get("estilo") if d.get("estilo") in ESTILOS else "escuro",
        "cor": cor,
        "entrada": d.get("entrada") if d.get("entrada") in ENTRADAS else "pop",
        "saida": d.get("saida") if d.get("saida") in SAIDAS else "fade",
        "camada": d.get("camada") if d.get("camada") in CAMADAS else "frente",
        "numero": _num(d.get("numero"), 0.0, -1e12, 1e12),
        "prefixo": str(d.get("prefixo") or "")[:12],
        "sufixo": str(d.get("sufixo") or "")[:12],
        "angulo": _num(d.get("angulo"), 0.0, -360.0, 360.0),
        "logo": re.sub(r"[^a-z0-9_-]", "", str(d.get("logo") or "").lower())[:60],
        "opacidade": _num(d.get("opacidade"), 1.0, 0.1, 1.0),
        "enabled": bool(d.get("enabled", True)),
        "origem": str(d.get("origem") or "")[:20],
    }
    if d.get("id"):
        out["id"] = str(d["id"])[:40]
    return out
