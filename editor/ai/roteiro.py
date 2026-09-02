"""O que a IA opina sobre o roteiro, e como isso vira edição.

Um pedido só, com três respostas:

    1. ETAPA de cada bloco   (gancho, dor, mecanismo, ... , cta)
    2. ÊNFASE de cada bloco  (aberto | normal | fechado)
    3. ANEXO por bloco       (que mídia entra, onde e por quanto tempo)

Nada disso é um tempo, um corte ou um número de zoom. A etapa vira
enquadramento pela tabela SECTIONS, que é a mesma de sempre; a ênfase só
escolhe entre os degraus que a escada JÁ produziu; o anexo passa inteiro por
editor/anexos.py. Se a IA disser bobagem, a bobagem é recusada com o motivo —
nunca aplicada pela metade.

O que é mandado: o TEXTO de cada bloco, com tempo e duração. Não vai o vídeo,
não vai o caminho de nenhum arquivo, não vai um segundo de imagem do usuário —
a não ser os quadros dos ANEXOS dele, quando ele pede ajuda para posicioná-los,
porque sem ver a imagem não há como decidir onde ela cabe.
"""
from __future__ import annotations

import re
import unicodedata

from dataclasses import dataclass

from ..models import SECTIONS
from . import gemini

ETAPAS = list(SECTIONS.keys())
ENFASES = ["aberto", "normal", "fechado"]
TIPOS = ["cobertura", "sobreposicao"]

MAX_BLOCOS = 220          # acima disso o pedido fica grande e a resposta, rasa
MAX_QUADROS = 6           # quadros de anexo por chamada
MIN_ANEXO = 1.2           # anexo mais curto que isto pisca e atrapalha
MAX_ANEXO = 6.0

INSTRUCAO = """Você edita vídeos verticais de anúncio em português do Brasil \
(VSL e criativo de Facebook/Instagram): uma pessoa falando para a câmera, \
já cortado e legendado.

Sua tarefa é dizer a INTENÇÃO de cada bloco, nunca números de edição. Você não \
escolhe tempo de corte, nem valor de zoom, nem posição em pixels — quem faz \
isso é o programa, com regras que você não vê.

Como decidir a ETAPA: leia o texto do bloco e diga em que ponto da argumentação \
ele está. Use a posição no vídeo como pista, não como regra — um vídeo pode \
abrir com prova ou fechar sem oferta.

Como decidir a ÊNFASE, que é o que dá ritmo:
- "fechado" quando o bloco é o ponto alto: a revelação, o número que \
impressiona, a promessa, a chamada final. Plano fechado cola o olho na cara.
- "aberto" quando o bloco é respiro: contexto, explicação longa, transição. \
Depois de fechar, abrir dá alívio e faz o próximo fechamento valer.
- "normal" no resto. NÃO marque tudo como fechado: se tudo é ponto alto, nada é. \
Use "fechado" em no máximo um terço dos blocos, e evite dois "fechado" seguidos.

=== CARTÕES ===
Você também escreve os CARTÕES: um painel de texto que aparece por cima do
vídeo. Quem desenha é o programa, com a fonte e o traço dele — você escreve as
palavras e diz em que bloco entra.

Dois tipos, e só dois:
- "topicos": um título curto e de 2 a 4 linhas. Para quando a pessoa ENUMERA
  ("são três coisas", "primeiro... segundo..."), lista o que está incluído, ou
  explica um passo a passo. As linhas saem da FALA dela, encurtadas — nunca
  invente item que ela não disse.
- "numero": um número grande com uma legenda curta embaixo. Para quando ela diz
  um número que importa: preço, quantidade de clientes, porcentagem, prazo.
  Em "numero" vai só o número ("347", "97", "82%", "30 dias") e em "titulo" o
  que ele é ("clientes atendidos").

Regras que valem mais que sua vontade de enfeitar:
- No máximo UM cartão a cada 20 segundos de vídeo, e no máximo 5 no total.
  Cartão demais vira apresentação de slides e a pessoa some do próprio anúncio.
- Título de no máximo 6 palavras. Tópico de no máximo 5 palavras. Se não cabe
  em 5 palavras, não é tópico de cartão, é fala.
- O cartão entra EM CIMA da fala que ele resume, não antes nem depois.
- Se o vídeo não enumera nada e não diz número nenhum, devolva a lista vazia.
  Nenhum cartão é melhor que um cartão genérico.

=== ANEXOS ===
Quando o usuário anexou mídias (vídeos e imagens), TODAS entram no vídeo,
cada uma exatamente uma vez — ele anexou de propósito, para valorizar o
anúncio. Sua tarefa é dizer em QUE BLOCO cada uma entra: o bloco em que a
fala trata do que a mídia mostra, ou do que o usuário escreveu sobre ela.
Vídeo e imagem entram como "sobreposicao": uma JANELA por cima do vídeo
(picture-in-picture), com quem fala continuando visível e a voz continuando
por baixo — o usuário depois arrasta e redimensiona a janela na prévia. Use
"cobertura" (a mídia cobre a tela inteira, a voz continua) SOMENTE quando o
que o usuário escreveu sobre a mídia pedir tela cheia. Se nenhum bloco falar
exatamente daquilo, escolha o mais próximo do assunto. Nunca devolva a lista
de anexos vazia havendo mídia na lista.

Responda somente o JSON do esquema."""


