"""OS LOGOS 3D NO GANCHO — sempre, flutuando e passando por trás dele.

"Eu quero também o 3D da logo do Airbnb e Booking. Quero que sempre apareça
nos hooks, animado, flutuante, que esconde atrás de mim."

Com a copy do gancho no começo do vídeo, os logos escolhidos na primeira tela
(padrão: os logos de plataforma da biblioteca — os PNG que ele pôs, como
Airbnb e Booking) entram em 3D:

- nascem ATRÁS dele (pequenos, no meio, escondidos pelo corpo), saem para os
  lados — um à esquerda, outro à direita — e sobem um pouco;
- ficam flutuando (o próprio 3D balança e respira);
- no fim do gancho voltam para trás dele e somem.

Passar "por trás" usa o recorte da pessoa (o mesmo do texto atrás). Sem o
recorte, eles entram pelas bordas, na frente, e somem com um fade — nunca
passam por cima do rosto.

O render do logo é um só por logo (4,5 s, a duração máxima do gancho), em
cache GLOBAL: do segundo vídeo em diante é instantâneo. O logo da MARCA não
entra junto (a regra do kit: nunca logo de plataforma ao lado da marca) — o
logo de canto já sai de cena durante o gancho.
"""
from __future__ import annotations

import threading

from .config import DATA_DIR

CACHE = DATA_DIR / "cache-3d"
DURACAO_DO_RENDER = 4.5
ORIGEM = "gancho"
LADOS = (0.2, 0.8)
_trava = threading.Lock()
_pendentes: set[tuple[str, str]] = set()


def padrao() -> list[str]:
    """Sem escolha feita: os logos de plataforma da biblioteca (os PNG dele)."""
    from . import marca as MK

    return sorted(MK.logos_extras())[:2]


def janela(project) -> tuple[float, float] | None:
    g = next((g for g in project.plan.graficos
              if getattr(g, "origem", "") == ORIGEM and g.enabled), None)
    return (float(g.out_start), float(g.out_end)) if g else None


def _nome(logo: str) -> str:
    return f"Logo 3D · {logo}"


def _trajeto(lado: float, dur: float, atras: bool, atraso: float) -> list[dict]:
    """Os marcos da sobreposição, relativos ao começo do gancho."""
    sai, volta = min(dur * 0.35, 0.9), max(dur - 0.75, dur * 0.7)
    if atras:
        # de trás dele (no meio, pequeno) para o lado, e de volta para trás
        return [{"t": 0.0, "x": 0.5, "y": 0.5, "escala": 0.45},
                {"t": atraso, "x": 0.5, "y": 0.5, "escala": 0.45},
                {"t": atraso + sai, "x": lado, "y": 0.42, "escala": 1.0, "easing": "sai"},
                {"t": volta, "x": lado, "y": 0.4, "escala": 1.0},
                {"t": dur, "x": 0.5, "y": 0.46, "escala": 0.45, "easing": "entra"}]
    # sem recorte: entra pela borda do lado dele, na frente, e some no fade
    borda = -0.2 if lado < 0.5 else 1.2
    return [{"t": 0.0, "x": borda, "y": 0.42},
            {"t": atraso, "x": borda, "y": 0.42},
            {"t": atraso + sai, "x": lado, "y": 0.42, "easing": "sai"},
            {"t": dur, "x": lado, "y": 0.4}]


