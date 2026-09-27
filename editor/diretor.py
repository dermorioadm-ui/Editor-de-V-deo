"""Direção editorial compartilhada: intenção, decupagem e conferência real.

O modelo decide; este módulo guarda o plano e exige evidências da versão
atual da montagem. Uma revisão vencida nunca aprova uma edição nova.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import time

PERFIS = ("editorial", "cinema", "dinamico")
COMPOSICOES = ("gancho", "comparacao", "passos", "prova", "fechamento")

GUIA = """
DIREÇÃO CRIATIVA — você responde pela edição inteira, não por enfeitar a tela.
1. Leia a transcrição inteira e a marca. Identifique público, ideia central,
   tensão, prova e ação desejada. Preserve ressalvas, negações e o significado.
   Não transforme uma hipótese em promessa nem invente números ou depoimentos.
2. Decida o ritmo por intenção: gancho direto, explicação com espaço para
   entender, prova com tempo de leitura, fechamento claro. Pausa expressiva
   fica; hesitação vazia sai. Não acelere toda a fala para simular energia.
3. Depois dos cortes, releia a montagem e use direcao(acao=planejar): informe
   objetivo, linguagem e momentos com inicio/fim, fala e intenção visual.
   Tempos sempre do vídeo FINAL. Se recortar de novo, atualize o plano.
4. Construa uma gramática visual: uma família tipográfica, a paleta da marca,
   hierarquia, alinhamento e movimento consistentes. Perfil editorial é
   preciso e contido; cinema dá mais respiro; dinamico usa contraste de ritmo.
   Preserve os ajustes explícitos do dono. Em modo pos, não mexa nos cortes.
5. Cada intervenção precisa esclarecer, provar ou conduzir o olhar. Se a fala
   já funciona sozinha, mantenha a pessoa em tela. Nunca uma cota de efeitos.
   Não repita o mesmo pop em todos os títulos. Use entrada=cinema para chegar
   com suavidade e entrada=linhas para revelar títulos em sequência.
   estilo=editorial dispensa contornos grossos: use sobre fundo escuro e
   estável, depois de conferir contraste. Sobre imagem variável, use vidro.
6. compor cria cenas coordenadas: gancho, comparacao, passos, prova e
   fechamento. Leia analisar_cena antes, respeite rosto/legenda e ajuste a
   composição ao espaço livre. Use grafico/cena para afinar o resultado.
   B-roll precisa mostrar algo específico da fala; não é preenchimento.
   Para cenas novas, consulte arte(acao=catalogo): o motor nativo compõe
   formas, tipografia, números, traços e grupos com movimentos independentes.
   Crie composições com intenção; os modelos são exemplos editáveis. Pode
   desenhar mecanismos, diagramas, infográficos, órbitas e morph de formas.
   Prefira cor=marca/texto e a fonte do kit. Não cubra a pessoa com um painel
   opaco. Coloque composição atrás da pessoa ou no espaço livre quando couber.
   arte_3d usa Blender LOCAL, quando disponível: geometria, texto extrudado,
   câmera e luzes. Consulte o job até terminar antes de conferir/exportar.
   Nunca diga que há tracking ou rotoscopia livre: esses recursos não existem.
7. CONFIRA de verdade: ver_quadros no início, meio e fim de cada momento
   planejado (direcao ler informa os tempos). Confira tipografia, contraste,
   rosto, legenda, continuidade e marca; corrija e confira novamente.
   direcao(acao=revisar, parecer=...) registra sua avaliação apenas quando
   os quadros da versão atual tiverem sido gerados. Não finja ter ouvido o
   áudio ou visto o movimento inteiro a partir de imagens estáticas.
8. Entregue um relatório curto: decisões e porquês, trechos trabalhados e
   limitações verificáveis. O programa exporta. Texto bonito sem ferramentas
   não constitui uma edição. Se nada precisa mudar, justifique a decisão.