@dataclass
class Bloco:
    i: int
    inicio: float
    fim: float
    texto: str


def _esquema(com_anexos: bool) -> dict:
    """propertyOrdering em TODO objeto: sem ele a API ordena em ordem
    alfabética, e quando a ordem do exemplo não bate com a do esquema a
    resposta sai divagante."""
    esquema = {
        "type": "OBJECT",
        "properties": {
            "leitura": {"type": "STRING",
                        "description": "uma frase sobre o que o vídeo vende"},
            "blocos": {"type": "ARRAY", "items": {
                "type": "OBJECT",
                "properties": {
                    "i": {"type": "INTEGER", "description": "índice do bloco"},
                    "etapa": {"type": "STRING", "enum": ETAPAS},
                    "enfase": {"type": "STRING", "enum": ENFASES},
                    "porque": {"type": "STRING",
                               "description": "no máximo 12 palavras"},
                },
                "required": ["i", "etapa", "enfase", "porque"],
                "propertyOrdering": ["i", "etapa", "enfase", "porque"],
            }},
        },
        "required": ["leitura", "blocos"],
        "propertyOrdering": ["leitura", "blocos"],
    }
    if com_anexos:
        esquema["properties"]["anexos"] = {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {
                "midia": {"type": "INTEGER", "description": "número da mídia na lista"},
                "bloco": {"type": "INTEGER", "description": "índice do bloco onde entra"},
                "tipo": {"type": "STRING", "enum": TIPOS},
                "segundos": {"type": "NUMBER"},
                "porque": {"type": "STRING"},
            },
            "required": ["midia", "bloco", "tipo", "segundos", "porque"],
            "propertyOrdering": ["midia", "bloco", "tipo", "segundos", "porque"],
        }}
        esquema["required"].append("anexos")
        esquema["propertyOrdering"].append("anexos")
    esquema["properties"]["cartoes"] = {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {
            "bloco": {"type": "INTEGER", "description": "índice do bloco onde entra"},
            "tipo": {"type": "STRING", "enum": ["topicos", "numero"]},
            "titulo": {"type": "STRING"},
            "topicos": {"type": "ARRAY", "items": {"type": "STRING"}},
            "numero": {"type": "STRING"},
            "segundos": {"type": "NUMBER"},
        },
        "required": ["bloco", "tipo", "titulo", "segundos"],
        "propertyOrdering": ["bloco", "tipo", "titulo", "topicos", "numero",
                             "segundos"],
    }}
    esquema["propertyOrdering"].append("cartoes")
    return esquema


