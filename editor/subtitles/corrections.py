"""Dicionário de correções (Parte 5.4).

Aplicado automaticamente, editável pela interface, persistente entre projetos.
A correção preserva o que vem depois da palavra: "cheque," vira "check-in,".

O padrão aceita o curinga ``{n}`` para qualquer número — é assim que
"R $ 300" -> "R$300" também pega "R $ 47".
"""
from __future__ import annotations

import re
import unicodedata

DEFAULTS: list[dict] = [
    # exatamente as que o Whisper erra sempre nestas gravações
    {"from": "rdng", "to": "Airbnb"},
    {"from": "RBMP", "to": "Airbnb"},
    {"from": "RBB", "to": "Airbnb"},
    {"from": "RBMB", "to": "Airbnb"},
    {"from": "cheque", "to": "check-in"},
    {"from": "chequinho", "to": "check-in"},
    {"from": "óssepe", "to": "hóspede"},
    {"from": "ospede", "to": "hóspede"},
    {"from": "hospedis", "to": "hóspede"},
    {"from": "visto da sala", "to": "piso da sala"},
    {"from": "apumulo", "to": "acúmulo"},
    {"from": "de TFLM de", "to": "defendendo"},
    {"from": "20 %", "to": "20%"},
    {"from": "R $ 300", "to": "R$300"},
    # versões generalizadas dos dois últimos
    {"from": "{n} %", "to": "{n}%"},
    {"from": "R $ {n}", "to": "R${n}"},
]

_NUM_RE = re.compile(r"^\d+([.,]\d+)?$")
_TRAIL = ".,!?;:)\"'»…"
_LEAD = "(\"'«¿¡"


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _core(text: str) -> tuple[str, str, str]:
    """Separa (prefixo, miolo, sufixo de pontuação)."""
    lead = ""
    while text and text[0] in _LEAD:
        lead, text = lead + text[0], text[1:]
    trail = ""
    while text and text[-1] in _TRAIL:
        trail, text = text[-1] + trail, text[:-1]
    return lead, text, trail


def default_corrections() -> list[dict]:
    return [{"id": i, "from": d["from"], "to": d["to"], "enabled": True}
            for i, d in enumerate(DEFAULTS)]


def _match_at(words: list[dict], i: int, tokens: list[str]) -> list[str] | None:
    """Tenta casar ``tokens`` a partir de ``words[i]``. Devolve as capturas."""
    if i + len(tokens) > len(words):
        return None
    captures: list[str] = []
    for k, tok in enumerate(tokens):
        _, core, _ = _core(words[i + k]["text"])
        if tok == "{n}":
            if not _NUM_RE.match(core):
                return None
            captures.append(core)
        elif _fold(core) != _fold(tok):
            return None
    return captures


def _restore_case(original: str, replacement: str) -> str:
    if original[:1].isupper() and replacement[:1].islower():
        return replacement[0].upper() + replacement[1:]
    return replacement


def apply_corrections(words: list[dict], rules: list[dict]) -> tuple[list[dict], list[dict]]:
    """Devolve (palavras corrigidas, log de aplicações).

    As palavras corrigidas mantêm ``i`` original quando possível; quando uma
    regra funde vários tokens num só, os tempos são unidos.
    """
    active = [r for r in rules if r.get("enabled", True) and r.get("from")]
    parsed = [(r, str(r["from"]).split()) for r in active]
    # padrões mais longos primeiro, senão "20" comeria "20 %"
    parsed.sort(key=lambda p: -len(p[1]))

    out: list[dict] = []
    log: list[dict] = []
    i = 0
    while i < len(words):
        applied = False
        for rule, tokens in parsed:
            caps = _match_at(words, i, tokens)
            if caps is None:
                continue
            group = words[i:i + len(tokens)]
            lead, _, _ = _core(group[0]["text"])
            _, _, trail = _core(group[-1]["text"])
            target = str(rule["to"])
            for cap in caps:
                target = target.replace("{n}", cap, 1)
            target = _restore_case(_core(group[0]["text"])[1], target)

            pieces = target.split()
            span_start = float(group[0]["start"])
            span_end = float(group[-1]["end"])
            total_chars = sum(len(p) for p in pieces) or 1
            cursor = span_start
            for k, piece in enumerate(pieces):
                share = (span_end - span_start) * (len(piece) / total_chars)
                end = span_end if k == len(pieces) - 1 else cursor + share
                text = piece
                if k == 0:
                    text = lead + text
                if k == len(pieces) - 1:
                    text = text + trail
                out.append({
                    "i": len(out), "start": round(cursor, 3), "end": round(end, 3),
                    "text": text, "prob": min(w.get("prob", 1.0) for w in group),
                    "src_i": group[0]["i"], "corrected": True,
                    "original": " ".join(w["text"] for w in group),
                })
                cursor = end
            log.append({
                "rule": rule.get("id"),
                "from": " ".join(w["text"] for w in group),
                "to": target, "at": round(span_start, 3),
            })
            i += len(tokens)
            applied = True
            break
        if not applied:
            w = dict(words[i])
            w["src_i"] = w.get("src_i", w["i"])
            w["i"] = len(out)
            out.append(w)
            i += 1
    return out, log