""".strip()


def assinatura(project) -> str:
    from . import marca
    from .projects import regras_de_correcao
    plano = project.plan.to_dict()
    # Cues automáticos ganham novos IDs a cada prévia. Compare as entradas
    # editoriais, não esse cache derivado; alterações manuais continuam valendo.
    plano["subtitles"] = [{k: v for k, v in s.items() if k != "id"}
        for s in plano.get("subtitles", [])
        if s.get("edited") or s.get("start_off") or s.get("end_off")]
    dados = {"plano": plano, "marca": marca.do_projeto(project),
             "palavras": project.words, "fontes": project.fontes,
             "removidas": project.analysis.get("removed_word_ids", []),
             "correcoes": regras_de_correcao(project)}
    return hashlib.sha256(json.dumps(dados, sort_keys=True, ensure_ascii=False,
                                     default=str).encode()).hexdigest()


def ativo(project) -> bool:
    return bool(getattr(project.plan, "direcao", {}).get("ativa"))


def instrucoes(project) -> str:
    if not ativo(project):
        return ""
    perfil = project.plan.direcao.get("perfil", "editorial")
    return f"\n\n{GUIA}\nPERFIL ESCOLHIDO: {perfil}."


def _numero(v, nome: str) -> float:
    try:
        n = float(v)
    except (ValueError, TypeError):
        raise ValueError(f"{nome} precisa ser numérico") from None
    if not math.isfinite(n):
        raise ValueError(f"{nome} precisa ser finito")
    return n


def _duracao(project) -> float:
    from .projects import duracao_de_saida
    return duracao_de_saida(project)


def planejar(project, dados: dict) -> dict:
    dur = _duracao(project)
    objetivo = str(dados.get("objetivo") or "").strip()[:600]
    linguagem = str(dados.get("linguagem") or "").strip()[:600]
    brutos = dados.get("momentos")
    if not objetivo or not linguagem or not isinstance(brutos, list) or not 1 <= len(brutos) <= 40:
        raise ValueError("diga objetivo, linguagem visual e de 1 a 40 momentos")
    momentos = []
    for m in brutos:
        if not isinstance(m, dict):
            raise ValueError("cada momento deve ser um objeto")
        ini, fim = _numero(m.get("inicio"), "inicio"), _numero(m.get("fim"), "fim")
        if not 0 <= ini < fim <= dur + 0.05:
            raise ValueError(f"momento fora da montagem (0 a {dur:.2f} s)")
        fala, intencao = str(m.get("fala") or "").strip(), str(m.get("intencao") or "").strip()
        if not fala or not intencao:
            raise ValueError("cada momento precisa de fala e intenção editorial")
        momentos.append({"inicio": round(ini, 3), "fim": round(min(fim, dur), 3),
                         "fala": fala[:400], "intencao": intencao[:600]})
    momentos.sort(key=lambda m: m["inicio"])
    project.analysis["direcao"] = {"objetivo": objetivo, "linguagem": linguagem,
                                   "momentos": momentos, "quando": time.time(),
                                   "duracao": dur, "revisao": None}
    project.analysis.pop("direcao_quadros", None)
    project.save_analysis()
    return estado(project)


def tempos_de_revisao(project) -> list[float]:
    momentos = (project.analysis.get("direcao") or {}).get("momentos", [])
    tempos = []
    for m in momentos:
        ini, fim = m["inicio"], m["fim"]
        margem = min(0.3, (fim - ini) / 4)
        tempos += [ini + margem, (ini + fim) / 2, fim - margem]
    return sorted(set(round(t, 3) for t in tempos))


def registrar_quadros(project, tempos: list[float], renderizada: str | None = None) -> None:
    """Chamado só DEPOIS de a rota ter renderizado as imagens com sucesso."""
    if not project.analysis.get("direcao"):
        return
    sig = assinatura(project)
    if renderizada is not None and renderizada != sig:
        return  # O usuário mudou a montagem durante a renderização.
    antes = project.analysis.get("direcao_quadros") or {}
    vistos = antes.get("tempos", []) if antes.get("assinatura") == sig else []
    project.analysis["direcao_quadros"] = {"assinatura": sig,
        "tempos": sorted(set(vistos + [round(float(t), 3) for t in tempos]))[-240:]}
    project.save_analysis()


def estado(project) -> dict:
    from .jobs import get_queue
    pendentes = [j.id for j in get_queue().list(project.id)
                 if j.kind == "arte-3d" and j.status in ("fila", "rodando")]
    plano = project.analysis.get("direcao") or {}
    fotos = project.analysis.get("direcao_quadros") or {}
    sig = assinatura(project)
    vistos = fotos.get("tempos", []) if fotos.get("assinatura") == sig else []
    esperados = tempos_de_revisao(project)
    faltam = [t for t in esperados if not any(abs(t - v) <= 0.18 for v in vistos)]
    revisao = plano.get("revisao") or {}
    duracao_ok = abs(plano.get("duracao", -1) - _duracao(project)) <= 0.05
    return {**plano, "tempos_conferir": esperados, "faltam_quadros": faltam, "renders_pendentes": pendentes,
            "montagem_mudou": bool(plano) and not duracao_ok,
            "aprovada": bool(plano and duracao_ok and not faltam and not pendentes
                             and revisao.get("assinatura") == sig)}


def revisar(project, parecer: str) -> dict:
    est = estado(project)
    if est["renders_pendentes"]:
        raise ValueError("aguarde a arte 3D terminar antes de revisar: " + ", ".join(est["renders_pendentes"]))
    if not est.get("momentos"):
        raise ValueError("registre o plano com direcao planejar antes da revisão")
    if est["montagem_mudou"]:
        raise ValueError("a montagem mudou; atualize os tempos com direcao planejar")
    if est["faltam_quadros"]:
        raise ValueError(f"confira com ver_quadros estes tempos: {est['faltam_quadros'][:12]}")
    if len(parecer.strip()) < 20:
        raise ValueError("descreva o que conferiu e eventuais limitações (mínimo 20 caracteres)")
    project.analysis["direcao"]["revisao"] = {"parecer": parecer.strip()[:2000],
        "assinatura": assinatura(project), "quando": time.time(),
        "escopo": "quadros estáticos renderizados; movimento e áudio não certificados"}
    project.save_analysis()
    return estado(project)


def compor(project, dados: dict) -> dict:
    """Receitas coordenadas e atômicas, construídas com os gráficos do motor."""
    from . import pos_edicao as P
    tipo = str(dados.get("tipo") or "")
    if tipo not in COMPOSICOES:
        raise ValueError("composição desconhecida")
    ini, fim = _numero(dados.get("inicio"), "inicio"), _numero(dados.get("fim"), "fim")
    if not 0 <= ini < fim <= _duracao(project) + 0.05 or fim - ini < 1:
        raise ValueError("a composição precisa de ao menos 1 s dentro da montagem")
    titulo = str(dados.get("titulo") or "").strip()[:80]
    if not titulo:
        raise ValueError("diga um título curto, fiel à fala")
    x = _numero(dados.get("x", 0.5), "x")
    y = _numero(dados.get("y", 0.24), "y")
    if not 0.08 <= x <= 0.92 or not 0.08 <= y <= 0.7:
        raise ValueError("posição fora da área útil (x 0.08–0.92, y 0.08–0.7)")
    origem = dados.get("origem", "manual")
    estilo = dados.get("estilo", "limpo")
    base = {"out_start": ini, "out_end": fim, "x": x, "y": y,
            "estilo": estilo, "entrada": "cinema", "saida": "fade", "origem": origem}
    graficos = []
    if tipo in ("gancho", "fechamento"):
        graficos.append({**base, "tipo": "titulo", "texto": titulo,
                         "subtexto": str(dados.get("apoio") or "")[:100],
                         "tamanho": 1.2 if tipo == "gancho" else 1.0, "entrada": "linhas"})
    elif tipo == "prova":
        valor = _numero(dados.get("numero"), "numero")
        graficos.append({**base, "tipo": "numero", "texto": titulo, "numero": valor,
            "prefixo": str(dados.get("prefixo") or "")[:12],
            "sufixo": str(dados.get("sufixo") or "")[:12]})
    else:
        itens = dados.get("itens")
        if not isinstance(itens, list) or not 2 <= len(itens) <= (2 if tipo == "comparacao" else 5):
            raise ValueError("comparação exige 2 itens; passos exige de 2 a 5")
        normalizados = []
        for item in itens:
            if not isinstance(item, dict) or not str(item.get("texto") or "").strip():
                raise ValueError("cada item precisa de texto e em (atraso relativo à composição)")
            em = _numero(item.get("em"), "em")
            if not 0 <= em <= fim - ini - 0.5:
                raise ValueError("cada item precisa de pelo menos 0,5 s de leitura")
            normalizados.append({"texto": str(item["texto"])[:80], "em": em})
        rotulos = dados.get("rotulos") or []
        if not isinstance(rotulos, list):
            raise ValueError("rótulos precisam ser uma lista")
        graficos.append({**base, "tipo": "comparacao" if tipo == "comparacao" else "lista", "texto": titulo,
                         "itens": normalizados, "rotulos": [str(r)[:24] for r in rotulos[:2]],
                         "entrada": "cinema"})
    anterior = copy.deepcopy(project.plan)
    try:
        feitos = [P.por_grafico(project, g) for g in graficos]
        project.save_plan()
    except Exception:
        project.plan = anterior
        raise
    return {"tipo": tipo, "ids": [g.id for g in feitos],
            "inicio": ini, "fim": fim, "conferir": [ini + 0.3, (ini + fim) / 2, fim - 0.3]}