def montar_pedido(blocos: list[Bloco], midias: list[dict],
                  duracao: float) -> str:
    linhas = [f"Vídeo de {duracao:.0f} segundos, {len(blocos)} blocos.", "",
              "BLOCOS (índice | início | duração | fala):"]
    for b in blocos:
        linhas.append(f"{b.i} | {b.inicio:6.1f}s | {b.fim - b.inicio:4.1f}s | "
                      f"{b.texto.strip()[:400]}")
    if midias:
        linhas += ["", "MÍDIAS QUE O USUÁRIO ANEXOU (na ordem das imagens acima):"]
        for n, m in enumerate(midias):
            tipo = "vídeo" if m.get("kind") == "video" else "imagem"
            dur = (m.get("info") or {}).get("duration")
            extra = f", {float(dur):.1f}s" if dur else ""
            # A DESCRIÇÃO é o que decide. O quadro mostra o que a imagem
            # mostra; só o usuário sabe a intenção — "tela de cadastro, entra
            # quando eu falo do passo 1". Sem ela o modelo chuta pelo pixel.
            desc = str(m.get("descricao") or "").strip()
            linhas.append(f"{n} | {tipo}{extra} | {m.get('name', '')}"
                          + (f" | O USUÁRIO DIZ: {desc}" if desc else ""))
        linhas += [
            "",
            "Para cada mídia, diga em QUE BLOCO ela entra e por quantos segundos.",
            "Quando houver 'O USUÁRIO DIZ', OBEDEÇA: é ele quem sabe o que o "
            "arquivo é e onde quer. O quadro serve para você conferir o "
            "enquadramento, não para discordar da intenção dele.",
            "Tudo entra como 'sobreposicao' (uma janela por cima do vídeo, quem "
            "fala continua visível). 'cobertura' (tela cheia) SÓ quando 'O "
            "USUÁRIO DIZ' pedir tela cheia.",
            f"Entre {MIN_ANEXO:.0f} e {MAX_ANEXO:.0f} segundos. Uma mídia por "
            "bloco. TODA mídia da lista entra, exatamente uma vez: se nenhum "
            "bloco fala exatamente daquilo, escolha o mais próximo do assunto.",
        ]
    return "\n".join(linhas)


def blocos_do_plano(plan, palavras: list[dict]) -> list[Bloco]:
    """Os blocos de fala, com o texto que caiu dentro de cada um.

    O texto é reunido pelo tempo na FONTE — que é a régua em que as palavras
    do Whisper vivem — e não pelo tempo de saída, que muda a cada corte.
    """
    saida: list[Bloco] = []
    for i, c in enumerate(plan.active_clips):
        if c.source != "main" or c.kind == "photo" or c.src_end <= c.src_start:
            continue
        dentro = [w for w in palavras
                  if min(float(w["end"]), c.src_end)
                  - max(float(w["start"]), c.src_start) > 0.01]
        texto = " ".join(str(w.get("text", "")).strip() for w in dentro).strip()
        if not texto:
            continue        # bloco sem fala não tem o que classificar
        saida.append(Bloco(i=i, inicio=c.src_start, fim=c.src_end, texto=texto))
    return saida


