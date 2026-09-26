"""B-ROLL AUTOMÁTICO: onde entra, quanto dura, o que mostrar, e põe sozinho.

ONDE e QUANTO: a IA (Gemini) quando há chave, lendo a fala com os tempos;
sem chave, a regra do programa (uma frase a cada N segundos, e as palavras
de conteúdo dela viram a busca). O QUÊ: por padrão o BANCO GRÁTIS (Pexels e
Pixabay, com as chaves que ele cadastrou), sempre preferindo vídeo que ainda
não foi usado em outro anúncio; a biblioteca local entra quando o banco não
responde. Com a fonte "biblioteca", a ordem se inverte: primeiro os vídeos
dele, pelas palavras-chave, e o banco só completa.

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
# de onde vem o vídeo de cada b-roll: o banco grátis primeiro (o padrão) ou a
# biblioteca dele primeiro
FONTES = ("banco", "biblioteca")


def _fonte(fonte: str | None) -> str:
    return fonte if fonte in FONTES else "banco"


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
        en = [" ".join(str(x or "").split())[:60] for x in (s.get("buscas_en") or [])]
        if s.get("busca_en"):
            en.insert(0, " ".join(str(s["busca_en"]).split())[:60])
        en = list(dict.fromkeys(x for x in en if x))[:4]
        saida.append({"inicio": round(a, 3), "fim": round(b, 3), "busca": busca,
                      "busca_en": en[0] if en else "", "buscas_en": en,
                      "cena": str(s.get("cena") or "")[:200],
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
falando para a câmera) e escolhe o B-ROLL: vídeos ilustrativos de banco de \
imagem (Pexels, Pixabay) que cobrem a imagem enquanto a voz continua.

PRIMEIRO leia o vídeo INTEIRO e entenda o contexto:
- "tema": em uma frase, do que o vídeo fala e o que ele vende.
- "cenario": o universo VISUAL desse tema — lugares, objetos, pessoas e \
situações que aparecem nesse assunto (ex.: num vídeo sobre segurança de \
Airbnb: "apartamento de temporada, anfitrião entregando chaves, fechadura \
eletrônica, câmera de segurança, hóspede com mala").

DEPOIS escolha os momentos do b-roll. Regras:
- Cada b-roll ilustra o que está sendo dito naquele instante, SEMPRE dentro \
do tema e do cenário do vídeo. Uma palavra solta da frase não é busca: \
"essas pessoas invadiram" num vídeo de Airbnb é "burglar entering vacation \
rental apartment", não "people".
- Nunca cubra a promessa principal, o preço ou a chamada para ação: ali o \
rosto vende. Não use os primeiros 2 segundos nem os últimos 3.
- Cada b-roll dura de 2 a 5 segundos, sem se sobrepor a outro, espalhados \
pelo vídeo inteiro.
- "cena": em português, o que o plano ideal MOSTRA (quem, onde, fazendo o \
quê), em uma frase.
- "buscas_en": 2 ou 3 buscas EM INGLÊS de banco de vídeo, de 2 a 5 palavras \
cada, da mais específica para a mais ampla, todas dentro do cenário \
(ex.: "airbnb host handing keys", "vacation rental front door", \
"smart lock door"). Nada abstrato ("security", "success"), nada de uma \
palavra só.
- "busca": a busca principal em português (2 a 4 palavras).
Responda só o JSON do esquema."""

ESQUEMA = {
    "type": "OBJECT",
    "properties": {
        "tema": {"type": "STRING"},
        "cenario": {"type": "STRING"},
        "brolls": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "inicio": {"type": "NUMBER"},
                    "fim": {"type": "NUMBER"},
                    "cena": {"type": "STRING"},
                    "buscas_en": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "busca": {"type": "STRING"},
                    "porque": {"type": "STRING"},
                },
                "required": ["inicio", "fim", "cena", "buscas_en", "busca"],
                "propertyOrdering": ["inicio", "fim", "cena", "buscas_en", "busca",
                                     "porque"],
            },
        },
    },
    "required": ["tema", "cenario", "brolls"],
    "propertyOrdering": ["tema", "cenario", "brolls"],
}


