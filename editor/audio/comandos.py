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

    O critério é por SEQUÊNCIA, não por palavra: primeiro se junta a corrida
    de palavras de vocabulário emendadas ("corta", "corta corta", "corta ok"),
    e o isolamento — pausa dos dois lados — é exigido nas bordas EXTERNAS da
    corrida inteira. A revisão adversarial reproduziu os três buracos da
    versão por palavra:

      "Corta, corta pra cena do produto"  a primeira palavra virava comando
                                          (o vizinho ser do vocabulário
                                          dispensava a pausa) e o take BOM
                                          era apagado;
      "Corta. não. Corta."                dois comandos fundiam atravessando
                                          conteúdo, porque a fusão só olhava
                                          distância no tempo;
      "corta ok" emendado                 os dois se anulavam (cada um exigia
                                          pausa contra o outro) e NADA era
                                          cortado.

    Agora: a corrida emendada em conteúdo não é comando nenhum; a fusão só
    acontece dentro da própria corrida; e "corta ok" vira os dois comandos,
    na ordem em que foram ditos.
    """
    import uuid

    vocab = CORTA | OK
    n = len(words)

    def token(i: int) -> str:
        return _norm(str(words[i].get("text", "")))

    def dur_ok(i: int) -> bool:
        return float(words[i]["end"]) - float(words[i]["start"]) <= MAX_DUR

    achados: list[Comando] = []
    brutos: list[tuple[str, int, int]] = []
    i = 0
    while i < n:
        if token(i) not in vocab or not dur_ok(i):
            i += 1
            continue
        # a corrida: palavras de vocabulário emendadas umas nas outras
        j = i
        while (j + 1 < n and token(j + 1) in vocab and dur_ok(j + 1)
               and float(words[j + 1]["start"]) - float(words[j]["end"])
               < PAUSA_MIN):
            j += 1
        # isolamento nas bordas EXTERNAS da corrida inteira
        antes = float(words[i - 1]["end"]) if i > 0 else -1e9
        depois = float(words[j + 1]["start"]) if j + 1 < n else 1e9
        isolada = (float(words[i]["start"]) - antes >= PAUSA_MIN
                   and depois - float(words[j]["end"]) >= PAUSA_MIN)
        if isolada:
            # dentro da corrida, um comando por trecho contíguo do mesmo tipo:
            # "corta corta ok" -> [corta] e [ok], na ordem
            k = i
            while k <= j:
                tipo = "corta" if token(k) in CORTA else "ok"
                m = k
                while m + 1 <= j and ("corta" if token(m + 1) in CORTA
                                      else "ok") == tipo:
                    m += 1
                brutos.append((tipo, k, m))
                k = m + 1
        i = j + 1

    # "corta ... corta" com pausa no meio mas SEM NENHUMA PALAVRA entre eles
    # ainda é um gesto só: funde. A adjacência é por índice — havendo
    # conteúdo no meio, não funde nunca (era o buraco da fusão por tempo).
    fundidos: list[list] = []
    for tipo, k0, k1 in brutos:
        if (fundidos and fundidos[-1][0] == tipo
                and k0 == fundidos[-1][2] + 1
                and float(words[k0]["start"]) - float(words[fundidos[-1][2]]["end"])
                < PAUSA_MIN + MAX_DUR):
            fundidos[-1][2] = k1
        else:
            fundidos.append([tipo, k0, k1])

    for tipo, k0, k1 in fundidos:
        bloco = words[k0:k1 + 1]
        achados.append(Comando(
            id=f"cmd_{uuid.uuid4().hex[:8]}", tipo=tipo,
            time=round((float(bloco[0]["start"])
                        + float(bloco[-1]["end"])) / 2.0, 3),
            start=round(float(bloco[0]["start"]), 3),
            end=round(float(bloco[-1]["end"]), 3),
            word_ids=[w.get("id", idx) for idx, w in enumerate(words)
                      if k0 <= idx <= k1],
            texto=" ".join(str(w.get("text", "")).strip() for w in bloco)))
    return achados


def ids_de_comando(comandos: list) -> set[int]:
    """As palavras que são comando saem do vídeo e da legenda."""
    ids: set[int] = set()
    for c in comandos:
        lista = c.word_ids if isinstance(c, Comando) else c.get("word_ids", [])
        if (c.enabled if isinstance(c, Comando) else c.get("enabled", True)):
            ids.update(int(x) for x in lista)
    return ids