# ------------------------------------------------------------------ aplicação
def aplicar(plan, resposta: dict, midias: list[dict],
            duracao_saida: float, blocos: list | None = None,
            completar: bool = False) -> dict:
    """Traduz a intenção em edição — recusando tudo que não couber.

    Devolve o relatório do que entrou e do que foi recusado, com motivo. Nada
    aqui é aplicado "quase": ou a sugestão respeita as invariantes, ou ela não
    acontece e aparece na lista de recusas.

    ``completar`` é a regra da IMPRESSORA: toda mídia que o usuário anexou
    sai no vídeo. O que a IA não posicionou (ou posicionou errado e foi
    recusado) o programa posiciona — pelas palavras da descrição contra o
    texto dos blocos e, na falta delas, espalhado no meio do vídeo. Fica
    desligado por padrão para a aplicação manual, em que o usuário está
    olhando cada item; no clique único é obrigatório.
    """
    from .. import anexos

    porindice = {i: c for i, c in enumerate(plan.active_clips)}
    aplicados: list[dict] = []
    recusados: list[dict] = []

    for item in (resposta.get("blocos") or []):
        try:
            i = int(item.get("i", -1))
        except (TypeError, ValueError):
            continue
        clip = porindice.get(i)
        if clip is None:
            recusados.append({"o_que": f"bloco {i}", "motivo": "esse bloco não existe"})
            continue
        etapa = str(item.get("etapa", "")).strip()
        enfase = str(item.get("enfase", "")).strip()
        if etapa not in SECTIONS:
            recusados.append({"o_que": f"bloco {i}",
                              "motivo": f"etapa desconhecida: {etapa!r}"})
            continue
        if enfase not in ENFASES:
            enfase = "normal"
        if clip.zoom_locked:
            recusados.append({"o_que": f"bloco {i}",
                              "motivo": "você travou o enquadramento desse bloco"})
            continue
        clip.section = etapa
        clip.section_source = "ia"
        clip.emphasis = "" if enfase == "normal" else enfase
        clip.emphasis_source = "ia"
        aplicados.append({"o_que": f"bloco {i}", "etapa": etapa, "enfase": enfase,
                          "porque": str(item.get("porque", ""))[:120]})

    # ÊNFASE COM PARCIMÔNIA: se tudo é ponto alto, nada é. O modelo é avisado
    # disso na instrução; aqui a regra é imposta, porque instrução não é
    # garantia. Os "fechado" que sobram viram "normal", do fim para o começo.
    fechados = [c for c in plan.active_clips if c.emphasis == "fechado"]
    limite = max(1, len(plan.active_clips) // 3)
    if len(fechados) > limite:
        for c in fechados[limite:]:
            c.emphasis = ""
        recusados.append({
            "o_que": "ênfase",
            "motivo": f"{len(fechados)} blocos vinham como ponto alto; deixei "
                      f"{limite}. Fechar em tudo é o mesmo que não fechar em nada."})

    anexados, quantos = [], 0
    for item in (resposta.get("anexos") or []):
        try:
            n = int(item.get("midia", -1))
            bloco = int(item.get("bloco", -1))
            segundos = float(item.get("segundos", 0.0))
        except (TypeError, ValueError):
            continue
        if not (0 <= n < len(midias)):
            recusados.append({"o_que": "anexo", "motivo": f"mídia {n} não existe"})
            continue
        m = midias[n]
        clip = porindice.get(bloco)
        if clip is None:
            recusados.append({"o_que": f"anexo de {m.get('name', '')}",
                              "motivo": f"bloco {bloco} não existe"})
            continue
        if quantos >= len(midias):
            break
        tipo = str(item.get("tipo", "")).strip()
        if tipo not in TIPOS:
            tipo = "sobreposicao"
        notas: list[str] = []
        # TELA CHEIA SÓ QUANDO O USUÁRIO PEDE. A IA gostava de "cobertura":
        # o vídeo do usuário sumia atrás da gravação de tela e ele não tinha
        # como mexer — janela ele arrasta, encolhe e apaga na prévia.
        if tipo == "cobertura" and not pede_tela_cheia(m):
            tipo = "sobreposicao"
            notas.append("entrou como janela por cima do vídeo; só cobre a "
                         "tela inteira quando a descrição pede tela cheia")
        esperado = "video" if tipo == "cobertura" else "any"
        inicio = _inicio_na_saida(plan, bloco)
        segundos = max(MIN_ANEXO, min(MAX_ANEXO, segundos))
        try:
            midia = anexos.validar(midias, m["id"], esperado)
            janela = anexos.encaixar(midia, inicio, inicio + segundos,
                                     limite=duracao_saida)
            if tipo == "cobertura":
                anexos.sem_sobreposicao(plan.cutaways, janela.out_start,
                                        janela.out_end)
        except anexos.AnexoInvalido as exc:
            recusados.append({"o_que": f"anexo de {m.get('name', '')}",
                              "motivo": str(exc)})
            continue
        anexados.append({"media_id": midia["id"], "tipo": tipo,
                         "out_start": janela.out_start, "out_end": janela.out_end,
                         "nome": m.get("name", ""),
                         "porque": str(item.get("porque", ""))[:120],
                         "ajustes": notas + janela.ajustes})
        quantos += 1

    if completar:
        anexados += _completar_anexos(plan, midias, anexados, blocos or [],
                                      duracao_saida, recusados)

    return {"leitura": str(resposta.get("leitura", ""))[:300],
            "aplicados": aplicados, "anexos": anexados,
            "cartoes": _cartoes_pedidos(resposta, plan, porindice, duracao_saida,
                                        recusados),
            "recusados": recusados}


# Palavras que aparecem em qualquer descrição e não dizem do que a mídia trata.
_VAZIAS = {"para", "com", "que", "uma", "isso", "essa", "este", "esta", "aqui",
           "quando", "onde", "como", "mais", "muito", "voce", "dele", "dela",
           "tela", "video", "imagem", "print", "foto", "gravacao", "mostra",
           "mostrando", "entra", "falo", "falar", "parte", "trecho", "hora",
           "momento", "whatsapp", "screenshot", "captura"}


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto)
                   if not unicodedata.combining(c))


