"""As ferramentas que o Claude enxerga.

DUAS REGRAS DE DESENHO, as duas por experiência de quem já usou servidor MCP:

1. POUCAS E GROSSAS. Uma ferramenta por GESTO do usuário, não uma por rota.
   Ele diz "corta o silêncio e me entrega em vertical", não "POST /oneclick com
   receita {...}". Cem ferramentas finas viram cem decisões para o modelo errar.

2. A RESPOSTA É TEXTO QUE ORIENTA. Nunca o JSON cru: o resumo da linha do tempo
   de um vídeo de um minuto passa de centenas de KB, e despejar isso enche a
   conversa de ruído e some com o que importa. Cada ferramenta devolve poucas
   linhas em português dizendo o que mudou e qual é o próximo passo possível.

O QUE NÃO EXISTE AQUI, DE PROPÓSITO: apagar projeto, abrir pasta no Explorer,
trocar a pasta de saída. São gestos irreversíveis ou que saem do editor, e a
máquina é dele — quem aperta esses botões é ele, na tela, não eu por comando.
"""
from __future__ import annotations

from .cliente import Cliente

FERRAMENTAS: list[dict] = []


def ferramenta(nome: str, descricao: str, esquema: dict):
    def registrar(fn):
        FERRAMENTAS.append({
            "name": nome,
            "description": descricao,
            "inputSchema": {"type": "object", **esquema},
            "_fn": fn,
        })
        return fn
    return registrar