def pedido_para_ia(falas: list[dict], duracao: float, n: int,
                   assunto: str = "") -> str:
    linhas = [f"Duração do vídeo: {duracao:.1f} s. Quero cerca de {n} b-roll(s)."]
    if assunto:
        linhas.append(f"O dono do vídeo disse que o assunto é: {assunto}")
    linhas += ["", "A fala inteira, frase a frase, com os tempos em segundos:"]
    for f in falas[:400]:
        linhas.append(f"[{f['start']:.1f}–{f['end']:.1f}] {f['text']}")
    return "\n".join(linhas)


def pela_ia(chave: str, modelo: str, falas: list[dict], duracao: float,
            frequencia: str, assunto: str = "") -> dict:
    """O plano da IA: o contexto do vídeo e, dentro dele, cada b-roll."""
    from .ai import gemini

    n = quantos(duracao, frequencia)
    if not n or not falas:
        return {"slots": [], "tema": "", "cenario": ""}
    escolhido = gemini.escolher_modelo(chave, modelo)
    resposta = gemini.gerar_json(chave, escolhido["id"], INSTRUCAO,
                                 pedido_para_ia(falas, duracao, n, assunto), ESQUEMA,
                                 temperatura=0.3,
                                 maximo=min(escolhido.get("saida") or 8192, 8192))
    return {"slots": validar(list(resposta.get("brolls") or []), duracao,
                             int(n * 1.5) + 1),
            "tema": str(resposta.get("tema") or "")[:300],
            "cenario": str(resposta.get("cenario") or "")[:400],
            "modelo": escolhido["id"]}


# A IA OLHA ANTES DE ESCOLHER. A busca de banco de imagem devolve de tudo —
# "front door" traz porta de igreja, de carro, de desenho animado. Ler o
# título não basta; é olhando o quadro que se vê se ele mostra a cena.
INSTRUCAO_ESCOLHA = """Você escolhe b-roll para um anúncio em vídeo. Para cada \
b-roll há uma CENA desejada e algumas opções, cada uma um quadro de um vídeo \
de banco de imagem. Escolha a opção que MELHOR mostra a cena e combina com o \
tema do vídeo. Se nenhuma combina de verdade (assunto errado, país/época \
estranhos, desenho quando o vídeo é realista, texto ou marca d'água \
aparecendo), responda -1: é melhor ficar sem b-roll do que pôr um que não \
tem nada a ver. Responda só o JSON do esquema."""

ESQUEMA_ESCOLHA = {
    "type": "OBJECT",
    "properties": {
        "escolhas": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "broll": {"type": "INTEGER"},
                    "opcao": {"type": "INTEGER"},
                    "porque": {"type": "STRING"},
                },
                "required": ["broll", "opcao"],
                "propertyOrdering": ["broll", "opcao", "porque"],
            },
        },
    },
    "required": ["escolhas"],
}