def _bloco_por_palavras(midia: dict, textos: dict, elegiveis: list):
    """O bloco cuja fala mais repete as palavras da descrição — ou nenhum."""
    base = f"{midia.get('descricao') or ''} {midia.get('name') or ''}".lower()
    base = _sem_acento(re.sub(r"[_\-\.\d]+", " ", base))
    tokens = set(re.findall(r"[a-z]{4,}", base)) - _VAZIAS
    if not tokens:
        return None
    melhor, pontos = None, 0
    for x in elegiveis:
        txt = _sem_acento(textos.get(x[0], "")).lower()
        p = sum(1 for t in tokens if t in txt)
        if p > pontos:
            melhor, pontos = x, p
    return melhor if pontos > 0 else None


# O que, na descrição, pede que a mídia cubra a tela inteira em vez de entrar
# como janela. Sem isso na descrição, TUDO é janela: é o que se arrasta e
# redimensiona na prévia.
_TELA_CHEIA = ("tela cheia", "tela inteira", "cobre a tela", "cobrindo a tela",
               "cobre o video", "no lugar do video", "substitui o video",
               "fullscreen", "full screen", "cobertura")


def pede_tela_cheia(midia: dict) -> bool:
    """A descrição do usuário pede tela cheia? Imagem nunca cobre a tela."""
    if midia.get("kind") != "video":
        return False
    texto = _sem_acento(str(midia.get("descricao") or "").lower())
    return any(p in texto for p in _TELA_CHEIA)