def pedidos(project) -> list[dict]:
    """Um pedido de render por logo do gancho (vazio se não há o que fazer)."""
    from . import blender_local as B
    from . import logo3d
    from . import marca as MK
    from .render import recorte

    jan = janela(project)
    if not jan:
        return []
    blender = bool(B.executavel())
    nomes = list(getattr(project.plan, "gancho_logos", None) or [])
    if not nomes:
        return []
    kit = MK.do_projeto(project)
    atras = recorte.pronto()
    ini, fim = jan
    dur = min(DURACAO_DO_RENDER, fim - ini)
    if dur < 1.0:
        return []
    out = []
    for k, logo in enumerate(nomes[:2]):
        caminho = MK.caminho_do_logo(kit, logo)
        if not caminho:
            continue
        try:
            cams = logo3d.camadas(caminho)
        except ValueError:
            continue
        cena = B.cena_de_logo(cams["camadas"], DURACAO_DO_RENDER)
        if not blender and not B.tem_pronto(cena, [CACHE]):
            continue            # sem Blender, só o logo que JÁ foi gerado
        lado = LADOS[k % 2] if len(nomes) > 1 else LADOS[1]
        out.append({"cena": cena, "inicio": ini, "janela": dur, "x": lado, "y": 0.42,
                    "tamanho": 0.3, "cache": str(CACHE), "camada": "atras" if atras else "",
                    "some": not atras, "trajeto": _trajeto(lado, dur, atras, 0.25 * k),
                    "nome": _nome(logo), "origem": ORIGEM, "substituir": True})
    return out


def aplicar(project) -> int:
    """Põe (ou refaz) os logos 3D do gancho. Idempotente e barato quando já
    está tudo no lugar (não relê PNG nenhum). Devolve quantos renders pediu."""
    from . import blender_local as B
    from . import projects as P
    from .jobs import get_queue

    jan = janela(project)
    nomes = list(getattr(project.plan, "gancho_logos", None) or [])[:2]
    queridos = {_nome(n) for n in nomes} if jan else set()
    atuais = [o for o in project.plan.overlays if o.origem == ORIGEM]
    sobra = [o for o in atuais if P.nome_da_midia(project, o.media_id) not in queridos]
    if sobra:
        # tirou o gancho, ou um logo da lista: o logo 3D dele sai junto
        ids = {o.id for o in sobra}
        project.plan.overlays = [o for o in project.plan.overlays if o.id not in ids]
        project.save_plan()
    if not queridos:
        return 0
    ini, fim = jan
    dur = min(DURACAO_DO_RENDER, fim - ini)
    feitos = {P.nome_da_midia(project, o.media_id) for o in project.plan.overlays
              if o.origem == ORIGEM and abs(o.out_start - ini) < 0.02
              and abs(o.out_end - (ini + dur)) < 0.02}
    if queridos <= feitos:
        return 0
    n = 0
    fila = get_queue()
    for dados in pedidos(project):
        if dados["nome"] in feitos:
            continue
        chave = (project.id, f"{dados['nome']}|{ini:.2f}|{dur:.2f}")
        with _trava:
            if chave in _pendentes:
                continue
            _pendentes.add(chave)

        def rodar(ctx, _d=dados, _c=chave, _pid=project.id):
            try:
                return B.trabalho(_pid, _d, ctx)
            finally:
                with _trava:
                    _pendentes.discard(_c)
        # o logo que JÁ FOI GERADO (em qualquer vídeo) entra na hora
        fila.submit("arte-3d", project.id, rodar,
                    paralelo=B.tem_pronto(dados["cena"], [CACHE]))
        n += 1
    return n


# ---------------------------------------------------------- a cada gravação
# O gancho muda por vários caminhos (primeira tela, clique único, aba Pós,
# Claude, MCP): quem manda é o plano gravado. Espera 1 s parado e confere.
_relogios: dict[str, threading.Timer] = {}


def plano_mudou(pid: str) -> None:
    import os

    if os.environ.get("SHARKCUT_GANCHO_3D", "1") == "0":
        return
    with _trava:
        velho = _relogios.pop(pid, None)
        if velho:
            velho.cancel()
        t = threading.Timer(1.0, _conferir, args=(pid,))
        t.daemon = True
        _relogios[pid] = t
        t.start()


def _conferir(pid: str) -> None:
    from . import projects as P

    with _trava:
        _relogios.pop(pid, None)
    try:
        aplicar(P.load(pid))
    except Exception:  # noqa: BLE001 — logo do gancho é enfeite: nunca derruba nada
        pass
