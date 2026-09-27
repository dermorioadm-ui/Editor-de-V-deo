"""A pós-edição: o que o Claude (ou a tela) usa para editar por cima do corte.

Três coisas vivem aqui:

1. O ROTEIRO da pós: o que é dito em cada segundo do vídeo FINAL, os blocos
   (onde cabem transições), o formato, onde mora a legenda, o que já existe.
   É o que o editor precisa ler antes de decidir onde entra um título.
2. OS OLHOS: o quadro EXATO do vídeo final num instante — renderizado pelo
   MESMO comando do encode (mesmo grafo, mesma legenda, mesmo gráfico, mesma
   camada), só que em tamanho de conferência e num quadro só. Não é uma
   prévia parecida: é o encode de verdade, parado num instante.
3. A CENA: onde a pessoa está no quadro (pelo mesmo recorte das camadas),
   quanto do quadro ela ocupa em cada terço, onde sobra espaço para gráfico
   sem cobrir o rosto, a luz do quadro e o que está sendo dito ali.

Nada disso manda o vídeo para fora da máquina.
"""
from __future__ import annotations

import base64
import copy
import itertools
import math
import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np

from .config import ExportParams
from .edit.timeline import Timeline
from . import marca as MK
from .models import Camada, Cena, Grafico, Transicao
from .render import camadas as CM
from .render import cenas as CN
from .render import motion as MG
from .render import recorte as RC
from .render import renderer as R
from .render import transicoes as TR

LADO_DO_QUADRO = 720          # lado MENOR do quadro de conferência
MAX_QUADROS = 6
# número de cada render de conferência: a tela e o Claude podem pedir quadros
# ao mesmo tempo, e os arquivos de trabalho de um não podem pisar nos do outro
_numero = itertools.count(100_000)


# ----------------------------------------------------------------- palavras
def palavras_na_saida(project, tl: Timeline) -> list[dict]:
    """Todas as palavras que FICARAM, com o tempo no vídeo final."""
    from .projects import corrected_words
    from .subtitles.corrections import apply_corrections
    from .subtitles.remap import remap_words
    from . import db

    removed = set(project.analysis.get("removed_word_ids", []))
    words, _ = corrected_words(project)
    words = [w for w in words if w.get("src_i", w["i"]) not in removed]
    mapped = remap_words(words, tl)
    regras = db.list_corrections()
    for mid in project.fontes_com_fala()[1:]:
        extras = [w for w in project.words_de(mid)
                  if w.get("src_i", w["i"]) not in removed]
        if extras:
            corrigidas, _ = apply_corrections(extras, regras)
            mapped += remap_words(corrigidas, tl, source=mid)
    return sorted(mapped, key=lambda w: (float(w.get("start", 0.0)),
                                         float(w.get("end", 0.0))))


def _frases(palavras: list[dict], pausa: float = 0.55, maximo: int = 18) -> list[dict]:
    """Palavras em frases: quebra na pontuação final, na pausa ou no tamanho."""
    frases: list[dict] = []
    atual: list[dict] = []
    for w in palavras:
        if atual and (float(w["start"]) - float(atual[-1]["end"]) > pausa
                      or len(atual) >= maximo):
            frases.append(atual)
            atual = []
        atual.append(w)
        if str(w.get("text", "")).rstrip().endswith((".", "!", "?")):
            frases.append(atual)
            atual = []
    if atual:
        frases.append(atual)
    return [{"inicio": round(float(f[0]["start"]), 2),
             "fim": round(float(f[-1]["end"]), 2),
             "texto": " ".join(str(w.get("text", "")).strip() for w in f)}
            for f in frases]