def _completar_anexos(plan, midias: list[dict], ja: list[dict], blocos: list,
                      duracao_saida: float, recusados: list[dict]) -> list[dict]:
    """Posiciona o que ficou de fora. O usuário anexou; o vídeo sai com tudo.

    Ordem de preferência para o lugar: (1) o bloco cuja fala repete as
    palavras da descrição; (2) espalhado — a k-ésima de n mídias cai em
    (k+1)/(n+1) do vídeo. Nunca no gancho (os primeiros 8 s ou 15%) e nunca
    nos últimos 2 s. Tudo entra como JANELA por cima do vídeo; cobertura
    (tela cheia) só quando a descrição pede, e nunca em cima de outra.
    Mídia que já está no vídeo (de uma rodada anterior) não entra de novo.
    """
    from .. import anexos

    usados = {a["media_id"] for a in ja}
    usados |= {c.media_id for c in plan.cutaways if getattr(c, "enabled", True)}
    usados |= {o.media_id for o in plan.overlays if getattr(o, "enabled", True)}
    faltam = [m for m in midias
              if m.get("kind") in ("video", "image")
              and m.get("id") not in usados
              and not str(m.get("id", "")).startswith("k_")]
    clips = plan.active_clips
    if not faltam or not clips or duracao_saida <= 0:
        return []

    gancho = min(8.0, duracao_saida * 0.15)
    marcos, t = [], 0.0
    for i, c in enumerate(clips):
        marcos.append((i, round(t, 4), round(t + c.out_duration, 4)))
        t += c.out_duration
    elegiveis = [x for x in marcos if x[1] >= gancho and x[1] < duracao_saida - 2.0] \
        or marcos
    textos = {b.i: b.texto for b in blocos}

    saida: list[dict] = []
    for k, m in enumerate(faltam):
        tipo = "cobertura" if pede_tela_cheia(m) else "sobreposicao"
        dur_m = float((m.get("info") or {}).get("duration") or 0.0)
        segundos = max(MIN_ANEXO, min(MAX_ANEXO, dur_m if dur_m > 0 else 4.0))
        pela_palavra = _bloco_por_palavras(m, textos, elegiveis)
        alvo_frac = (k + 1) / (len(faltam) + 1) * duracao_saida
        ordem = sorted(elegiveis, key=lambda x: abs(x[1] - alvo_frac))
        candidatos = ([pela_palavra] if pela_palavra else []) + \
            [x for x in ordem if x != pela_palavra]
        colocado = None
        for cand in candidatos:
            inicio = cand[1]
            try:
                midia = anexos.validar(midias, m["id"],
                                       "video" if tipo == "cobertura" else "any")
                janela = anexos.encaixar(midia, inicio, inicio + segundos,
                                         limite=duracao_saida)
                if tipo == "cobertura":
                    anexos.sem_sobreposicao(plan.cutaways, janela.out_start,
                                            janela.out_end)
                # DOIS ANEXOS NO MESMO INSTANTE, NÃO. Duas janelas nascem no
                # mesmo canto do quadro: sobrepostas no tempo, uma tapa a
                # outra. E cobertura em cima de cobertura o render descarta
                # calado. Então o programa procura outro bloco livre.
                ocupados = [(a["out_start"], a["out_end"]) for a in ja + saida]
                ocupados += [(o.out_start, o.out_end) for o in plan.overlays
                             if getattr(o, "enabled", True)]
                for a0, a1 in ocupados:
                    if min(janela.out_end, a1) - max(janela.out_start, a0) > 0.02:
                        raise anexos.AnexoInvalido("colide com outro anexo")
            except anexos.AnexoInvalido:
                continue
            colocado = {
                "media_id": midia["id"], "tipo": tipo,
                "out_start": janela.out_start, "out_end": janela.out_end,
                "nome": m.get("name", ""), "origem": "programa",
                "porque": ("entrou no bloco que fala das palavras da sua descrição"
                           if cand == pela_palavra else
                           "a IA não disse onde: entrou no ponto mais equilibrado do vídeo"),
                "ajustes": janela.ajustes,
            }
            break
        if colocado:
            saida.append(colocado)
        else:
            recusados.append({"o_que": f"anexo de {m.get('name', '')}",
                              "motivo": "não achei uma janela livre para ele"})
    return saida


MAX_CARTOES = 5
INTERVALO_CARTAO = 20.0       # um a cada 20 s de vídeo, no máximo
DUR_CARTAO_MIN = 1.8
DUR_CARTAO_MAX = 5.0


