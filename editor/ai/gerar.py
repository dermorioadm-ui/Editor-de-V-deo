"""Gerar imagem e vídeo com o Gemini, de dentro do editor.

Duas portas da mesma API, ambas conferidas contra o documento de discovery
(``$discovery/rest?version=v1beta``) e contra a documentação pública:

- IMAGEM: ``models/{m}:generateContent`` com ``responseModalities: ["IMAGE"]``
  num modelo de imagem (o "Nano Banana" — ``gemini-*-image*``). A imagem volta
  como ``inlineData`` base64 dentro de ``candidates[0].content.parts``.
- VÍDEO: ``models/{veo}:predictLongRunning`` devolve uma OPERAÇÃO; o
  resultado é lido em ``GET /v1beta/{operation.name}`` até ``done`` e o MP4
  é baixado do ``uri`` que vem em
  ``response.generateVideoResponse.generatedSamples[0].video.uri`` — com a
  mesma chave no cabeçalho.

O que a geração NÃO faz: não manda o vídeo do usuário para lugar nenhum. Vai
o texto que ele escreveu e, opcionalmente, uma imagem de referência que ele
mesmo escolheu. O arquivo gerado desce para o disco dele e vira mídia do
projeto — dali em diante é um anexo como qualquer outro.

Nada aqui foi testado contra a API viva nesta máquina (sem chave): as formas
das requisições e respostas seguem a documentação e os testes cobrem o
tratamento com HTTP simulado.
"""
from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Callable

from .gemini import (BASE, ErroDaIA, _cliente, _erro_da_resposta, _post,
                     listar_modelos)

# ordem de preferência quando o usuário não escolheu; a lista real manda
PREFERIDOS_IMAGEM = ("gemini-3-pro-image", "gemini-3.1-flash-image",
                     "gemini-3-flash-image", "gemini-2.5-flash-image",
                     "nano-banana")
PREFERIDOS_VIDEO = ("veo-3.1-fast", "veo-3.1", "veo-3.0-fast", "veo-3.0",
                    "veo-3", "veo-2")
ESPERA_VIDEO = 900.0        # o Veo leva de 1 a 6 minutos; acima disto desiste
INTERVALO_VIDEO = 8.0
PROPORCOES_IMAGEM = ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9")
PROPORCOES_VIDEO = ("16:9", "9:16")


def modelos_de_imagem(chave: str) -> list[dict]:
    """Modelos desta chave que geram IMAGEM (Nano Banana e afins)."""
    return [m for m in listar_modelos(chave, metodo="generateContent")
            if "image" in m["id"] or "banana" in m["id"]]


def modelos_de_video(chave: str) -> list[dict]:
    """Modelos desta chave que geram VÍDEO (Veo)."""
    return [m for m in listar_modelos(chave, metodo="predictLongRunning")
            if "veo" in m["id"]]


def _escolher(lista: list[dict], pedido: str, preferidos: tuple[str, ...]) -> str:
    ids = [m["id"] for m in lista]
    if pedido and pedido in ids:
        return pedido
    for p in preferidos:
        for i in ids:
            if i.startswith(p) or p in i:
                # o estável antes do preview, quando houver os dois
                if "preview" in i and any(j.startswith(p) and "preview" not in j
                                          for j in ids):
                    continue
                return i
    return ids[0]


def _imagem_da_resposta(dados: dict) -> tuple[bytes, str]:
    candidatos = dados.get("candidates") or []
    if not candidatos:
        motivo = (dados.get("promptFeedback") or {}).get("blockReason", "")
        raise ErroDaIA("o Gemini não devolveu imagem"
                       + (f" (bloqueado: {motivo})" if motivo else "") + ".")
    partes = (candidatos[0].get("content") or {}).get("parts") or []
    texto = ""
    for p in partes:
        bloco = p.get("inlineData") or p.get("inline_data")
        if bloco and str(bloco.get("mimeType") or bloco.get("mime_type", "")).startswith("image/"):
            mime = str(bloco.get("mimeType") or bloco.get("mime_type"))
            return base64.b64decode(bloco.get("data", "")), mime
        if p.get("text"):
            texto += p["text"]
    razao = candidatos[0].get("finishReason", "")
    detalhe = (texto.strip()[:160] or razao or "sem motivo")
    raise ErroDaIA(f"o Gemini respondeu sem imagem ({detalhe}). Tente descrever "
                   f"a cena de outro jeito.")


def gerar_imagem(chave: str, prompt: str, destino: Path, proporcao: str = "16:9",
                 modelo: str = "", referencia: bytes | None = None,
                 mime_referencia: str = "image/png",
                 on_progress: Callable[[float, str], None] | None = None) -> dict:
    """Uma imagem a partir do texto (e de uma referência opcional). Grava em disco."""
    lista = modelos_de_imagem(chave)
    if not lista:
        raise ErroDaIA("essa chave não tem nenhum modelo de IMAGEM (o Nano Banana, "
                       "gemini-*-image). Confira no Google AI Studio se a geração "
                       "de imagem está liberada para a sua conta.")
    m = _escolher(lista, modelo, PREFERIDOS_IMAGEM)
    if on_progress:
        on_progress(0.1, f"pedindo a imagem ao {m}")
    partes: list[dict] = []
    if referencia:
        partes.append({"inline_data": {"mime_type": mime_referencia,
                                       "data": base64.b64encode(referencia).decode("ascii")}})
    partes.append({"text": prompt})
    proporcao = proporcao if proporcao in PROPORCOES_IMAGEM else "16:9"
    corpo = {
        "contents": [{"role": "user", "parts": partes}],
        "generationConfig": {"responseModalities": ["IMAGE"],
                             "imageConfig": {"aspectRatio": proporcao}},
    }
    try:
        dados = _post(chave, f"models/{m}:generateContent", corpo)
    except ErroDaIA as exc:
        # modelo mais antigo que não conhece imageConfig: tenta sem a proporção
        if exc.status == 400 and not exc.retentar:
            corpo["generationConfig"].pop("imageConfig", None)
            dados = _post(chave, f"models/{m}:generateContent", corpo)
        else:
            raise
    conteudo, mime = _imagem_da_resposta(dados)
    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(mime, ".png")
    destino = Path(destino).with_suffix(ext)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(conteudo)
    if on_progress:
        on_progress(1.0, "imagem pronta")
    return {"path": str(destino), "modelo": m, "mime": mime, "bytes": len(conteudo)}


