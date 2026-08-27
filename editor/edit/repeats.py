"""Repetição de fala (o segundo take sem palma).

Quem grava sozinho refaz a frase o tempo todo — às vezes bate palma, às vezes
só respira e recomeça. O detector de palma pega o primeiro caso; este pega o
segundo, que é o mais comum: o texto sai duas vezes quase igual e as duas
versões ficam no vídeo.

A regra é a mesma que a pessoa usaria: **quando você fala duas vezes a mesma
coisa, a que vale é a última.** A primeira foi a que deu errado.

Nada aqui pergunta nada. O que sai vai para a lista do que foi removido
sozinho, com o texto das duas versões lado a lado, e volta com um clique.
"""
from __future__ import annotations

import difflib
import unicodedata
import uuid
from dataclasses import asdict, dataclass

from ..audio.envelope import Envelope
from ..audio.segments import split_narrative

MIN_WORDS = 5           # abaixo disso é ênfase de propósito, não erro de take
# O sinal forte é o CONTEÚDO. Medido em 16 pares: refeitura fica em 0,80-1,00
# de conteúdo, frase diferente em 0,00-0,67 — separação limpa. A estrutura
# sozinha se sobrepõe (0,71 numa refeitura contra 0,75 em frases diferentes),
# então ela entra só como trava fraca contra frase que compartilha substantivo
# por acaso.
SIMILARITY = 0.55           # trava fraca: estrutura
CONTENT_SIMILARITY = 0.75   # o que decide: as palavras de conteúdo
MIN_CONTENT = 2         # frase sem duas palavras de conteúdo não é comparável
LOOKAHEAD = 4           # quantos trechos à frente comparar
MAX_GAP = 45.0          # refazer uma frase 45 s depois já é outro assunto

# Palavra de ligação não diz do que a frase trata. Sem tirá-las, "eu vou te
# mostrar o print DA CONTA" e "eu vou te mostrar o print DO EXTRATO" batem
# 0,75 — e são duas frases diferentes. Só o conteúdo separa refeitura de
# paralelismo ("não é sobre PREÇO é sobre VALOR" / "sobre SORTE / MÉTODO").
STOPWORDS = {
    "a", "o", "as", "os", "um", "uma", "uns", "umas", "de", "da", "do", "das",
    "dos", "em", "na", "no", "nas", "nos", "por", "pra", "para", "pro", "com",
    "sem", "sobre", "ate", "ate", "e", "ou", "mas", "que", "se", "ja", "so",
    "nao", "sim", "eu", "voce", "vc", "tu", "ele", "ela", "nos", "eles", "meu",
    "minha", "seu", "sua", "teu", "isso", "isto", "aquilo", "esse", "essa",
    "este", "esta", "aqui", "ali", "la", "me", "te", "lhe", "vai", "vou",
    "ser", "e", "sao", "esta", "estao", "ta", "tao", "foi", "era", "tem",
    "ter", "the", "of", "muito", "mais", "menos", "bem", "todo", "toda",
    "todos", "todas", "quando", "como", "porque", "entao", "ai", "assim",
}

# Muleta no começo da refeitura ("então", "aí", "é") não conta para a
# comparação: "vou te contar" e "então vou te contar" são a mesma frase.
EDGE_FILLERS = {
    "entao", "ai", "e", "eh", "ah", "tipo", "assim", "bom", "ok", "certo",
    "olha", "veja", "poxa", "cara", "ne", "ta", "то",
}


@dataclass
class RepeatedTake:
    id: str
    start: float
    end: float
    text: str               # o que foi removido
    kept_start: float       # onde está a versão que ficou
    kept_end: float
    kept_text: str
    similarity: float
    word_ids: list[int]
    restored: bool = False
    reason: str = "repeticao"

    def to_dict(self) -> dict:
        return asdict(self)


def _norm(text: str) -> str:
    """Minúsculo, sem acento e sem pontuação — para comparar o que foi DITO."""
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return "".join(c for c in t if c.isalnum())


