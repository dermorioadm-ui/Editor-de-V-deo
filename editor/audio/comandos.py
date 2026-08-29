"""Comandos FALADOS: "corta" apaga a tentativa, "ok" aprova.

Ideia do próprio usuário, e é a mais robusta das três formas de marcar:

    palma    -> detecção acústica por timbre (pode confundir com estouro)
    assobio  -> detecção acústica por tom   (pode confundir com sopro)
    PALAVRA  -> o Whisper JÁ transcreveu, com o tempo exato de cada uma

Palavra não tem falso positivo de acústica, não depende do microfone, não
precisa de calibração — e a IA que decide os cortes vê "corta" escrito no
texto, no lugar exato onde foi dito.

O critério para uma palavra virar comando é o ISOLAMENTO: ela precisa estar
sozinha, com pausa dos dois lados. "Corta" no meio de "corta para a cena do
produto" é conteúdo; "…o preço é esse. (pausa) Corta. (pausa) O preço…" é
comando. É a mesma diferença que um humano ouve.

A palavra de comando NUNCA aparece no vídeo final nem na legenda: ela é
instrução para o editor, não fala.
"""
from __future__ import annotations

import unicodedata
from dataclasses import asdict, dataclass

# Vocabulário curto de propósito: cada palavra a mais é uma chance a mais de
# um falso positivo. "corta" e "ok" foram o pedido; os sinônimos são os que
# saem naturalmente no set de gravação.
CORTA = {"corta", "apaga", "descarta", "errei"}
OK = {"ok", "okay", "oquei", "boa", "fechou"}

PAUSA_MIN = 0.35         # silêncio exigido dos dois lados do comando
MAX_DUR = 1.2            # "corta" dito arrastado ainda cabe; frase não


@dataclass
class Comando:
    id: str
    tipo: str                   # "corta" | "ok"
    time: float                 # meio da palavra
    start: float
    end: float
    word_ids: list[int]
    texto: str
    enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def _norm(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in t if c.isalnum())


def detectar(words: list[dict]) -> list[Comando]:
    """Acha os comandos falados na transcrição.

    Um comando é UMA palavra do vocabulário, isolada por pausa dos dois lados.
    Duas seguidas do mesmo tipo ("corta, corta") fundem num comando só.
    """
    import uuid

    achados: list[Comando] = []
    n = len(words)
    for i, w in enumerate(words):
        token = _norm(str(w.get("text", "")))
        tipo = "corta" if token in CORTA else ("ok" if token in OK else None)
        if tipo is None:
            continue
        inicio = float(w["start"])
        fim = float(w["end"])
        if fim - inicio > MAX_DUR:
            continue
        antes = float(words[i - 1]["end"]) if i > 0 else -1e9
        depois = float(words[i + 1]["start"]) if i + 1 < n else 1e9
        # o vizinho pode ser OUTRO comando do mesmo tipo ("corta, corta"):
        # aí a pausa exigida é a de fora do par
        viz_antes = _norm(str(words[i - 1].get("text", ""))) if i > 0 else ""
        viz_depois = _norm(str(words[i + 1].get("text", ""))) if i + 1 < n else ""
        mesmo = CORTA if tipo == "corta" else OK
        if inicio - antes < PAUSA_MIN and viz_antes not in mesmo:
            continue
        if depois - fim < PAUSA_MIN and viz_depois not in mesmo:
            continue
        wid = w.get("id", i)
        if achados and achados[-1].tipo == tipo \
                and inicio - achados[-1].end < PAUSA_MIN + MAX_DUR:
            achados[-1].end = round(fim, 3)
            achados[-1].time = round((achados[-1].start + fim) / 2.0, 3)
            achados[-1].word_ids.append(wid)
            achados[-1].texto += f" {w.get('text', '')}".rstrip()
            continue
        achados.append(Comando(
            id=f"cmd_{uuid.uuid4().hex[:8]}", tipo=tipo,
            time=round((inicio + fim) / 2.0, 3),
            start=round(inicio, 3), end=round(fim, 3),
            word_ids=[wid], texto=str(w.get("text", "")).strip()))
    return achados


def ids_de_comando(comandos: list) -> set[int]:
    """As palavras que são comando saem do vídeo e da legenda."""
    ids: set[int] = set()
    for c in comandos:
        lista = c.word_ids if isinstance(c, Comando) else c.get("word_ids", [])
        if (c.enabled if isinstance(c, Comando) else c.get("enabled", True)):
            ids.update(int(x) for x in lista)
    return ids
