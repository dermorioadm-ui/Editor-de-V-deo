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
            linhas.append(f"{n} | {tipo}{extra} | {m.get('name', '')}")
        linhas += [
            "",
            "Para cada mídia, diga em QUE BLOCO ela entra e por quantos segundos.",
            "Vídeo entra como 'cobertura' (cobre a imagem, a voz continua por "
            "baixo). Imagem entra como 'sobreposicao' (aparece por cima).",
            f"Entre {MIN_ANEXO:.0f} e {MAX_ANEXO:.0f} segundos. Uma mídia só, por "
            "bloco: se ela não ajuda em nenhum bloco, não a use.",
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
            duracao_saida: float) -> dict:
    """Traduz a intenção em edição — recusando tudo que não couber.

    Devolve o relatório do que entrou e do que foi recusado, com motivo. Nada
    aqui é aplicado "quase": ou a sugestão respeita as invariantes, ou ela não
    acontece e aparece na lista de recusas.
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
        esperado = "video" if tipo == "cobertura" else "image"
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
                         "ajustes": janela.ajustes})
        quantos += 1

    return {"leitura": str(resposta.get("leitura", ""))[:300],
            "aplicados": aplicados, "anexos": anexados, "recusados": recusados}


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