# ------------------------------------------------------------------ roteiro
def contexto(project) -> dict:
    """O roteiro da pós-edição, em dados."""
    from .projects import list_media, timeline_summary

    plan = project.plan
    resumo = timeline_summary(project)
    fps = project.info.fps if project.info else None
    tl = Timeline(plan.active_clips, fps)
    palavras = palavras_na_saida(project, tl)
    nomes = {m["id"]: m.get("name") or m["id"] for m in list_media(project.id)}
    blocos = []
    for b in resumo.get("blocks", []):
        ini, fim = float(b["out_start"]), float(b["out_end"])
        blocos.append({
            "clip_id": b["id"], "inicio": round(ini, 2), "fim": round(fim, 2),
            "gravacao": ("principal" if b.get("source", "main") == "main"
                         else nomes.get(b.get("source"), b.get("source"))),
            "tipo": b.get("kind", "video"),
            "velocidade": round(float(b.get("speed") or 1.0), 2),
            "zoom": round(float(b.get("zoom") or 1.0), 2),
            "etapa": b.get("section") or "",
            "texto": " ".join(str(w.get("text", "")).strip() for w in palavras
                              if ini - 1e-3 <= float(w.get("start", 0.0)) < fim)[:400],
        })
    main = project.info
    W, H = (R.target_size(main, replace(plan.export, scale="source"))
            if main else (1080, 1920))
    st = plan.style
    legenda = None
    if plan.export.burn_subtitles:
        # a faixa da legenda, em fração da altura: gráfico não entra ali
        altura = (st.margin_v + st.fontsize * 1.25 * max(1, getattr(st, "max_lines", 2)))
        ph = main.display_size[1] if main else H
        frac = min(0.5, altura / max(1.0, ph))
        if st.align in (1, 2, 3):
            legenda = {"de": round(1 - frac, 3), "ate": 1.0}
        elif st.align in (7, 8, 9):
            legenda = {"de": 0.0, "ate": round(frac, 3)}
        else:
            legenda = {"de": round(0.5 - frac / 2, 3), "ate": round(0.5 + frac / 2, 3)}
    return {
        "duracao": resumo.get("duration", 0.0),
        "formato": {"largura": W, "altura": H,
                    "proporcao": plan.export.aspect or "fonte"},
        "legenda": legenda,
        "blocos": blocos,
        "brolls": [{"id": c["id"], "inicio": round(float(c["out_start"]), 2),
                    "fim": round(float(c["out_end"]), 2),
                    "termo": c.get("termo") or ""}
                   for c in resumo.get("cutaways", []) if c.get("enabled", True)],
        "frases": _frases(palavras),
        "graficos": [g.to_dict() for g in plan.graficos],
        "camadas": [c.to_dict() for c in plan.camadas],
        "transicoes": [x.to_dict() for x in plan.transicoes],
        "cenas": [c.to_dict() for c in getattr(plan, "cenas", [])],
        "marca": MK.resumo(MK.do_projeto(project)),
        "recorte": RC.estado(),
        "editor": getattr(plan, "editor", ""),
    }


# ------------------------------------------------------------------- olhos
def _plano_de_conferencia(project, lado: int):
    """O plano do export, reduzido para o lado menor = ``lado``."""
    plan = copy.copy(project.plan)
    main = project.info
    W, H = R.target_size(main, replace(plan.export, scale="source"))
    k = min(1.0, float(lado) / max(1.0, min(W, H)))
    escala = "source" if k >= 0.999 else str(int(round(main.display_size[0] * k)))
    plan.export = ExportParams(**{**plan.export.__dict__, "scale": escala,
                                  "preset": "ultrafast", "crf": 20,
                                  "extras": ()})
    return plan


def _cues(project, plan, tl: Timeline, main) -> list[dict]:
    from .projects import cue_list, rebuild_subtitles

    if not plan.export.burn_subtitles:
        return []
    _pw, _ph, estilo = R.regua_da_legenda(main, plan.export, plan.style)
    # o projeto é o carregado para esta chamada e NÃO é gravado: refazer as
    # legendas aqui não muda nada do que está salvo
    rebuild_subtitles(project, tl, estilo if estilo is not plan.style else None)
    return cue_list(project)


