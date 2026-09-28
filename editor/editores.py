"""Seleção de diretor por etapa, compatível com os projetos anteriores."""
from __future__ import annotations


def acabamento(plan) -> str:
    escolha = getattr(plan, "pos_editor", None)
    return escolha if escolha is not None else ("claude" if plan.pos_claude else "")


def executar(pid, ctx, provedor: str, modo: str, pedido: str = "") -> dict:
    from . import claude_editor, codex_editor, diretor, projects as svc
    if provedor not in ("claude", "codex"):
        raise ValueError("escolha Claude ou Codex")
    modulo = codex_editor if provedor == "codex" else claude_editor
    p = svc.load(pid)
    if diretor.ativo(p):
        # Uma conferência antiga não aprova automaticamente uma nova execução.
        p.analysis.pop("direcao_quadros", None)
        if p.analysis.get("direcao"):
            p.analysis["direcao"]["revisao"] = None
        p.save_analysis()
    resultado = modulo.editar(pid, ctx, retoque=pedido, modo=modo)
    p = svc.load(pid)
    if resultado.get("ok") and diretor.ativo(p):
        qualidade = diretor.estado(p)
        resultado["qualidade"] = qualidade
        if not qualidade["aprovada"]:
            # A EDIÇÃO ACONTECEU: o que a IA pôs está no plano. Faltar o
            # registro da conferência dos quadros é aviso, não erro — antes
            # isto derrubava o trabalho inteiro, a prévia e o vídeo final não
            # saíam, e ele ficava sem ter onde "disparar" a correção.
            resultado["aviso"] = ("a IA terminou sem registrar a conferência de todos os "
                                  "quadros — confira na prévia")
    resultado["provedor"] = provedor
    p.analysis["diretor_edicao"] = resultado
    p.save_analysis()
    return resultado


def pipeline(pid, ctx, fontes_extras: list[str]) -> dict:
    from . import projects as svc
    p = svc.load(pid)
    inicial, pos = p.plan.editor, acabamento(p.plan)
    agente = inicial in ("claude", "codex")
    res = svc._scoped(ctx, 0.0, 0.55, lambda c: svc.one_click(
        svc.load(pid), c, fontes_extras=fontes_extras,
        para_o_claude=agente, sem_previa=True))
    etapas = []
    if agente:
        etapas.append((inicial, "completo" if pos == inicial else "edicao"))
    if pos and pos != inicial:
        etapas.append((pos, "pos"))
    res["diretores"] = []
    for i, (provedor, modo) in enumerate(etapas):
        lo, hi = 0.55 + 0.4 * i / len(etapas), 0.55 + 0.4 * (i + 1) / len(etapas)
        r = svc._scoped(ctx, lo, hi, lambda c: executar(pid, c, provedor, modo))
        res["diretores"].append(r)
        if not r.get("ok"):
            raise RuntimeError(f"{provedor.title()} ({modo}): {r.get('erro', 'etapa incompleta')}")
    return res
