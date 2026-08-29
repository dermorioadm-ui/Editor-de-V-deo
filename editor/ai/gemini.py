"""Cliente do Gemini — REST puro sobre httpx, sem SDK.

A API do Gemini é JSON sobre HTTPS. O SDK oficial arrastaria dezenas de
dependências para um app que hoje tem cinco, e não faria nada que este arquivo
não faça. Tudo aqui foi conferido contra o documento de discovery da própria
Google (https://generativelanguage.googleapis.com/$discovery/rest?version=v1beta),
que é a fonte que não muda de endereço quando a documentação é reorganizada.

Decisões que valem estar escritas:

- A chave vai no cabeçalho ``x-goog-api-key``, nunca em ``?key=``. As duas
  funcionam; a segunda vaza a chave em log de proxy, histórico e Referer.
- O modelo é DESCOBERTO em runtime (GET /v1beta/models). Os identificadores
  mudaram de 2.0 para 2.5, 3.1, 3.5 e 3.7 em menos de dois anos: nome chumbado
  é um app que quebra sozinho num dia em que ninguém tocou no código.
- A saída é forçada com ``responseMimeType: application/json`` mais
  ``responseSchema``, e TODO objeto declara ``propertyOrdering``. Sem ele a API
  ordena as propriedades em ordem alfabética, e quando a ordem do exemplo no
  prompt não bate com a do esquema a resposta sai divagante.
- Erro 429 obedece o ``retryDelay`` que vem em ``details[]`` quando ele vem.
  Cota diária estourada não é retentada — esperar não resolve.
- O cliente é SÍNCRONO de propósito: ele roda dentro da thread da fila de
  trabalho, nunca dentro de uma rota. Chamada de rede de 60 s no laço de
  eventos congelaria a barra de progresso de todos os trabalhos, exportação
  inclusive.
"""
from __future__ import annotations

import base64
import json
import random
import time
from typing import Any

BASE = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT_LEITURA = 120.0
TENTATIVAS = 4
ESPERA_MAX = 60.0
CACHE_MODELOS = 3600.0

# preferência quando o usuário não escolheu — a lista real manda.
# gemini-3.5-flash primeiro por pedido explícito do usuário: é o modelo que
# ele quer decidindo os cortes. O resto é escada de queda para o dia em que a
# Google renomear tudo de novo.
PREFERIDOS = ("gemini-3.5-flash", "gemini-3.7-flash", "gemini-3.5-flash-lite",
              "gemini-2.5-flash", "gemini-2.0-flash")


def chave_guardada() -> str:
    """A chave, de onde quer que ela venha — e ela FICA guardada.

    Ordem: o banco local (colada uma vez na aba IA; o banco mora em
    Vídeos/Editor de Vídeo, fora da pasta do programa, então sobrevive a
    reinstalação e atualização) e, por cima dele, a variável de ambiente
    EDITOR_GEMINI_KEY para quem prefere não ter chave em disco nenhum.
    """
    import os

    from .. import db

    return (os.environ.get("EDITOR_GEMINI_KEY", "").strip()
            or str(db.get_setting("gemini_api_key", "") or "").strip())

_modelos: dict[str, Any] = {"quando": 0.0, "lista": []}


class ErroDaIA(RuntimeError):
    """Falha ao falar com o Gemini, com a mensagem já em português."""

    def __init__(self, mensagem: str, *, retentar: bool = False,
                 status: int = 0) -> None:
        super().__init__(mensagem)
        self.retentar = retentar
        self.status = status


def _cliente(chave: str):
    import httpx

    return httpx.Client(
        timeout=httpx.Timeout(connect=10.0, read=TIMEOUT_LEITURA,
                              write=30.0, pool=10.0),
        headers={"x-goog-api-key": chave, "Content-Type": "application/json"},
    )


