"""B-ROLL AUTOMÁTICO: onde entra, quanto dura, o que mostrar, e põe sozinho.

ONDE e QUANTO: a IA (Gemini) quando há chave, lendo a fala com os tempos;
sem chave, a regra do programa (uma frase a cada N segundos, e as palavras
de conteúdo dela viram a busca). O QUÊ: primeiro a biblioteca local (o que
ele enviou ou já baixou, pelas palavras-chave), depois o banco grátis.

A fala nunca é tocada: b-roll é cobertura, troca só a imagem. O começo (o
gancho, que é o rosto) e o fim (a chamada, que também é o rosto) ficam
livres.

Os b-rolls postos aqui levam ``origem="auto"``: refazer tira só esses e
deixa os que ele pôs à mão.
"""
from __future__ import annotations

import re
import unicodedata

from . import banco

# segundos entre um b-roll e o próximo, em média
FREQUENCIAS = {"pouco": 20.0, "medio": 12.0, "muito": 7.0}
NOMES = {"pouco": "pouco (1 a cada ~20 s)", "medio": "médio (1 a cada ~12 s)",
         "muito": "muito (1 a cada ~7 s)"}
MIN_DUR, MAX_DUR, DUR_PADRAO = 2.0, 5.0, 3.5
LIVRE_NO_COMECO = 2.0
LIVRE_NO_FIM = 2.5


