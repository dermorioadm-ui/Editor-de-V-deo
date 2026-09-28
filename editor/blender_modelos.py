"""OBJETOS 3D PRONTOS — roda DENTRO do Blender, chamado pelo blender_worker.

"Vai falar do imóvel de temporada, faz um desenho 3D ali, foda." Montar uma
casa com cubos soltos, pelo modelo, saía tosco. Aqui cada objeto é modelado
de verdade (bordas arredondadas, proporção de ilustração 3D, peças que se
encaixam) e pintado com a paleta da MARCA do vídeo: a cor principal na peça
que importa, branco quente no resto, detalhe escuro. Nada de textura, nada
de gradiente — o estilo "ícone 3D premium" de app.

Cada modelo devolve as peças na ordem em que MONTAM (a animação "montar"
faz cada uma chegar com um pequeno atraso e assentar com um leve
ultrapassar). O grupo inteiro gira devagar, nunca uma volta: 3D aqui é para
ilustrar o produto, não para fazer pirotecnia.

Este arquivo só recebe dados já validados pelo Sharkcut (nomes da lista
MODELOS, cores #RRGGBB, números) — nenhum código vindo do modelo de IA.
"""
import math

import bpy
from mathutils import Matrix, Vector

MODELOS = ("casa", "predio", "chave", "cadeado", "escudo", "documento", "celular",
           "calendario", "check", "estrela", "grafico", "mala")
ANIMACOES = ("montar", "surgir", "flutuar", "nenhuma")