def escolher_olhando(chave: str, modelo: str, tema: str, cenario: str,
                     pares: list[tuple[dict, list[dict]]]) -> dict[int, tuple[int, str]]:
    """Manda os quadros dos candidatos e a IA escolhe um por b-roll (ou -1).

    Devolve {índice do b-roll: (índice da opção, porquê)}. Candidato sem
    quadro não entra na conversa (não dá para julgar sem ver).
    """
    from .ai import gemini

    imagens: list = []
    linhas = [f"TEMA do vídeo: {tema}", f"CENÁRIO: {cenario}", ""]
    for k, (slot, cands) in enumerate(pares):
        opcoes = [j for j, c in enumerate(cands) if c.get("_quadro")]
        if not opcoes:
            continue
        linhas.append(f"B-ROLL {k}: cena = {slot.get('cena') or slot['busca']} "
                      f"(opções: {', '.join(str(j) for j in opcoes)})")
        for j in opcoes:
            imagens.append((f"B-ROLL {k}, opção {j}:", cands[j]["_quadro"]))
    if not imagens:
        return {}
    linhas.append("")
    linhas.append("Para cada B-ROLL, diga o número da opção escolhida (ou -1).")
    escolhido = gemini.escolher_modelo(chave, modelo)
    resposta = gemini.gerar_json(chave, escolhido["id"], INSTRUCAO_ESCOLHA,
                                 "\n".join(linhas), ESQUEMA_ESCOLHA,
                                 imagens=imagens, temperatura=0.1, maximo=2048)
    saida: dict[int, tuple[int, str]] = {}
    for e in resposta.get("escolhas") or []:
        try:
            k, j = int(e.get("broll")), int(e.get("opcao"))
        except (TypeError, ValueError):
            continue
        if 0 <= k < len(pares) and (j == -1 or 0 <= j < len(pares[k][1])):
            saida[k] = (j, str(e.get("porque") or "")[:160])
    return saida


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
    assunto = str((project.plan.broll or {}).get("assunto") or "").strip()[:200]
    aviso = ""
    if usar_ia and chave_guardada():
        try:
            r = pela_ia(chave_guardada(), db.get_setting("gemini_model", "") or "",
                        falas, duracao, frequencia, assunto)
            if r["slots"]:
                return {**r, "quem": "ia", "aviso": "", "assunto": assunto}
            aviso = "a IA não sugeriu nenhum ponto; usei a regra do programa"
        except Exception as exc:  # noqa: BLE001 — sem IA, a regra decide
            aviso = (f"a IA não respondeu ({exc}); usei a regra do programa, que "
                     f"busca pelas palavras da frase")
    elif usar_ia:
        aviso = ("sem a chave do Gemini, a busca é pelas palavras da frase — com a "
                 "chave, a IA lê o vídeo inteiro e busca dentro do assunto")
    slots = pela_regra(falas, duracao, frequencia, palavras_da_biblioteca())
    return {"slots": slots, "quem": "regra", "aviso": aviso, "tema": assunto,
            "cenario": "", "assunto": assunto}


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
            # o vídeo que ELE mandou ganha do que só ficou guardado de um
            # download do banco: "minha biblioteca" é, antes de tudo, a dele
            nota = (len(comum) * 10 - peso * 3
                    + (8 if item.get("fonte") == "meu" else 0)
                    + (5 if float(item.get("duracao") or 0) >= precisa else 0))
            if melhor is None or nota > melhor[0]:
                melhor = (nota, item)
    return melhor[1] if melhor else None


def do_banco(buscas: list[tuple[str, str]], orientacao: str, usados_ids: set[str],
             precisa: float, alvo_w: int, alvo_h: int, cancelado=None) -> dict | None:
    """Busca no banco grátis (Pexels e Pixabay) e baixa o primeiro que serve.

    ``buscas`` é uma lista de (termo, idioma); a em inglês vem primeiro quando
    a IA a deu. Entre os resultados, o que NÃO foi usado neste vídeo nem já
    baixado para outro anúncio vem antes — b-roll repetido de um criativo
    para o outro é o que denuncia banco de imagem —, e depois o que cobre a
    duração inteira. Se um download falha, tenta o próximo.
    """
    if not banco.estado().get("alguma"):
        return None
    ja_baixados = banco.ids_baixados()
    for termo, idioma in buscas:
        try:
            res = banco.buscar(termo, orientacao, 1, 15, idioma)
        except banco.ErroDoBanco:
            continue
        itens = [it for it in res.get("itens") or [] if it["id"] not in usados_ids]
        itens.sort(key=lambda it: (it["id"] in ja_baixados,
                                   float(it.get("duracao") or 0) < precisa))
        for it in itens[:3]:
            try:
                return banco.baixar(it["id"], alvo_w, alvo_h, termo, cancelado=cancelado)
            except banco.ErroDoBanco:
                continue
    return None