def _baixar(chave: str, uri: str) -> bytes:
    import httpx

    with _cliente(chave) as c:
        try:
            r = c.get(uri, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise ErroDaIA(f"não consegui baixar o vídeo gerado: {exc}",
                           retentar=True) from exc
        if r.status_code >= 300:
            raise _erro_da_resposta(r)
        return r.content


def gerar_video(chave: str, prompt: str, destino: Path, proporcao: str = "16:9",
                duracao: int = 8, modelo: str = "", imagem: bytes | None = None,
                mime_imagem: str = "image/png",
                on_progress: Callable[[float, str], None] | None = None,
                cancel: Callable[[], bool] | None = None,
                espera_max: float | None = None,
                intervalo: float | None = None) -> dict:
    """Um clipe do Veo a partir do texto (e de uma imagem inicial opcional).

    Bloqueia enquanto a operação roda — por isso só é chamada de dentro de um
    job. ``cancel`` é consultado a cada volta.
    """
    espera_max = ESPERA_VIDEO if espera_max is None else espera_max
    intervalo = INTERVALO_VIDEO if intervalo is None else intervalo
    lista = modelos_de_video(chave)
    if not lista:
        raise ErroDaIA("essa chave não tem nenhum modelo de VÍDEO (Veo). O Veo "
                       "exige faturamento ligado na conta Google — o nível "
                       "gratuito não gera vídeo.")
    m = _escolher(lista, modelo, PREFERIDOS_VIDEO)
    proporcao = proporcao if proporcao in PROPORCOES_VIDEO else "16:9"
    duracao = 4 if duracao <= 5 else (6 if duracao <= 7 else 8)
    instancia: dict = {"prompt": prompt}
    if imagem:
        instancia["image"] = {"bytesBase64Encoded": base64.b64encode(imagem).decode("ascii"),
                              "mimeType": mime_imagem}
    corpo = {"instances": [instancia],
             "parameters": {"aspectRatio": proporcao, "durationSeconds": duracao,
                            "personGeneration": "allow_adult"}}
    if on_progress:
        on_progress(0.02, f"pedindo o vídeo ao {m}")
    op = _post(chave, f"models/{m}:predictLongRunning", corpo)
    nome = str(op.get("name") or "")
    if not nome:
        raise ErroDaIA("o Veo não devolveu a operação do pedido.")

    import httpx

    t0 = time.monotonic()
    dados: dict = op
    with _cliente(chave) as c:
        while not dados.get("done"):
            if cancel and cancel():
                raise KeyboardInterrupt("cancelado")
            passado = time.monotonic() - t0
            if passado > espera_max:
                raise ErroDaIA("o Veo não terminou em tempo hábil. Tente de novo "
                               "daqui a pouco.", retentar=True)
            if on_progress:
                # a barra anda com o tempo típico (~3 min) sem nunca fechar
                on_progress(min(0.9, 0.05 + passado / 240.0),
                            f"o Veo está gerando ({int(passado)} s)")
            time.sleep(max(0.0, intervalo))
            try:
                r = c.get(f"{BASE}/{nome}")
            except httpx.HTTPError:
                continue
            if r.status_code >= 300:
                raise _erro_da_resposta(r)
            dados = r.json()
    if dados.get("error"):
        err = dados["error"] or {}
        raise ErroDaIA(f"o Veo recusou o pedido: {err.get('message') or err}")
    resp = dados.get("response") or {}
    amostras = ((resp.get("generateVideoResponse") or {}).get("generatedSamples")
                or resp.get("generatedSamples") or [])
    if not amostras:
        filtros = ((resp.get("generateVideoResponse") or {}).get("raiMediaFilteredReasons")
                   or resp.get("raiMediaFilteredReasons") or [])
        motivo = "; ".join(str(f) for f in filtros)[:200] if filtros else "sem motivo"
        raise ErroDaIA(f"o Veo não gerou o vídeo ({motivo}). Mude a descrição e "
                       f"tente de novo.")
    uri = str(((amostras[0].get("video") or {}).get("uri")) or "")
    if not uri:
        raise ErroDaIA("o Veo terminou mas não disse onde está o vídeo.")
    if on_progress:
        on_progress(0.92, "baixando o vídeo gerado")
    conteudo = _baixar(chave, uri)
    destino = Path(destino).with_suffix(".mp4")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(conteudo)
    if on_progress:
        on_progress(1.0, "vídeo pronto")
    return {"path": str(destino), "modelo": m, "bytes": len(conteudo),
            "duracao_pedida": duracao}