# ------------------------------------------------------------------ materiais
def _linear(hexcor):
    s = [int(hexcor[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    return [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in s]


def _mistura(a, b, t):
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02X%02X%02X" % tuple(round(x + (y - x) * t) for x, y in zip(ca, cb))


_cache_mat = {}


def material(cor, rug=0.42, metal=0.0, coat=0.3, emissao=0.0):
    chave = (cor, rug, metal, coat, emissao)
    if chave in _cache_mat:
        return _cache_mat[chave]
    m = bpy.data.materials.new(f"M{len(_cache_mat):02d}")
    m.use_nodes = True
    b = m.node_tree.nodes.get("Principled BSDF")
    lin = _linear(cor)
    b.inputs["Base Color"].default_value = (*lin, 1)
    b.inputs["Roughness"].default_value = rug
    b.inputs["Metallic"].default_value = metal
    for nome in ("Coat Weight", "Clearcoat"):
        if nome in b.inputs:
            b.inputs[nome].default_value = coat
            break
    if emissao:
        for nome in ("Emission Color", "Emission"):
            if nome in b.inputs:
                b.inputs[nome].default_value = (*lin, 1)
                break
        if "Emission Strength" in b.inputs:
            b.inputs["Emission Strength"].default_value = emissao
    _cache_mat[chave] = m
    return m


# --------------------------------------------------------------------- peças
def _acabar(obj, mat, bevel=0.06, segs=4, suave=True):
    obj.data.materials.append(mat)
    if bevel:
        mod = obj.modifiers.new("Bordas", "BEVEL")
        mod.width = bevel
        mod.segments = segs
        mod.limit_method = "ANGLE"
    if suave:
        for p in obj.data.polygons:
            p.use_smooth = True
        try:
            obj.modifiers.new("Normais", "WEIGHTED_NORMAL").keep_sharp = True
        except Exception:  # noqa: BLE001 — versão sem o modificador: fica só o suave
            pass
    return obj


def _novo(nome, mesh, pos, rot=(0, 0, 0)):
    obj = bpy.data.objects.new(nome, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = pos
    obj.rotation_euler = [math.radians(r) for r in rot]
    return obj


def caixa(nome, dims, pos, mat, bevel=0.06, rot=(0, 0, 0), segs=4):
    bpy.ops.mesh.primitive_cube_add(size=1)
    tmp = bpy.context.object
    me = tmp.data
    me.transform(Matrix.Diagonal((*dims, 1)))
    bpy.data.objects.remove(tmp)
    me.name = nome
    b = min(bevel, min(dims) * 0.45)
    return _acabar(_novo(nome, me, pos, rot), mat, b, segs)


def cilindro(nome, r, h, pos, mat, rot=(0, 0, 0), verts=64, bevel=0.03):
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=r, depth=h)
    tmp = bpy.context.object
    me = tmp.data
    bpy.data.objects.remove(tmp)
    return _acabar(_novo(nome, me, pos, rot), mat, min(bevel, r * 0.4, h * 0.4))


def cone(nome, r, h, pos, mat, rot=(0, 0, 0), verts=64):
    bpy.ops.mesh.primitive_cone_add(vertices=verts, radius1=r, radius2=0.0, depth=h)
    tmp = bpy.context.object
    me = tmp.data
    bpy.data.objects.remove(tmp)
    return _acabar(_novo(nome, me, pos, rot), mat, 0.02)


def anel(nome, maior, menor, pos, mat, rot=(0, 0, 0)):
    bpy.ops.mesh.primitive_torus_add(major_radius=maior, minor_radius=menor,
                                     major_segments=72, minor_segments=24)
    tmp = bpy.context.object
    me = tmp.data
    bpy.data.objects.remove(tmp)
    return _acabar(_novo(nome, me, pos, rot), mat, 0, suave=True)


def esfera(nome, r, pos, mat):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=r, segments=48, ring_count=24)
    tmp = bpy.context.object
    me = tmp.data
    bpy.data.objects.remove(tmp)
    return _acabar(_novo(nome, me, pos), mat, 0, suave=True)


def extrudado(nome, pontos, espessura, pos, mat, rot=(90, 0, 0), bevel=0.05):
    """Um contorno 2D (x, y) extrudado — escudo, estrela, telhado."""
    n = len(pontos)
    h = espessura / 2
    verts = [(x, y, -h) for x, y in pontos] + [(x, y, h) for x, y in pontos]
    faces = [list(range(n - 1, -1, -1)), list(range(n, 2 * n))]
    faces += [[i, (i + 1) % n, n + (i + 1) % n, n + i] for i in range(n)]
    me = bpy.data.meshes.new(nome)
    me.from_pydata(verts, [], faces)
    me.update()
    obj = _novo(nome, me, pos, rot)
    return _acabar(obj, mat, bevel, 4, suave=True)


def traco(nome, pontos, raio, pos, mat, rot=(90, 0, 0)):
    """Uma linha grossa arredondada (assinatura, seta) por uma curva."""
    cu = bpy.data.curves.new(nome, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = raio
    cu.bevel_resolution = 6
    cu.use_fill_caps = True
    sp = cu.splines.new("POLY")
    sp.points.add(len(pontos) - 1)
    for p, (x, y) in zip(sp.points, pontos):
        p.co = (x, y, 0, 1)
    obj = bpy.data.objects.new(nome, cu)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = pos
    obj.rotation_euler = [math.radians(r) for r in rot]
    obj.data.materials.append(mat)
    return obj


def check_mark(nome, largura, pos, mat, espessura=0.16, rot=(90, 0, 0)):
    w = largura
    pts = [(-0.5 * w, 0.02 * w), (-0.14 * w, -0.32 * w), (0.5 * w, 0.34 * w)]
    return traco(nome, pts, espessura * w * 0.5, pos, mat, rot)


# ------------------------------------------------------------------- modelos
def _paleta(p):
    marca = p.get("marca", "#FF385C")
    return {"marca": marca,
            "escura": p.get("escura") or _mistura(marca, "#000000", 0.25),
            "clara": p.get("clara", "#FBF8F4"),
            "base": p.get("base") or _mistura(p.get("clara", "#FBF8F4"), "#C9C2BA", 0.35),
            "texto": p.get("texto", "#2A2A2E"),
            "suave": _mistura(marca, "#FFFFFF", 0.78),
            "ouro": "#F4B942"}


def casa(c):
    """Imóvel de temporada: casa, terreno, piscina e guarda-sol."""
    M = {k: material(v) for k, v in c.items()}
    vidro = material("#8ED0E6", rug=0.12, coat=1.0)
    agua = material("#6FD3E0", rug=0.08, coat=1.0)
    p = []
    p.append(caixa("terreno", (3.0, 2.3, 0.22), (0, 0, 0.11), M["base"], 0.1))
    p.append(caixa("paredes", (1.7, 1.35, 1.05), (-0.55, 0.2, 0.745), M["clara"], 0.07))
    telhado = [(-1.08, 0), (1.08, 0), (0.0, 0.78)]
    p.append(extrudado("telhado", telhado, 1.6, (-0.55, 0.2, 1.24), M["marca"], rot=(90, 0, 0),
                       bevel=0.06))
    p.append(caixa("chamine", (0.2, 0.2, 0.5), (-0.05, 0.45, 1.75), M["escura"], 0.04))
    p.append(caixa("porta", (0.34, 0.06, 0.6), (-0.55, -0.49, 0.53), M["escura"], 0.04))
    p.append(caixa("janela1", (0.32, 0.05, 0.3), (-1.05, -0.48, 0.82), vidro, 0.03))
    p.append(caixa("janela2", (0.32, 0.05, 0.3), (-0.05, -0.48, 0.82), vidro, 0.03))
    p.append(caixa("borda", (1.0, 0.72, 0.08), (0.9, -0.45, 0.26), M["clara"], 0.04))
    p.append(caixa("piscina", (0.84, 0.56, 0.05), (0.9, -0.45, 0.285), agua, 0.02))
    p.append(cilindro("haste", 0.025, 1.0, (1.12, 0.5, 0.72), M["texto"], verts=16, bevel=0))
    p.append(cone("guarda_sol", 0.48, 0.26, (1.12, 0.5, 1.24), M["marca"]))
    return p


def predio(c):
    M = {k: material(v) for k, v in c.items()}
    vidro = material("#8ED0E6", rug=0.12, coat=1.0)
    p = [caixa("base", (1.9, 1.9, 0.2), (0, 0, 0.1), M["base"], 0.08),
         caixa("torre", (1.1, 1.1, 2.3), (0, 0, 1.35), M["clara"], 0.08)]
    for i in range(4):
        for j in range(2):
            p.append(caixa(f"jan{i}{j}", (0.3, 0.04, 0.3),
                           (-0.22 + 0.44 * j, -0.56, 0.75 + 0.45 * i), vidro, 0.03))
    p.append(caixa("porta", (0.4, 0.05, 0.5), (0, -0.56, 0.45), M["marca"], 0.04))
    p.append(caixa("topo", (1.2, 1.2, 0.14), (0, 0, 2.55), M["marca"], 0.05))
    return p


def chave(c):
    M = material(c["marca"], rug=0.28, metal=0.35, coat=0.6)
    return [anel("cabeca", 0.5, 0.16, (-0.85, 0, 1.2), M, rot=(90, 0, 0)),
            cilindro("haste", 0.13, 1.6, (0.3, 0, 1.2), M, rot=(0, 90, 0), bevel=0.04),
            caixa("dente1", (0.16, 0.24, 0.38), (0.75, 0, 0.98), M, 0.04),
            caixa("dente2", (0.16, 0.24, 0.26), (1.02, 0, 1.03), M, 0.04)]


def cadeado(c):
    corpo = material(c["marca"], rug=0.35, coat=0.5)
    metal = material("#CDD2D9", rug=0.22, metal=0.9)
    escuro = material(c["texto"])
    return [caixa("corpo", (1.5, 0.7, 1.2), (0, 0, 0.6), corpo, 0.16),
            anel("argola", 0.46, 0.11, (0, 0, 1.25), metal, rot=(90, 0, 0)),
            cilindro("furo", 0.13, 0.08, (0, -0.35, 0.72), escuro, rot=(90, 0, 0), bevel=0.01),
            caixa("fenda", (0.1, 0.08, 0.3), (0, -0.35, 0.52), escuro, 0.03)]


def _contorno_escudo(n=40):
    pts = []
    for k in range(n + 1):
        t = k / n
        x = -0.85 + 1.7 * t
        pts.append((x, 1.0 - 0.1 * math.sin(math.pi * t)))
    for k in range(1, n):
        t = k / n
        a = math.pi * t
        x = 0.85 * math.cos(a)
        y = 0.2 - 1.15 * math.sin(a) ** 1.4
        pts.append((x, y))
    return pts


def escudo(c):
    M = material(c["marca"], rug=0.32, coat=0.6)
    branco = material(c["clara"])
    return [extrudado("escudo", _contorno_escudo(), 0.34, (0, 0, 1.25), M, rot=(90, 0, 0),
                      bevel=0.08),
            check_mark("check", 0.95, (0, -0.2, 1.3), branco, 0.2)]


def documento(c):
    papel = material(c["clara"], rug=0.6, coat=0.1)
    linha = material("#C4BEB7", rug=0.6)
    M = material(c["marca"], coat=0.5)
    p = [caixa("folha", (1.3, 0.06, 1.7), (0, 0, 0.95), papel, 0.04)]
    for i, w in enumerate((0.9, 0.95, 0.8, 0.9, 0.55)):
        p.append(caixa(f"linha{i}", (w, 0.02, 0.07), (-0.08 - (0.95 - w) / 2, -0.04, 1.5 - i * 0.2),
                       linha, 0.01))
    assinatura = [(-0.45, 0.0), (-0.3, 0.12), (-0.18, -0.05), (-0.05, 0.1), (0.05, -0.02),
                  (0.2, 0.08), (0.35, 0.0)]
    p.append(traco("assinatura", assinatura, 0.035, (-0.12, -0.05, 0.42), M))
    p.append(cilindro("selo", 0.17, 0.06, (0.42, -0.06, 0.4), M, rot=(90, 0, 0), bevel=0.02))
    return p


def celular(c):
    corpo = material("#1F1F24", rug=0.25, coat=0.8)
    tela = material(c["clara"], rug=0.3, emissao=0.08)
    M = material(c["marca"], emissao=0.2)
    cinza = material("#D6D0C9")
    p = [caixa("corpo", (1.0, 0.1, 1.95), (0, 0, 1.0), corpo, 0.14),
         caixa("tela", (0.88, 0.02, 1.8), (0, -0.055, 1.0), tela, 0.1),
         caixa("barra", (0.78, 0.02, 0.18), (0, -0.07, 1.72), M, 0.04)]
    for i in range(3):
        p.append(caixa(f"card{i}", (0.74, 0.02, 0.26), (0, -0.07, 1.4 - i * 0.34), cinza, 0.06))
    p.append(caixa("botao", (0.6, 0.02, 0.18), (0, -0.075, 0.3), M, 0.09))
    return p


def calendario(c):
    papel = material(c["clara"], rug=0.5)
    M = material(c["marca"], coat=0.4)
    cinza = material("#D6D0C9")
    escuro = material(c["texto"], metal=0.4, rug=0.3)
    p = [caixa("folha", (1.6, 0.16, 1.5), (0, 0, 0.8), papel, 0.1),
         caixa("topo", (1.6, 0.17, 0.36), (0, 0, 1.4), M, 0.08)]
    for k, x in enumerate((-0.45, 0.45)):
        p.append(anel(f"argola{k}", 0.1, 0.03, (x, 0, 1.62), escuro, rot=(0, 90, 0)))
    for i in range(3):
        for j in range(4):
            dia = M if (i, j) == (1, 2) else cinza
            p.append(caixa(f"d{i}{j}", (0.22, 0.03, 0.2), (-0.51 + 0.34 * j, -0.09, 1.0 - 0.3 * i),
                           dia, 0.04))
    return p


def check(c):
    M = material(c["marca"], rug=0.3, coat=0.7)
    branco = material(c["clara"])
    return [cilindro("disco", 1.0, 0.3, (0, 0, 1.1), M, rot=(90, 0, 0), verts=96, bevel=0.1),
            check_mark("check", 1.05, (0, -0.2, 1.12), branco, 0.2)]


def estrela(c):
    M = material(c["ouro"], rug=0.3, metal=0.3, coat=0.6)
    pts = []
    for k in range(10):
        a = math.pi / 2 + k * math.pi / 5
        r = 1.0 if k % 2 == 0 else 0.46
        pts.append((r * math.cos(a), r * math.sin(a)))
    return [extrudado("estrela", pts, 0.3, (0, 0, 1.1), M, rot=(90, 0, 0), bevel=0.08)]


def grafico(c):
    M = material(c["marca"], coat=0.5)
    cinza = material("#DCD7D1")
    base = material(c["clara"])
    p = [caixa("base", (2.3, 1.0, 0.14), (0, 0, 0.07), base, 0.06)]
    for k, h in enumerate((0.6, 0.95, 1.3, 1.9)):
        p.append(caixa(f"barra{k}", (0.36, 0.36, h), (-0.78 + 0.52 * k, 0, 0.14 + h / 2),
                       M if k == 3 else cinza, 0.06))
    return p


def mala(c):
    M = material(c["marca"], rug=0.35, coat=0.5)
    branco = material(c["clara"])
    escuro = material(c["texto"], rug=0.4)
    return [caixa("corpo", (1.15, 0.55, 1.45), (0, 0, 0.9), M, 0.16),
            caixa("faixa", (1.17, 0.57, 0.14), (0, 0, 1.0), branco, 0.05),
            anel("alca", 0.24, 0.05, (0, 0, 1.68), escuro, rot=(90, 0, 0)),
            cilindro("roda1", 0.1, 0.08, (-0.4, 0, 0.12), escuro, rot=(90, 0, 0), bevel=0.02),
            cilindro("roda2", 0.1, 0.08, (0.4, 0, 0.12), escuro, rot=(90, 0, 0), bevel=0.02)]


# os objetos chapados (tela, folha, disco) olham para a câmera; os de volume
# mostram a quina, que é onde o 3D aparece
DE_FRENTE = {"celular": 32, "calendario": 30, "documento": 30, "check": 24, "escudo": 24,
             "estrela": 20, "chave": 12, "cadeado": 18, "mala": 20}

CONSTRUTORES = {"casa": casa, "predio": predio, "chave": chave, "cadeado": cadeado,
                "escudo": escudo, "documento": documento, "celular": celular,
                "calendario": calendario, "check": check, "estrela": estrela,
                "grafico": grafico, "mala": mala}


# ----------------------------------------------------------------- animação
def _chave(obj, caminho, frame, interp="BEZIER"):
    bpy.context.preferences.edit.keyframe_new_interpolation_type = interp
    obj.keyframe_insert(data_path=caminho, frame=frame)


def construir(raw, fps, duracao):
    """Monta o modelo com a paleta e anima. Devolve o objeto-pai (grupo)."""
    cores = _paleta(raw.get("paleta") or {})
    pecas = CONSTRUTORES[raw["modelo"]](cores)
    grupo = bpy.data.objects.new(f"Grupo_{raw['modelo']}", None)
    bpy.context.scene.collection.objects.link(grupo)
    for p in pecas:
        p.parent = grupo
    grupo.location = raw.get("posicao", [0, 0, 0])
    grupo.scale = raw.get("escala", [1, 1, 1])
    rot = [math.radians(r) for r in raw.get("rotacao", [0, 0, 0])]
    rot[2] += math.radians(DE_FRENTE.get(raw["modelo"], 0))
    grupo.rotation_euler = rot
    anim = raw.get("animacao", "montar")
    fim = max(2, round(duracao * fps))
    if anim == "montar":
        passo = max(1, round(min(0.12, 0.9 / max(1, len(pecas))) * fps))
        dur = max(4, round(0.42 * fps))
        for k, p in enumerate(pecas):
            f0 = 1 + k * passo
            alvo_loc = p.location.copy()
            alvo_esc = p.scale.copy()
            p.location = alvo_loc + Vector((0, 0, 0.9))
            p.scale = alvo_esc * 0.001
            _chave(p, "location", f0, "BACK")
            _chave(p, "scale", f0, "BACK")
            p.location = alvo_loc
            p.scale = alvo_esc
            _chave(p, "location", f0 + dur)
            _chave(p, "scale", f0 + dur)
    elif anim == "surgir":
        alvo = grupo.scale.copy()
        grupo.scale = alvo * 0.001
        _chave(grupo, "scale", 1, "BACK")
        grupo.scale = alvo
        _chave(grupo, "scale", 1 + round(0.55 * fps))
    if anim != "nenhuma":
        # o giro de apresentação: pouco, devagar, e para (nunca uma volta inteira)
        r0 = list(rot)
        grupo.rotation_euler = (r0[0], r0[1], r0[2] - math.radians(28))
        _chave(grupo, "rotation_euler", 1, "SINE")
        grupo.rotation_euler = r0
        _chave(grupo, "rotation_euler", fim)
        # e respira: sobe um pouco e volta
        z0 = grupo.location.z
        _chave(grupo, "location", 1, "SINE")
        grupo.location.z = z0 + 0.08
        _chave(grupo, "location", max(2, fim // 2), "SINE")
        grupo.location.z = z0
        _chave(grupo, "location", fim, "SINE")
    bpy.context.preferences.edit.keyframe_new_interpolation_type = "BEZIER"
    return grupo


def transicao_faixas(paleta, largura_cena, altura_cena, fps, duracao):
    """A transição 3D: três faixas largas, arredondadas, nas cores da marca,
    entram pela esquerda em diagonal, TAMPAM A TELA INTEIRA no meio do tempo
    (é ali que fica o corte) e saem pela direita."""
    c = _paleta(paleta or {})
    mats = [material(c["marca"], rug=0.3, coat=0.7), material(c["escura"], rug=0.3, coat=0.7),
            material(c["clara"], rug=0.4, coat=0.4)]
    fim = max(8, round(duracao * fps))
    meio = round(fim / 2)
    diag = math.hypot(largura_cena, altura_cena)
    comp = diag * 1.3
    fora = largura_cena / 2 + comp / 2 + altura_cena * 0.3
    h = altura_cena * 0.62
    faixas = []
    for k, mat in enumerate(mats):
        f = caixa(f"faixa{k}", (comp, 0.4, h), (0, 0.3 * k, 0), mat, min(0.6, h * 0.2),
                  rot=(0, -14, 0))
        z = (k - 1) * h * 0.72
        chega = max(2, meio - 2 + k)          # a última chega no meio: aí tudo tampado
        sai = min(fim - 1, meio + 1 + k)
        f.location = (-fora, 0.3 * k, z)
        _chave(f, "location", 1 + k)
        f.location = (0, 0.3 * k, z)
        _chave(f, "location", chega)
        _chave(f, "location", sai)
        f.location = (fora, 0.3 * k, z)
        _chave(f, "location", fim)
        faixas.append(f)
    bpy.context.preferences.edit.keyframe_new_interpolation_type = "BEZIER"
    return faixas

def enquadrar(cam, alvo, objetos, cena, margem=1.18):
    """Põe a câmera na distância certa para o objeto ocupar o quadro (na pose
    final), mantendo a direção de onde ela olha. Sem isso, a casa ficava
    pequena num canto e o celular sumia de perfil."""
    cena.frame_set(cena.frame_end)
    bpy.context.view_layer.update()
    pts = []
    for o in objetos:
        if o.type in ("MESH", "CURVE"):
            pts += [o.matrix_world @ Vector(c) for c in o.bound_box]
    if not pts:
        return
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    centro = (lo + hi) / 2
    raio = max((p - centro).length for p in pts)
    direcao = (cam.location - alvo.location).normalized()
    fov = cam.data.angle
    dist = raio / math.sin(fov / 2) * margem / 1.08
    alvo.location = centro
    cam.location = centro + direcao * dist
    cam.keyframe_insert(data_path="location", frame=1)
    alvo.keyframe_insert(data_path="location", frame=1)
    cena.frame_set(1)