def _freq(frequencia: str) -> str:
    f = _sem_acento(str(frequencia or "")).lower()
    return f if f in FREQUENCIAS else "medio"


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def quantos(duracao: float, frequencia: str) -> int:
    util = max(0.0, duracao - LIVRE_NO_COMECO - LIVRE_NO_FIM)
    if util < MIN_DUR * 1.5:
        return 0
    return max(1, int(util // FREQUENCIAS[_freq(frequencia)]))


def _falas(project) -> list[dict]:
    return sorted(({"start": float(s.start), "end": float(s.end),
                    "text": str(s.text).replace("\n", " ").strip()}
                   for s in project.plan.subtitles if s.end > s.start),
                  key=lambda f: f["start"])


# ------------------------------------------------------------------ onde
def validar(slots: list[dict], duracao: float, maximo: int) -> list[dict]:
    """Ordena, prende nos limites e tira sobreposição e o que ficou curto."""
    fim_livre = duracao - LIVRE_NO_FIM
    saida: list[dict] = []
    for s in sorted(slots, key=lambda x: float(x.get("inicio") or 0.0)):
        a = max(LIVRE_NO_COMECO, float(s.get("inicio") or 0.0))
        b = min(fim_livre, float(s.get("fim") or a + DUR_PADRAO), a + MAX_DUR)
        if saida and a < saida[-1]["fim"] + 0.3:
            a = saida[-1]["fim"] + 0.3
        busca = " ".join(str(s.get("busca") or "").split())[:60]
        if b - a < MIN_DUR * 0.75 or not busca:
            continue
        saida.append({"inicio": round(a, 3), "fim": round(b, 3), "busca": busca,
                      "alternativas": list(s.get("alternativas") or [])[:3],
                      "porque": str(s.get("porque") or "")[:160]})
        if len(saida) >= maximo:
            break
    return saida


def pela_regra(falas: list[dict], duracao: float, frequencia: str,
               biblioteca: set[str] | None = None) -> list[dict]:
    """Sem IA: uma frase por intervalo; as palavras dela são a busca.

    Em cada intervalo (o tamanho vem da frequência), escolhe a frase que
    MELHOR se ilustra: a que tem vídeo na biblioteca dele (pelas palavras-
    chave) e a que tem uma coisa filmável — não a primeira que aparece. "Eu
    acredito que não" não tem o que mostrar; "na sua casa sem avisar" tem.
    """
    passo = FREQUENCIAS[_freq(frequencia)]
    biblioteca = biblioteca or set()
    ultima_possivel = duracao - LIVRE_NO_FIM - MIN_DUR

    def nota(f: dict, termos: list[str]) -> float:
        na_bib = any(_palavras(t) & biblioteca for t in termos)
        coisa = bool(termos) and not banco._parece_verbo(termos[0])
        return 20 * na_bib + 5 * coisa - 0.1 * f["start"]

    slots: list[dict] = []
    cursor = LIVRE_NO_COMECO
    while True:
        janela = [f for f in falas
                  if cursor - 0.01 <= f["start"] < cursor + passo
                  and f["start"] <= ultima_possivel]
        if not janela:
            depois = [f for f in falas if cursor + passo <= f["start"] <= ultima_possivel]
            if not depois:
                break
            cursor = depois[0]["start"]
            continue
        opcoes = [(f, banco.sugerir_termos(f["text"], 4)) for f in janela]
        opcoes = [(f, t) for f, t in opcoes if t]
        if not opcoes:
            cursor += passo
            continue
        f, termos = max(opcoes, key=lambda ft: nota(*ft))
        # a busca é a palavra que a biblioteca tem, se tiver; senão a primeira
        termos = sorted(termos, key=lambda t: not (_palavras(t) & biblioteca))
        dur = min(max(f["end"] - f["start"], MIN_DUR), DUR_PADRAO + 0.5)
        slots.append({"inicio": f["start"], "fim": f["start"] + dur,
                      "busca": termos[0], "alternativas": termos[1:],
                      "porque": f"fala: “{f['text'][:80]}”"})
        cursor = f["start"] + passo
    return validar(slots, duracao, quantos(duracao, frequencia))


INSTRUCAO = """Você edita vídeos de anúncio falados em português (uma pessoa \
falando para a câmera). Sua tarefa é escolher os momentos em que entra \
B-ROLL: um vídeo ilustrativo que cobre a imagem enquanto a voz continua.

Regras:
- B-roll ilustra o que está sendo DITO naquele instante: um objeto, lugar, \
ação ou situação concreta. Nunca cubra a promessa principal, o preço ou a \
chamada para ação: ali o rosto vende.
- Não use os primeiros 2 segundos (o gancho) nem os últimos 3 segundos.
- Cada b-roll dura de 2 a 5 segundos, sem se sobrepor a outro.
- "busca" são 1 a 3 palavras em português, CONCRETAS e FILMÁVEIS, do jeito \
que alguém procuraria num banco de vídeos (ex.: "casa de praia", \
"ladrão arrombando porta", "celular na mão"). Nada abstrato ("segurança", \
"sucesso"). Dê também 1 ou 2 "alternativas" mais genéricas.
- Espalhe os b-rolls pelo vídeo inteiro.
Responda só o JSON do esquema."""

ESQUEMA = {
    "type": "object",
    "properties": {
        "brolls": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "inicio": {"type": "number"},
                    "fim": {"type": "number"},
                    "busca": {"type": "string"},
                    "alternativas": {"type": "array", "items": {"type": "string"}},
                    "porque": {"type": "string"},
                },
                "required": ["inicio", "fim", "busca"],
            },
        },
    },
    "required": ["brolls"],
}


def pedido_para_ia(falas: list[dict], duracao: float, n: int) -> str:
    linhas = [f"Duração do vídeo: {duracao:.1f} s. Quero cerca de {n} b-roll(s).",
              "", "A fala, frase a frase, com os tempos em segundos:"]
    for f in falas[:400]:
        linhas.append(f"[{f['start']:.1f}–{f['end']:.1f}] {f['text']}")
    return "\n".join(linhas)


def pela_ia(chave: str, modelo: str, falas: list[dict], duracao: float,
            frequencia: str) -> list[dict]:
    from .ai import gemini

    n = quantos(duracao, frequencia)
    if not n or not falas:
        return []
    escolhido = gemini.escolher_modelo(chave, modelo)
    resposta = gemini.gerar_json(chave, escolhido["id"], INSTRUCAO,
                                 pedido_para_ia(falas, duracao, n), ESQUEMA,
                                 temperatura=0.3,
                                 maximo=min(escolhido.get("saida") or 4096, 4096))
    return validar(list(resposta.get("brolls") or []), duracao, int(n * 1.5) + 1)


