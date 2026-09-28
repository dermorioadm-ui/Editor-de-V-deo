"""O GOSTO DO DONO — aprendido com o que ele apaga e troca, e com o que ele escreve.

"Parece que está engessado. Eu quero que faça as coisas de acordo com o meu
gosto." Uma receita fixa na habilidade fazia todo vídeo sair com o mesmo
roteiro, e ele passava a edição corrigindo. Aqui fica a memória dessas
correções, para a próxima edição já nascer do jeito dele:

- quando ELE (pela tela) apaga um gráfico, cena, transição ou objeto 3D que a
  IA pôs (Claude, Codex, Gemini), fica anotado "apagou: cena vidro3d";
- quando ele TROCA algo que a IA pôs (estilo, entrada, camada, fundo, lado),
  fica anotado "trocou estilo vidro → marca";
- e as NOTAS que ele escreve na primeira tela ("nada pirotécnico", "3D só
  para mostrar o produto", "pele natural, sem filtro").

O diretor (Claude ou Codex) lê o resumo pela ferramenta ``gosto`` antes de
planejar. O que a própria IA muda e apaga não conta — só a mão dele.

Fica em ``DATA_DIR/gosto.json``, nesta máquina.
"""
from __future__ import annotations

import json
import threading
import time
from collections import Counter

from .config import DATA_DIR

ARQUIVO = DATA_DIR / "gosto.json"
AUTORES_IA = ("claude", "codex", "gemini", "ia")
MAX_SINAIS = 400
MAX_NOTAS = 2000
# o que vale anotar quando ele troca: o que é GOSTO, não posição fina
CAMPOS = ("tipo", "estilo", "entrada", "saida", "camada", "fundo", "lado", "icone", "logo",
          "anim_in", "anim_out")
_trava = threading.Lock()


def ler() -> dict:
    try:
        d = json.loads(ARQUIVO.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return {"notas": str(d.get("notas") or "")[:MAX_NOTAS],
                    "sinais": [s for s in d.get("sinais") or [] if isinstance(s, dict)]}
    except (OSError, ValueError):
        pass
    return {"notas": "", "sinais": []}


def _gravar(d: dict) -> None:
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    tmp = ARQUIVO.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(ARQUIVO)


def salvar_notas(texto: str) -> dict:
    with _trava:
        d = ler()
        d["notas"] = str(texto or "").strip()[:MAX_NOTAS]
        _gravar(d)
    return d


def esquecer() -> dict:
    """Zera o que foi aprendido (as notas escritas ficam)."""
    with _trava:
        d = ler()
        d["sinais"] = []
        _gravar(d)
    return d


def _nome(colecao: str, item: dict) -> str:
    """"cena vidro3d", "gráfico lista (marca)", "objeto 3D casa"."""
    if colecao == "cenas":
        return f"cena {item.get('tipo', '')}".strip()
    if colecao == "transicoes":
        return f"transição {item.get('tipo', '')}".strip()
    if colecao == "camadas":
        return f"camada {item.get('tipo', '') or item.get('efeito', '')}".strip()
    if colecao == "overlays":
        nome = str(item.get("nome") or item.get("name") or "")
        return nome if nome.startswith(("3D", "Transição 3D")) else "sobreposição"
    tipo = item.get("tipo", "")
    extra = item.get("icone") if tipo == "icone" else item.get("estilo")
    return f"gráfico {tipo}" + (f" ({extra})" if extra else "")


def _autor(item: dict) -> str:
    return str(item.get("origem") or "")


def registrar(acao: str, colecao: str, antes: dict, depois: dict | None = None,
              projeto: str = "") -> bool:
    """Anota uma correção DELE num item que a IA pôs. Devolve se anotou."""
    if _autor(antes) not in AUTORES_IA:
        return False
    sinal = {"quando": round(time.time()), "projeto": projeto, "acao": acao,
             "o_que": _nome(colecao, antes)}
    if acao == "trocou":
        mudou = {k: [antes.get(k), (depois or {}).get(k)] for k in CAMPOS
                 if k in antes and (depois or {}).get(k) != antes.get(k)}
        if not mudou:
            return False            # mexeu só em posição/tempo: ajuste fino, não gosto
        sinal["mudou"] = mudou
    with _trava:
        d = ler()
        d["sinais"] = (d["sinais"] + [sinal])[-MAX_SINAIS:]
        _gravar(d)
    return True


def resumo() -> str:
    """O gosto em texto, para o diretor ler antes de planejar."""
    d = ler()
    linhas = []
    if d["notas"]:
        linhas.append("O QUE ELE ESCREVEU SOBRE O GOSTO DELE (vale acima de qualquer regra):")
        linhas.append(d["notas"])
    apagou = Counter(s["o_que"] for s in d["sinais"] if s.get("acao") == "apagou")
    trocas = Counter()
    for s in d["sinais"]:
        if s.get("acao") == "trocou":
            for campo, (a, b) in (s.get("mudou") or {}).items():
                trocas[f"{s['o_que']}: {campo} {a or '—'} → {b or '—'}"] += 1
    if apagou:
        linhas.append("O QUE ELE JÁ APAGOU do que a IA pôs (não use de novo — "
                      "ou use muito menos, se foi 1 vez):")
        linhas += [f"- {o} ×{n}" for o, n in apagou.most_common(15)]
    if trocas:
        linhas.append("O QUE ELE TROCOU (use já do jeito que ele deixou):")
        linhas += [f"- {o} ×{n}" for o, n in trocas.most_common(15)]
    if not linhas:
        return ("Ainda não há gosto anotado: nenhuma nota escrita e nenhuma correção "
                "dele. Siga os critérios da habilidade (produto primeiro, sóbrio, pouco "
                "e variado).")
    return "\n".join(linhas)


def publico() -> dict:
    d = ler()
    return {"notas": d["notas"], "resumo": resumo(), "correcoes": len(d["sinais"])}