def _erro_da_resposta(r) -> ErroDaIA:
    """Traduz o envelope de erro da Google para uma frase que ajuda."""
    try:
        corpo = r.json().get("error", {}) or {}
    except Exception:  # noqa: BLE001
        corpo = {}
    detalhe = str(corpo.get("message") or r.text or "").strip()[:300]
    codigo = int(corpo.get("code") or r.status_code or 0)
    if codigo == 400 and "API_KEY_INVALID" in r.text:
        return ErroDaIA("a chave do Gemini não foi aceita. Confira se copiou "
                        "ela inteira em Ajustes > IA.", status=codigo)
    if codigo in (401, 403):
        return ErroDaIA(f"o Gemini recusou a chave ({detalhe or 'sem permissão'}). "
                        f"Verifique se a chave está ativa no Google AI Studio.",
                        status=codigo)
    if codigo == 404:
        return ErroDaIA(f"esse modelo não existe mais nessa conta ({detalhe}). "
                        f"Deixe o campo de modelo em branco para o app escolher.",
                        status=codigo)
    if codigo == 429:
        quota_diaria = "PerDay" in r.text or "per day" in r.text.lower()
        if quota_diaria:
            return ErroDaIA("a cota diária gratuita do Gemini acabou. Ela volta "
                            "na virada do dia (fuso do Pacífico) — ou ligue o "
                            "faturamento na conta Google.", status=429)
        return ErroDaIA("o Gemini pediu para esperar (limite de chamadas por "
                        "minuto).", retentar=True, status=429)
    if codigo >= 500:
        return ErroDaIA(f"o Gemini está fora do ar no momento ({codigo}).",
                        retentar=True, status=codigo)
    return ErroDaIA(f"o Gemini recusou o pedido: {detalhe or codigo}", status=codigo)


def _espera_pedida(r) -> float | None:
    """O ``retryDelay`` que a própria Google manda em details[], se mandar."""
    try:
        for d in (r.json().get("error", {}) or {}).get("details", []) or []:
            if str(d.get("@type", "")).endswith("google.rpc.RetryInfo"):
                bruto = str(d.get("retryDelay", "")).rstrip("s")
                return max(0.0, min(ESPERA_MAX, float(bruto)))
    except Exception:  # noqa: BLE001
        pass
    return None


def _post(chave: str, caminho: str, corpo: dict) -> dict:
    import httpx

    ultimo: ErroDaIA | None = None
    with _cliente(chave) as c:
        for tentativa in range(TENTATIVAS):
            try:
                r = c.post(f"{BASE}/{caminho}", json=corpo)
            except httpx.TimeoutException:
                ultimo = ErroDaIA("o Gemini demorou demais para responder.",
                                  retentar=True)
            except httpx.HTTPError as exc:
                ultimo = ErroDaIA(f"não consegui falar com o Gemini: {exc}. "
                                  f"Sem internet? O editor continua funcionando "
                                  f"sem a IA.", retentar=True)
            else:
                if r.status_code < 300:
                    return r.json()
                ultimo = _erro_da_resposta(r)
                if not ultimo.retentar:
                    raise ultimo
                pedida = _espera_pedida(r)
                if pedida is not None:
                    time.sleep(pedida)
                    continue
            if tentativa == TENTATIVAS - 1:
                break
            # recuo exponencial com sorteio, para duas chamadas não baterem juntas
            time.sleep(min(ESPERA_MAX, (2 ** tentativa) * (1.0 + random.random())))
    raise ultimo or ErroDaIA("não consegui falar com o Gemini.")