def palavras_da_biblioteca() -> set[str]:
    return set().union(*[_palavras(" ".join(str(b.get(k) or "") for k in
                                            ("termo", "descricao", "nome")))
                         for b in banco.baixados()] or [set()])


def planejar(project, frequencia: str, usar_ia: bool = True) -> dict:
    """Os lugares e as buscas — ainda sem vídeo nenhum."""
    from . import db
    from .ai.gemini import chave_guardada
    from .projects import duracao_de_saida

    duracao = duracao_de_saida(project)
    falas = _falas(project)
    aviso = ""
    if usar_ia and chave_guardada():
        try:
            slots = pela_ia(chave_guardada(), db.get_setting("gemini_model", "") or "",
                            falas, duracao, frequencia)
            if slots:
                return {"slots": slots, "quem": "ia", "aviso": ""}
            aviso = "a IA não sugeriu nenhum ponto; usei a regra do programa"
        except Exception as exc:  # noqa: BLE001 — sem IA, a regra decide
            aviso = f"a IA não respondeu ({exc}); usei a regra do programa"
    return {"slots": pela_regra(falas, duracao, frequencia, palavras_da_biblioteca()),
            "quem": "regra", "aviso": aviso}


# ------------------------------------------------------------------ o quê
def _palavras(texto: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", _sem_acento(str(texto or "")).lower())
            if len(w) >= 3}


def da_biblioteca(buscas: list[str], usados: set[str],
                  precisa: float) -> dict | None:
    """Um vídeo da biblioteca local cujas palavras-chave casam com a busca."""
    melhor = None
    for item in banco.baixados():
        if item["path"] in usados:
            continue
        chaves = _palavras(" ".join(str(item.get(k) or "") for k in
                                    ("termo", "descricao", "nome", "arquivo")))
        for peso, busca in enumerate(buscas):
            comum = _palavras(busca) & chaves
            if not comum:
                continue
            nota = (len(comum) * 10 - peso * 3
                    + (5 if float(item.get("duracao") or 0) >= precisa else 0))
            if melhor is None or nota > melhor[0]:
                melhor = (nota, item)
    return melhor[1] if melhor else None


def do_banco(buscas: list[str], orientacao: str, usados_ids: set[str],
             precisa: float, alvo_w: int, alvo_h: int, cancelado=None) -> dict | None:
    """Busca no banco grátis e baixa o primeiro que serve (e não foi usado)."""
    if not banco.estado().get("alguma"):
        return None
    for busca in buscas:
        try:
            res = banco.buscar(busca, orientacao, 1, 15)
        except banco.ErroDoBanco:
            continue
        itens = [it for it in res.get("itens") or [] if it["id"] not in usados_ids]
        if not itens:
            continue
        itens.sort(key=lambda it: float(it.get("duracao") or 0) < precisa)
        try:
            return banco.baixar(itens[0]["id"], alvo_w, alvo_h, busca,
                                cancelado=cancelado)
        except banco.ErroDoBanco:
            continue
    return None


# ----------------------------------------------------------------- aplicar
def tirar_automaticos(project) -> int:
    antes = len(project.plan.cutaways)
    project.plan.cutaways = [c for c in project.plan.cutaways
                             if getattr(c, "origem", "") != "auto"]
    return antes - len(project.plan.cutaways)


