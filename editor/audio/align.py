"""Encaixa os tempos das palavras no áudio de verdade.

O Whisper não devolve fronteira acústica: ele devolve fronteira de
*alinhamento*. Quando o alinhamento erra, uma palavra de duas letras vem
ocupando cinco segundos — e esses cinco segundos são, na prática, uma pausa
enorme escondida dentro de uma palavra.

Isso quebra o corte de silêncio inteiro. O corte nasce do BURACO ENTRE
palavras; se a palavra cobre o silêncio, não existe buraco, não existe corte,
e o vale fica no vídeo para o usuário apagar na mão.

Aqui cada palavra é encolhida até onde há som. Depois disso o buraco reaparece
e o corte automático volta a funcionar.
"""
from __future__ import annotations

from .envelope import Envelope

EDGE_SILENCE = 0.10     # silêncio colado na borda: some
INNER_SILENCE = 0.30    # silêncio no meio: a palavra atravessou uma pausa
KEEP = 0.03             # sobra deixada de cada lado, para não comer o ataque
MIN_WORD = 0.06


def trim_words(words: list[dict], env: Envelope) -> tuple[list[dict], list[dict]]:
    """Devolve (palavras encaixadas, relatório do que mudou muito).

    Só encolhe: uma palavra nunca cresce aqui. Encolher é seguro — o pior caso
    é o corte ficar um pouco mais conservador. Crescer restauraria silêncio.
    """
    out: list[dict] = []
    fixes: list[dict] = []
    for w in words:
        a0 = float(w["start"])
        b0 = float(w["end"])
        a, b = a0, b0
        if b - a > 0.10:
            # ILHAS de fala dentro do intervalo (o complemento do silêncio).
            # Tratar "silêncio no começo" e "silêncio no fim" como duas regras
            # separadas quebrava quando UM silêncio cobria a palavra inteira:
            # as duas regras disparavam e a palavra ia parar no fim do vazio.
            runs = env.silence_runs(a, b, min_duration=EDGE_SILENCE)
            ilhas: list[tuple[float, float]] = []
            cursor = a
            for r in runs:
                if r.start - cursor > 0.02:
                    ilhas.append((cursor, r.start))
                cursor = max(cursor, r.end)
            if b - cursor > 0.02:
                ilhas.append((cursor, b))
            if ilhas:
                # a PRIMEIRA ilha. O Whisper acerta o ataque muito melhor que
                # o fim — o modo de falhar dele é esticar o fim até a próxima
                # palavra, nunca puxar o começo para trás.
                ia, ib = ilhas[0]
                a = max(a, ia - KEEP)
                b = min(b, max(ib + KEEP, a + MIN_WORD))
            else:
                # o intervalo inteiro é silêncio: a palavra não tem onde
                # morar. Fica um fiapo no começo, que é onde ela foi ouvida.
                b = min(b, a + MIN_WORD)
        if b - a < MIN_WORD:
            b = min(b0, a + MIN_WORD)
        novo = dict(w)
        novo["start"] = round(a, 3)
        novo["end"] = round(b, 3)
        if (b0 - a0) - (b - a) > 0.25:
            fixes.append({
                "i": w["i"], "text": w.get("text", ""),
                "from": [round(a0, 3), round(b0, 3)],
                "to": [round(a, 3), round(b, 3)],
                "ganho": round((b0 - a0) - (b - a), 3),
            })
        out.append(novo)
    return out, fixes


def long_silences_inside(clips, env: Envelope, minimum: float) -> list[dict]:
    """Vales de silêncio que SOBRARAM dentro do que vai para o vídeo.

    É a prova de que o corte automático fez o trabalho: esta lista tem que
    ficar vazia. Se não ficar, sobrou vale para o usuário apagar na mão — que
    é exatamente a reclamação que este módulo existe para matar.
    """
    achados: list[dict] = []
    for c in clips:
        if not c.enabled or c.source != "main":
            continue
        for r in env.silence_runs(c.src_start, c.src_end, min_duration=minimum):
            if r.start <= c.src_start + 0.02 or r.end >= c.src_end - 0.02:
                continue        # borda do bloco: é o ar do corte, não um vale
            achados.append({"clip_id": c.id, "start": round(r.start, 3),
                            "end": round(r.end, 3),
                            "duration": round(r.end - r.start, 3)})
    return achados
