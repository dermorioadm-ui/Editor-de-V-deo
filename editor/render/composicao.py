"""Composição vetorial nativa: dados editáveis, sem scripts ou render pago.

Coordenadas em uma prancheta; tempos relativos ao início do gráfico. A mesma
avaliação desenha a conferência e o export. Os eventos ASS são amostrados em
30 Hz, com relógio ancorado na composição (não em cada corte do vídeo).
"""
from __future__ import annotations

import math
import re

VERSION = 1
TIPOS = ("grupo", "texto", "numero", "retangulo", "elipse", "tracado")
CURVAS = ("linear", "suave", "entrada", "saida", "organica", "salto")
FAIXAS = {"x": (-8000, 8000), "y": (-8000, 8000), "escala": (0.001, 20),
          "rotacao": (-3600, 3600), "opacidade": (0, 1), "largura": (0.1, 8000),
          "altura": (0.1, 8000), "progresso": (0, 1), "morph": (0, 1),
          "valor": (-1e12, 1e12)}
PADRAO = {"x": 0, "y": 0, "escala": 1, "rotacao": 0, "opacidade": 1,
          "largura": 200, "altura": 100, "progresso": 1, "morph": 0, "valor": 0}


def numero(v, nome, lo, hi):
    if isinstance(v, bool):
        raise ValueError(f"{nome}: informe um número")
    try:
        n = float(v)
    except (ValueError, TypeError):
        raise ValueError(f"{nome}: informe um número") from None
    if not math.isfinite(n) or not lo <= n <= hi:
        raise ValueError(f"{nome}: precisa estar entre {lo:g} e {hi:g}")
    return n