def candidatos_do_banco(buscas: list[tuple[str, str]], orientacao: str,
                        usados_ids: set[str], precisa: float,
                        maximo: int = 5) -> list[dict]:
    """Os melhores resultados das buscas de UM b-roll, sem baixar nada.

    Da busca mais específica para a mais ampla; entre os achados, o que ainda
    não foi usado em outro anúncio vem antes, depois o que cobre a duração.
    """
    if not banco.estado().get("alguma"):
        return []
    ja_baixados = banco.ids_baixados()
    vistos: set[str] = set()
    saida: list[dict] = []
    for termo, idioma in buscas:
        try:
            res = banco.buscar(termo, orientacao, 1, 15, idioma)
        except banco.ErroDoBanco:
            continue
        itens = [it for it in res.get("itens") or []
                 if it["id"] not in usados_ids and it["id"] not in vistos]
        itens.sort(key=lambda it: (it["id"] in ja_baixados,
                                   float(it.get("duracao") or 0) < precisa))
        for it in itens[:3]:
            vistos.add(it["id"])
            saida.append({**banco.detalhe(it["id"]), "_busca": termo})
        if len(saida) >= maximo:
            break
    return saida[:maximo]


# ----------------------------------------------------------------- aplicar
def tirar_automaticos(project) -> int:
    antes = len(project.plan.cutaways)
    project.plan.cutaways = [c for c in project.plan.cutaways
                             if getattr(c, "origem", "") != "auto"]
    return antes - len(project.plan.cutaways)


