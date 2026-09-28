"""O KIT DA MARCA: nome, cores, fonte, logos e regras de quem aparece no vídeo.

Sem o kit, o Claude inventava: título em caixa alta, "HospedPay" escrito
errado, cor que não é a da marca, fonte qualquer. Com o kit, a pós-edição
nasce na identidade: o estilo ``marca`` pinta com o coral dela, a fonte é a
dela (entregue ao libass junto do vídeo), o nome sai sempre na grafia exata —
no gráfico, na legenda e já na transcrição — e os logos são os arquivos dela,
com fundo transparente.

ONDE MORA. Os kits que vêm com o Sharkcut ficam em ``marcas/<nome>/`` na
pasta do programa (a hospedepay, tirada da identidade visual v1); os que o
usuário cria ficam em ``DATA_DIR/marcas/<nome>/``, com o mesmo formato. Cada
kit é um ``marca.json`` e as pastas ``logos/`` e ``fontes/``.

OS LOGOS DE PLATAFORMA (Airbnb, Booking…) NÃO vêm com o programa: são marcas
dos outros. Quem quer usá-los põe os PNG dele em ``DATA_DIR/marcas/_logos/``
(o botão "pôr logo" da aba Pós faz isso) e o Claude passa a enxergá-los pelo
nome do arquivo. A regra do kit continua valendo: nunca ao lado da marca.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import unicodedata
from pathlib import Path

from . import db
from .config import DATA_DIR

PASTA_DO_PROGRAMA = Path(__file__).resolve().parent.parent / "marcas"
PASTA_DO_USUARIO = DATA_DIR / "marcas"
PASTA_DE_LOGOS = PASTA_DO_USUARIO / "_logos"
# os logos de plataforma que já vêm com o programa (Airbnb, Booking), tirados
# do print dele — o que ele puser com o mesmo nome em _logos vale por cima
PLATAFORMAS = PASTA_DO_PROGRAMA / "_plataformas"
EXTENSOES = (".png", ".webp")
_SLUG = re.compile(r"[^a-z0-9_-]+")

_cache: dict[str, tuple[float, dict]] = {}


def _slug(nome: str) -> str:
    n = unicodedata.normalize("NFKD", str(nome or "").lower())
    n = "".join(c for c in n if not unicodedata.combining(c))
    return _SLUG.sub("-", n).strip("-")[:40]


def _pastas() -> list[Path]:
    return [PASTA_DO_USUARIO, PASTA_DO_PROGRAMA]


def _pasta_do_kit(slug: str) -> Path | None:
    slug = _slug(slug)
    if not slug or slug.startswith("_"):
        return None
    for base in _pastas():
        p = base / slug
        if (p / "marca.json").is_file():
            return p
    return None


def listar() -> list[dict]:
    vistos: dict[str, dict] = {}
    for base in _pastas():
        if not base.is_dir():
            continue
        for p in sorted(base.iterdir()):
            if p.name.startswith("_") or not (p / "marca.json").is_file() or p.name in vistos:
                continue
            k = carregar(p.name)
            if k:
                vistos[p.name] = {"slug": p.name, "nome": k["nome"],
                                  "cor": k["cores"].get("marca", "")}
    return list(vistos.values())


def carregar(slug: str) -> dict | None:
    """O kit, com os caminhos dos logos e das fontes já resolvidos."""
    pasta = _pasta_do_kit(slug)
    if not pasta:
        return None
    arq = pasta / "marca.json"
    try:
        mtime = arq.stat().st_mtime
    except OSError:
        return None
    em_cache = _cache.get(str(arq))
    if em_cache and em_cache[0] == mtime:
        return em_cache[1]
    try:
        bruto = json.loads(arq.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(bruto, dict) or not str(bruto.get("nome") or "").strip():
        return None
    kit = {
        "slug": pasta.name,
        "pasta": str(pasta),
        "nome": str(bruto["nome"]).strip()[:60],
        "descricao": str(bruto.get("descricao") or "")[:400],
        "cores": {str(k): str(v) for k, v in dict(bruto.get("cores") or {}).items()
                  if re.fullmatch(r"#[0-9a-fA-F]{6}", str(v))},
        "fonte": dict(bruto.get("fonte") or {}),
        "forma": dict(bruto.get("forma") or {}),
        "regras": [str(x)[:400] for x in bruto.get("regras") or []][:20],
        "voz": [str(x)[:400] for x in bruto.get("voz") or []][:10],
        "frases": [str(x)[:200] for x in bruto.get("frases") or []][:20],
        "vocabulario": [str(x)[:60] for x in bruto.get("vocabulario") or []][:30],
    }
    g = dict(bruto.get("grafia") or {})
    kit["grafia"] = {"exata": str(g.get("exata") or kit["nome"]),
                     "variantes": [str(v) for v in g.get("variantes") or []][:40]}
    logos: dict[str, dict] = {}
    for nome, d in dict(bruto.get("logos") or {}).items():
        d = d if isinstance(d, dict) else {"arquivo": d}
        caminho = (pasta / str(d.get("arquivo") or "")).resolve()
        if caminho.is_file() and pasta.resolve() in caminho.parents:
            logos[str(nome)] = {"caminho": str(caminho), "uso": str(d.get("uso") or "")[:200],
                                "da_marca": True}
    kit["logos"] = logos
    fontes = []
    for rel in kit["fonte"].get("arquivos") or []:
        f = (pasta / str(rel)).resolve()
        if f.is_file() and pasta.resolve() in f.parents:
            fontes.append(str(f))
    kit["fontes"] = fontes
    kit["hash"] = hashlib.sha1(json.dumps(bruto, sort_keys=True).encode()
                               + b"|".join(Path(x).name.encode() for x in fontes)).hexdigest()[:12]
    _cache[str(arq)] = (mtime, kit)
    return kit


def ativa_slug() -> str:
    """A marca ligada. Sem escolha feita e com um kit só, é ele."""
    s = db.get_setting("marca_ativa", None)
    if s is None:
        kits = listar()
        return kits[0]["slug"] if len(kits) == 1 else ""
    return str(s or "")


def ativa() -> dict | None:
    s = ativa_slug()
    return carregar(s) if s else None


def definir_ativa(slug: str) -> dict | None:
    slug = _slug(slug) if slug else ""
    if slug and not _pasta_do_kit(slug):
        raise ValueError("essa marca não existe")
    db.set_setting("marca_ativa", slug)
    return carregar(slug) if slug else None


def do_projeto(project) -> dict | None:
    """O kit que vale para este projeto: o que ficou gravado nele, ou o ativo."""
    slug = str(getattr(getattr(project, "plan", None), "marca", "") or "")
    if slug == "-":
        return None
    return carregar(slug) if slug else ativa()


def kit_do_plano(plan) -> dict | None:
    slug = str(getattr(plan, "marca", "") or "")
    if slug == "-":
        return None
    return carregar(slug) if slug else ativa()


# ------------------------------------------------------------------ logos
def logos_extras() -> dict[str, dict]:
    """Os logos de plataforma (Airbnb, Booking…): os que vêm com o programa e
    os que o usuário pôs, pelo nome do arquivo (o dele vale por cima)."""
    out: dict[str, dict] = {}
    for pasta, uso in ((PLATAFORMAS, "logo de plataforma (vem com o programa)"),
                       (PASTA_DE_LOGOS, "logo que você pôs")):
        if not pasta.is_dir():
            continue
        for p in sorted(pasta.iterdir()):
            if p.suffix.lower() in EXTENSOES and p.is_file():
                out[_slug(p.stem) or p.stem] = {"caminho": str(p), "uso": uso,
                                                "da_marca": False}
    return out


def logos(kit: dict | None) -> dict[str, dict]:
    todos = dict(logos_extras())
    if kit:
        todos.update(kit.get("logos") or {})
    return todos


def caminho_do_logo(kit: dict | None, nome: str) -> str:
    d = logos(kit).get(str(nome or "")) or logos(kit).get(_slug(nome))
    return d["caminho"] if d else ""


def guardar_logo(origem: str, nome: str = "") -> dict:
    """Põe um logo do disco na biblioteca (nada sai da máquina).

    PNG/WebP com transparência entram como estão. Print ou JPG com o logo
    sobre fundo liso (branco, cinza…) também servem: o fundo sai sozinho e o
    logo vira PNG transparente — "o logo do Airbnb e do Booking" que ele tira
    da internet chega assim."""
    src = Path(origem)
    if not src.is_file() or src.suffix.lower() not in EXTENSOES + (".jpg", ".jpeg"):
        raise ValueError("escolha a imagem do logo (PNG, WebP ou JPG)")
    if src.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("arquivo grande demais para um logo")
    slug = _slug(nome or src.stem) or "logo"
    PASTA_DE_LOGOS.mkdir(parents=True, exist_ok=True)
    from . import logo3d

    base = PASTA_DE_LOGOS / slug
    tmp = PASTA_DE_LOGOS / f".{slug}.novo"
    r = logo3d.limpar_para_biblioteca(str(src), str(tmp))     # erro: o antigo fica
    for ext in EXTENSOES:                     # o novo substitui o antigo com o mesmo nome
        antigo = base.with_suffix(ext)
        if antigo.is_file():
            antigo.unlink()
    destino = base.with_suffix(Path(r["caminho"]).suffix)
    Path(r["caminho"]).replace(destino)
    return {"nome": slug, "caminho": str(destino), "fundo_tirado": r["fundo_tirado"]}


def apagar_logo(nome: str) -> bool:
    slug = _slug(nome)
    apagou = False
    for ext in EXTENSOES:
        p = PASTA_DE_LOGOS / f"{slug}{ext}"
        if p.is_file():
            p.unlink()
            apagou = True
    return apagou


# ----------------------------------------------------------------- grafia
def _variantes_regex(kit: dict) -> re.Pattern | None:
    exata = kit["grafia"]["exata"]
    formas = {exata, *kit["grafia"]["variantes"]}
    partes = []
    for f in sorted(formas, key=len, reverse=True):
        toks = [re.escape(t) for t in re.split(r"[\s\-]+", f.strip()) if t]
        if toks:
            partes.append(r"[\s\-]*".join(toks))
    if not partes:
        return None
    return re.compile(r"(?<![\w])(?:" + "|".join(partes) + r")(?![\w])", re.I)


def _dobrar(t: str) -> str:
    t = unicodedata.normalize("NFKD", t)
    return "".join(c for c in t if not unicodedata.combining(c))


def corrigir_grafia(texto: str, kit: dict | None) -> str:
    """"HOSPEDEPAY", "Hospede Pay", "hóspede-pay" → "hospedepay"."""
    if not kit or not texto:
        return texto
    rx = _variantes_regex(kit)
    if not rx:
        return texto
    exata = kit["grafia"]["exata"]
    # compara sem acento: "hóspede pay" e "hospede pay" são a mesma variante
    dobrado = _dobrar(texto)
    if len(dobrado) != len(texto):
        return rx.sub(exata, texto)
    out, fim = [], 0
    for m in rx.finditer(dobrado):
        out.append(texto[fim:m.start()])
        out.append(exata)
        fim = m.end()
    out.append(texto[fim:])
    return "".join(out)


def regras_de_legenda(kit: dict | None) -> list[dict]:
    """Regras do dicionário de correções para a legenda (grafia EXATA)."""
    if not kit:
        return []
    exata = kit["grafia"]["exata"]
    out = []
    for v in {exata, *kit["grafia"]["variantes"]}:
        tokens = " ".join(t for t in re.split(r"[\s\-]+", v.strip()) if t)
        if tokens:
            out.append({"id": f"marca:{tokens}", "from": tokens, "to": exata,
                        "enabled": True, "exato": True})
    return out


def dica_de_transcricao(kit: dict | None) -> str:
    """As palavras que o Whisper tem que conhecer (vai como prompt inicial)."""
    if not kit:
        return ""
    vocab = [kit["grafia"]["exata"], *kit.get("vocabulario", [])]
    vistos: list[str] = []
    for v in vocab:
        if v and v not in vistos:
            vistos.append(v)
    return ", ".join(vistos)[:200]


# ---------------------------------------------------------------- o Claude
def resumo(kit: dict | None, com_logos: bool = True) -> str:
    """O kit em texto, para o Claude (vai no pedido e na ferramenta ``marca``)."""
    if not kit:
        extras = logos_extras()
        if not extras:
            return "Nenhuma marca ligada."
        return ("Nenhuma marca ligada. Logos disponíveis: "
                + ", ".join(sorted(extras)) + ".")
    c = kit["cores"]
    linhas = [f"MARCA: {kit['nome']} — {kit['descricao']}".strip(" —"),
              f"GRAFIA EXATA: {kit['grafia']['exata']} (o Sharkcut corrige sozinho, mas "
              f"escreva certo)",
              "CORES: " + ", ".join(f"{k} {v}" for k, v in c.items()),
              f"FONTE: {kit['fonte'].get('texto', '')}"
              + (f" (título: {kit['fonte'].get('titulo')})" if kit['fonte'].get('titulo') else "")
              + ("" if kit["fonte"].get("negrito", True) else ", SEM negrito")]
    if kit["regras"]:
        linhas.append("REGRAS DA MARCA:")
        linhas += [f"- {r}" for r in kit["regras"]]
    if kit["voz"]:
        linhas.append("VOZ (para qualquer texto que você escrever):")
        linhas += [f"- {v}" for v in kit["voz"]]
    if kit["frases"]:
        linhas.append("FRASES DA MARCA: " + " · ".join(kit["frases"]))
    if com_logos:
        todos = logos(kit)
        if todos:
            linhas.append("LOGOS (use pelo nome no gráfico tipo logo):")
            for nome, d in todos.items():
                dono = "" if d["da_marca"] else " (logo de plataforma/terceiro)"
                linhas.append(f"- {nome}{dono}: {d['uso']}".rstrip(": "))
    return "\n".join(linhas)


def publico(kit: dict | None) -> dict | None:
    """O kit para a tela: sem caminhos do disco."""
    if not kit:
        return None
    return {"slug": kit["slug"], "nome": kit["nome"], "descricao": kit["descricao"],
            "cores": kit["cores"], "fonte": {k: v for k, v in kit["fonte"].items()
                                             if k != "arquivos"},
            "regras": kit["regras"], "voz": kit["voz"], "frases": kit["frases"],
            "logos": {n: {"uso": d["uso"], "da_marca": d["da_marca"]}
                      for n, d in logos(kit).items()},
            "tem_fonte": bool(kit["fontes"])}
