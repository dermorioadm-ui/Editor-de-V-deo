"""Banco de b-roll grátis: Pexels e Pixabay.

Os dois são bancos de vídeo de uso livre, inclusive comercial, sem exigir
crédito, e com API gratuita. Cada um pede uma CHAVE grátis (um minuto para
criar); a chave fica no banco local, como a do Gemini, e nunca sai por rota
nenhuma.

O que sai da máquina é só a PALAVRA DA BUSCA. O vídeo dele não vai a lugar
nenhum: a regra "o arquivo nunca sai da minha máquina" continua de pé. O
caminho é o contrário, o b-roll escolhido é BAIXADO para a pasta de dados e
fica numa biblioteca local, reaproveitada entre projetos sem rede.

Nunca se baixa por uma URL que veio da tela. A tela manda o ID do vídeo, e o
servidor acha o arquivo na resposta do próprio banco. Sem isso, qualquer
página aberta no navegador poderia fazer o editor baixar o que quisesse.
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

FONTES = ("pexels", "pixabay")
NOMES = {"pexels": "Pexels", "pixabay": "Pixabay"}
CHAVES = {"pexels": "pexels_api_key", "pixabay": "pixabay_api_key"}
AMBIENTE = {"pexels": "EDITOR_PEXELS_KEY", "pixabay": "EDITOR_PIXABAY_KEY"}
ONDE_CRIAR = {"pexels": "https://www.pexels.com/api/",
              "pixabay": "https://pixabay.com/api/docs/"}
# os endereços podem ser trocados por variável de ambiente — é assim que o
# teste fala com um banco falso em 127.0.0.1
URL_PEXELS = os.environ.get("EDITOR_PEXELS_URL", "https://api.pexels.com")
URL_PIXABAY = os.environ.get("EDITOR_PIXABAY_URL", "https://pixabay.com")
# de onde um arquivo pode ser baixado. O Pexels já serviu pelo Vimeo.
HOSTS = {"pexels": ("pexels.com", "vimeo.com", "vimeocdn.com"),
         "pixabay": ("pixabay.com",)}
TETO_BYTES = 600 * 1024 * 1024   # um b-roll maior que isso é 4K longo demais
VALIDADE = 24 * 3600             # o Pixabay pede cache de 24 h nas buscas

_cache_busca: dict[str, tuple[float, dict]] = {}
# o resultado do último teste de cada chave: {fonte: {"ok", "mensagem", "quando"}}
_teste: dict[str, dict] = {}
_itens: dict[str, dict] = {}     # id → item, com os arquivos (fica no servidor)


class ErroDoBanco(RuntimeError):
    """Falha ao falar com o banco, com a mensagem já em português.

    NUNCA carrega a URL: a do Pixabay leva a chave na query (é o único jeito
    que a API dele aceita), e a mensagem vai para a tela.
    """


# ------------------------------------------------------------------ chaves
def chave(fonte: str) -> str:
    from . import db

    return (os.environ.get(AMBIENTE[fonte], "").strip()
            or str(db.get_setting(CHAVES[fonte], "") or "").strip())


def guardar_chave(fonte: str, valor: str) -> None:
    from . import db

    if fonte not in FONTES:
        raise ValueError(f"banco desconhecido: {fonte}")
    db.set_setting(CHAVES[fonte], str(valor or "").strip())


def estado() -> dict:
    """O que a tela precisa saber — NUNCA a chave, só se existe e o final."""
    saida = {}
    for f in FONTES:
        c = chave(f)
        t = _teste.get(f) or {}
        saida[f] = {"nome": NOMES[f], "tem_chave": bool(c),
                    "final": c[-4:] if len(c) > 8 else "",
                    "onde_criar": ONDE_CRIAR[f],
                    # testada nesta sessão? None = ainda não
                    "funciona": (t.get("ok") if c and t.get("final") == c[-4:] else None),
                    "aviso": (t.get("mensagem", "") if c and t.get("final") == c[-4:] else "")}
    saida["alguma"] = any(saida[f]["tem_chave"] for f in FONTES)
    return saida


def testar_chave(fonte: str) -> dict:
    """Uma busca mínima, para dizer JÁ se a chave funciona.

    Guardar a chave e só descobrir que ela estava errada na hora de gerar o
    vídeo — com o b-roll saindo vazio — é o pior momento para descobrir.
    """
    c = chave(fonte)
    if not c:
        r = {"ok": False, "mensagem": f"falta a chave do {NOMES[fonte]}"}
    else:
        try:
            _buscar_numa(fonte, "natureza", "", 1, 3, "pt")
            r = {"ok": True, "mensagem": f"{NOMES[fonte]} funcionando"}
        except ErroDoBanco as exc:
            r = {"ok": False, "mensagem": str(exc)}
    _teste[fonte] = {**r, "final": c[-4:] if c else "", "quando": time.time()}
    return r


# ---------------------------------------------------------------- formato
def orientacao_de(largura: float, altura: float) -> str:
    prop = float(largura) / max(float(altura), 1e-9)
    return "landscape" if prop > 1.2 else "square" if prop >= 0.9 else "portrait"


def _combina(w: float | None, h: float | None, orientacao: str) -> bool:
    if not w or not h or not orientacao:
        return True
    return orientacao_de(w, h) == orientacao


# --------------------------------------------------------------- normalizar
def _item_pexels(v: dict) -> dict | None:
    arquivos = []
    for f in v.get("video_files") or []:
        link = str(f.get("link") or "")
        tipo = str(f.get("file_type") or "")
        if not link or str(f.get("quality") or "") == "hls" or "mp4" not in tipo:
            continue
        arquivos.append({"url": link, "largura": int(f.get("width") or 0),
                         "altura": int(f.get("height") or 0),
                         "tamanho": int(f.get("size") or 0)})
    if not arquivos:
        return None
    usuario = v.get("user") or {}
    fotos = v.get("video_pictures") or []
    return {
        "id": f"pexels:{v.get('id')}", "fonte": "pexels",
        "duracao": float(v.get("duration") or 0),
        "largura": int(v.get("width") or 0), "altura": int(v.get("height") or 0),
        "miniatura": str(v.get("image") or (fotos[0].get("picture") if fotos else "")),
        # um quadro PEQUENO do meio do vídeo, para a IA olhar (a "image" do
        # Pexels é a capa em tamanho grande)
        "quadro": str((fotos[len(fotos) // 2].get("picture") if fotos else "")
                      or v.get("image") or ""),
        "autor": str(usuario.get("name") or ""),
        "autor_url": str(usuario.get("url") or ""),
        "pagina": str(v.get("url") or ""),
        "descricao": "",
        "arquivos": arquivos,
    }


def _item_pixabay(h: dict) -> dict | None:
    arquivos = []
    miniatura = ""
    for nome in ("large", "medium", "small", "tiny"):
        f = (h.get("videos") or {}).get(nome) or {}
        if f.get("url"):
            arquivos.append({"url": str(f["url"]), "largura": int(f.get("width") or 0),
                             "altura": int(f.get("height") or 0),
                             "tamanho": int(f.get("size") or 0)})
        miniatura = miniatura or str(f.get("thumbnail") or "")
    if not arquivos:
        return None
    maior = max(arquivos, key=lambda a: a["largura"] * a["altura"])
    pequeno = next((str(((h.get("videos") or {}).get(n) or {}).get("thumbnail") or "")
                    for n in ("tiny", "small", "medium", "large")
                    if ((h.get("videos") or {}).get(n) or {}).get("thumbnail")), "")
    return {
        "quadro": pequeno or miniatura,
        "id": f"pixabay:{h.get('id')}", "fonte": "pixabay",
        "duracao": float(h.get("duration") or 0),
        "largura": maior["largura"], "altura": maior["altura"],
        "miniatura": miniatura,
        "autor": str(h.get("user") or ""),
        "autor_url": (f"https://pixabay.com/users/{h.get('user')}-{h.get('user_id')}/"
                      if h.get("user") and h.get("user_id") else ""),
        "pagina": str(h.get("pageURL") or ""),
        "descricao": str(h.get("tags") or ""),
        "arquivos": arquivos,
    }


def _previa(item: dict) -> str:
    """O menor arquivo, para a tela tocar ao passar o mouse."""
    arqs = sorted(item["arquivos"], key=lambda a: (a["largura"] * a["altura"]) or 10**9)
    return arqs[0]["url"] if arqs else ""


def para_tela(item: dict) -> dict:
    """O item SEM a lista de arquivos: a tela não escolhe de onde baixar."""
    return {k: v for k, v in item.items() if k != "arquivos"} | {"previa": _previa(item)}


# ------------------------------------------------------------------- rede
def _cliente():
    import httpx

    return httpx.Client(timeout=httpx.Timeout(connect=10.0, read=30.0,
                                              write=30.0, pool=10.0),
                        follow_redirects=True,
                        headers={"User-Agent": "Sharkcut (editor local)"})


def _get_json(fonte: str, caminho: str, params: dict) -> dict:
    import httpx

    c = chave(fonte)
    if not c:
        raise ErroDoBanco(f"falta a chave do {NOMES[fonte]}")
    if fonte == "pexels":
        url, headers = URL_PEXELS.rstrip("/") + caminho, {"Authorization": c}
    else:
        url, headers = URL_PIXABAY.rstrip("/") + caminho, {}
        params = {**params, "key": c}
    try:
        with _cliente() as cli:
            r = cli.get(url, params=params, headers=headers)
    except httpx.HTTPError as exc:
        # o texto do httpx traz a URL — com a chave do Pixabay dentro
        raise ErroDoBanco(f"sem conexão com o {NOMES[fonte]} "
                          f"({type(exc).__name__})") from None
    if r.status_code in (401, 403) or (fonte == "pixabay" and r.status_code == 400
                                       and "key" in r.text.lower()):
        raise ErroDoBanco(f"o {NOMES[fonte]} recusou a chave: confira se ela "
                          f"foi colada inteira")
    if r.status_code == 429:
        raise ErroDoBanco(f"o {NOMES[fonte]} pediu uma pausa (limite de buscas "
                          f"por hora da conta grátis). Tente de novo daqui a pouco.")
    if r.status_code >= 400:
        raise ErroDoBanco(f"o {NOMES[fonte]} respondeu erro {r.status_code}")
    try:
        return r.json()
    except ValueError:
        raise ErroDoBanco(f"o {NOMES[fonte]} respondeu algo que não é JSON") from None


def _buscar_numa(fonte: str, termo: str, orientacao: str, pagina: int,
                 por_pagina: int, idioma: str = "pt") -> list[dict]:
    if fonte == "pexels":
        params: dict[str, Any] = {"query": termo, "page": pagina,
                                  "per_page": min(80, por_pagina),
                                  "locale": "en-US" if idioma == "en" else "pt-BR",
                                  "size": "medium"}
        if orientacao:
            params["orientation"] = orientacao
        dados = _get_json("pexels", "/videos/search", params)
        itens = [_item_pexels(v) for v in dados.get("videos") or []]
    else:
        params = {"q": termo[:100], "page": pagina,
                  "per_page": max(3, min(200, por_pagina)),
                  "lang": "en" if idioma == "en" else "pt",
                  "safesearch": "true", "video_type": "film"}
        dados = _get_json("pixabay", "/api/videos/", params)
        itens = [_item_pixabay(h) for h in dados.get("hits") or []]
    return [i for i in itens if i]


def buscar(termo: str, orientacao: str = "", pagina: int = 1,
           por_pagina: int = 20, idioma: str = "pt") -> dict:
    """Busca nos bancos que têm chave. Um banco fora do ar não derruba o outro.

    O Pixabay não filtra vídeo por orientação: os que combinam com o quadro
    vêm primeiro, e os outros depois (entram com fundo desfocado, sem tarja).
    """
    termo = " ".join(str(termo or "").split())[:100]
    if not termo:
        raise ErroDoBanco("escreva o que procurar")
    orientacao = orientacao if orientacao in ("portrait", "landscape", "square") else ""
    com_chave = [f for f in FONTES if chave(f)]
    if not com_chave:
        raise ErroDoBanco("falta a chave grátis do Pexels ou do Pixabay")
    idioma = "en" if idioma == "en" else "pt"
    chave_cache = json.dumps([termo.lower(), orientacao, pagina, por_pagina,
                              com_chave, idioma])
    agora = time.time()
    guardado = _cache_busca.get(chave_cache)
    if guardado and agora - guardado[0] < VALIDADE:
        return guardado[1]
    itens: list[dict] = []
    avisos: list[str] = []
    por_fonte: dict[str, list[dict]] = {}
    for f in com_chave:
        try:
            por_fonte[f] = _buscar_numa(f, termo, orientacao, pagina, por_pagina,
                                        idioma)
        except ErroDoBanco as exc:
            avisos.append(str(exc))
    # intercala os bancos: os dois aparecem na primeira tela de resultados
    listas = [por_fonte.get(f, []) for f in com_chave]
    for i in range(max((len(x) for x in listas), default=0)):
        for lista in listas:
            if i < len(lista):
                itens.append(lista[i])
    itens.sort(key=lambda it: not _combina(it["largura"], it["altura"], orientacao))
    if not itens and avisos:
        raise ErroDoBanco("; ".join(avisos))
    for it in itens:
        _itens[it["id"]] = it
    saida = {"termo": termo, "orientacao": orientacao,
             "itens": [para_tela(it) for it in itens], "avisos": avisos,
             "fontes": com_chave}
    if itens:
        _cache_busca[chave_cache] = (agora, saida)
    return saida


def detalhe(item_id: str) -> dict:
    """O item com os arquivos — da busca recente ou pedido de novo ao banco."""
    if item_id in _itens:
        return _itens[item_id]
    fonte, _, num = str(item_id).partition(":")
    if fonte not in FONTES or not num.isdigit():
        raise ErroDoBanco(f"id de b-roll inválido: {item_id}")
    if fonte == "pexels":
        item = _item_pexels(_get_json("pexels", f"/videos/videos/{num}", {}))
    else:
        dados = _get_json("pixabay", "/api/videos/", {"id": num})
        hits = dados.get("hits") or []
        item = _item_pixabay(hits[0]) if hits else None
    if not item:
        raise ErroDoBanco(f"o {NOMES[fonte]} não achou o vídeo {num}")
    _itens[item_id] = item
    return item


# ------------------------------------------------------------- biblioteca
def pasta() -> Path:
    from .config import MEDIA_DIR

    p = MEDIA_DIR / "broll"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _nome_seguro(item_id: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", item_id.lower())


def escolher_arquivo(item: dict, alvo_w: int, alvo_h: int) -> dict:
    """O menor arquivo que ainda dá a nitidez do quadro de saída.

    4K para um vídeo que sai em 1080 é download de 10x o tamanho para nada;
    e abaixo do lado menor do quadro o b-roll fica mole ao lado da fala.
    """
    lado = min(alvo_w, alvo_h) or 1080
    conhecidos = [a for a in item["arquivos"] if a["largura"] and a["altura"]]
    if not conhecidos:
        return item["arquivos"][0]
    bons = [a for a in conhecidos if min(a["largura"], a["altura"]) >= lado]
    if bons:
        return min(bons, key=lambda a: a["largura"] * a["altura"])
    return max(conhecidos, key=lambda a: a["largura"] * a["altura"])


TETO_QUADRO = 1_500_000


def quadro_bytes(item: dict) -> bytes | None:
    """O quadro (JPG) de um resultado, para a IA olhar antes de escolher.

    Só de dentro do próprio banco, e pequeno: é uma espiada, não download.
    """
    import httpx

    url = str(item.get("quadro") or item.get("miniatura") or "")
    if not url or not _host_permitido(item.get("fonte", ""), url):
        return None
    try:
        with _cliente() as cli:
            r = cli.get(url, timeout=10.0)
        if r.status_code >= 400 or len(r.content) > TETO_QUADRO or not r.content:
            return None
        return r.content
    except httpx.HTTPError:
        return None


def _host_permitido(fonte: str, url: str) -> bool:
    try:
        u = urlparse(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if u.scheme != "https" and not host.startswith("127."):
        return False
    if fonte not in HOSTS:
        return False
    base_teste = urlparse(URL_PEXELS if fonte == "pexels" else URL_PIXABAY).hostname
    return (host == base_teste
            or any(host == h or host.endswith("." + h) for h in HOSTS[fonte]))


def _miniatura_local(video: Path, destino: Path, duracao: float = 0.0) -> bool:
    """Um quadro do vídeo em JPG, para a biblioteca se ver sem internet."""
    import subprocess

    from .config import FFMPEG

    instante = min(1.0, max(0.0, duracao / 2)) if duracao else 0.5
    try:
        subprocess.run([FFMPEG, "-y", "-v", "error", "-ss", f"{instante:.2f}",
                        "-i", str(video), "-frames:v", "1",
                        "-vf", "scale=320:-2", "-q:v", "5", str(destino)],
                       check=True, capture_output=True, timeout=60)
        return destino.exists() and destino.stat().st_size > 0
    except Exception:  # noqa: BLE001 — sem miniatura a biblioteca funciona igual
        return False


def _url_local(nome: str) -> str:
    return f"/api/banco/arquivo/{nome}"


def ids_baixados() -> set[str]:
    """Os ids do banco que já estão na biblioteca (já usados em algum vídeo)."""
    saida = set()
    for meta in pasta().glob("*.json"):
        try:
            saida.add(str(json.loads(meta.read_text(encoding="utf-8")).get("id") or ""))
        except (OSError, ValueError):
            continue
    return saida


def baixados() -> list[dict]:
    """A biblioteca local: o que foi baixado do banco e o que ele enviou.

    Cada item traz o crédito, as palavras-chave e, quando existe, a
    miniatura LOCAL (a do banco só aparece com internet).
    """
    saida = []
    for meta in sorted(pasta().glob("*.json"), key=lambda p: p.stat().st_mtime,
                       reverse=True):
        try:
            dados = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        arq = pasta() / str(dados.get("arquivo") or "")
        if not (dados.get("arquivo") and arq.exists()):
            continue
        mini = pasta() / f"{meta.stem}.jpg"
        extra = {"miniatura": _url_local(mini.name)} if mini.exists() else {}
        saida.append({**dados, **extra, "path": str(arq.resolve()),
                      "video": _url_local(arq.name)})
    return saida


VIDEOS = {".mp4", ".mov", ".mkv", ".m4v", ".webm", ".avi"}


def guardar_proprio(caminho: str, palavras: str = "") -> dict:
    """Um vídeo DELE vai para a biblioteca de b-roll.

    Copiado para a pasta de dados (como a música de fundo): o original vive
    em Downloads, num pendrive, numa pasta que muda de nome, e a biblioteca
    tem que sobreviver a isso. A identidade é o CONTEÚDO — mandar o mesmo
    vídeo duas vezes não duplica, só atualiza as palavras-chave.

    As ``palavras`` são o que o b-roll automático procura: "academia,
    treino, halteres" faz esse vídeo entrar quando a fala toca no assunto.
    """
    import hashlib
    import shutil

    from .ffmpeg_utils import probe

    src = Path(str(caminho or "")).expanduser()
    if not src.exists() or not src.is_file():
        raise FileNotFoundError(f"arquivo não encontrado: {src}")
    if src.suffix.lower() not in VIDEOS:
        raise ErroDoBanco(f"{src.name} não é vídeo — b-roll é vídeo")
    h = hashlib.sha1()
    with open(src, "rb") as fh:
        for pedaco in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(pedaco)
    base = f"meu_{h.hexdigest()[:12]}"
    palavras = " ".join(str(palavras or "").split())[:200]
    meta_path = pasta() / f"{base}.json"
    if meta_path.exists():
        try:
            dados = json.loads(meta_path.read_text(encoding="utf-8"))
            arq = pasta() / str(dados.get("arquivo") or "")
            if arq.exists():
                if palavras:
                    dados["termo"] = dados["descricao"] = palavras
                    meta_path.write_text(json.dumps(dados, ensure_ascii=False),
                                         encoding="utf-8")
                return {**dados, "path": str(arq.resolve()), "repetido": True}
        except (OSError, ValueError):
            pass
    destino = pasta() / f"{base}{src.suffix.lower()}"
    if not destino.exists():
        shutil.copy2(src, destino)
    try:
        info = probe(destino)
        dur = float(info.duration or 0.0)
        w, hh = info.display_size
    except Exception as exc:  # noqa: BLE001
        destino.unlink(missing_ok=True)
        raise ErroDoBanco(f"não consegui ler {src.name}: {exc}") from None
    _miniatura_local(destino, pasta() / f"{base}.jpg", dur)
    dados = {"id": f"meu:{base[4:]}", "fonte": "meu", "arquivo": destino.name,
             "nome": src.stem, "duracao": dur, "largura": int(w), "altura": int(hh),
             "autor": "você", "autor_url": "", "pagina": "", "miniatura": "",
             "termo": palavras, "descricao": palavras, "baixado_em": time.time()}
    meta_path.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
    return {**dados, "path": str(destino.resolve()), "repetido": False}


def mudar_palavras(item_id: str, palavras: str) -> dict:
    """As palavras-chave de um vídeo da biblioteca (o que o automático busca)."""
    base = _nome_seguro(item_id)
    meta_path = pasta() / f"{base}.json"
    if not meta_path.exists():
        raise FileNotFoundError("esse vídeo não está na biblioteca")
    dados = json.loads(meta_path.read_text(encoding="utf-8"))
    dados["termo"] = dados["descricao"] = " ".join(str(palavras or "").split())[:200]
    meta_path.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
    return dados


def tirar_da_biblioteca(item_id: str) -> bool:
    """Apaga a CÓPIA da biblioteca (nunca o original dele, que fica onde está).

    Um vídeo em uso num projeto continua no disco: só some da lista.
    """
    from . import db

    base = _nome_seguro(item_id)
    meta_path = pasta() / f"{base}.json"
    if not meta_path.exists():
        return False
    try:
        dados = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        dados = {}
    arq = pasta() / str(dados.get("arquivo") or "")
    meta_path.unlink(missing_ok=True)
    (pasta() / f"{base}.jpg").unlink(missing_ok=True)
    if dados.get("arquivo") and arq.exists() and arq.resolve().parent == pasta().resolve():
        em_uso = any(Path(m["path"]).resolve() == arq.resolve()
                     for m in db.q("SELECT path FROM media WHERE kind='video'"))
        if not em_uso:
            arq.unlink(missing_ok=True)
    return True


def baixar(item_id: str, alvo_w: int, alvo_h: int, termo: str = "",
           on_progress: Callable[[float, str], None] | None = None,
           cancelado: Callable[[], bool] | None = None) -> dict:
    """Baixa o vídeo para a biblioteca. Já baixado? Devolve sem rede."""
    import httpx

    base = _nome_seguro(item_id)
    meta_path = pasta() / f"{base}.json"
    if meta_path.exists():
        try:
            dados = json.loads(meta_path.read_text(encoding="utf-8"))
            arq = pasta() / str(dados.get("arquivo") or "")
            if arq.exists() and arq.stat().st_size > 0:
                return {**dados, "path": str(arq.resolve()), "reaproveitado": True}
        except (OSError, ValueError):
            pass
    item = detalhe(item_id)
    escolhido = escolher_arquivo(item, alvo_w, alvo_h)
    if not _host_permitido(item["fonte"], escolhido["url"]):
        raise ErroDoBanco(f"o {NOMES[item['fonte']]} mandou baixar de um "
                          f"endereço fora dele — recusei")
    destino = pasta() / f"{base}_{escolhido['largura']}x{escolhido['altura']}.mp4"
    parcial = destino.with_suffix(".part")
    try:
        with _cliente() as cli, cli.stream("GET", escolhido["url"]) as r:
            if r.status_code >= 400:
                raise ErroDoBanco(f"o {NOMES[item['fonte']]} não entregou o "
                                  f"arquivo (erro {r.status_code})")
            total = int(r.headers.get("content-length") or escolhido["tamanho"] or 0)
            feito = 0
            with open(parcial, "wb") as fh:
                for pedaco in r.iter_bytes(1024 * 256):
                    if cancelado and cancelado():
                        raise ErroDoBanco("download cancelado")
                    fh.write(pedaco)
                    feito += len(pedaco)
                    if feito > TETO_BYTES:
                        raise ErroDoBanco("o arquivo passou de 600 MB — escolha outro")
                    if on_progress and total:
                        on_progress(min(1.0, feito / total),
                                    f"baixando {feito / 1e6:.1f} de {total / 1e6:.1f} MB")
    except httpx.HTTPError as exc:
        parcial.unlink(missing_ok=True)
        raise ErroDoBanco(f"o download caiu ({type(exc).__name__})") from None
    except BaseException:
        parcial.unlink(missing_ok=True)
        raise
    if feito == 0:
        parcial.unlink(missing_ok=True)
        raise ErroDoBanco("o banco entregou um arquivo vazio")
    parcial.replace(destino)
    dados = {"id": item_id, "fonte": item["fonte"], "arquivo": destino.name,
             "duracao": item["duracao"], "largura": escolhido["largura"],
             "altura": escolhido["altura"], "autor": item["autor"],
             "autor_url": item["autor_url"], "pagina": item["pagina"],
             "miniatura": item["miniatura"], "termo": termo[:100],
             "descricao": item.get("descricao", ""), "baixado_em": time.time()}
    meta_path.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
    _miniatura_local(destino, pasta() / f"{base}.jpg", float(item["duracao"] or 0))
    return {**dados, "path": str(destino.resolve()), "reaproveitado": False}


# ---------------------------------------------------------------- sugestão
PALAVRAS_VAZIAS = set("""
a o as os um uma uns umas de da do das dos em na no nas nos por pela pelo
pelas pelos para pra pro com sem sob sobre entre ate até e ou mas que se
quando como onde porque porquê pois entao então isso isto esse essa este esta
aquele aquela aqui ali la lá ja já nao não sim eu tu ele ela nos nós voces
vocês eles elas me te lhe mim ti meu minha meus minhas seu sua seus suas teu
tua nosso nossa ser estar ter haver fazer ir vou vai vamos foi era é sao são
tem tinha tenho temos está esta estão estou fica ficar muito muita muitos
muitas mais menos bem mal so só tambem também ainda sempre nunca todo toda
todos todas cada qual quais quem coisa coisas tipo gente agora hoje depois
comigo contigo conosco consigo nisso nisto naquilo disso disto daquilo
esses essas estes estas aqueles aquelas desse dessa deste desta desses dessas
destes destas nesse nessa neste nesta nesses nessas nestes nestas daquele
daquela naquele naquela outro outra outros outras mesmo mesma mesmos mesmas
algum alguma alguns algumas nenhum nenhuma tanto tanta tantos tantas pouco
pouca poucos poucas qualquer quaisquer certo certa certos certas vários
várias varios varias seja sejam sendo sido tudo nada algo alguem alguém
ninguem ninguém lugar forma jeito maneira parte vida mundo ponto caso fato
vocês voces
antes então aí ai né ne olha olhe veja vc voce você pode posso poder quer
quero querer sabe saber vai vem dia dias vez vezes ano anos
""".split())


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


# terminações de VERBO conjugado. Não saem da lista (a regra erra às vezes:
# "museu" termina em "eu"), só vão para o fim: b-roll se busca por COISA —
# "a porta de casa ficou aberta" tem que buscar "porta", não "ficou"
_VERBO = ("ou", "ei", "eu", "iu", "ava", "avam", "ando", "endo", "indo",
          "aram", "eram", "iram", "amos", "emos", "imos", "asse", "esse")


def _parece_verbo(p: str) -> bool:
    return len(p) > 4 and _sem_acento(p).endswith(_VERBO)


def sugerir_termos(texto: str, limite: int = 4) -> list[str]:
    """As palavras de conteúdo da fala — o que um b-roll pode ilustrar.

    Sem IA e sem rede: tira as palavras vazias; a repetida vem primeiro (é o
    assunto), depois a que aparece PRIMEIRO na frase — em português o
    substantivo costuma vir antes do adjetivo ("porta aberta"), e escolher a
    mais longa pegava justamente o adjetivo. Forma de verbo vai para o fim.
    O Pexels busca em português (locale pt-BR) e o Pixabay também (lang=pt),
    então não precisa traduzir.
    """
    palavras = re.findall(r"[A-Za-zÀ-ÿ]+", str(texto or "").lower())
    vistas: dict[str, int] = {}
    ordem: list[str] = []
    for p in palavras:
        if len(p) < 4 or p in PALAVRAS_VAZIAS or _sem_acento(p) in PALAVRAS_VAZIAS:
            continue
        if p not in vistas:
            ordem.append(p)
        vistas[p] = vistas.get(p, 0) + 1
    posicao = {p: i for i, p in enumerate(ordem)}
    return sorted(ordem, key=lambda p: (-vistas[p], _parece_verbo(p),
                                         posicao[p]))[:limite]
