"""Quebra de linha das legendas (Parte 5.2)."""
from __future__ import annotations

import re

from ..config import SubtitleStyle

CONNECTORS = {"e", "mas", "ou", "porque", "que", "quando", "para", "então",
              "entao", "se", "como", "com", "de", "do", "da", "em", "no", "na",
              "pra", "por", "até", "ate"}
SENTENCE_END = ".!?…"
SOFT_END = ",;:"
_MAX_GAP = 0.65          # buraco maior que isso sempre fecha a legenda


def wrap(text: str, max_chars: int, max_lines: int) -> list[str] | None:
    """Quebra em até ``max_lines`` linhas de ``max_chars``. None se não couber."""
    words = text.split()
    if not words:
        return None
    if any(len(w) > max_chars for w in words):
        # palavra sozinha maior que a linha: aceita estourar, não corta palavra
        max_chars = max(max_chars, max(len(w) for w in words))
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if len(cand) <= max_chars:
            cur = cand
        else:
            lines.append(cur)
            cur = w
            if len(lines) >= max_lines:
                return None
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        return None
    if len(lines) == 2:
        lines = _balance(lines, max_chars)
    return lines


def _balance(lines: list[str], max_chars: int) -> list[str]:
    """Evita uma linha cheia e outra com uma palavra só."""
    words = (lines[0] + " " + lines[1]).split()
    best, best_score = lines, 10**9
    for k in range(1, len(words)):
        a = " ".join(words[:k])
        b = " ".join(words[k:])
        if len(a) > max_chars or len(b) > max_chars:
            continue
        score = abs(len(a) - len(b))
        if words[k].lower().strip(",.;:") in CONNECTORS:
            score -= 4          # quebrar antes de conector é preferível
        if a.rstrip()[-1:] in SOFT_END:
            score -= 6
        if score < best_score:
            best, best_score = [a, b], score
    return best


def _break_score(words: list[dict], i: int, style: SubtitleStyle) -> float:
    """Quanto vale fechar a legenda depois de ``words[i]``."""
    text = words[i]["text"].rstrip()
    score = 0.0
    if text[-1:] in SENTENCE_END:
        score += 100
    elif text[-1:] in SOFT_END:
        score += 60
    if i + 1 < len(words):
        gap = words[i + 1]["start"] - words[i]["end"]
        if gap >= 0.20:
            score += 40 + min(gap, 1.0) * 20
        nxt = re.sub(r"[^\wÀ-ÿ]", "", words[i + 1]["text"]).lower()
        if nxt in CONNECTORS:
            score += 20
    else:
        score += 120
    return score


def build_cues(words: list[dict], style: SubtitleStyle,
               limit: float | None = None) -> list[dict]:
    """Agrupa palavras (já na linha do tempo de SAÍDA) em legendas."""
    if not words:
        return []
    max_chars = style.max_chars_per_line
    cues: list[dict] = []
    i = 0
    n = len(words)
    while i < n:
        best_j = i
        best_lines = wrap(words[i]["text"], max_chars, style.max_lines) or [words[i]["text"]]
        best_score = -1e9
        j = i
        while j < n:
            text = " ".join(w["text"] for w in words[i:j + 1])
            lines = wrap(text, max_chars, style.max_lines)
            duration = words[j]["end"] - words[i]["start"]
            if lines is None or duration > style.max_duration:
                break
            fill = len(text) / float(max_chars * style.max_lines)
            score = _break_score(words, j, style) + fill * 25
            if score >= best_score:
                best_score, best_j, best_lines = score, j, lines
            if j + 1 < n and words[j + 1]["start"] - words[j]["end"] > _MAX_GAP:
                best_score, best_j, best_lines = 1e9, j, lines
                break
            j += 1
        group = words[i:best_j + 1]
        cues.append({
            "start": float(group[0]["start"]),
            "end": float(group[-1]["end"]),
            "text": "\n".join(best_lines),
            "word_ids": [w.get("i") for w in group],
        })
        i = best_j + 1

    return _post(cues, style, limit)


def _post(cues: list[dict], style: SubtitleStyle,
          limit: float | None = None) -> list[dict]:
    # funde legendas muito curtas na seguinte
    merged: list[dict] = []
    for cue in cues:
        plain = cue["text"].replace("\n", " ")
        if (len(plain.strip()) < style.merge_below_chars and merged
                and cue["start"] - merged[-1]["end"] < 0.6):
            prev = merged[-1]
            text = prev["text"].replace("\n", " ") + " " + plain
            lines = wrap(text, style.max_chars_per_line, style.max_lines)
            if lines is not None and cue["end"] - prev["start"] <= style.max_duration * 1.2:
                prev["text"] = "\n".join(lines)
                prev["end"] = cue["end"]
                prev["word_ids"] = prev["word_ids"] + cue["word_ids"]
                continue
        merged.append(cue)

    # estende cada legenda, sem invadir a próxima
    for k, cue in enumerate(merged):
        stop = (merged[k + 1]["start"] - 0.02 if k + 1 < len(merged)
                else cue["end"] + style.extend)
        cue["end"] = round(min(cue["end"] + style.extend, max(stop, cue["end"])), 3)
        if limit is not None:
            cue["end"] = round(min(cue["end"], limit), 3)
            cue["start"] = round(min(cue["start"], max(0.0, limit - 0.05)), 3)
        cue["start"] = round(cue["start"], 3)
    return [c for c in merged if c["end"] > c["start"] + 0.01]
