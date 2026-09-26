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
        },
        "required": ["projeto"],
    },
)
def editar_sozinho(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    receita: dict = {}
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
    "O que foi dito, com o número de cada palavra. Os números são o que as "
    "ferramentas de corte pedem. Peça um pedaço por vez em vídeo longo.",
    {
        "properties": {
            "projeto": {"type": "string"},
            "de": {"type": "integer", "description": "primeira palavra (padrão 0)"},
            "ate": {"type": "integer", "description": "última palavra (padrão: 400 adiante)"},
        },
        "required": ["projeto"],
    },
)
def transcricao(c: Cliente, a: dict) -> str:
    pid = str(a.get("projeto") or "")
    p = _projeto(c, pid)
    palavras = ((p.get("analysis") or {}).get("words")) or []
    if not palavras:
        return "ainda não há transcrição. Rode editar_sozinho primeiro."
    de = max(0, int(a.get("de") or 0))
    ate = int(a.get("ate") if a.get("ate") is not None else de + 400)
    fatia = palavras[de:ate + 1]
    fora = set((p.get("analysis") or {}).get("removed_word_ids") or [])
    texto = " ".join(
        f"[{w.get('i', i + de)}]{'~' if w.get('i', i + de) in fora else ''}"
        f"{w.get('text', '')}"
        for i, w in enumerate(fatia))
    return (f"palavras {de} a {min(ate, len(palavras) - 1)} de {len(palavras) - 1} "
            f"(~ = já cortada):\n{texto}")


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
    if a.get("palavras"):
        ids = [int(x) for x in a["palavras"]]
        r = c.post(f"/api/projects/{pid}/ops/remove-words", {"word_ids": ids})
        o_que = f"{len(ids)} palavra(s)"
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
            + (f"\n{explica}" if explica else ""))


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
                     "description": "quanto cada um cobre no máximo (padrão 5 s)"},
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
        },
        "required": [],
    },
)
def buscar_broll(c: Cliente, a: dict) -> str:
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
    return "\n".join(linhas)


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
                     "description": "quanto cada um cobre no máximo (padrão 5 s)"},
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
                                   "enum": ["linear", "suave", "entra", "sai"]},
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
    "Põe uma música de fundo. A música abaixa sozinha quando alguém fala.",
    {
        "properties": {
            "projeto": {"type": "string"},
            "caminho": {"type": "string", "description": "caminho do MP3 na máquina"},
            "volume_db": {"type": "number",
                          "description": "padrão -18; mais negativo é mais baixo"},
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
    return (f"trilha posta a {corpo.get('gain_db', -18)} dB, abaixando sozinha "
            f"na fala.")


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


def chamar(c: Cliente, nome: str, argumentos: dict) -> str:
    """Executa uma ferramenta e SEMPRE devolve texto.

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