def _pedaco(seg, t: float, fps: float):
    """Um trecho curto em volta de ``t``, igual ao trecho de verdade ali.

    Renderizar o trecho INTEIRO para ver um quadro é pagar um minuto de
    decodificação por uma foto. O pedaço começa um pouco antes de ``t`` (o
    recorte precisa de uns quadros para assentar) e herda do trecho o que
    depende da posição: a borda de transição só vem junto se o pedaço começa
    (ou termina) onde o trecho começa (ou termina).
    """
    ini, fim = seg.t_start, seg.t_start + seg.out_theoretical
    if seg.kind == "photo":
        return seg, t - ini
    a = max(ini, t - 0.6)
    if seg.trans_entra and t - ini <= float(seg.trans_entra["dur"]) + 0.1:
        a = ini
    b = min(fim, t + 3.0 / max(fps, 1.0))
    sai = None
    if seg.trans_sai and fim - t <= float(seg.trans_sai["dur"]) + 0.1:
        b, sai = fim, seg.trans_sai
    frac_a = (a - ini) / max(seg.out_theoretical, 1e-9)
    frac_b = (b - ini) / max(seg.out_theoretical, 1e-9)
    novo = replace(seg, src_start=seg.src_start + frac_a * seg.src_duration,
                   src_duration=(frac_b - frac_a) * seg.src_duration,
                   out_theoretical=b - a, t_start=a,
                   trans_entra=seg.trans_entra if a <= ini + 1e-6 else None,
                   trans_sai=sai, avisos=[])
    return novo, t - a


def _preparar(project, lado: int):
    from .projects import sources_for

    if not project.plan.active_clips:
        raise RuntimeError("o vídeo ainda não tem blocos — rode a edição antes")
    plan = _plano_de_conferencia(project, lado)
    sources = sources_for(project)
    main = sources["main"]["info"]
    fps = R.fps_de_saida(main, plan.export)
    tl = Timeline(plan.active_clips, fps)
    segs = R.plan_segments(plan, tl, sources, main)
    cues = _cues(project, plan, tl, main)
    media_paths = {k: v["path"] for k, v in sources.items()}
    return plan, main, fps, tl, segs, cues, media_paths


def _trecho_em(segs, t: float):
    if not segs:
        raise RuntimeError("nada para renderizar")
    t = max(0.0, t)
    for s in segs:
        if s.t_start - 1e-6 <= t < s.t_start + s.out_theoretical:
            return s
    return segs[-1]


def quadros(project, tempos: list[float], lado: int = LADO_DO_QUADRO) -> list[dict]:
    """Os quadros EXATOS do vídeo final nos instantes pedidos (JPEG).

    Devolve [{"t", "jpeg": bytes, "avisos"}]. Cada quadro sai do mesmo comando
    do encode, com gráficos, camadas, transições, look e legenda.
    """
    plan, main, fps, tl, segs, cues, media_paths = _preparar(project, lado)
    pasta = project.dir / "work" / "quadros"
    ass_dir = pasta / "ass"
    ass_dir.mkdir(parents=True, exist_ok=True)
    fim = tl.duration
    out = []
    for i, t in enumerate(list(tempos)[:MAX_QUADROS]):
        t = max(0.0, min(float(t), max(0.0, fim - 1.0 / max(fps, 1.0))))
        seg = _trecho_em(segs, t)
        pedaco, desloc = _pedaco(seg, t, fps)
        pedaco.index = next(_numero)
        recorte = None
        janelas = R._janelas_do_recorte(pedaco, plan)
        if janelas and RC.pronto():
            recorte = R._preparar_recorte(pedaco, plan, main, cues, ass_dir,
                                          media_paths, None, pasta / "recortes",
                                          janelas, None)
        args, _ = R._build_video_command(pedaco, plan, main, cues, ass_dir,
                                         media_paths, None, recorte=recorte)
        corte = args.index("[vout]") + 1
        destino = pasta / f"quadro_{pedaco.index}.jpg"
        # o último quadro do pedaço começa um quadro ANTES do fim dele: pedir
        # o instante da emenda (t no último quadro do bloco) mandava o -ss
        # para depois do último quadro, e o ffmpeg saía sem erro e sem foto
        # (visto com o Claude de verdade, pedindo o quadro de 10.00 s)
        ultimo = max(0.0, (math.ceil(pedaco.out_theoretical * fps - 1e-6) - 1) / fps)
        r = None
        for ss in dict.fromkeys((min(max(0.0, desloc), ultimo), max(0.0, ultimo - 1.0 / fps), 0.0)):
            cmd = (args[:corte]
                   + ["-ss", f"{ss:.4f}", "-frames:v", "1",
                      "-q:v", "3", "-f", "image2", "-c:v", "mjpeg", str(destino)])
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0 and destino.exists():
                break
        if r is None or r.returncode != 0 or not destino.exists():
            raise RuntimeError(f"o quadro de {t:.2f} s não saiu: {(r.stderr if r else '')[-300:]}")
        avisos = list(pedaco.avisos)
        if janelas and not RC.pronto():
            avisos.append("o recorte da pessoa não está instalado: camadas e "
                          "gráficos atrás da pessoa não aparecem neste quadro")
        out.append({"t": round(t, 3), "jpeg": destino.read_bytes(), "avisos": avisos})
        destino.unlink(missing_ok=True)
    return out