def _seg(v) -> str:
    """Segundos em algo que se lê: 95,4 s vira 1 min 35 s."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "?"
    if v < 60:
        return f"{v:.1f} s"
    return f"{int(v // 60)} min {v % 60:.0f} s"


def _projeto(c: Cliente, pid: str) -> dict:
    return c.get(f"/api/projects/{pid}")


def _falhou(job: dict) -> str | None:
    if job.get("_estourou"):
        return (f"ainda rodando depois do tempo que esperei "
                f"({job.get('stage') or '?'}, {int(job.get('progress', 0) * 100)}%). "
                f"O editor continua trabalhando — pergunte o estado daqui a pouco.")
    if job.get("status") in ("erro", "error"):
        return f"deu erro: {job.get('error') or 'sem detalhe'}"
    if job.get("status") in ("cancelado", "cancelled"):
        return "foi cancelado."
    return None


# --------------------------------------------------------------- diagnóstico
@ferramenta(
    "estado_do_editor",
    "Diz se o editor está no ar, se o ffmpeg está bom, se há GPU, onde os "
    "vídeos são salvos e o que está sendo processado agora. Use antes de "
    "qualquer coisa quando algo parecer errado.",
    {"properties": {}},
)
def estado_do_editor(c: Cliente, _a: dict) -> str:
    h = c.get("/api/health")
    pasta = c.get("/api/output-dir")
    jobs = c.get("/api/jobs")
    ff = h.get("ffmpeg", {})
    dev = h.get("device", {})
    linhas = [
        f"editor no ar em {c.base}",
        f"ffmpeg: {'ok' if ff.get('ok') else 'NÃO ENCONTRADO — ' + str(ff.get('detail'))}",
        f"transcrição: {'faster-whisper ' + str(h.get('whisper_model')) if h.get('faster_whisper') else 'faster-whisper NÃO instalado'}"
        f" em {dev.get('device', '?')}",
        f"vídeos salvos em: {pasta.get('path') or pasta.get('dir') or '?'}",
    ]
    rodando = [j for j in jobs if j.get("status") in ("rodando", "fila", "running")]
    linhas.append(f"trabalhando agora: {len(rodando)} — "
                  + (", ".join(f"{j['kind']} {int(j.get('progress', 0) * 100)}%"
                               for j in rodando) if rodando else "nada"))
    return "\n".join(linhas)


@ferramenta(
    "listar_projetos",
    "Lista os projetos que já existem no editor, do mais recente para o mais "
    "antigo, com o id de cada um. Use quando ele falar de um vídeo que já "
    "estava aberto.",
    {"properties": {"quantos": {"type": "integer",
                                "description": "quantos listar (padrão 10)"}}},
)
def listar_projetos(c: Cliente, a: dict) -> str:
    quantos = max(1, min(50, int(a.get("quantos") or 10)))
    ps = c.get("/api/projects")[:quantos]
    if not ps:
        return "nenhum projeto ainda. Abra um vídeo com abrir_video."
    return "\n".join(
        f"{p.get('id')}  {p.get('name') or '(sem nome)'}  "
        f"[{p.get('status', '?')}]  {p.get('source_path', '')}"
        for p in ps)


# -------------------------------------------------------------------- abrir
@ferramenta(
    "abrir_video",
    "Abre um vídeo do disco dele como um projeto novo. Recebe o CAMINHO do "
    "arquivo na máquina — o arquivo não é copiado nem enviado para lugar "
    "nenhum. Devolve o id do projeto, que todas as outras ferramentas pedem.",
    {
        "properties": {
            "caminho": {"type": "string",
                        "description": "caminho completo do arquivo, ex.: C:\\\\Users\\\\...\\\\vsl.mp4"},
            "nome": {"type": "string", "description": "apelido do projeto (opcional)"},
            "preset": {"type": "string",
                       "description": "VSL, Reels, Anúncio... (opcional, padrão VSL)"},
        },
        "required": ["caminho"],
    },
)
def abrir_video(c: Cliente, a: dict) -> str:
    caminho = str(a.get("caminho") or "").strip()
    if not caminho:
        return "faltou o caminho do arquivo."
    sonda = c.post("/api/probe", {"path": caminho})
    p = c.post("/api/projects", {"source_path": caminho,
                                 "name": a.get("nome") or "",
                                 "preset": a.get("preset") or "VSL"})
    info = sonda.get("info") or sonda
    w = info.get("width") or (info.get("display_size") or [0, 0])[0]
    h = info.get("height") or (info.get("display_size") or [0, 0])[1]
    forma = "vertical" if h and w and h > w else ("quadrado" if h == w else "horizontal")
    return (f"projeto {p.get('id')} criado de {caminho}\n"
            f"{w}x{h} ({forma}), {_seg(info.get('duration'))}\n"
            f"próximo passo: editar_sozinho para cortar, legendar e montar.")


# ------------------------------------------------------------------- editar
@ferramenta(
    "editar_sozinho",
    "O serviço inteiro de uma vez: transcreve, corta o silêncio, decide a "
    "aceleração de cada trecho, legenda, posiciona o que foi anexado e monta o "
    "vídeo. É o que o botão de um clique faz na tela. Espera terminar e conta "
    "o resultado.",
    {
        "properties": {
            "projeto": {"type": "string"},
            "formato": {"type": "string",
                        "description": "9:16, 4:5 (feed), 1:1 ou 16:9 — o formato principal (opcional)"},
            "resumir_para": {"type": "number",
                             "description": "segundos que o vídeo tem que caber; a IA escolhe o que sai (opcional)"},
            "corte": {"type": "number",
                      "description": "0 a 1: 0 aproxima as falas, 1 deixa respiro (opcional)"},
            "sem_gemini": {"type": "boolean",
                           "description": "true = VOCÊ (Claude) é o editor: o Gemini não "
                                          "decide cortes nem b-roll; o corte sai pela regra "
                                          "do programa e você revisa e faz a pós-edição"},
        },
        "required": ["projeto"],
    },
)
def editar_sozinho(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    receita: dict = {}
    if a.get("sem_gemini") is not None:
        receita["editor"] = "claude" if a.get("sem_gemini") else ""
    if a.get("formato"):
        receita["aspect"] = str(a["formato"])
    if a.get("resumir_para"):
        receita["alvo_duracao"] = float(a["resumir_para"])
    if a.get("corte") is not None:
        receita["corte"] = float(a["corte"])
    job = c.post(f"/api/projects/{pid}/oneclick",
                 {"receita": receita} if receita else {})
    fim = c.esperar_job(pid, job.get("id", ""))
    ruim = _falhou(fim)
    if ruim:
        return f"a montagem {ruim}"
    r = fim.get("result") or {}
    tl = _projeto(c, pid).get("timeline") or {}
    blocos = len(tl.get("blocks") or [])
    legendas = len(tl.get("subtitles") or [])
    cortados = len(tl.get("removed") or [])
    linhas = [
        f"pronto: {_seg(r.get('duracao') or tl.get('duration'))} em {blocos} blocos, "
        f"{legendas} legendas, {cortados} trechos cortados",
    ]
    resumo = r.get("resumo") or {}
    if resumo.get("aplicado"):
        linhas.append(f"resumo: {resumo.get('explicacao') or 'encurtado para caber'}")
    linhas.append("o MP4 final está sendo encodado por baixo — "
                  "use exportar para saber quando e onde ele ficou.")
    return "\n".join(linhas)


@ferramenta(
    "ver_projeto",
    "Conta como o vídeo está agora: duração, quantos blocos, o que foi "
    "cortado, quantas legendas, o que está anexado. Use para saber onde "
    "mexer antes de mandar cortar.",
    {"properties": {"projeto": {"type": "string"}}, "required": ["projeto"]},
)
def ver_projeto(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    p = _projeto(c, pid)
    tl = p.get("timeline") or {}
    plano = p.get("plan") or {}
    blocos = tl.get("blocks") or []
    rapidos = [b for b in blocos if float(b.get("speed", 1)) > 1.01]
    linhas = [
        f"{p.get('name') or p.get('id')} — {_seg(tl.get('duration'))} "
        f"(fonte: {_seg(tl.get('source_duration'))})",
        f"{len(blocos)} blocos, {len(rapidos)} acelerados, "
        f"{len(tl.get('removed') or [])} trechos cortados",
        f"{len(tl.get('subtitles') or [])} legendas, "
        f"{len(plano.get('overlays') or [])} sobreposições, "
        f"{len(plano.get('blurs') or [])} desfoques",
    ]
    if (plano.get("music") or {}).get("media_id"):
        linhas.append(f"trilha: {plano['music'].get('gain_db')} dB")
    if plano.get("look") and plano["look"] != "nenhum":
        linhas.append(f"filtro de cinema: {plano['look']}")
    if plano.get("alvo_duracao"):
        linhas.append(f"alvo de duração: {_seg(plano['alvo_duracao'])}")
    return "\n".join(linhas)


@ferramenta(
    "transcricao",
    "O que foi dito — TODAS as gravações do vídeo, na ordem da montagem — com "
    "o número de cada palavra entre colchetes (é o número que cortar e "
    "devolver pedem). Uma linha por frase ou pausa. ~ marca o que já foi "
    "cortado. restante=true mostra só o que FICOU, para reler o texto como "
    "ele vai soar. 'de' e 'ate' são posições na lista (400 por vez).",
    {
        "properties": {
            "projeto": {"type": "string"},
            "de": {"type": "integer", "description": "posição inicial (padrão 0)"},
            "ate": {"type": "integer", "description": "posição final (padrão: 400 adiante)"},
            "restante": {"type": "boolean",
                         "description": "true = só as palavras que ficaram no vídeo"},
        },
        "required": ["projeto"],
    },
)
def transcricao(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    analise = _projeto(c, pid).get("analysis") or {}
    palavras = _palavras_da_montagem(analise)
    if not palavras:
        return "ainda não há transcrição. Rode editar_sozinho primeiro."
    fora = set(analise.get("removed_word_ids") or [])
    if a.get("restante"):
        palavras = [w for w in palavras if w.get("i") not in fora]
    de = max(0, int(a.get("de") or 0))
    ate = int(a.get("ate") if a.get("ate") is not None else de + 400)
    fatia = palavras[de:ate + 1]
    linhas: list[str] = []
    atual: list[str] = []
    fonte = None
    for k, w in enumerate(fatia):
        if w.get("source", "main") != fonte:
            if atual:
                linhas.append(" ".join(atual))
                atual = []
            if fonte is not None or w.get("source", "main") != "main":
                linhas.append(f"— gravação {w.get('_n', 1)} —")
            fonte = w.get("source", "main")
        i = w.get("i")
        atual.append(f"[{i}]{'~' if i in fora else ''}{w.get('text', '')}")
        prox = fatia[k + 1] if k + 1 < len(fatia) else None
        fim_de_frase = str(w.get("text", "")).rstrip().endswith((".", "?", "!"))
        pausa = prox is not None and float(prox.get("start", 0)) - float(w.get("end", 0)) > 0.7
        if fim_de_frase or pausa:
            linhas.append(" ".join(atual))
            atual = []
    if atual:
        linhas.append(" ".join(atual))
    titulo = "o que FICOU no vídeo" if a.get("restante") else "~ = já cortada"
    return (f"palavras {de} a {min(ate, len(palavras) - 1)} de {len(palavras) - 1} "
            f"({titulo}):\n" + "\n".join(linhas))


def _palavras_da_montagem(analise: dict) -> list[dict]:
    """As palavras de todas as gravações, na ordem da montagem."""
    out = [{**w, "source": w.get("source", "main"), "_n": 1}
           for w in analise.get("words") or []]
    fontes = sorted((analise.get("fontes") or {}).values(),
                    key=lambda f: (f or {}).get("ordem", 0))
    for n, f in enumerate(fontes, start=2):
        out += [{**w, "source": w.get("source") or f.get("media_id"), "_n": n}
                for w in (f or {}).get("words") or []]
    return out


# ------------------------------------------------------------------- cortar
@ferramenta(
    "cortar",
    "Tira um pedaço do vídeo. Pode ser por PALAVRAS (os números da "
    "transcrição) ou por TEMPO (segundos na linha do tempo final). A borda "
    "sempre encaixa no vale de silêncio mais próximo, então o corte não come "
    "palavra.",
    {
        "properties": {
            "projeto": {"type": "string"},
            "palavras": {"type": "array", "items": {"type": "integer"},
                         "description": "números das palavras a tirar"},
            "inicio": {"type": "number", "description": "segundo inicial (corte por tempo)"},
            "fim": {"type": "number", "description": "segundo final (corte por tempo)"},
        },
        "required": ["projeto"],
    },
)
def cortar(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    antes = ((_projeto(c, pid).get("timeline") or {}).get("duration")) or 0.0
    recusa = ""
    if a.get("palavras"):
        ids = [int(x) for x in a["palavras"]]
        r = c.post(f"/api/projects/{pid}/ops/remove-words", {"word_ids": ids})
        # cada trecho contínuo de palavras é um corte; o que não coube (sem
        # vale de silêncio entre as vizinhas) volta DITO, com o texto — quem
        # revisa precisa saber o que ficou para decidir o que fazer
        grupos = r.get("applied") or []
        feitos = [g for g in grupos if g.get("ok")]
        recusados = [g for g in grupos if not g.get("ok")]
        o_que = f"{len(ids)} palavra(s) em {len(feitos)} trecho(s)"
        if recusados:
            texto = {w.get("i"): w.get("text", "") for w in _palavras_da_montagem(
                _projeto(c, pid).get("analysis") or {})}
            recusa = "\n".join(
                f"NÃO cortado: \"{' '.join(texto.get(i, str(i)) for i in g.get('words') or [])}\""
                f" — {g.get('reason') or 'sem motivo'} (corte a expressão inteira em "
                f"volta, ou deixe)" for g in recusados)
            if not feitos:
                return recusa
    elif a.get("inicio") is not None and a.get("fim") is not None:
        r = c.post(f"/api/projects/{pid}/ops/delete-range",
                   {"start": float(a["inicio"]), "end": float(a["fim"])})
        o_que = f"de {a['inicio']} s a {a['fim']} s"
    else:
        return "diga as palavras (lista de números) ou início e fim em segundos."
    if not r.get("ok", True):
        return f"não deu: {r.get('reason') or 'motivo não dito'}"
    depois = ((_projeto(c, pid).get("timeline") or {}).get("duration")) or 0.0
    explica = " | ".join((r.get("explain") or [])[:2])
    return (f"cortado {o_que}: {_seg(antes)} → {_seg(depois)}"
            + (f"\n{explica}" if explica else "")
            + (f"\n{recusa}" if recusa else ""))


@ferramenta(
    "resumir",
    "Encurta o vídeo até caber na duração pedida, deixando a IA escolher o que "
    "sai da copy. Não acelera a fala: escolhe o que pode sair. As travas de "
    "sempre valem (gancho protegido, vale nas duas bordas, ideia inteira).",
    {
        "properties": {
            "projeto": {"type": "string"},
            "segundos": {"type": "number", "description": "duração alvo"},
        },
        "required": ["projeto", "segundos"],
    },
)
def resumir(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    antes = ((_projeto(c, pid).get("timeline") or {}).get("duration")) or 0.0
    r = c.post(f"/api/projects/{pid}/ops/resumir",
               {"alvo": float(a["segundos"])}, timeout=600.0)
    depois = ((_projeto(c, pid).get("timeline") or {}).get("duration")) or 0.0
    nota = r.get("explicacao") or r.get("resumo", {}).get("explicacao") or ""
    return (f"resumido para o alvo de {_seg(a['segundos'])}: "
            f"{_seg(antes)} → {_seg(depois)}" + (f"\n{nota}" if nota else ""))


@ferramenta(
    "velocidade",
    "Acelera ou desacelera o vídeo inteiro por um fator (1.0 é o normal). "
    "A aceleração por trecho que a IA decidiu continua valendo por baixo.",
    {
        "properties": {
            "projeto": {"type": "string"},
            "fator": {"type": "number", "description": "1.0 = normal, 1.2 = 20% mais rápido"},
        },
        "required": ["projeto", "fator"],
    },
)
def velocidade(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    antes = ((_projeto(c, pid).get("timeline") or {}).get("duration")) or 0.0
    c.post(f"/api/projects/{pid}/ops/speed", {"global": float(a["fator"])})
    depois = ((_projeto(c, pid).get("timeline") or {}).get("duration")) or 0.0
    return f"velocidade global {a['fator']}x: {_seg(antes)} → {_seg(depois)}"


# ------------------------------------------------------------------ anexar
@ferramenta(
    "anexar",
    "Põe uma imagem ou um vídeo por cima do quadro, como janela, num instante "
    "do vídeo. Recebe o CAMINHO do arquivo na máquina dele. Devolve o id da "
    "sobreposição, que animar, recortar_forma e efeito pedem.",
    {
        "properties": {
            "projeto": {"type": "string"},
            "caminho": {"type": "string", "description": "caminho do arquivo na máquina"},
            "em": {"type": "number", "description": "segundo em que aparece"},
            "dura": {"type": "number", "description": "quantos segundos fica (padrão 3)"},
            "descricao": {"type": "string",
                          "description": "o que é isto, para a IA saber onde usar"},
        },
        "required": ["projeto", "caminho", "em"],
    },
)
def anexar(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    caminho = str(a["caminho"])
    tipo = "video" if caminho.lower().rsplit(".", 1)[-1] in (
        "mp4", "mov", "mkv", "m4v", "avi", "webm", "wmv", "ts") else "image"
    m = c.post(f"/api/projects/{pid}/media",
               {"path": caminho, "kind": tipo,
                "descricao": a.get("descricao") or ""})
    em = float(a["em"])
    dura = float(a.get("dura") or 3.0)
    o = c.post(f"/api/projects/{pid}/overlays",
               {"media_id": m.get("id"), "out_start": em, "out_end": em + dura})
    ov = o.get("overlay") or {}
    ajustes = o.get("ajustes") or []
    return (f"anexado: sobreposição {ov.get('id')} de {_seg(ov.get('out_start'))} "
            f"a {_seg(ov.get('out_end'))}"
            + (f"\najustes: {'; '.join(str(x) for x in ajustes)}" if ajustes else "")
            + "\npróximo passo possível: animar, recortar_forma ou efeito.")


@ferramenta(
    "broll",
    "Põe um ou mais VÍDEOS de b-roll por cima da fala, depois da edição. A "
    "fala continua por baixo (só a imagem troca), nenhuma palavra sai do "
    "lugar. Vários arquivos entram em sequência a partir do segundo 'em', "
    "cada um no primeiro vão livre — nunca um em cima do outro. Recebe os "
    "CAMINHOS dos arquivos na máquina dele.",
    {
        "properties": {
            "projeto": {"type": "string"},
            "caminhos": {"type": "array", "items": {"type": "string"},
                         "description": "vídeos, na ordem em que devem entrar"},
            "em": {"type": "number", "description": "segundo em que o primeiro entra"},
            "dura": {"type": "number",
                     "description": "quanto cada um cobre no máximo (padrão: a "
                                    "duração que ele escolheu na primeira tela; "
                                    "5 s se não escolheu)"},
        },
        "required": ["projeto", "caminhos", "em"],
    },
)
def broll(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    caminhos = a.get("caminhos") or []
    if isinstance(caminhos, str):
        caminhos = [caminhos]
    corpo = {"paths": [str(x) for x in caminhos], "at": float(a.get("em") or 0.0)}
    if a.get("dura"):
        corpo["duracao"] = float(a["dura"])
    r = c.post(f"/api/projects/{pid}/brolls", corpo)
    linhas = [f"b-roll {p.get('name') or p.get('media_id')}: "
              f"{_seg(p.get('out_start'))} a {_seg(p.get('out_end'))}"
              for p in r.get("postos") or []]
    for x in r.get("recusados") or []:
        linhas.append(f"não entrou {x.get('path')}: {x.get('motivo')}")
    return "\n".join(linhas) or "nada entrou"


@ferramenta(
    "buscar_broll",
    "Procura vídeos de b-roll GRÁTIS (Pexels e Pixabay, uso comercial livre) "
    "por palavra, em português. Com 'projeto' e 'em', sugere as palavras a "
    "partir do que está sendo dito naquele ponto e já filtra pela orientação "
    "do vídeo. Devolve os ids que broll_do_banco usa. Só a palavra sai da "
    "máquina; precisa da chave grátis do Pexels ou do Pixabay colada no editor.",
    {
        "properties": {
            "termo": {"type": "string", "description": "o que procurar (ex.: café, academia)"},
            "projeto": {"type": "string"},
            "em": {"type": "number",
                   "description": "segundo do vídeo, para sugerir pela fala"},
            "ver": {"type": "boolean",
                    "description": "true = devolve também a MINIATURA de cada "
                                   "resultado (até 6), para escolher olhando"},
        },
        "required": [],
    },
)
def buscar_broll(c: Cliente, a: dict):
    pid = str(a.get("projeto") or "")
    termo = str(a.get("termo") or "").strip()
    linhas: list[str] = []
    orientacao = ""
    if pid:
        sug = c.get(f"/api/projects/{pid}/banco/sugestao", t=float(a.get("em") or 0.0))
        orientacao = sug.get("orientacao") or ""
        if sug.get("texto"):
            linhas.append(f"fala nesse ponto: \"{sug['texto'][:160]}\"")
        if sug.get("termos"):
            linhas.append("palavras sugeridas: " + ", ".join(sug["termos"]))
        if not termo and sug.get("termos"):
            termo = sug["termos"][0]
    if not termo:
        return "\n".join(linhas + ["diga o que procurar em 'termo'"])
    r = c.get("/api/banco/buscar", q=termo, orientacao=orientacao, pid=pid)
    itens = r.get("itens") or []
    linhas.append(f"busca: {termo} ({len(itens)} vídeos)")
    for it in itens[:15]:
        linhas.append(f"- {it['id']}: {_seg(it.get('duracao'))}, "
                      f"{it.get('largura')}x{it.get('altura')}, de "
                      f"{it.get('autor') or '?'}"
                      + (f" — {it['descricao']}" if it.get("descricao") else ""))
    for aviso in r.get("avisos") or []:
        linhas.append(f"aviso: {aviso}")
    linhas.append("próximo passo: broll_do_banco com os ids escolhidos")
    if not a.get("ver") or not itens:
        return "\n".join(linhas)
    m = c.post("/api/banco/miniaturas", {"q": termo, "orientacao": orientacao,
                                         "ids": [it["id"] for it in itens[:6]]})
    conteudo = [{"type": "text", "text": "\n".join(linhas)}]
    for mini in m.get("miniaturas") or []:
        conteudo.append({"type": "text", "text": f"miniatura de {mini['id']}:"})
        conteudo.append({"type": "image", "data": mini["dados"],
                         "mimeType": mini["mime"]})
    return {"content": conteudo, "isError": False}


@ferramenta(
    "broll_do_banco",
    "Baixa vídeos do banco grátis (ids de buscar_broll) para a máquina dele e "
    "põe como b-roll por cima da fala, em sequência a partir do segundo 'em'. "
    "O que já foi baixado antes é reaproveitado sem rede.",
    {
        "properties": {
            "projeto": {"type": "string"},
            "ids": {"type": "array", "items": {"type": "string"},
                    "description": "ids de buscar_broll, na ordem em que entram"},
            "em": {"type": "number", "description": "segundo em que o primeiro entra"},
            "dura": {"type": "number",
                     "description": "quanto cada um cobre no máximo (padrão: a "
                                    "duração que ele escolheu na primeira tela; "
                                    "5 s se não escolheu)"},
        },
        "required": ["projeto", "ids", "em"],
    },
)
def broll_do_banco(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    ids = a.get("ids") or []
    if isinstance(ids, str):
        ids = [ids]
    corpo = {"ids": [str(x) for x in ids], "at": float(a.get("em") or 0.0)}
    if a.get("dura"):
        corpo["duracao"] = float(a["dura"])
    job = c.post(f"/api/projects/{pid}/banco/usar", corpo)
    fim = c.esperar_job(pid, job.get("id", ""))
    ruim = _falhou(fim)
    if ruim:
        return f"o b-roll do banco {ruim}"
    r = fim.get("result") or {}
    linhas = [f"b-roll {p.get('name') or p.get('media_id')}: "
              f"{_seg(p.get('out_start'))} a {_seg(p.get('out_end'))}"
              for p in r.get("postos") or []]
    for x in r.get("recusados") or []:
        linhas.append(f"não entrou {x.get('path')}: {x.get('motivo')}")
    autores = sorted({f"{k.get('autor')} ({k.get('fonte')})"
                      for k in r.get("creditos") or [] if k.get("autor")})
    if autores:
        linhas.append("vídeos de: " + ", ".join(autores))
    return "\n".join(linhas) or "nada entrou"


@ferramenta(
    "broll_automatico",
    "Põe b-roll SOZINHO no vídeo montado: a IA (ou a regra do programa, sem "
    "chave do Gemini) escolhe os momentos da fala que dá para ilustrar, quanto "
    "cada b-roll dura e o que buscar; o vídeo sai da biblioteca dele ou do "
    "banco grátis. A fala continua por baixo. Refazer troca só os automáticos; "
    "os postos à mão ficam.",
    {
        "properties": {
            "projeto": {"type": "string"},
            "frequencia": {"type": "string", "enum": ["pouco", "medio", "muito"],
                           "description": "pouco ~1 a cada 20 s, medio ~12 s, muito ~7 s"},
            "fonte": {"type": "string", "enum": ["banco", "biblioteca"],
                      "description": "banco = Pexels e Pixabay primeiro (padrão); "
                                     "biblioteca = os vídeos dele primeiro"},
        },
        "required": ["projeto"],
    },
)
def broll_automatico(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    corpo = {"frequencia": a.get("frequencia") or "medio"}
    if a.get("fonte"):
        corpo["fonte"] = a["fonte"]
    job = c.post(f"/api/projects/{pid}/broll-auto", corpo)
    fim = c.esperar_job(pid, job.get("id", ""))
    ruim = _falhou(fim)
    if ruim:
        return f"o b-roll automático {ruim}"
    r = fim.get("result") or {}
    linhas = [f"quem escolheu os pontos: {'a IA' if r.get('quem') == 'ia' else 'a regra do programa'}"
              + (f" ({r['aviso']})" if r.get("aviso") else "")]
    for p in r.get("postos") or []:
        linhas.append(f"b-roll “{p.get('busca')}” de {_seg(p.get('out_start'))} a "
                      f"{_seg(p.get('out_end'))} ({p.get('fonte') or p.get('de')})")
    for x in r.get("pulados") or []:
        linhas.append(f"sem b-roll em {_seg(x.get('inicio'))}: {x.get('motivo')}")
    return "\n".join(linhas)


@ferramenta(
    "animar",
    "Faz uma sobreposição se mover, crescer, girar ou aparecer ao longo do "
    "tempo. Cada marco é um instante e o valor das propriedades naquele "
    "instante: x e y de 0 a 1 (fração da tela), scale (1 = tamanho natural), "
    "opacity de 0 a 1, rotation em graus. A curva pode ser linear, suave, "
    "entra ou sai.",
    {
        "properties": {
            "projeto": {"type": "string"},
            "sobreposicao": {"type": "string", "description": "id devolvido por anexar"},
            "marcos": {
                "type": "array",
                "description": "ex.: [{\"t\":2,\"x\":0.2,\"scale\":0.5},{\"t\":5,\"x\":0.8,\"scale\":1,\"easing\":\"suave\"}]",
                "items": {
                    "type": "object",
                    "properties": {
                        "t": {"type": "number", "description": "segundo na linha do tempo final"},
                        "x": {"type": "number"}, "y": {"type": "number"},
                        "scale": {"type": "number"}, "opacity": {"type": "number"},
                        "rotation": {"type": "number"},
                        "easing": {"type": "string",
                                   "enum": ["linear", "suave", "entra", "sai", "cinema", "organica"]},
                    },
                    "required": ["t"],
                },
            },
        },
        "required": ["projeto", "sobreposicao", "marcos"],
    },
)
def animar(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    oid = str(a.get("sobreposicao") or "")
    r = c.put(f"/api/projects/{pid}/overlays/{oid}",
              {"keyframes": a.get("marcos") or []})
    kfs = (r.get("overlay") or {}).get("keyframes") or []
    pedidos = len(a.get("marcos") or [])
    nota = ""
    if len(kfs) < pedidos:
        nota = (f"\n({pedidos - len(kfs)} marco(s) recusado(s) por virem sem "
                f"número válido — o render monta expressão com isso)")
    if not kfs:
        return "nenhum marco válido. Cada marco precisa de 't' e ao menos uma "\
               "propriedade (x, y, scale, opacity, rotation)."
    props = sorted({k for m in kfs for k in m if k not in ("t", "easing")})
    return (f"animado: {len(kfs)} marcos de {kfs[0]['t']} s a {kfs[-1]['t']} s, "
            f"mexendo em {', '.join(props)}{nota}")


@ferramenta(
    "recortar_forma",
    "Recorta uma sobreposição numa forma, com borda suave: retangulo, elipse "
    "ou arredondado. Passe forma vazia para tirar o recorte.",
    {
        "properties": {
            "projeto": {"type": "string"},
            "sobreposicao": {"type": "string"},
            "forma": {"type": "string",
                      "enum": ["retangulo", "elipse", "arredondado", ""]},
            "suavizar": {"type": "number",
                         "description": "largura da borda suave, 0 a 1 (padrão 0.04)"},
            "raio": {"type": "number",
                     "description": "quanto o canto arredonda, 0 a 1 (só em arredondado)"},
        },
        "required": ["projeto", "sobreposicao", "forma"],
    },
)
def recortar_forma(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    oid = str(a.get("sobreposicao") or "")
    forma = str(a.get("forma") or "")
    mascara = None
    if forma:
        mascara = {"shape": forma}
        if a.get("suavizar") is not None:
            mascara["feather"] = float(a["suavizar"])
        if a.get("raio") is not None:
            mascara["radius"] = float(a["raio"])
    r = c.put(f"/api/projects/{pid}/overlays/{oid}", {"mask": mascara})
    m = (r.get("overlay") or {}).get("mask")
    if not m:
        return ("sem recorte (forma vazia, ou forma que não existe — valem "
                "retangulo, elipse e arredondado).")
    return (f"recortado em {m['shape']}, borda suave de {m['feather']}"
            + (f", canto {m['radius']}" if m["shape"] == "arredondado" else ""))


@ferramenta(
    "efeito",
    "Põe efeitos num bloco do vídeo ou numa sobreposição. A lista substitui a "
    "anterior, então lista vazia limpa. No bloco valem desfoque, cor, vinheta "
    "e flash; na sobreposição valem desfoque, cor, chroma e tremor.",
    {
        "properties": {
            "projeto": {"type": "string"},
            "alvo": {"type": "string", "enum": ["clipe", "sobreposicao"]},
            "id": {"type": "string", "description": "id do bloco ou da sobreposição"},
            "efeitos": {
                "type": "array",
                "description": "ex.: [{\"kind\":\"desfoque\",\"sigma\":10}] ou [{\"kind\":\"vinheta\",\"amount\":0.6}]",
                "items": {"type": "object",
                          "properties": {"kind": {"type": "string"}},
                          "required": ["kind"]},
            },
        },
        "required": ["projeto", "alvo", "id", "efeitos"],
    },
)
def efeito(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    r = c.post(f"/api/projects/{pid}/ops/efeito",
               {"alvo": a.get("alvo"), "id": a.get("id"),
                "effects": a.get("efeitos") or []})
    es = r.get("effects") or []
    if not es:
        return "efeitos limpos."
    return "efeitos: " + ", ".join(e.get("kind", "?") for e in es)


@ferramenta(
    "trilha",
    "Põe uma música de fundo, com volume CONSTANTE do começo ao fim (ela não "
    "abaixa na fala — é o que o usuário quer). Só o volume é ajustável.",
    {
        "properties": {
            "projeto": {"type": "string"},
            "caminho": {"type": "string", "description": "caminho do MP3 na máquina"},
            "volume_db": {"type": "number",
                          "description": "quantos dB ABAIXO DA VOZ (o Sharkcut mede "
                                         "as duas); padrão -18; mais negativo é mais baixo"},
        },
        "required": ["projeto", "caminho"],
    },
)
def trilha(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    m = c.post(f"/api/projects/{pid}/media",
               {"path": str(a["caminho"]), "kind": "audio"})
    corpo = {"media_id": m.get("id"), "enabled": True}
    if a.get("volume_db") is not None:
        corpo["gain_db"] = float(a["volume_db"])
    c.post(f"/api/projects/{pid}/ops/music", corpo)
    return f"trilha posta a {corpo.get('gain_db', -18)} dB, constante."



@ferramenta(
    "formatos",
    "Escolhe em que formatos o vídeo sai. Todos vêm do MESMO corte e cada um "
    "é encodado a partir da fonte, nunca do MP4 já comprimido.",
    {
        "properties": {
            "projeto": {"type": "string"},
            "principal": {"type": "string",
                          "enum": ["fonte", "9:16", "4:5", "1:1", "16:9"],
                          "description": "'fonte' mantém a proporção do arquivo original"},
            "extras": {"type": "array", "items": {"type": "string"},
                       "description": "outros formatos a entregar junto"},
        },
        "required": ["projeto", "principal"],
    },
)
def formatos(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    # A receita é ANINHADA: aplicar_receita (server.py:449) percorre um mapa de
    # {chave: dataclass} e só aceita os campos que existem naquele dataclass.
    # Mandar {"aspect": ...} na raiz é gravar nada e receber 200 — foi o que o
    # teste do MCP pegou.
    export = {"aspect": str(a["principal"])}
    if a.get("extras"):
        export["extras"] = [str(x) for x in a["extras"]]
    r = c.post(f"/api/projects/{pid}/params", {"export": export})
    saiu = (r.get("plan") or {}).get("export") or {}
    principal = saiu.get("aspect", "?")
    extras = list(saiu.get("extras") or [])
    if principal != export["aspect"]:
        return (f"o editor não aceitou '{export['aspect']}' como formato "
                f"principal (ficou '{principal}'). Valem: fonte, 9:16, 4:5, 1:1, 16:9.")
    return ("vai entregar em " + ", ".join([principal] + extras)
            + "\ncada um sai do mesmo corte, encodado a partir da fonte.")


# --------------------------------------------------------------- exportar
@ferramenta(
    "exportar",
    "Monta o arquivo final e espera ficar pronto. Diz onde ele ficou, quanto "
    "tempo levou e o tamanho.",
    {"properties": {"projeto": {"type": "string"}}, "required": ["projeto"]},
)
def exportar(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    job = c.post(f"/api/projects/{pid}/export-final")
    fim = c.esperar_job(pid, job.get("id", ""))
    ruim = _falhou(fim)
    if ruim:
        return f"a exportação {ruim}"
    r = fim.get("result") or {}
    levou = ""
    try:
        levou = f", em {_seg(float(fim['updated_at']) - float(fim['created_at']))}"
    except (KeyError, TypeError, ValueError):
        pass
    tam = r.get("size_bytes")
    tamanho = f", {float(tam) / 1_048_576:.1f} MB" if tam else ""
    pasta = r.get("pasta") or ""
    nome = r.get("nome") or ""
    return (f"pronto: {nome}{tamanho}{levou}\nna pasta: {pasta}")


@ferramenta(
    "adicionar_video",
    "Acrescenta outra gravação ao MESMO projeto: ela entra na linha do tempo "
    "depois do que já está lá, com a fala dela, e recebe o mesmo tratamento do "
    "primeiro vídeo — transcrição, corte de silêncio, aceleração e legenda. É "
    "isto que junta várias tomadas num vídeo só. Diferente de anexar, que põe "
    "uma janela por cima do quadro.",
    {
        "properties": {
            "projeto": {"type": "string"},
            "caminho": {"type": "string",
                        "description": "caminho do arquivo na máquina"},
            "descricao": {"type": "string",
                          "description": "o que é esta gravação (opcional)"},
        },
        "required": ["projeto", "caminho"],
    },
)
def adicionar_video(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    antes = ((_projeto(c, pid).get("timeline") or {}).get("duration")) or 0.0
    job = c.post(f"/api/projects/{pid}/adicionar-video",
                 {"path": str(a["caminho"]),
                  "descricao": a.get("descricao") or ""})
    fim = c.esperar_job(pid, job.get("id", ""))
    ruim = _falhou(fim)
    if ruim:
        return f"acrescentar a gravação {ruim}"
    r = fim.get("result") or {}
    return (f"gravação acrescentada: {r.get('name') or a['caminho']}, "
            f"{r.get('words', '?')} palavras\n"
            f"o vídeo agora tem {_seg(r.get('duracao_total'))} em "
            f"{r.get('blocos', '?')} blocos (era {_seg(antes)})\n"
            f"ela foi cortada no áudio dela, não no do primeiro vídeo.")


@ferramenta(
    "juntar_videos",
    "Cria um projeto de VÁRIOS arquivos de uma vez e roda a esteira inteira: "
    "cada um é transcrito e cortado no silêncio DELE, todos são montados na "
    "ordem da lista, e sai um vídeo só com legenda contínua. Use quando ele "
    "der mais de um arquivo — três tomadas, uma abertura e um depoimento.",
    {
        "properties": {
            "caminhos": {
                "type": "array",
                "items": {"type": "string"},
                "description": "os caminhos na máquina, NA ORDEM DA MONTAGEM",
            },
            "formato": {"type": "string",
                        "description": "9:16, 4:5 (feed), 1:1, 16:9 ou fonte (opcional)"},
            "resumir_para": {"type": "number",
                             "description": "segundos que o vídeo tem que caber (opcional)"},
            "preset": {"type": "string", "description": "VSL, Reels... (opcional)"},
        },
        "required": ["caminhos"],
    },
)
def juntar_videos(c: Cliente, a: dict) -> str:
    caminhos = [str(x) for x in (a.get("caminhos") or []) if str(x).strip()]
    if not caminhos:
        return "faltou a lista de caminhos."
    if len(caminhos) == 1:
        return ("para um arquivo só, use abrir_video e depois editar_sozinho — "
                "juntar_videos é para dois ou mais.")
    receita: dict = {}
    if a.get("formato"):
        receita["export"] = {"aspect": str(a["formato"])}
    if a.get("resumir_para"):
        receita["alvo_duracao"] = float(a["resumir_para"])
    r = c.post("/api/projects/pacote",
               {"paths": caminhos, "preset": a.get("preset") or "VSL",
                "receita": receita})
    pid = (r.get("project") or {}).get("id", "")
    recusados = r.get("recusados") or []
    job = (r.get("job") or {}).get("id", "")
    fim = c.esperar_job(pid, job)
    ruim = _falhou(fim)
    if ruim:
        return f"projeto {pid} criado, mas a montagem {ruim}"
    tl = _projeto(c, pid).get("timeline") or {}
    linhas = [
        f"projeto {pid}: {r.get('fontes')} gravações numa esteira só",
        f"{_seg(tl.get('duration'))} em {len(tl.get('blocks') or [])} blocos, "
        f"{len(tl.get('subtitles') or [])} legendas, "
        f"{len(tl.get('removed') or [])} trechos cortados",
    ]
    if recusados:
        linhas.append("ficaram de fora: "
                      + "; ".join(f"{x['path']} ({x['motivo']})" for x in recusados))
    linhas.append("use exportar para saber onde o arquivo ficou.")
    return "\n".join(linhas)


@ferramenta(
    "gravacoes",
    "As tomadas que ele gravou dentro do app, da mais recente para a mais "
    "antiga, com duração e caminho. Use quando ele falar de 'a tomada que "
    "acabei de gravar' em vez de dar um caminho.",
    {"properties": {}},
)
def gravacoes(c: Cliente, _a: dict) -> str:
    gs = c.get("/api/gravacoes")
    if not gs:
        return ("nenhuma gravação ainda. Ele grava pelo botão 'Gravar agora' "
                "da primeira tela.")
    return "\n".join(
        f"{g['nome']}  {_seg(g['duracao'])}  "
        f"{g['largura']}x{g['altura']}{'' if g['tem_audio'] else '  SEM ÁUDIO'}\n"
        f"   {g['path']}"
        for g in gs[:20])


# ------------------------------------------- o que a IA do Gemini decidia
# Ritmo e câmera por bloco, legenda, devolver o que o corte levou, o fôlego
# do corte de silêncio. Com estas, quem edita pelo MCP decide TUDO o que a IA
# do Gemini decidia — e o Gemini pode ficar fora da edição.

ETAPAS = ("gancho", "dor", "mecanismo", "explicacao", "revelacao", "prova",
          "monetizacao", "oferta", "garantia", "cta")


@ferramenta(
    "ritmo",
    "RITMO E CÂMERA, bloco a bloco (o que a IA decidia): a VELOCIDADE de cada "
    "bloco (1.0 a 1.3 — acima de 1.25 a fala soa artificial), o ZOOM (1.0 = "
    "aberto; 1.06–1.15 = mais fechado, para ênfase) e a ETAPA do roteiro "
    "(gancho, dor, mecanismo, explicacao, revelacao, prova, monetizacao, "
    "oferta, garantia, cta — a etapa escolhe o enquadramento dos blocos sem "
    "zoom travado). Os clip_id estão em pos_contexto. 'global' multiplica a "
    "velocidade do vídeo inteiro.",
    {"properties": {
        "projeto": {"type": "string"},
        "blocos": {"type": "array", "items": {"type": "object", "properties": {
            "bloco": {"type": "string", "description": "clip_id"},
            "velocidade": {"type": "number"},
            "zoom": {"type": "number"},
            "etapa": {"type": "string", "enum": list(ETAPAS)}},
            "required": ["bloco"]}},
        "global": {"type": "number", "description": "multiplicador do vídeo inteiro (opcional)"}},
     "required": ["projeto"]},
)
def ritmo(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    antes = ((_projeto(c, pid).get("timeline") or {}).get("duration")) or 0.0
    feitos, recusados, avisos = 0, [], []
    if a.get("global") is not None:
        c.post(f"/api/projects/{pid}/ops/speed", {"global": float(a["global"])})
        feitos += 1
    for b in a.get("blocos") or []:
        cid = str(b.get("bloco") or "")
        try:
            if b.get("etapa"):
                c.post(f"/api/projects/{pid}/ops/section",
                       {"clip_id": cid, "section": str(b["etapa"])})
            if b.get("velocidade") is not None:
                r = c.post(f"/api/projects/{pid}/ops/speed",
                           {"clip_id": cid, "speed": float(b["velocidade"])})
                if r.get("warn"):
                    avisos.append(f"{cid}: {r.get('warn_message')}")
            if b.get("zoom") is not None:
                c.post(f"/api/projects/{pid}/ops/zoom",
                       {"clip_id": cid, "zoom": float(b["zoom"])})
            feitos += 1
        except Exception as exc:  # noqa: BLE001 — um bloco ruim não derruba os outros
            recusados.append(f"{cid}: {exc}")
    depois = ((_projeto(c, pid).get("timeline") or {}).get("duration")) or 0.0
    linhas = [f"{feitos} ajuste(s) de ritmo/câmera: {_seg(antes)} → {_seg(depois)}"]
    linhas += [f"atenção: {x}" for x in avisos]
    linhas += [f"recusado: {x}" for x in recusados]
    return "\n".join(linhas)


@ferramenta(
    "legendas",
    "A LEGENDA, de ponta a ponta. acao='ver': as legendas com id e tempo; "
    "'corrigir': troca uma palavra ou expressão mal transcrita EM TODO o vídeo "
    "(e nos próximos — vai para o dicionário de correções), ex.: errado='air "
    "bnb', certo='Airbnb'; 'editar': reescreve uma legenda pelo id; 'estilo': "
    "tamanho (0.6–1.6), posicao (baixo/meio/alto), maiusculas, cor (#RRGGBB) "
    "e ligada (false = vídeo sem legenda).",
    {"properties": {
        "projeto": {"type": "string"},
        "acao": {"type": "string", "enum": ["ver", "corrigir", "editar", "estilo"]},
        "de": {"type": "number", "description": "ver: segundo inicial"},
        "ate": {"type": "number", "description": "ver: segundo final"},
        "errado": {"type": "string"}, "certo": {"type": "string"},
        "id": {"type": "string"}, "texto": {"type": "string"},
        "tamanho": {"type": "number"},
        "posicao": {"type": "string", "enum": ["baixo", "meio", "alto"]},
        "maiusculas": {"type": "boolean"},
        "cor": {"type": "string"},
        "ligada": {"type": "boolean"}},
     "required": ["projeto", "acao"]},
)
def legendas(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    acao = str(a.get("acao") or "ver")
    if acao == "corrigir":
        errado, certo = str(a.get("errado") or "").strip(), str(a.get("certo") or "").strip()
        if not errado or not certo:
            return "diga 'errado' e 'certo'."
        c.post("/api/corrections", {"from": errado, "to": certo})
        r = c.post(f"/api/projects/{pid}/subtitles/rebuild")
        return (f"'{errado}' → '{certo}' em todo o vídeo (e guardado no dicionário "
                f"para os próximos). {len(r.get('subtitles') or [])} legendas refeitas.")
    if acao == "editar":
        if not a.get("id"):
            return "diga o id da legenda (veja com acao='ver')."
        r = c.put(f"/api/projects/{pid}/subtitles/{a['id']}", {"text": str(a.get("texto") or "")})
        return f"legenda {a['id']}: \"{r['subtitle']['text']}\""
    if acao == "estilo":
        p = _projeto(c, pid)
        altura = float(((p.get("info") or {}).get("display_size") or [0, 1920])[1]
                       or (p.get("info") or {}).get("height") or 1920)
        estilo: dict = {}
        if a.get("tamanho") is not None:
            estilo["fontsize_scale"] = max(0.6, min(1.6, float(a["tamanho"])))
        if a.get("posicao"):
            estilo.update({"baixo": {"align": 2, "margin_v": int(altura * 0.12)},
                           "meio": {"align": 5, "margin_v": 0},
                           "alto": {"align": 8, "margin_v": int(altura * 0.1)}}
                          [str(a["posicao"])])
        if a.get("maiusculas") is not None:
            estilo["uppercase"] = bool(a["maiusculas"])
        if a.get("cor"):
            estilo["primary"] = str(a["cor"])
        corpo: dict = {"style": estilo, "rebuild_subtitles": True}
        if a.get("ligada") is not None:
            corpo["export"] = {"burn_subtitles": bool(a["ligada"])}
        c.post(f"/api/projects/{pid}/params", corpo)
        return "estilo da legenda aplicado: " + ", ".join(
            f"{k}={v}" for k, v in {**estilo, **corpo.get("export", {})}.items())
    subs = ((_projeto(c, pid).get("timeline") or {}).get("subtitles")) or []
    de = float(a.get("de") or 0.0)
    ate = float(a.get("ate") if a.get("ate") is not None else 1e9)
    vis = [s for s in subs if s["end"] >= de and s["start"] <= ate]
    if not vis:
        return "nenhuma legenda nesse intervalo."
    return "\n".join(f"{s['id']}  {s['start']:.2f}–{s['end']:.2f}  "
                     f"{s['text'].replace(chr(10), ' / ')}" for s in vis[:200])


@ferramenta(
    "devolver",
    "Devolve ao vídeo palavras que o corte (automático ou seu) tirou — pelos "
    "números da transcrição (os marcados com ~). Use quando o corte comeu uma "
    "palavra ou quando um take descartado era o bom.",
    {"properties": {"projeto": {"type": "string"},
                    "palavras": {"type": "array", "items": {"type": "integer"}}},
     "required": ["projeto", "palavras"]},
)
def devolver(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    ids = [int(i) for i in a.get("palavras") or []]
    if not ids:
        return "diga os números das palavras."
    antes = ((_projeto(c, pid).get("timeline") or {}).get("duration")) or 0.0
    c.post(f"/api/projects/{pid}/ops/restore-words", {"word_ids": ids})
    depois = ((_projeto(c, pid).get("timeline") or {}).get("duration")) or 0.0
    return f"{len(ids)} palavra(s) de volta: {_seg(antes)} → {_seg(depois)}"


@ferramenta(
    "respiro",
    "O FÔLEGO DO CORTE DE SILÊNCIO do vídeo inteiro: 0 = corte seco, falas "
    "coladas (ritmo de anúncio); 1 = deixa respiro entre as frases. Refaz o "
    "corte automático mantendo o que foi cortado ou devolvido à mão. Use ANTES "
    "da pós-edição (gráficos e camadas vivem no tempo do vídeo final).",
    {"properties": {"projeto": {"type": "string"},
                    "corte": {"type": "number", "description": "0 a 1"}},
     "required": ["projeto", "corte"]},
)
def respiro(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    valor = max(0.0, min(1.0, float(a["corte"])))
    antes = ((_projeto(c, pid).get("timeline") or {}).get("duration")) or 0.0
    c.post(f"/api/projects/{pid}/params", {"cut": {"aggressiveness": valor}})
    job = c.post(f"/api/projects/{pid}/autoedit")
    fim = c.esperar_job(pid, job.get("id", ""), limite=900)
    ruim = _falhou(fim)
    if ruim:
        return f"refazer o corte {ruim}"
    depois = ((_projeto(c, pid).get("timeline") or {}).get("duration")) or 0.0
    return f"corte refeito com fôlego {valor:.2f}: {_seg(antes)} → {_seg(depois)}"


# ------------------------------------------------------------- pós-edição
# O Claude como editor: lê o roteiro com os tempos do vídeo FINAL, olha os
# quadros de verdade, sabe onde a pessoa está, e põe por cima o que o corte
# automático não entrega — títulos, telas de tópico, listas, números,
# transições, fundo desfocado, texto atrás da pessoa.

def _itens_da_pos(d: dict) -> list[str]:
    linhas = []
    for g in d.get("graficos") or []:
        linhas.append(f"  gráfico {g['id']}: {g['tipo']} {_seg(g['out_start'])}–"
                      f"{_seg(g['out_end'])} \"{(g.get('texto') or '')[:40]}\" "
                      f"({g['estilo']}, entra {g['entrada']}"
                      f"{', ATRÁS da pessoa' if g.get('camada') == 'atras' else ''})")
    for x in d.get("camadas") or []:
        linhas.append(f"  camada {x['id']}: {x['efeito']} {_seg(x['out_start'])}–"
                      f"{_seg(x['out_end'])} força {x['forca']}")
    for t in d.get("transicoes") or []:
        linhas.append(f"  transição {t['id']}: {t['tipo']} {t['duracao']} s "
                      f"na entrada do bloco {t['clip_id']}")
    for c in d.get("cenas") or []:
        linhas.append(f"  cena {c['id']}: {c['tipo']} {_seg(c['out_start'])}–"
                      f"{_seg(c['out_end'])}" + (f" lado {c['lado']}" if c.get("lado") else "")
                      + (f" fundo {c['fundo']}" if c.get("fundo") else "")
                      + (f" logos {', '.join(c['logos'])}" if c.get("logos") else ""))
    return linhas


@ferramenta(
    "pos_contexto",
    "O ROTEIRO DA PÓS-EDIÇÃO: o que é dito em cada segundo do vídeo FINAL "
    "(frases com início e fim), os blocos e onde ficam as emendas (é nelas que "
    "entram transições), o formato do quadro, a faixa da legenda (onde gráfico "
    "não deve entrar), os b-rolls e o que já foi posto na pós. Leia isto antes "
    "de pôr qualquer gráfico — os tempos das outras ferramentas de pós são "
    "estes.",
    {"properties": {"projeto": {"type": "string"},
                    "de": {"type": "number", "description": "segundo inicial (opcional)"},
                    "ate": {"type": "number", "description": "segundo final (opcional)"}},
     "required": ["projeto"]},
)
def pos_contexto(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    d = c.get(f"/api/projects/{pid}/pos/contexto")
    de = float(a.get("de") or 0.0)
    ate = float(a.get("ate") if a.get("ate") is not None else 1e9)
    f = d.get("formato") or {}
    leg = d.get("legenda")
    rec = d.get("recorte") or {}
    linhas = [
        d.get("marca") or "Nenhuma marca ligada.",
        "",
        f"vídeo final: {_seg(d.get('duracao'))}, quadro {f.get('largura')}x{f.get('altura')}"
        f" ({f.get('proporcao')})",
        (f"legenda queimada ocupa a faixa y={leg['de']}–{leg['ate']} da altura: "
         f"não ponha gráfico ali" if leg else "sem legenda queimada"),
        ("recorte da pessoa: pronto (camadas e gráfico atrás da pessoa funcionam)"
         if rec.get("pronto") else
         "recorte da pessoa: " + ("modelo ainda não baixado (baixa sozinho no "
                                  "primeiro uso)" if rec.get("runtime")
                                  else "falta o onnxruntime — camadas não saem")),
        f"editor: {d.get('editor') or 'padrão'}",
        "",
        "BLOCOS (clip_id · tempo no vídeo final · velocidade · zoom · etapa — "
        "a transição entra na emenda de ENTRADA do bloco):",
    ]
    for b in d.get("blocos") or []:
        if b["fim"] < de or b["inicio"] > ate:
            continue
        linhas.append(f"  {b['clip_id']}  {b['inicio']:.2f}–{b['fim']:.2f}  "
                      f"{b.get('velocidade', 1)}x zoom {b.get('zoom', 1)} "
                      f"{b.get('etapa') or '-'}  {b['gravacao']}"
                      f"{'' if b['tipo'] in ('video', 'speech') else ' (' + b['tipo'] + ')'}"
                      + (f"\n      \"{b['texto'][:160]}\"" if b.get("texto") else ""))
    if d.get("brolls"):
        linhas.append("B-ROLLS (a pessoa não aparece nestes intervalos):")
        linhas += [f"  {x['inicio']:.2f}–{x['fim']:.2f} {x.get('termo') or ''}"
                   for x in d["brolls"] if not (x["fim"] < de or x["inicio"] > ate)]
    linhas.append("FALA (início–fim: frase):")
    linhas += [f"  {x['inicio']:.2f}–{x['fim']:.2f}: {x['texto']}"
               for x in d.get("frases") or [] if not (x["fim"] < de or x["inicio"] > ate)]
    ja = _itens_da_pos(d)
    linhas.append("JÁ NA PÓS:" if ja else "JÁ NA PÓS: nada ainda")
    linhas += ja
    return "\n".join(linhas)


@ferramenta(
    "ver_quadros",
    "SEUS OLHOS: devolve as IMAGENS do vídeo final nos segundos pedidos — o "
    "encode de verdade parado naquele quadro, com gráficos, camadas, "
    "transições, filtro e legenda. Use para conferir cada coisa que puser "
    "(título legível? cobriu o rosto? a transição ficou boa?) e para ver o "
    "ambiente antes de decidir. Até 6 instantes por vez.",
    {"properties": {"projeto": {"type": "string"},
                    "tempos": {"type": "array", "items": {"type": "number"},
                               "description": "segundos do vídeo final"},
                    "lado": {"type": "integer",
                             "description": "lado menor da imagem em px (padrão 540)"}},
     "required": ["projeto", "tempos"]},
)
def ver_quadros(c: Cliente, a: dict):
    pid = str(a.get("projeto") or "")
    tempos = [float(t) for t in (a.get("tempos") or [])][:6]
    if not tempos:
        return "diga em que segundos quer ver (tempos)."
    r = c.post(f"/api/projects/{pid}/pos/quadros",
               {"tempos": tempos, "lado": int(a.get("lado") or 540)}, timeout=600)
    conteudo = []
    for q in r.get("quadros") or []:
        aviso = ("  (" + "; ".join(q["avisos"]) + ")") if q.get("avisos") else ""
        conteudo.append({"type": "text", "text": f"quadro em {q['t']:.2f} s{aviso}"})
        conteudo.append({"type": "image", "data": q["jpeg_b64"], "mimeType": "image/jpeg"})
    return {"content": conteudo or [{"type": "text", "text": "nenhum quadro saiu"}],
            "isError": False}


@ferramenta(
    "analisar_cena",
    "A PROFUNDIDADE do quadro num instante: se há pessoa, onde ela está "
    "(caixa, centro, topo da cabeça, quanto ocupa em cada terço do quadro), "
    "que regiões estão LIVRES para gráfico sem cobrir ninguém, a luz, o que "
    "está sendo dito ali e se é b-roll. Use para decidir a posição (x, y) dos "
    "gráficos e se vale pôr texto ATRÁS da pessoa (camada=atras).",
    {"properties": {"projeto": {"type": "string"},
                    "tempo": {"type": "number", "description": "segundo do vídeo final"}},
     "required": ["projeto", "tempo"]},
)
def analisar_cena(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    d = c.get(f"/api/projects/{pid}/pos/cena", t=float(a.get("tempo") or 0.0))
    linhas = [f"cena em {d['t']:.2f} s — bloco {d.get('bloco')}"
              f"{' (b-roll)' if d.get('e_broll') else ''}, luz {d.get('luz')}"]
    p = d.get("pessoa")
    if p is None:
        linhas.append(d.get("aviso") or "sem informação da pessoa")
    elif not p.get("presente"):
        linhas.append("sem pessoa no quadro: o quadro inteiro está livre")
    else:
        cx0, cy0, cx1, cy1 = p["caixa"]
        linhas.append(f"pessoa ocupa {int(p['ocupa'] * 100)}% do quadro; caixa "
                      f"x {cx0}–{cx1}, y {cy0}–{cy1}; centro {p['centro']}; "
                      f"topo da cabeça em y={p['topo_da_cabeca']}")
        g = p["grade_3x3"]
        linhas.append("pessoa por terço (alto/meio/baixo × esquerda/centro/direita): "
                      + " | ".join(" ".join(f"{v:.2f}" for v in linha) for linha in g))
    if d.get("livre") is not None:
        linhas.append("livre para gráfico: " + (", ".join(d["livre"]) or "quase nada — "
                      "use gráfico pequeno, ou camada=atras"))
    for f in d.get("fala") or []:
        linhas.append(f"fala {f['inicio']:.2f}–{f['fim']:.2f}: {f['texto']}")
    return "\n".join(linhas)


_ESQUEMA_GRAFICO = {
    "projeto": {"type": "string"},
    "id": {"type": "string", "description": "para MUDAR um gráfico que já existe"},
    "tipo": {"type": "string",
             "enum": ["titulo", "tela", "lista", "comparacao", "destaque", "numero", "texto",
                      "nome", "seta", "circulo", "barra", "logo", "barras",
                      "linha", "rosca", "icone"],
             "description": "titulo: título com barra de destaque; tela: TELA "
                            "CHEIA de tópico/capítulo (cobre o vídeo; aceita "
                            "prefixo 'PARTE 2' e itens); lista: tópicos que "
                            "entram um a um; destaque: palavra-chave em adesivo; "
                            "numero: contador que sobe até 'numero' (prefixo "
                            "'R$ ', sufixo '%'); texto: caixa de texto; nome: "
                            "lower third (texto=nome, subtexto=função; x,y = "
                            "borda ESQUERDA); seta: aponta para (x,y) — a PONTA "
                            "— na direção 'angulo'; circulo: anel em volta de "
                            "(x,y); barra: progresso ('numero' em %); logo: a "
                            "IMAGEM de um logo (campo logo = o nome, veja a "
                            "ferramenta marca), no centro (x,y), com opacidade — "
                            "de lado (x 0.08 ou 0.92) e transparente é o padrão "
                            "bonito; camada=atras o põe ATRÁS da pessoa. "
                            "GRÁFICOS DE DADOS (animados, em vidro): barras = "
                            "colunas que SOBEM uma a uma com o número contando "
                            "(valores + rotulos; a última em destaque); linha = "
                            "a linha que se DESENHA subindo, com o valor final "
                            "contando (valores, prefixo/sufixo); rosca = anel que "
                            "enche até 'numero' % com o número no meio (texto = "
                            "legenda); icone = ícone DESENHADO no traço (campo "
                            "icone: check, x, seta_cima, seta_baixo, casa, "
                            "casa_x (vulnerável), cadeado, chave, dinheiro, "
                            "relogio, estrela, alerta, calendario, pessoa, "
                            "grafico; texto = rótulo). O ícone nasce num disco "
                            "SÓLIDO da cor da marca com o traço branco desenhado "
                            "na hora"},
    "inicio": {"type": "number", "description": "segundo do vídeo final"},
    "fim": {"type": "number", "description": "segundo do vídeo final"},
    "texto": {"type": "string"},
    "subtexto": {"type": "string"},
    "itens": {"type": "array", "items": {"type": "string"},
              "description": "lista/tela: os tópicos (até 6)"},
    "itens_em": {"type": "array", "items": {"type": "number"},
                 "description": "opcional: em que segundo (desde o INÍCIO do "
                                "gráfico) cada item entra — para casar com a fala"},
    "x": {"type": "number", "description": "0–1, centro na largura"},
    "y": {"type": "number", "description": "0–1, centro na altura"},
    "tamanho": {"type": "number", "description": "0.4–2.5 (1 = normal)"},
    "estilo": {"type": "string", "enum": ["vidro", "limpo", "editorial", "escuro", "claro", "neon", "marca"],
               "description": "vidro (PADRÃO) = painel translúcido, a imagem "
                              "aparece por trás; limpo = só o texto, com sombra. "
                              "escuro/claro/marca/neon são CARTÕES SÓLIDOS: ele não "
                              "quer cartão sólido por cima dele — o Sharkcut troca "
                              "por vidro, exceto em tipo=tela e no lado livre de "
                              "uma moldura"},
    "cor": {"type": "string", "description": "#RRGGBB da cor de destaque"},
    "entrada": {"type": "string", "enum": ["pop", "slide", "subir", "3d", "digitar", "fade", "cinema", "linhas"]},
    "saida": {"type": "string", "enum": ["fade", "slide", "pop", "corte"]},
    "camada": {"type": "string", "enum": ["frente", "atras"],
               "description": "atras = o gráfico passa ATRÁS da pessoa (precisa do recorte)"},
    "numero": {"type": "number"},
    "prefixo": {"type": "string"},
    "sufixo": {"type": "string"},
    "angulo": {"type": "number", "description": "seta: 0 direita, 90 baixo, 180 esquerda, -90 cima"},
    "logo": {"type": "string", "description": "tipo logo: o nome do logo (veja marca)"},
    "opacidade": {"type": "number", "description": "tipo logo: 0.1–1 (padrão 1)"},
    "valores": {"type": "array", "items": {"type": "number"},
                "description": "barras/linha: os números, em ordem (até 8) — só os "
                               "que a fala diz; nunca invente número"},
    "rotulos": {"type": "array", "items": {"type": "string"},
                "description": "barras/linha: o nome de cada valor (jan, fev… ou "
                               "'antes', 'depois'), curtos"},
    "icone": {"type": "string",
              "enum": ["check", "x", "seta_cima", "seta_baixo", "casa", "casa_x",
                       "cadeado", "chave", "dinheiro", "relogio", "estrela", "alerta",
                       "calendario", "pessoa", "grafico"],
              "description": "tipo icone: qual desenho"},
}


@ferramenta(
    "grafico",
    "Põe (ou muda, com id) um GRÁFICO ANIMADO no vídeo final — o After "
    "Effects do Sharkcut, desenhado no mesmo encode, sem perda de qualidade. "
    "Tempos em segundos do vídeo FINAL (os de pos_contexto). Posição e tamanho "
    "em fração do quadro. Depois de pôr, confira com ver_quadros.",
    {"properties": _ESQUEMA_GRAFICO, "required": ["projeto"]},
)
def grafico(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    gid = str(a.get("id") or "")
    corpo = {k: a[k] for k in ("tipo", "texto", "subtexto", "x", "y", "tamanho",
                               "estilo", "cor", "entrada", "saida", "camada",
                               "numero", "prefixo", "sufixo", "angulo", "logo",
                               "opacidade", "valores", "rotulos", "icone")
             if a.get(k) is not None}
    if a.get("inicio") is not None:
        corpo["out_start"] = float(a["inicio"])
    if a.get("fim") is not None:
        corpo["out_end"] = float(a["fim"])
    if a.get("itens") is not None:
        em = list(a.get("itens_em") or [])
        corpo["itens"] = [({"texto": t, "em": em[i]} if i < len(em) else t)
                          for i, t in enumerate(a["itens"])]
    if not gid:
        corpo["origem"] = c.origem
        if "out_start" not in corpo:
            return "faltou o início (segundo do vídeo final)."
        r = c.post(f"/api/projects/{pid}/pos/graficos", corpo)
    else:
        r = c.put(f"/api/projects/{pid}/pos/graficos/{gid}", corpo)
    g = r["item"]
    if g["tipo"] == "logo":
        return (f"logo {g['id']} ({g['logo']}) de {g['out_start']:.2f} a {g['out_end']:.2f} s, "
                f"opacidade {g['opacidade']}{' — ATRÁS da pessoa' if g['camada'] == 'atras' else ''}. "
                f"Confira com ver_quadros em {min(g['out_end'], g['out_start'] + 1.0):.2f}.")
    troca = ""
    if a.get("estilo") and a.get("estilo") != g.get("estilo"):
        troca = (f" Estilo {a['estilo']} trocado por {g.get('estilo')}: cartão sólido "
                 f"por cima da pessoa, não (é o que ele pediu).")
    return (f"gráfico {g['id']} ({g['tipo']}) de {g['out_start']:.2f} a "
            f"{g['out_end']:.2f} s{' — ATRÁS da pessoa' if g['camada'] == 'atras' else ''}."
            f"{troca} Confira com ver_quadros em {min(g['out_end'], g['out_start'] + 1.0):.2f}.")


@ferramenta(
    "camada",
    "Separa a PESSOA do FUNDO (recorte por IA, na máquina) num intervalo e "
    "trata o fundo: desfoque = lente aberta, pessoa nítida; escurecer = "
    "holofote na pessoa; parallax = a pessoa salta para a frente do fundo "
    "(efeito 3D); recorte = a pessoa recortada sobre cor lisa com contorno. "
    "Com id, muda uma camada que já existe.",
    {"properties": {"projeto": {"type": "string"},
                    "id": {"type": "string"},
                    "efeito": {"type": "string",
                               "enum": ["desfoque", "escurecer", "parallax", "recorte"]},
                    "inicio": {"type": "number"}, "fim": {"type": "number"},
                    "forca": {"type": "number", "description": "0–1 (padrão 0.6)"}},
     "required": ["projeto"]},
)
def camada(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    corpo = {k: a[k] for k in ("efeito", "forca") if a.get(k) is not None}
    if a.get("inicio") is not None:
        corpo["out_start"] = float(a["inicio"])
    if a.get("fim") is not None:
        corpo["out_end"] = float(a["fim"])
    if a.get("id"):
        r = c.put(f"/api/projects/{pid}/pos/camadas/{a['id']}", corpo)
    else:
        if "out_start" not in corpo:
            return "faltou o início."
        corpo["origem"] = c.origem
        r = c.post(f"/api/projects/{pid}/pos/camadas", corpo)
    x = r["item"]
    rec = r.get("recorte") or {}
    aviso = "" if rec.get("pronto") or rec.get("runtime") else (
        " ATENÇÃO: falta o onnxruntime nesta máquina — a camada não vai sair "
        "até ele rodar o instalar.bat de novo.")
    return (f"camada {x['id']}: {x['efeito']} de {x['out_start']:.2f} a "
            f"{x['out_end']:.2f} s, força {x['forca']}.{aviso}")


@ferramenta(
    "transicao",
    "Põe uma TRANSIÇÃO na emenda entre dois blocos: zoom (mergulho), chicote "
    "(whip pan), flash, glitch, desfoque, luz (light leak) ou giro. Não é "
    "crossfade: o corte continua seco e a fala intacta; o efeito cresce até a "
    "emenda e se desfaz depois. Diga o bloco que ENTRA (clip_id) ou um tempo "
    "perto da emenda. Uma por emenda — a nova substitui a antiga.",
    {"properties": {"projeto": {"type": "string"},
                    "id": {"type": "string"},
                    "bloco": {"type": "string", "description": "clip_id do bloco que entra"},
                    "tempo": {"type": "number", "description": "ou: um segundo perto da emenda"},
                    "tipo": {"type": "string",
                             "enum": ["zoom", "chicote", "flash", "glitch",
                                      "desfoque", "luz", "giro"]},
                    "duracao": {"type": "number", "description": "0.1–1.2 s (padrão 0.4)"}},
     "required": ["projeto"]},
)
def transicao(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    corpo = {k: a[k] for k in ("tipo", "duracao", "tempo") if a.get(k) is not None}
    if a.get("bloco"):
        corpo["clip_id"] = str(a["bloco"])
    if a.get("id"):
        r = c.put(f"/api/projects/{pid}/pos/transicoes/{a['id']}", corpo)
    else:
        if "clip_id" not in corpo and "tempo" not in corpo:
            return "diga o bloco que entra (bloco) ou um tempo perto da emenda (tempo)."
        corpo["origem"] = c.origem
        r = c.post(f"/api/projects/{pid}/pos/transicoes", corpo)
    x = r["item"]
    return (f"transição {x['id']}: {x['tipo']} de {x['duracao']} s na entrada do "
            f"bloco {x['clip_id']}.")


@ferramenta(
    "marca",
    "A MARCA do vídeo: o nome na grafia exata, as cores (hex), a fonte, as "
    "regras de uso, a voz e os LOGOS disponíveis (os nomes que vão no gráfico "
    "tipo logo e na cena vidro3d). Leia antes de pôr qualquer gráfico: tudo "
    "que você puser tem de parecer da marca.",
    {"properties": {"projeto": {"type": "string",
                                "description": "opcional: a marca deste vídeo"}}},
)
def marca(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    if pid:
        return c.get(f"/api/projects/{pid}/pos/contexto").get("marca") or "Nenhuma marca ligada."
    d = c.get("/api/marca")
    kit = d.get("kit")
    if not kit:
        return ("Nenhuma marca ligada. Logos disponíveis: "
                + (", ".join(sorted(d.get("logos") or {})) or "nenhum"))
    linhas = [f"MARCA: {kit['nome']}", "CORES: " + ", ".join(f"{k} {v}" for k, v in kit["cores"].items())]
    linhas += [f"- {r}" for r in kit.get("regras") or []]
    linhas.append("LOGOS: " + ", ".join(sorted(d.get("logos") or {})))
    return "\n".join(linhas)


@ferramenta(
    "cena",
    "Uma CENA: o quadro inteiro muda de arranjo por alguns segundos (mínimo "
    "1,5 s; uma cena por vez). moldura = o vídeo encolhe, da tela cheia, para "
    "um cartão de canto largo de um LADO (direita/esquerda; no vertical, "
    "baixo/cima) sobre o fundo (desfoque = o próprio vídeo desfocado; marca; "
    "claro; escuro) — o outro lado fica LIVRE: ponha ali os gráficos que "
    "explicam (x≈0.26 com o vídeo à direita). No fim, o cartão volta à tela. "
    "vidro3d = camadas de vidro girando: REPROVADA pelo dono (pirotécnica, "
    "sem sentido para o produto) — não use, a menos que ele peça com essas "
    "palavras; para 3D que explica o produto use arte_3d acao=objeto. Com id, "
    "muda uma cena.",
    {"properties": {"projeto": {"type": "string"},
                    "id": {"type": "string"},
                    "tipo": {"type": "string", "enum": ["moldura", "vidro3d"]},
                    "inicio": {"type": "number"}, "fim": {"type": "number"},
                    "lado": {"type": "string", "enum": ["direita", "esquerda", "cima", "baixo"],
                             "description": "moldura: onde fica o vídeo; vidro3d: para onde gira"},
                    "fundo": {"type": "string",
                              "description": "desfoque | marca | claro | escuro | #RRGGBB"},
                    "logos": {"type": "array", "items": {"type": "string"},
                              "description": "vidro3d: os logos da placa do meio (até 4)"},
                    "forca": {"type": "number",
                              "description": "0–1: moldura = tamanho do cartão; vidro3d = "
                                             "o quanto gira e abre"}},
     "required": ["projeto"]},
)
def cena(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    corpo = {k: a[k] for k in ("tipo", "lado", "fundo", "logos", "forca")
             if a.get(k) is not None}
    if a.get("inicio") is not None:
        corpo["out_start"] = float(a["inicio"])
    if a.get("fim") is not None:
        corpo["out_end"] = float(a["fim"])
    if a.get("id"):
        r = c.put(f"/api/projects/{pid}/pos/cenas/{a['id']}", corpo)
    else:
        if "out_start" not in corpo:
            return "faltou o início."
        corpo["origem"] = c.origem
        r = c.post(f"/api/projects/{pid}/pos/cenas", corpo)
    x = r["item"]
    rec = r.get("recorte") or {}
    aviso = ""
    if x["tipo"] == "vidro3d" and not (rec.get("pronto") or rec.get("runtime")):
        aviso = " ATENÇÃO: sem o recorte da pessoa, as placas saem sem a pessoa separada."
    meio = (x["out_start"] + x["out_end"]) / 2
    return (f"cena {x['id']}: {x['tipo']} de {x['out_start']:.2f} a {x['out_end']:.2f} s"
            + (f", lado {x['lado']}" if x.get("lado") else "")
            + (f", logos {', '.join(x['logos'])}" if x.get("logos") else "")
            + f".{aviso} Confira com ver_quadros em {x['out_start'] + 0.4:.2f}, {meio:.2f} "
              f"e {x['out_end'] - 0.3:.2f}.")


@ferramenta(
    "tirar_da_pos",
    "Tira itens da pós-edição (gráficos, camadas, transições, cenas) pelos ids — ou "
    "todos os que VOCÊ pôs (tudo=true), sem mexer no que ele pôs à mão.",
    {"properties": {"projeto": {"type": "string"},
                    "ids": {"type": "array", "items": {"type": "string"}},
                    "tudo": {"type": "boolean"}},
     "required": ["projeto"]},
)
def tirar_da_pos(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    r = c.post(f"/api/projects/{pid}/pos/tirar",
               {"ids": list(a.get("ids") or []), "tudo": bool(a.get("tudo")),
                "origem": c.origem if a.get("tudo") else ""})
    return f"{r.get('tirados', 0)} item(ns) tirado(s) da pós."


@ferramenta(
    "direcao",
    "Plano editorial e revisão: ler informa o plano e os tempos a conferir; "
    "planejar registra objetivo, linguagem e momentos da montagem FINAL; "
    "revisar registra seu parecer após ver_quadros da versão atual. "
    "Planeje antes de compor e atualize o plano se mudar o corte.",
    {"properties": {
        "projeto": {"type": "string"},
        "acao": {"type": "string", "enum": ["ler", "planejar", "revisar"]},
        "objetivo": {"type": "string"}, "linguagem": {"type": "string"},
        "parecer": {"type": "string"},
        "momentos": {"type": "array", "maxItems": 40, "items": {"type": "object",
            "properties": {"inicio": {"type": "number"}, "fim": {"type": "number"},
                           "fala": {"type": "string"}, "intencao": {"type": "string"}},
            "required": ["inicio", "fim", "fala", "intencao"]}}},
     "required": ["projeto", "acao"]},
)
def direcao(c: Cliente, a: dict) -> str:
    import json
    rota = f"/api/projects/{a['projeto']}/direcao"
    r = c.get(rota) if a.get("acao") == "ler" else c.post(rota, a)
    return json.dumps(r, ensure_ascii=False)


@ferramenta(
    "compor",
    "Cria uma composição coordenada: gancho/fechamento com título em linhas, "
    "comparacao em duas colunas que entram na fala (rotulos nomeia cada lado), passos com itens sincronizados, "
    "prova com número fiel à fala. Leia analisar_cena para escolher x/y. "
    "itens.em é o atraso em segundos desde inicio; depois confira com ver_quadros.",
    {"properties": {
        "projeto": {"type": "string"},
        "tipo": {"type": "string", "enum": ["gancho", "comparacao", "passos", "prova", "fechamento"]},
        "inicio": {"type": "number"}, "fim": {"type": "number"},
        "titulo": {"type": "string"}, "apoio": {"type": "string"},
        "x": {"type": "number"}, "y": {"type": "number"},
        "estilo": {"type": "string", "enum": ["limpo", "editorial", "vidro", "marca", "claro", "escuro"]},
        "numero": {"type": "number"}, "prefixo": {"type": "string"}, "sufixo": {"type": "string"},
        "rotulos": {"type": "array", "maxItems": 2, "items": {"type": "string"}},
        "itens": {"type": "array", "maxItems": 5, "items": {"type": "object",
            "properties": {"texto": {"type": "string"}, "em": {"type": "number"}},
            "required": ["texto", "em"]}}},
     "required": ["projeto", "tipo", "inicio", "fim", "titulo"]},
)
def compor(c: Cliente, a: dict) -> str:
    import json
    r = c.post(f"/api/projects/{a['projeto']}/pos/compor", {**a, "origem": c.origem})
    return json.dumps(r, ensure_ascii=False)


@ferramenta(
    "arte",
    "Composição VETORIAL NATIVA. catalogo revela recursos e exemplo completo; criar/atualizar aceita "
    "modelo (fluxo, tipografia, orbita, grafico) OU composicao livre. Elementos: grupo, texto, numero, "
    "retangulo, elipse, tracado; id único, pai opcional declarado antes. tela=[1000,1000]; x/y em "
    "unidades dessa prancheta, filhos locais ao grupo. marcos=[{t, x?,y?,escala?,rotacao?,opacidade?, "
    "largura?,altura?,progresso?,morph?,valor?,curva?}], tempo relativo à composição; curvas linear, "
    "suave, entrada, saida, organica, salto. Formas têm cor,contorno,espessura,raio; texto tem texto, "
    "corpo,fonte opcional,negrito; numero usa valor,prefixo,sufixo,casas. tracado usa pontos=[[x,y],...] "
    "e fechado; progresso desenha o traço, pontos_fim+morph transformam a forma. Cor #RRGGBB ou "
    "marca/texto/fundo/nenhuma. inicio/fim dos elementos também são relativos. Até 48 elementos, "
    "60 s. x/y externos normalizados posicionam a composição inteira. Planeje e confira com ver_quadros.",
    {"properties": {"projeto":{"type":"string"}, "acao":{"type":"string","enum":["catalogo","criar","atualizar"]},
        "id":{"type":"string"},"nome":{"type":"string"},"inicio":{"type":"number"},"fim":{"type":"number"},
        "modelo":{"type":"string","enum":["fluxo","tipografia","orbita","grafico"]},
        "composicao":{"type":"object","properties":{"versao":{"type":"integer"},
            "tela":{"type":"array","items":{"type":"number"},"minItems":2,"maxItems":2},
            "elementos":{"type":"array","maxItems":48,"items":{"type":"object"}}},"required":["elementos"]},
        "x":{"type":"number"},"y":{"type":"number"},"tamanho":{"type":"number"},
        "camada":{"type":"string","enum":["frente","atras"]},"estilo":{"type":"string"}},
     "required":["projeto","acao"]},
)
def arte(c: Cliente, a: dict) -> str:
    import json
    if a.get("acao")=="catalogo":
        return json.dumps(c.get(f"/api/projects/{a['projeto']}/arte"),ensure_ascii=False)
    if a.get("acao")=="atualizar" and not a.get("id"):
        return "informe id para atualizar a arte existente"
    r=c.post(f"/api/projects/{a['projeto']}/pos/arte",{**a,"origem":c.origem})
    return json.dumps(r,ensure_ascii=False)


@ferramenta(
    "gosto",
    "O GOSTO DO DONO — leia ANTES de planejar a pós. Traz as notas que ele "
    "escreveu sobre o gosto dele e o que ele já APAGOU ou TROCOU do que a IA "
    "pôs em edições anteriores (aprendido da mão dele). O que ele apagou não "
    "volta; o que ele trocou, use já trocado. Vale acima da habilidade.",
    {"properties": {}},
)
def gosto(c: Cliente, a: dict) -> str:
    return str(c.get("/api/gosto").get("resumo") or "")


@ferramenta(
    "arte_3d",
    "Blender LOCAL (sem serviço pago), saída com fundo transparente na linha do tempo. "
    "PREFIRA acao=objeto: um OBJETO 3D PRONTO, modelado com acabamento de ícone 3D premium, "
    "nas cores da marca do vídeo, montando peça por peça e girando pouco (nunca uma volta). "
    "objeto: casa (imóvel de temporada, com piscina e guarda-sol), predio (apartamento), "
    "chave (check-in, entrega da chave), cadeado (segurança, proteção), escudo (garantia, "
    "proteção), documento (contrato, termo assinado), celular (app, mensagem), calendario "
    "(reserva, data, prazo), check (aprovado, confirmado), estrela (avaliação), grafico "
    "(crescimento, resultado), mala (hóspede, viagem). Use quando a FALA nomeia o conceito — "
    "ele ilustra o produto; não é enfeite. lado=esquerda|direita (o lado livre, longe do "
    "rosto) ou centro (tela cheia/moldura); duracao 2–4 s; animacao montar|surgir|flutuar. "
    "acao=transicao: faixas 3D da marca que TAMPAM a tela no instante em= (a troca de "
    "assunto) — no máximo 1 ou 2 por vídeo. acao=criar: cena livre com primitivas "
    "(cena={duracao,largura,altura,fps,camera:{posicao,alvo,lente},objetos:[{tipo cubo/esfera/"
    "torus/cilindro/plano/texto/modelo,...}]}). Tudo retorna job: use consultar com job até "
    "ok/erro ANTES de revisar. Não executa scripts arbitrários.",
    {"properties":{"projeto":{"type":"string"},
        "acao":{"type":"string","enum":["objeto","transicao","criar","consultar"]},
        "job":{"type":"string"},"nome":{"type":"string"},"inicio":{"type":"number"},
        "objeto":{"type":"string","enum":["casa","predio","chave","cadeado","escudo","documento",
                                          "celular","calendario","check","estrela","grafico","mala"]},
        "duracao":{"type":"number"},"lado":{"type":"string","enum":["esquerda","direita","centro"]},
        "tamanho":{"type":"number"},"animacao":{"type":"string","enum":["montar","surgir","flutuar"]},
        "em":{"type":"number"},"cena":{"type":"object"}},"required":["projeto","acao"]},
)
def arte_3d(c: Cliente,a: dict) -> str:
    import json
    pid=a["projeto"]
    if a.get("acao")=="consultar":
        r=next((j for j in c.get("/api/jobs",project_id=pid) if j["id"]==a.get("job") and j["kind"]=="arte-3d"),None)
        return json.dumps(r or {"erro":"job 3D não encontrado neste projeto"},ensure_ascii=False)
    if a.get("acao")=="objeto":
        r=c.post(f"/api/projects/{pid}/objeto-3d",{**a,"origem":c.origem})
    elif a.get("acao")=="transicao":
        r=c.post(f"/api/projects/{pid}/transicao-3d",{**a,"origem":c.origem})
    else:
        r=c.post(f"/api/projects/{pid}/arte-3d",{**a,"origem":c.origem})
    return json.dumps(r,ensure_ascii=False)


def chamar(c: Cliente, nome: str, argumentos: dict):
    """Executa uma ferramenta e SEMPRE devolve texto (ou conteúdo com imagem).

    O "sempre" é o ponto. Quem está do outro lado é um modelo: um texto dizendo
    "esse projeto não existe, veja listar_projetos" ele lê e conserta; uma
    exceção ele vê como falha opaca e desiste ou repete o mesmo erro.
    """
    from .cliente import EditorFora, ErroDoEditor

    for f in FERRAMENTAS:
        if f["name"] == nome:
            try:
                return f["_fn"](c, argumentos or {})
            except EditorFora as exc:
                return str(exc)
            except ErroDoEditor as exc:
                return f"o editor recusou: {exc}"
            except (KeyError, TypeError, ValueError) as exc:
                return (f"{nome}: faltou ou veio errado um argumento ({exc}). "
                        f"Veja a descrição da ferramenta.")
    return (f"não existe ferramenta chamada '{nome}'. As que existem: "
            + ", ".join(f["name"] for f in FERRAMENTAS))


def catalogo() -> list[dict]:
    return [{k: v for k, v in f.items() if not k.startswith("_")}
            for f in FERRAMENTAS]