def listar_modelos(chave: str, forcar: bool = False) -> list[dict]:
    """Os modelos que ESTA chave pode usar para generateContent."""
    import httpx

    agora = time.time()
    if not forcar and _modelos["lista"] and agora - _modelos["quando"] < CACHE_MODELOS:
        return _modelos["lista"]
    with _cliente(chave) as c:
        try:
            r = c.get(f"{BASE}/models", params={"pageSize": 200})
        except httpx.HTTPError as exc:
            raise ErroDaIA(f"não consegui listar os modelos: {exc}") from exc
        if r.status_code >= 300:
            raise _erro_da_resposta(r)
        dados = r.json()
    saida = []
    for m in dados.get("models", []) or []:
        if "generateContent" not in (m.get("supportedGenerationMethods") or []):
            continue
        saida.append({
            "id": str(m.get("name", "")).removeprefix("models/"),
            "nome": m.get("displayName") or "",
            "entrada": int(m.get("inputTokenLimit") or 0),
            "saida": int(m.get("outputTokenLimit") or 0),
        })
    _modelos.update({"quando": agora, "lista": saida})
    return saida


def escolher_modelo(chave: str, pedido: str = "") -> dict:
    """O modelo pedido, se existir; senão o melhor da lista real."""
    lista = listar_modelos(chave)
    if not lista:
        raise ErroDaIA("essa chave não tem nenhum modelo disponível.")
    por_id = {m["id"]: m for m in lista}
    if pedido and pedido in por_id:
        return por_id[pedido]
    if pedido:
        # o usuário pediu um que sumiu: cai no automático, mas não em silêncio
        for p in PREFERIDOS:
            if p in por_id:
                return {**por_id[p], "trocado_de": pedido}
    for p in PREFERIDOS:
        if p in por_id:
            return por_id[p]
    flash = [m for m in lista if "flash" in m["id"]]
    return (flash or lista)[0]


def gerar_json(chave: str, modelo: str, instrucao: str, pedido: str,
               esquema: dict, imagens: list[bytes] | None = None,
               temperatura: float = 0.2, maximo: int = 4096) -> dict:
    """Uma chamada, saída validada contra o esquema.

    ``imagens`` são bytes JPEG já prontos — a Files API fica de fora de
    propósito: ela só é necessária acima do limite de anexo embutido, e o único
    arquivo grande aqui é o vídeo, que não sai da máquina.
    """
    partes: list[dict] = []
    for img in (imagens or []):
        partes.append({"inline_data": {
            "mime_type": "image/jpeg",
            "data": base64.b64encode(img).decode("ascii")}})
    partes.append({"text": pedido})

    corpo = {
        "contents": [{"role": "user", "parts": partes}],
        "systemInstruction": {"parts": [{"text": instrucao}]},
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": esquema,
            "temperature": temperatura,
            "maxOutputTokens": maximo,
        },
    }
    dados = _post(chave, f"models/{modelo}:generateContent", corpo)
    candidatos = dados.get("candidates") or []
    if not candidatos:
        motivo = (dados.get("promptFeedback") or {}).get("blockReason", "")
        raise ErroDaIA(f"o Gemini não devolveu resposta"
                       + (f" (bloqueado: {motivo})" if motivo else "") + ".")
    razao = candidatos[0].get("finishReason", "")
    texto = "".join(p.get("text", "")
                    for p in (candidatos[0].get("content") or {}).get("parts", []))
    if not texto.strip():
        raise ErroDaIA(f"o Gemini devolveu resposta vazia (motivo: {razao or '?'}).")
    try:
        return json.loads(texto)
    except json.JSONDecodeError as exc:
        if razao == "MAX_TOKENS":
            raise ErroDaIA("a resposta do Gemini foi cortada no meio por limite "
                           "de tamanho. Tente um vídeo mais curto ou divida em "
                           "partes.") from exc
        raise ErroDaIA("o Gemini devolveu algo que não é JSON.") from exc


def testar_chave(chave: str, modelo: str = "") -> dict:
    """Vale para o botão 'testar' da tela: diz o que a chave alcança."""
    escolhido = escolher_modelo(chave, modelo)
    lista = listar_modelos(chave)
    return {"ok": True, "modelo": escolhido["id"], "nome": escolhido.get("nome", ""),
            "trocado_de": escolhido.get("trocado_de", ""),
            "disponiveis": [m["id"] for m in lista][:40]}