def aplicar(project, ctx, frequencia: str = "medio", usar_ia: bool = True,
            substituir: bool = True, fonte: str | None = None) -> dict:
    """Planeja, acha os vídeos e põe como b-roll. Mexe no ``project`` dado.

    Mexe no objeto recebido (e grava): o clique único segue com ele para a
    prévia, e recarregar do banco aqui faria a prévia sair sem os b-rolls.
    """
    from . import anexos
    from .models import Cutaway
    from .projects import add_media, duracao_de_saida, list_media, timeline_summary

    frequencia = _freq(frequencia)
    fonte = _fonte(fonte or (project.plan.broll or {}).get("fonte"))
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
    bloqueio: dict[int, str] = {}
    for k, slot in enumerate(slots):
        a, b = slot["inicio"], slot["fim"]
        if any(min(b, fb) - max(a, fa) > 0.02 for fa, fb in fotos):
            bloqueio[k] = "em cima de uma foto inserida"
        elif any(min(b, c.out_end) - max(a, c.out_start) > 0.02
                 for c in project.plan.cutaways):
            bloqueio[k] = "já tem b-roll aí"

    # A IA ESCOLHE OLHANDO. Com a IA e o banco, cada b-roll ganha alguns
    # candidatos (das buscas em inglês, dentro do assunto, e da em português),
    # e a IA vê o quadro de cada um antes de escolher — ou recusa todos. O que
    # a busca devolve sozinha é de tudo; é o olhar que tira o vídeo que não
    # tem nada a ver.
    pre: dict[int, list[dict]] = {}
    recusados_ia: dict[int, str] = {}
    aviso_escolha = ""
    ia_olhou = False
    if plano["quem"] == "ia" and fonte == "banco" and banco.estado().get("alguma"):
        from . import db
        from .ai.gemini import chave_guardada

        pares: list[tuple[dict, list[dict]]] = []
        indices: list[int] = []
        for k, slot in enumerate(slots):
            if k in bloqueio:
                continue
            ctx.progress(0.05 + 0.35 * k / max(1, len(slots)),
                         f"buscando vídeos para: {slot.get('busca_en') or slot['busca']}",
                         "broll")
            buscas_k = ([(t, "en") for t in slot.get("buscas_en") or []]
                        + [(slot["busca"], "pt")])
            cands = candidatos_do_banco(buscas_k, orientacao, set(),
                                        slot["fim"] - slot["inicio"])
            for cnd in cands:
                cnd["_quadro"] = banco.quadro_bytes(cnd)
            pre[k] = cands
            pares.append((slot, cands))
            indices.append(k)
        if any(cands for _s, cands in pares):
            try:
                ctx.progress(0.45, "a IA está olhando os vídeos achados", "broll")
                mapa = escolher_olhando(chave_guardada(),
                                        db.get_setting("gemini_model", "") or "",
                                        plano.get("tema", ""), plano.get("cenario", ""),
                                        pares)
                ia_olhou = True
                for pos, k in enumerate(indices):
                    if pos not in mapa:
                        continue
                    j, porque = mapa[pos]
                    if j < 0:
                        recusados_ia[k] = porque
                    else:
                        pre[k] = [pre[k][j]] + [c for x, c in enumerate(pre[k]) if x != j]
            except Exception as exc:  # noqa: BLE001 — fica o primeiro de cada busca
                aviso_escolha = (f"a IA não conseguiu olhar os vídeos ({exc}); "
                                 f"fiquei com o primeiro de cada busca")

    for k, slot in enumerate(slots):
        ctx.progress(0.5 + 0.45 * k / max(1, len(slots)),
                     f"b-roll {k + 1} de {len(slots)}: {slot['busca']}", "broll")
        if getattr(ctx, "cancelled", None) and ctx.cancelled():
            break
        a, b = slot["inicio"], slot["fim"]
        if k in bloqueio:
            pulados.append({**slot, "motivo": bloqueio[k]})
            continue
        if k in recusados_ia:
            pulados.append({**slot, "motivo": (
                f"a IA olhou os vídeos achados e nenhum mostrava "
                f"“{slot.get('cena') or slot['busca']}”"
                + (f" ({recusados_ia[k]})" if recusados_ia[k] else ""))})
            continue
        buscas = [slot["busca"], *slot.get("alternativas", [])]
        no_banco = ([(t, "en") for t in slot.get("buscas_en") or []]
                    + [(t, "pt") for t in buscas])
        assunto = plano.get("assunto") or ""
        if assunto and plano["quem"] == "regra":
            # sem IA, o assunto que ele escreveu dá o contexto que a palavra
            # solta não tem: "pessoas" vira "pessoas airbnb segurança"
            no_banco = [(f"{slot['busca']} {assunto}", "pt")] + no_banco

        def _do_banco(_k=k, _no_banco=no_banco, _a=a, _b=b):
            if pre.get(_k):
                # os candidatos já vistos (o escolhido pela IA na frente)
                for cnd in pre[_k]:
                    if cnd["id"] in usados_ids:
                        continue
                    try:
                        return banco.baixar(cnd["id"], w, h, cnd.get("_busca", ""),
                                            cancelado=getattr(ctx, "cancelled", None))
                    except banco.ErroDoBanco:
                        continue
                return None
            return do_banco(_no_banco, orientacao, usados_ids, _b - _a, w, h,
                            getattr(ctx, "cancelled", None))

        # a ordem de onde vem o vídeo: o banco grátis primeiro (o padrão), e a
        # biblioteca quando o banco não responde — ou o contrário, se ele pediu
        ordem = ((_do_banco, "banco"), (lambda: da_biblioteca(buscas, usados, b - a),
                                        "biblioteca"))
        if fonte == "biblioteca":
            ordem = ordem[::-1]
        item, origem_video = None, ""
        for achar, nome in ordem:
            item = achar()
            if item is not None:
                origem_video = nome
                break
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
                       "busca_en": slot.get("busca_en", ""), "cena": slot.get("cena", ""),
                       "de": origem_video, "autor": item.get("autor", ""),
                       "fonte": item.get("fonte", ""), "porque": slot.get("porque", "")})
    por_fonte: dict[str, int] = {}
    for x in postos:
        chave = "biblioteca" if x.get("fonte") == "meu" else (x.get("fonte") or x["de"])
        por_fonte[chave] = por_fonte.get(chave, 0) + 1
    aviso = "; ".join(x for x in (plano["aviso"], aviso_escolha) if x)
    resumo = {"frequencia": frequencia, "fonte": fonte, "quem": plano["quem"],
              "tema": plano.get("tema", ""), "cenario": plano.get("cenario", ""),
              "ia_olhou": ia_olhou, "aviso": aviso, "planejados": len(slots),
              "postos": postos, "pulados": pulados, "tirados": tirados,
              "por_fonte": por_fonte}
    project.plan.broll = {**(project.plan.broll or {}), "frequencia": frequencia,
                          "fonte": fonte,
                          "ultima": {k: v for k, v in resumo.items()
                                     if k not in ("postos", "pulados")}
                          | {"postos": len(postos), "pulados": len(pulados)}}
    project.save_plan()
    ctx.progress(1.0, f"{len(postos)} b-roll(s) no vídeo"
                      + (f", {len(pulados)} ponto(s) sem vídeo" if pulados else ""),
                 "broll")
    return resumo