def _tokens(words: list[dict]) -> list[str]:
    out = [_norm(w["text"]) for w in words]
    out = [t for t in out if t]
    while out and out[0] in EDGE_FILLERS:
        out.pop(0)
    while out and out[-1] in EDGE_FILLERS:
        out.pop()
    return out


def similarity(a: list[str], b: list[str]) -> float:
    """Quanto as duas falas se parecem, tolerando palavra a mais ou a menos."""
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def content(tokens: list[str]) -> list[str]:
    """Só as palavras que dizem do que a frase trata."""
    return [t for t in tokens if t not in STOPWORDS and len(t) > 1]


def is_repeat(tok_a: list[str], tok_b: list[str],
              threshold: float = SIMILARITY,
              content_threshold: float = CONTENT_SIMILARITY) -> float:
    """Semelhança se as DUAS medidas passarem; 0.0 se qualquer uma falhar.

    Estrutura parecida sozinha não basta (paralelismo retórico tem estrutura
    idêntica de propósito). Conteúdo parecido sozinho também não (duas frases
    do mesmo assunto compartilham substantivo). Refeitura tem as duas.
    """
    sim = similarity(tok_a, tok_b)
    if sim < threshold:
        return 0.0
    ca, cb = content(tok_a), content(tok_b)
    if len(ca) < MIN_CONTENT or len(cb) < MIN_CONTENT:
        return 0.0
    if similarity(ca, cb) < content_threshold:
        return 0.0
    return sim


def find_repeats(words: list[dict], env: Envelope,
                 pause: float = 0.80,
                 threshold: float = SIMILARITY,
                 min_words: int = MIN_WORDS,
                 lookahead: int = LOOKAHEAD,
                 already_removed: set[int] | None = None) -> list[RepeatedTake]:
    """Acha trechos ditos duas vezes e devolve os que devem SAIR (os antigos).

    Compara cada trecho narrativo com os ``lookahead`` seguintes. Se a
    semelhança passa do limiar, o ANTERIOR sai. Numa sequência de três
    tentativas isso sobra a última sozinho: 1 sai por causa de 2, e 2 sai por
    causa de 3.
    """
    already_removed = already_removed or set()
    segments = split_narrative(words, env, pause)
    live = []
    for seg in segments:
        kept = [w for w in seg.words if w["i"] not in already_removed]
        if kept:
            live.append((seg, kept, _tokens(kept)))

    out: list[RepeatedTake] = []
    dropped: set[int] = set()      # índices de trecho já marcados para sair
    for i, (seg_a, words_a, tok_a) in enumerate(live):
        if i in dropped or len(tok_a) < min_words:
            continue
        best: tuple[float, int] | None = None
        for j in range(i + 1, min(i + 1 + lookahead, len(live))):
            if j in dropped:
                continue
            seg_b, words_b, tok_b = live[j]
            if seg_b.start - seg_a.end > MAX_GAP:
                break
            if len(tok_b) < min_words:
                continue
            sim = is_repeat(tok_a, tok_b, threshold)
            if sim > 0 and (best is None or sim > best[0]):
                best = (sim, j)
        if best is None:
            continue
        sim, j = best
        seg_b, words_b, _ = live[j]
        dropped.add(i)
        out.append(RepeatedTake(
            id=uuid.uuid4().hex[:10],
            start=round(float(words_a[0]["start"]), 3),
            end=round(float(words_a[-1]["end"]), 3),
            text=" ".join(w["text"].strip() for w in words_a).strip(),
            kept_start=round(float(words_b[0]["start"]), 3),
            kept_end=round(float(words_b[-1]["end"]), 3),
            kept_text=" ".join(w["text"].strip() for w in words_b).strip(),
            similarity=round(float(sim), 3),
            word_ids=[int(w["i"]) for w in words_a],
        ))
    return out


def removed_word_ids(repeats: list) -> set[int]:
    ids: set[int] = set()
    for r in repeats:
        if isinstance(r, dict):
            if r.get("restored"):
                continue
            ids.update(int(i) for i in r.get("word_ids", []))
        else:
            if r.restored:
                continue
            ids.update(r.word_ids)
    return ids