def aplicar(project, ctx, frequencia: str = "medio", usar_ia: bool = True,
            substituir: bool = True) -> dict:
    """Planeja, acha os vídeos e põe como b-roll. Mexe no ``project`` dado.

    Mexe no objeto recebido (e grava): o clique único segue com ele para a
    prévia, e recarregar do banco aqui faria a prévia sair sem os b-rolls.
    """
    from . import anexos
    from .models import Cutaway
    from .projects import add_media, duracao_de_saida, list_media, timeline_summary

    frequencia = _freq(frequencia)
    tirados = tirar_automaticos(project) if substituir else 0
    ctx.stage("broll", "escolhendo onde entra b-roll")
    plano = planejar(project, frequencia, usar_ia)
    slots = plano["slots"]
    limite = duracao_de_saida(project)
    try:
        w, h = (int(x) for x in project.info.display_size)
    except Exception:  # noqa: BLE001
        w, h = 1080, 1920
    orientacao = banco.orientacao_de(w, h)
    midias = {m["path"]: m for m in list_media(project.id)}
    por_id = {m["id"]: m for m in midias.values()}
    usados = {por_id[c.media_id]["path"] for c in project.plan.cutaways
              if c.media_id in por_id}
    usados_ids: set[str] = set()
    fotos = [(float(b["out_start"]), float(b["out_end"]))
             for b in timeline_summary(project).get("blocks", [])
             if b.get("kind") == "photo"]
    postos: list[dict] = []
    pulados: list[dict] = []
    for k, slot in enumerate(slots):
        ctx.progress(0.1 + 0.85 * k / max(1, len(slots)),
                     f"b-roll {k + 1} de {len(slots)}: {slot['busca']}", "broll")
        if getattr(ctx, "cancelled", None) and ctx.cancelled():
            break
        a, b = slot["inicio"], slot["fim"]
        if any(min(b, fb) - max(a, fa) > 0.02 for fa, fb in fotos):
            pulados.append({**slot, "motivo": "em cima de uma foto inserida"})
            continue
        if any(min(b, c.out_end) - max(a, c.out_start) > 0.02
               for c in project.plan.cutaways):
            pulados.append({**slot, "motivo": "já tem b-roll aí"})
            continue
        buscas = [slot["busca"], *slot.get("alternativas", [])]
        item = da_biblioteca(buscas, usados, b - a)
        origem_video = "biblioteca"
        if item is None:
            item = do_banco(buscas, orientacao, usados_ids, b - a, w, h,
                            getattr(ctx, "cancelled", None))
            origem_video = "banco"
        if item is None:
            pulados.append({**slot, "motivo": (
                f"nem a biblioteca nem o banco acharam “{slot['busca']}”"
                if banco.estado().get("alguma") else
                f"nada na biblioteca com “{slot['busca']}”, e falta a chave "
                f"grátis do banco (Pexels ou Pixabay)")})
            continue
        caminho = item["path"]
        midia = midias.get(caminho)
        if midia is None:
            try:
                midia = add_media(project.id, caminho, "video", papel="anexo")
            except Exception as exc:  # noqa: BLE001
                pulados.append({**slot, "motivo": f"não li o vídeo ({exc})"})
                continue
            midias[caminho] = midia
        dur_midia = float((midia.get("info") or {}).get("duration") or 0.0)
        # começa um pouco para dentro: vídeo de banco costuma abrir parado
        entrada = max(0.0, min(1.0, (dur_midia - (b - a)) / 2)) if dur_midia else 0.0
        try:
            j = anexos.encaixar(midia, a, b, entrada, 1.0, limite=limite)
            anexos.sem_sobreposicao(project.plan.cutaways, j.out_start, j.out_end)
        except anexos.AnexoInvalido as exc:
            pulados.append({**slot, "motivo": str(exc)})
            continue
        corte = Cutaway(media_id=midia["id"], out_start=j.out_start,
                        out_end=j.out_end, media_start=j.media_start,
                        speed=j.speed, origem="auto", termo=slot["busca"])
        project.plan.cutaways.append(corte)
        usados.add(caminho)
        if item.get("id"):
            usados_ids.add(str(item["id"]))
        postos.append({**corte.to_dict(), "busca": slot["busca"],
                       "de": origem_video, "autor": item.get("autor", ""),
                       "fonte": item.get("fonte", ""), "porque": slot.get("porque", "")})
    resumo = {"frequencia": frequencia, "quem": plano["quem"], "aviso": plano["aviso"],
              "planejados": len(slots), "postos": postos, "pulados": pulados,
              "tirados": tirados}
    project.plan.broll = {**(project.plan.broll or {}), "frequencia": frequencia,
                          "ultima": {k: v for k, v in resumo.items()
                                     if k not in ("postos", "pulados")}
                          | {"postos": len(postos), "pulados": len(pulados)}}
    project.save_plan()
    ctx.progress(1.0, f"{len(postos)} b-roll(s) no vídeo"
                      + (f", {len(pulados)} ponto(s) sem vídeo" if pulados else ""),
                 "broll")
    return resumo
