"""Vícios de fala (Parte 5.5).

Marcar é sempre seguro. Remover só quando dá: se a palavra não tem pausa dos
dois lados, tirá-la danifica a vizinha. Vício limpo é melhor que palavra
quebrada.
"""
from __future__ import annotations

import unicodedata

from ..audio.envelope import Envelope

DEFAULT_FILLERS = [
    "simplesmente", "enfim", "tipo assim", "na verdade", "então", "né",
    "tipo", "assim", "sabe", "olha só", "quer dizer", "ou seja", "é isso",
]

MIN_PAUSE = 0.12        # pausa mínima de cada lado para o corte ser seguro
MIN_VALLEY = 0.06       # e o vale precisa existir no envelope


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return "".join(c for c in text if c.isalnum() or c == " ")


def find_fillers(words: list[dict], fillers: list[str] | None = None) -> list[dict]:
    """Todas as ocorrências, como n-gramas de índices."""
    terms = [(_fold(f), len(f.split())) for f in (fillers or DEFAULT_FILLERS)]
    terms.sort(key=lambda t: -t[1])
    found: list[dict] = []
    used: set[int] = set()
    for i in range(len(words)):
        if i in used:
            continue
        for folded, n in terms:
            if i + n > len(words):
                continue
            chunk = " ".join(_fold(words[i + k]["text"]) for k in range(n)).strip()
            if chunk != folded:
                continue
            ids = list(range(i, i + n))
            found.append({
                "id": f"f{i}",
                "word_ids": ids,
                "text": " ".join(words[k]["text"] for k in ids),
                "start": float(words[i]["start"]),
                "end": float(words[i + n - 1]["end"]),
            })
            used.update(ids)
            break
    return found


def removal_safety(words: list[dict], ids: list[int], env: Envelope | None = None) -> dict:
    """Checa se dá para remover sem quebrar a palavra vizinha."""
    first, last = min(ids), max(ids)
    before = (words[first]["start"] - words[first - 1]["end"]) if first > 0 else 99.0
    after = (words[last + 1]["start"] - words[last]["end"]) if last + 1 < len(words) else 99.0

    valley_before = valley_after = True
    if env is not None:
        if first > 0:
            valley_before = bool(env.silence_runs(
                words[first - 1]["end"], words[first]["start"], MIN_VALLEY))
        if last + 1 < len(words):
            valley_after = bool(env.silence_runs(
                words[last]["end"], words[last + 1]["start"], MIN_VALLEY))

    ok_before = before >= MIN_PAUSE and valley_before
    ok_after = after >= MIN_PAUSE and valley_after
    safe = ok_before and ok_after

    def fmt(gap: float) -> str:
        return "início/fim do trecho" if gap >= 90.0 else f"{gap*1000:.0f} ms"

    if safe:
        reason = (f"pausa de {fmt(before)} antes e {fmt(after)} depois: "
                  f"dá para remover sem encostar na palavra vizinha")
    else:
        faltas = []
        if not ok_before:
            faltas.append(f"antes só há {fmt(before)}"
                          + ("" if valley_before else " e nenhum vale no envelope"))
        if not ok_after:
            faltas.append(f"depois só há {fmt(after)}"
                          + ("" if valley_after else " e nenhum vale no envelope"))
        reason = ("remover aqui danifica a palavra vizinha — " + "; ".join(faltas)
                  + ". Sugestão: manter.")
    return {
        "safe": safe,
        "pause_before": round(before, 3),
        "pause_after": round(after, 3),
        "valley_before": valley_before,
        "valley_after": valley_after,
        "reason": reason,
    }


def annotate(words: list[dict], env: Envelope | None = None,
             fillers: list[str] | None = None) -> list[dict]:
    out = []
    for item in find_fillers(words, fillers):
        item = dict(item)
        item.update(removal_safety(words, item["word_ids"], env))
        out.append(item)
    return out