def quadros_b64(project, tempos: list[float], lado: int = LADO_DO_QUADRO) -> list[dict]:
    return [{"t": q["t"], "avisos": q["avisos"],
             "jpeg_b64": base64.b64encode(q["jpeg"]).decode("ascii")}
            for q in quadros(project, tempos, lado)]


# ------------------------------------------------------------------- cena
def cena(project, t: float) -> dict:
    """O que há no quadro em ``t``: pessoa, espaço livre, luz, fala, bloco."""
    lado = 540
    plan, main, fps, tl, segs, cues, media_paths = _preparar(project, lado)
    t = max(0.0, min(float(t), max(0.0, tl.duration - 1.0 / max(fps, 1.0))))
    seg = _trecho_em(segs, t)
    pedaco, desloc = _pedaco(seg, t, fps)
    pedaco.index = next(_numero)
    W, H = R.target_size(main, plan.export)
    mw, mh = RC.tamanho_do_recorte(W, H)
    ass_dir = project.dir / "work" / "quadros" / "ass"
    ass_dir.mkdir(parents=True, exist_ok=True)
    cmd, _ = R._build_video_command(pedaco, plan, main, cues, ass_dir,
                                    media_paths, None, ate_a_base=(mw, mh))
    r = subprocess.run(cmd, capture_output=True)
    tam = mw * mh * 3
    n = len(r.stdout) // tam
    if r.returncode != 0 or n == 0:
        raise RuntimeError("não consegui ler o quadro da cena")
    k = min(n - 1, int(round(desloc * fps)))
    quadros_rgb = np.frombuffer(r.stdout[:n * tam], np.uint8).reshape(n, mh, mw, 3)
    alvo = quadros_rgb[k].astype(np.float32)
    luz = float((0.2126 * alvo[..., 0] + 0.7152 * alvo[..., 1]
                 + 0.0722 * alvo[..., 2]).mean() / 255.0)

    fala = [f for f in _frases(palavras_na_saida(project, tl))
            if f["fim"] >= t - 1.5 and f["inicio"] <= t + 1.5]
    info = {
        "t": round(t, 3),
        "bloco": seg.clip_id,
        "e_broll": seg.kind == "cutaway",
        "luz": round(luz, 3),
        "fala": fala,
        "pessoa": None,
    }
    if not RC.pronto():
        info["aviso"] = ("o recorte da pessoa não está instalado; sem ele não "
                         "sei onde a pessoa está")
        return info
    alfa = RC.alfas(quadros_rgb[: k + 1])[-1]
    cobre = float(alfa.mean())
    if cobre < 0.01:
        info["pessoa"] = {"presente": False}
        info["livre"] = ["o quadro inteiro"]
        return info
    ys, xs = np.nonzero(alfa > 0.5)
    caixa = ([round(float(xs.min()) / mw, 3), round(float(ys.min()) / mh, 3),
              round(float(xs.max()) / mw, 3), round(float(ys.max()) / mh, 3)]
             if len(xs) else [0.0, 0.0, 0.0, 0.0])
    peso = alfa.sum()
    cx = float((alfa * np.arange(mw)[None, :]).sum() / peso / mw)
    cy = float((alfa * np.arange(mh)[:, None]).sum() / peso / mh)
    # a CABEÇA: o topo da silhueta, as primeiras linhas com pessoa
    topo = caixa[1]
    grade = []
    livres = []
    nomes_l = ("alto", "meio", "baixo")
    nomes_c = ("esquerda", "centro", "direita")
    for i in range(3):
        linha = []
        for j in range(3):
            bloco = alfa[i * mh // 3:(i + 1) * mh // 3, j * mw // 3:(j + 1) * mw // 3]
            v = round(float(bloco.mean()), 2)
            linha.append(v)
            if v < 0.08:
                livres.append(f"{nomes_l[i]} {nomes_c[j]}")
        grade.append(linha)
    info["pessoa"] = {
        "presente": True,
        "ocupa": round(cobre, 3),
        "caixa": caixa,                 # x0, y0, x1, y1 em fração do quadro
        "centro": [round(cx, 3), round(cy, 3)],
        "topo_da_cabeca": topo,
        "grade_3x3": grade,             # fração de pessoa em cada terço
    }
    info["livre"] = livres
    return info


# -------------------------------------------------------------- mudanças
def _duracao(project) -> float:
    from .projects import duracao_de_saida

    return float(duracao_de_saida(project))


def _achar(lista, iid: str):
    return next((x for x in lista if x.id == iid), None)


def _conferir_grafico(project, novo: dict) -> dict:
    """O nome da marca na grafia exata, e o logo pedido tem de existir."""
    kit = MK.do_projeto(project)
    for k in ("texto", "subtexto", "prefixo", "sufixo"):
        if novo.get(k):
            novo[k] = MK.corrigir_grafia(novo[k], kit)
    novo["itens"] = [({**it, "texto": MK.corrigir_grafia(it.get("texto", ""), kit)}
                      if isinstance(it, dict) else MK.corrigir_grafia(it, kit))
                     for it in novo.get("itens") or []]
    if novo.get("tipo") == "logo":
        disponiveis = sorted(MK.logos(kit))
        if novo.get("logo") not in disponiveis:
            raise KeyError(f"o logo '{novo.get('logo') or '(vazio)'}' não existe — os que "
                           f"existem: {', '.join(disponiveis) or 'nenhum (ponha um PNG na aba Pós)'}")
    return novo


def por_grafico(project, dados: dict, gid: str | None = None) -> Grafico:
    """Cria (sem ``gid``) ou atualiza um gráfico. Não grava."""
    plan = project.plan
    if gid:
        g = _achar(plan.graficos, gid)
        if g is None:
            raise KeyError(f"gráfico {gid} não existe")
        base = {**g.to_dict(), **{k: v for k, v in dados.items() if v is not None}}
        novo = _conferir_grafico(project, MG.normalizar(base, _duracao(project)))
        for k, v in novo.items():
            if k != "id":
                setattr(g, k, v)
        return g
    novo = _conferir_grafico(project, MG.normalizar(dados, _duracao(project)))
    novo.pop("id", None)
    g = Grafico(**novo)
    plan.graficos.append(g)
    return g


def por_camada(project, dados: dict, cid: str | None = None) -> Camada:
    plan = project.plan
    if cid:
        c = _achar(plan.camadas, cid)
        if c is None:
            raise KeyError(f"camada {cid} não existe")
        base = {**c.to_dict(), **{k: v for k, v in dados.items() if v is not None}}
        for k, v in CM.normalizar(base, _duracao(project)).items():
            if k != "id":
                setattr(c, k, v)
        return c
    novo = CM.normalizar(dados, _duracao(project))
    novo.pop("id", None)
    c = Camada(**novo)
    plan.camadas.append(c)
    return c


def por_cena(project, dados: dict, cid: str | None = None) -> Cena:
    """Cria (sem ``cid``) ou atualiza uma cena (moldura, camadas de vidro)."""
    plan = project.plan
    kit = MK.do_projeto(project)
    if cid:
        c = _achar(plan.cenas, cid)
        if c is None:
            raise KeyError(f"cena {cid} não existe")
        base = {**c.to_dict(), **{k: v for k, v in dados.items() if v is not None}}
        novo = CN.normalizar(base, _duracao(project))
    else:
        novo = CN.normalizar(dados, _duracao(project))
    faltam = [n for n in novo["logos"] if n not in MK.logos(kit)]
    if faltam:
        raise KeyError(f"logo(s) que não existem: {', '.join(faltam)} — os que existem: "
                       f"{', '.join(sorted(MK.logos(kit))) or 'nenhum'}")
    # uma cena por vez: a nova não pode se sobrepor a outra
    for outra in plan.cenas:
        if outra.id != cid and outra.enabled and \
                novo["out_start"] < outra.out_end and outra.out_start < novo["out_end"]:
            raise KeyError(f"já existe a cena {outra.id} ({outra.tipo}) de "
                           f"{outra.out_start:.2f} a {outra.out_end:.2f} s — uma cena por vez")
    if cid:
        for k, v in novo.items():
            if k != "id":
                setattr(c, k, v)
        return c
    novo.pop("id", None)
    c = Cena(**novo)
    plan.cenas.append(c)
    return c


def bloco_na_emenda(project, tempo: float) -> str:
    """O bloco que ENTRA na emenda mais perto de ``tempo`` (não o primeiro)."""
    tl = Timeline(project.plan.active_clips,
                  project.info.fps if project.info else None)
    melhor, dist = "", 1e9
    for placed in list(tl)[1:]:
        d = abs(placed.out_start - float(tempo))
        if d < dist:
            melhor, dist = placed.clip.id, d
    if not melhor:
        raise KeyError("o vídeo tem um bloco só: não há emenda para transição")
    return melhor


def por_transicao(project, dados: dict, xid: str | None = None) -> Transicao:
    plan = project.plan
    ids = {c.id for c in plan.active_clips}
    if dados.get("clip_id") in (None, "") and dados.get("tempo") is not None:
        dados = {**dados, "clip_id": bloco_na_emenda(project, float(dados["tempo"]))}
    if xid:
        x = _achar(plan.transicoes, xid)
        if x is None:
            raise KeyError(f"transição {xid} não existe")
        base = {**x.to_dict(), **{k: v for k, v in dados.items() if v is not None}}
        novo = TR.normalizar(base)
    else:
        novo = TR.normalizar(dados)
    if novo["clip_id"] not in ids:
        raise KeyError(f"o bloco {novo['clip_id'] or '(vazio)'} não está no vídeo")
    bloco = next(c for c in plan.active_clips if c.id == novo["clip_id"])
    novo["fonte"], novo["src_t"] = bloco.source, round(float(bloco.src_start), 4)
    # uma transição por emenda: a nova substitui a que já estava ali
    if xid:
        for k, v in novo.items():
            if k != "id":
                setattr(x, k, v)
        return x
    plan.transicoes = [t for t in plan.transicoes if t.clip_id != novo["clip_id"]]
    novo.pop("id", None)
    x = Transicao(**novo)
    plan.transicoes.append(x)
    return x


def tirar(project, ids: list[str] | None = None, tudo: bool = False,
          origem: str = "") -> int:
    """Tira itens da pós. ``tudo`` com ``origem`` tira só os daquela origem."""
    plan = project.plan
    alvo = set(ids or [])
    n = 0
    for nome in ("graficos", "camadas", "transicoes", "cenas"):
        antes = getattr(plan, nome)
        fica = [x for x in antes
                if not ((x.id in alvo)
                        or (tudo and (not origem or getattr(x, "origem", "") == origem)))]
        n += len(antes) - len(fica)
        setattr(plan, nome, fica)
    return n