def _cartoes_pedidos(resposta: dict, plan, porindice: dict,
                     duracao_saida: float, recusados: list[dict]) -> list[dict]:
    """Os cartões que a IA escreveu, validados — ainda sem desenhar.

    Desenhar é do render; aqui só se decide O QUE e QUANDO. As travas são de
    ritmo, não de estética: cartão demais vira apresentação de slides e a
    pessoa some do próprio anúncio.
    """
    teto = min(MAX_CARTOES, max(1, int(duracao_saida // INTERVALO_CARTAO)))
    saida: list[dict] = []
    for item in (resposta.get("cartoes") or []):
        try:
            bloco = int(item.get("bloco", -1))
            segundos = float(item.get("segundos", 3.0))
        except (TypeError, ValueError):
            continue
        titulo = str(item.get("titulo", "")).strip()[:60]
        tipo = str(item.get("tipo", "")).strip()
        topicos = [str(t).strip()[:48] for t in (item.get("topicos") or [])
                   if str(t).strip()][:4]
        numero = str(item.get("numero", "")).strip()[:12]
        if porindice.get(bloco) is None:
            recusados.append({"o_que": f"cartão “{titulo}”",
                              "motivo": f"bloco {bloco} não existe"})
            continue
        if tipo == "numero" and not numero:
            recusados.append({"o_que": f"cartão “{titulo}”",
                              "motivo": "cartão de número sem número"})
            continue
        if tipo == "topicos" and len(topicos) < 2:
            recusados.append({"o_que": f"cartão “{titulo}”",
                              "motivo": "menos de dois tópicos — isso é uma "
                                        "frase, não uma lista"})
            continue
        if len(saida) >= teto:
            recusados.append({
                "o_que": f"cartão “{titulo}”",
                "motivo": f"já são {teto} cartão(ões) neste vídeo (um a cada "
                          f"{INTERVALO_CARTAO:.0f} s). Mais que isso vira "
                          f"apresentação de slides."})
            continue
        inicio = _inicio_na_saida(plan, bloco)
        segundos = max(DUR_CARTAO_MIN, min(DUR_CARTAO_MAX, segundos))
        fim = min(duracao_saida, inicio + segundos)
        if fim - inicio < DUR_CARTAO_MIN * 0.6:
            recusados.append({"o_que": f"cartão “{titulo}”",
                              "motivo": "não sobra vídeo para ele aparecer"})
            continue
        # dois cartões em cima um do outro é um piscando por baixo do outro
        if saida and inicio < saida[-1]["out_end"] + 0.3:
            recusados.append({"o_que": f"cartão “{titulo}”",
                              "motivo": "cai em cima do cartão anterior"})
            continue
        saida.append({"tipo": "numero" if tipo == "numero" else "topicos",
                      "titulo": titulo, "topicos": topicos, "numero": numero,
                      "out_start": round(inicio, 3), "out_end": round(fim, 3)})
    return saida


def _inicio_na_saida(plan, indice: int) -> float:
    """Onde o bloco começa na linha do tempo de SAÍDA.

    A IA responde por índice de bloco justamente para não ter de acertar um
    instante: um índice sempre cai numa fronteira de bloco, que é onde um anexo
    pode entrar sem partir frase no meio.
    """
    t = 0.0
    for i, c in enumerate(plan.active_clips):
        if i == indice:
            return round(t, 4)
        t += c.out_duration
    return round(t, 4)


def pedir(chave: str, modelo: str, blocos: list[Bloco], midias: list[dict],
          duracao: float, quadros: list[bytes] | None = None) -> dict:
    """Uma chamada. Devolve o JSON já validado contra o esquema."""
    if not blocos:
        raise gemini.ErroDaIA("não há bloco nenhum para a IA olhar — rode a "
                              "edição automática primeiro.")
    if len(blocos) > MAX_BLOCOS:
        blocos = blocos[:MAX_BLOCOS]
    escolhido = gemini.escolher_modelo(chave, modelo)
    resposta = gemini.gerar_json(
        chave, escolhido["id"], INSTRUCAO,
        montar_pedido(blocos, midias, duracao),
        _esquema(bool(midias)), imagens=(quadros or [])[:MAX_QUADROS],
        maximo=min(escolhido.get("saida") or 8192, 8192))
    resposta["_modelo"] = escolhido["id"]
    return resposta