def _cor(v):
    if v in ("marca", "texto", "fundo", "nenhuma"):
        return v
    if not isinstance(v, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
        raise ValueError("cor: use #RRGGBB, marca, texto, fundo ou nenhuma")
    return v.upper()


def _pontos(v):
    if not isinstance(v, list) or not 2 <= len(v) <= 96:
        raise ValueError("tracado: use de 2 a 96 pontos [x,y]")
    out = []
    for p in v:
        if not isinstance(p, list) or len(p) != 2:
            raise ValueError("cada ponto precisa de [x,y]")
        out.append([numero(x, "ponto", -8000, 8000) for x in p])
    return out


def normalizar(bruto, duracao):
    if not isinstance(bruto, dict) or bruto.get("versao", 1) != VERSION:
        raise ValueError("composicao: objeto com versao=1 esperado")
    numero(duracao, "duração da composição", 0.3, 60)
    tela = bruto.get("tela", [1000, 1000])
    if not isinstance(tela, list) or len(tela) != 2:
        raise ValueError("tela: informe [largura,altura]")
    tela = [numero(v, "tela", 100, 4000) for v in tela]
    itens = bruto.get("elementos")
    if not isinstance(itens, list) or not 1 <= len(itens) <= 48:
        raise ValueError("composição: de 1 a 48 elementos")
    out, grupos, ids, escalas = [], {}, set(), {}
    for raw in itens:
        if not isinstance(raw, dict):
            raise ValueError("cada elemento precisa ser um objeto")
        k, ident = raw.get("tipo"), raw.get("id")
        if k not in TIPOS or not isinstance(ident, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", ident):
            raise ValueError("elemento: tipo conhecido e id único são obrigatórios")
        if ident in ids:
            raise ValueError(f"id repetido: {ident}")
        pai = raw.get("pai", "")
        if pai and pai not in grupos:
            raise ValueError("pai deve ser um grupo declarado antes do filho")
        nivel = grupos.get(pai, 0) + 1
        if nivel > 6:
            raise ValueError("máximo de 6 níveis de grupos")
        ids.add(ident)
        e = {"id": ident, "tipo": k, "pai": pai,
             "inicio": numero(raw.get("inicio", 0), "inicio", 0, 60),
             "fim": numero(raw.get("fim", duracao), "fim", 0, 60)}
        if e["fim"] <= e["inicio"]:
            raise ValueError("fim do elemento precisa vir depois do início")
        for key, (lo, hi) in FAIXAS.items():
            e[key] = numero(raw.get(key, PADRAO[key]), key, lo, hi)
        e.update(cor=_cor(raw.get("cor", "texto")),
                 contorno=_cor(raw.get("contorno", "nenhuma")),
                 espessura=numero(raw.get("espessura", 3), "espessura", 0, 100),
                 raio=numero(raw.get("raio", 0), "raio", 0, 1000),
                 texto=str(raw.get("texto", ""))[:400],
                 fonte=re.sub(r"[\\{},\r\n]", "", str(raw.get("fonte", "")))[:80],
                 corpo=numero(raw.get("corpo", 48), "corpo", 4, 500),
                 negrito=bool(raw.get("negrito", True)),
                 casas=int(numero(raw.get("casas", 0), "casas", 0, 3)),
                 prefixo=str(raw.get("prefixo", ""))[:20], sufixo=str(raw.get("sufixo", ""))[:20],
                 fechado=bool(raw.get("fechado", False)))
        if k == "grupo":
            grupos[ident] = nivel
        if k == "tracado":
            e["pontos"] = _pontos(raw.get("pontos"))
            if raw.get("pontos_fim") is not None:
                e["pontos_fim"] = _pontos(raw["pontos_fim"])
                if len(e["pontos_fim"]) != len(e["pontos"]):
                    raise ValueError("morph precisa do mesmo número de pontos")
        marcos = raw.get("marcos", [])
        if not isinstance(marcos, list) or len(marcos) > 40:
            raise ValueError("máximo de 40 marcos por elemento")
        e["marcos"] = []
        for m in marcos:
            if not isinstance(m, dict) or set(m) - {"t", "curva", *FAIXAS}:
                raise ValueError("marco: propriedade desconhecida")
            curva = m.get("curva", "suave")
            if curva not in CURVAS:
                raise ValueError("curva desconhecida")
            v = {"t": numero(m.get("t"), "t", 0, 60), "curva": curva}
            for key, (lo, hi) in FAIXAS.items():
                if key in m:
                    v[key] = numero(m[key], key, lo, hi)
            if len(v) == 2:
                raise ValueError("marco sem propriedade animada")
            e["marcos"].append(v)
        e["marcos"].sort(key=lambda m: m["t"])
        # Duplicatas de uma propriedade no mesmo tempo são ambíguas.
        vistos = set()
        for m in e["marcos"]:
            for key in FAIXAS.keys() & m.keys():
                par = (m["t"], key)
                if par in vistos:
                    raise ValueError("marcos repetidos no mesmo tempo/propriedade")
                vistos.add(par)
        escala_max=max([e["escala"]]+[m["escala"] for m in e["marcos"] if "escala" in m])
        escalas[ident]=escala_max*escalas.get(pai,1)
        if escalas[ident]>16:
            raise ValueError("escala combinada dos grupos excede 16 vezes")
        out.append(e)
    return {"versao": VERSION, "tela": tela, "elementos": out}


def easing(t, curva):
    if curva == "linear": return t
    if curva == "entrada": return t * t * t
    if curva == "saida": return 1 - (1 - t) ** 3
    if curva == "organica": return t ** 3 * (t * (6 * t - 15) + 10)
    if curva == "salto": return 1 + 2.70158 * (t - 1) ** 3 + 1.70158 * (t - 1) ** 2
    return t * t * (3 - 2 * t)


def valor(e, key, t):
    pontos = [(m["t"], m[key], m["curva"]) for m in e["marcos"] if key in m]
    if not pontos: return e[key]
    if pontos[0][0] > 0: pontos.insert(0, (0, e[key], "linear"))
    if t <= pontos[0][0]: return pontos[0][1]
    for (a, va, _), (b, vb, curva) in zip(pontos, pontos[1:]):
        if t <= b:
            p = easing((t - a) / (b - a), curva)
            v = va + (vb - va) * p
            # Overshoot só faz sentido para posição, ângulo e dimensões.
            return max(0, min(1, v)) if key in ("opacidade", "progresso", "morph") else v
    return pontos[-1][1]


def estado(cena, t):
    """Transforms hierárquicos avaliados sem depender do renderizador."""
    out, por_id = [], {}
    for e in cena["elementos"]:
        p = por_id.get(e["pai"])
        v = {k: valor(e, k, t) for k in FAIXAS}
        ativo = e["inicio"] <= t < e["fim"] and (p is None or p["ativo"])
        if p:
            ang = math.radians(p["rotacao"])
            x, y = v["x"] * p["escala"], v["y"] * p["escala"]
            v["x"] = p["x"] + x * math.cos(ang) - y * math.sin(ang)
            v["y"] = p["y"] + x * math.sin(ang) + y * math.cos(ang)
            v["escala"] *= p["escala"]
            v["rotacao"] += p["rotacao"]
            v["opacidade"] *= p["opacidade"]
        v["ativo"] = ativo
        por_id[e["id"]] = v
        if e["tipo"] != "grupo" and ativo and v["opacidade"] > 0.001:
            out.append((e, v))
    return out


def _recortar_traco(pontos, progresso):
    if progresso >= 1: return pontos
    dist = [math.hypot(b[0]-a[0], b[1]-a[1]) for a,b in zip(pontos, pontos[1:])]
    falta = sum(dist) * progresso
    out = [pontos[0]]
    for a, b, d in zip(pontos, pontos[1:], dist):
        if falta >= d:
            out.append(b); falta -= d
        elif d > 0:
            f = falta / d
            out.append((a[0]+(b[0]-a[0])*f, a[1]+(b[1]-a[1])*f))
            break
    return out


def _forma(e, v):
    w, h = max(0.01, v["largura"]), max(0.01, v["altura"])
    if e["tipo"] == "elipse":
        return [(w/2*math.cos(i*math.tau/64), h/2*math.sin(i*math.tau/64)) for i in range(65)], True
    if e["tipo"] == "retangulo":
        r = min(e["raio"], w/2, h/2)
        pts = []
        for cx, cy, a in [(w/2-r,-h/2+r,-90),(w/2-r,h/2-r,0),(-w/2+r,h/2-r,90),(-w/2+r,-h/2+r,180)]:
            pts += [(cx+r*math.cos(math.radians(a+i*90/8)),cy+r*math.sin(math.radians(a+i*90/8))) for i in range(9)]
        return pts+[pts[0]], True
    pts = e["pontos"]
    if "pontos_fim" in e:
        f = v["morph"]
        pts = [(a[0]+(b[0]-a[0])*f, a[1]+(b[1]-a[1])*f) for a,b in zip(pts,e["pontos_fim"])]
    if e["fechado"]: pts = [*pts, pts[0]]
    return pts, e["fechado"]


def eventos(g, W, H, t0, dur, base, fonte, kit):
    from . import motion as M
    cena = M._v(g, "composicao", {})
    if not cena: return []
    gs, ge = M._v(g,"out_start",0), M._v(g,"out_end",0)
    cw,ch = cena["tela"]
    s = min(W/cw,H/ch) * M._v(g,"tamanho",1)
    ox,oy = W*M._v(g,"x",0.5)-cw*s/2, H*M._v(g,"y",0.5)-ch*s/2
    paleta = M._paleta(g,kit)
    def cor(c):
        if c == "marca": return M._cor(paleta["acento"])
        if c == "texto": return M._cor(paleta["texto"])
        if c == "fundo": return M._cor(paleta["fundo"] or "#11141A")
        return M._cor(c) if c != "nenhuma" else M._cor("#FFFFFF")
    linhas=[]
    ultimos={}
    ordens={e["id"]:i for i,e in enumerate(cena["elementos"])}
    lo,hi = max(gs,t0),min(ge,t0+dur+0.05)
    if hi <= lo: return []
    # Quantização em centésimos é a resolução nativa do ASS. Ancorar a grade
    # em gs permite que quadros antes/depois de uma emenda tenham a mesma fase.
    n0,n1=math.floor((lo-gs)*30),math.ceil((hi-gs)*30)
    for frame in range(n0,n1):
        ini,fim=max(lo,gs+frame/30),min(hi,gs+(frame+1)/30)
        a,b=M._cs(ini-t0),M._cs(fim-t0)
        if b<=a: continue
        for j,(e,v) in enumerate(estado(cena,frame/30)):
            esc=s*v["escala"]
            x,y=ox+s*v["x"],oy+s*v["y"]
            alpha=round(255*(1-v["opacidade"]))
            tags=f"\\an5\\pos({x:.3f},{y:.3f})\\org({x:.3f},{y:.3f})\\alpha&H{alpha:02X}&\\shad0"
            conteudo=""
            if e["tipo"] in ("texto","numero"):
                texto=e["texto"]
                if e["tipo"]=="numero":
                    texto=e["prefixo"]+f"{v['valor']:.{e['casas']}f}".replace(".",",")+e["sufixo"]
                if kit:
                    from ..marca import corrigir_grafia
                    texto=corrigir_grafia(texto,kit)
                tags+=(f"\\fn{M._fonte(e['fonte'] or M.fonte_do_kit(kit,fonte))}\\fs{e['corpo']*esc:.3f}"
                       f"\\b{1 if e['negrito'] else 0}\\frz{-v['rotacao']:.3f}\\bord0\\1c{cor(e['cor'])}")
                conteudo=M._esc(texto).replace("\n",r"\N")
            else:
                pts, fechado=_forma(e,v)
                pts=_recortar_traco(pts,v["progresso"])
                if len(pts)<2: continue
                ang=math.radians(v["rotacao"])
                pts=[(x+esc*(px*math.cos(ang)-py*math.sin(ang)),y+esc*(px*math.sin(ang)+py*math.cos(ang))) for px,py in pts]
                caminho="m "+" l ".join(f"{px:.3f} {py:.3f}" for px,py in pts)
                # Desenhos em coordenadas globais não podem ser recentrados.
                tags=(f"\\an7\\pos(0,0)\\p1\\shad0\\alpha&H{alpha:02X}&"
                      f"\\1c{cor(e['cor'])}\\3c{cor(e['contorno'])}"
                      f"\\bord{e['espessura']*esc/2 if e['contorno']!='nenhuma' else 0:.3f}")
                if not fechado or v["progresso"]<0.999:
                    if e["contorno"] == "nenhuma" or e["espessura"] <= 0: continue
                    caminho=M._tracos([pts],e["espessura"]*esc)
                    tags=(f"\\an7\\pos(0,0)\\p1\\shad0\\bord0\\alpha&H{alpha:02X}&"
                          f"\\1c{cor(e['contorno'])}")
                elif e["cor"]=="nenhuma":
                    tags+="\\1a&HFF&"
                conteudo=caminho
            # Mantém a ordem original inclusive quando outro elemento some.
            ordem=base+ordens[e["id"]]
            corpo=f"{{{tags}}}{conteudo}"
            ultimo=ultimos.get(ordem)
            if ultimo and ultimo[2]==a and ultimo[3]==corpo:
                ultimo[2]=b
            else:
                evento=[ordem,a,b,corpo]
                linhas.append(evento);ultimos[ordem]=evento
    return [f"Dialogue: {z},{M._ts(a)},{M._ts(b)},G,,0,0,0,,{texto}" for z,a,b,texto in linhas]
