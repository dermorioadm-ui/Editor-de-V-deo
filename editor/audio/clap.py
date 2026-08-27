"""Detecção de palma e regra do take (Parte 2.4).

Protocolo do usuário: errou a frase, bate uma palma, conta 3 e refaz a frase.
A palma descarta a FRASE em andamento — não tudo que veio antes.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass

import numpy as np

from .envelope import Envelope, _mask_runs

PEAK_MIN_DBFS = -9.0        # pico absoluto mínimo
JUMP_MIN_DB = 30.0          # salto sobre a mediana do entorno
CONTEXT = 1.0               # 1 s antes e depois formam o "entorno"
MAX_DURATION = 0.60         # palma é curta
ATTACK_WINDOW = 0.20        # 200 ms antes têm que cair pro piso
PHRASE_PAUSE = 0.40         # fronteira de frase
COUNT_TOKENS = {
    "um", "uma", "dois", "duas", "tres", "três", "1", "2", "3",
    "one", "two", "three",
}
RESUME_MAX_AFTER_CLAP = 6.0


@dataclass
class ClapEvent:
    id: str
    time: float                 # instante do pico
    start: float                # início do transiente
    end: float                  # fim do transiente
    peak_db: float
    jump_db: float
    duration: float
    confirmed: bool             # passou nos 4 critérios
    suspect: bool               # falhou SÓ no critério do ataque
    attack_floor_db: float      # menor nível nos 200 ms anteriores
    reason: str = ""
    enabled: bool = True        # o usuário pode desligar uma palma suspeita

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DiscardedTake:
    id: str
    start: float
    end: float
    clap_id: str | None
    clap_time: float | None
    text: str = ""
    restored: bool = False      # o usuário pode recuperar um take
    reason: str = "palma"

    def to_dict(self) -> dict:
        return asdict(self)


def detect_claps(samples: np.ndarray, sample_rate: int, env: Envelope,
                 peak_min_dbfs: float = PEAK_MIN_DBFS) -> list[ClapEvent]:
    """Aplica os quatro critérios. Nunca descarta um candidato sozinho.

    Quem falha só no critério do ataque volta como ``suspect`` para o usuário
    confirmar na interface. Sem o critério do ataque uma sílaba tônica no meio
    de fala contínua vira palma — numa gravação real isso deu 31 falsos.
    """
    db = env.db
    if not db.size:
        return []

    above = db >= peak_min_dbfs
    events: list[ClapEvent] = []
    for i0, i1 in _mask_runs(above):
        seg = db[i0:i1]
        pk_rel = int(np.argmax(seg))
        peak_idx = i0 + pk_rel
        peak_db = float(seg[pk_rel])
        t_peak = env.time(peak_idx)

        # extensão do transiente: contíguo acima do limiar de fala
        lo = peak_idx
        while lo > 0 and db[lo - 1] >= env.speech_threshold:
            lo -= 1
        hi = peak_idx
        while hi < len(db) - 1 and db[hi + 1] >= env.speech_threshold:
            hi += 1
        t_start, t_end = env.time(lo), env.time(hi + 1)
        duration = t_end - t_start

        # entorno: 1 s antes e 1 s depois, ignorando o próprio transiente
        before = db[max(0, lo - int(CONTEXT / env.hop)):lo]
        after = db[hi + 1:hi + 1 + int(CONTEXT / env.hop)]
        context = np.concatenate([before, after]) if before.size or after.size else db
        median_ctx = float(np.median(context)) if context.size else env.noise_floor
        jump = peak_db - median_ctx

        # ataque a partir do silêncio
        a0 = max(0.0, t_start - ATTACK_WINDOW)
        attack_floor = env.min_db(a0, max(a0, t_start - 0.02))
        attack_ok = attack_floor <= env.silence_threshold

        crit_peak = peak_db >= peak_min_dbfs
        crit_jump = jump >= JUMP_MIN_DB
        crit_dur = duration <= MAX_DURATION
        if not (crit_peak and crit_jump and crit_dur):
            continue

        reasons = []
        if not attack_ok:
            reasons.append(
                f"ataque não vem do silêncio (mínimo {attack_floor:.1f} dB nos "
                f"200 ms anteriores, limiar {env.silence_threshold:.1f} dB)"
            )
        events.append(ClapEvent(
            id=uuid.uuid4().hex[:10],
            time=round(t_peak, 3),
            start=round(t_start, 3),
            end=round(t_end, 3),
            peak_db=round(peak_db, 2),
            jump_db=round(jump, 2),
            duration=round(duration, 3),
            confirmed=attack_ok,
            suspect=not attack_ok,
            attack_floor_db=round(attack_floor, 2),
            reason="; ".join(reasons),
            enabled=attack_ok,
        ))
    return events


def phrase_start_before(env: Envelope, t: float,
                        pause: float = PHRASE_PAUSE) -> float:
    """Início da frase em andamento: fim da pausa longa mais próxima antes."""
    runs = env.silence_runs(max(0.0, t - 60.0), t, min_duration=pause)
    if runs:
        return runs[-1].end
    return 0.0


def resume_point_after(env: Envelope, clap_end: float, words: list,
                       pause: float = PHRASE_PAUSE) -> float:
    """Onde a refeitura começa: depois da contagem, na próxima pausa."""
    limit = clap_end + RESUME_MAX_AFTER_CLAP
    cursor = clap_end
    # engole a contagem ("um, dois, três") logo depois da palma
    for w in words:
        start = float(w["start"] if isinstance(w, dict) else w.start)
        end = float(w["end"] if isinstance(w, dict) else w.end)
        text = (w["text"] if isinstance(w, dict) else w.text)
        if end <= clap_end:
            continue
        if start > limit:
            break
        token = "".join(ch for ch in text.lower() if ch.isalnum())
        if start <= cursor + 0.9 and token in COUNT_TOKENS:
            cursor = end
            continue
        if start > cursor + 0.05:
            break
    runs = env.silence_runs(cursor, min(limit, env.duration), min_duration=pause)
    if runs:
        return runs[0].end
    return cursor


def build_discarded_takes(env: Envelope, claps: list[ClapEvent],
                          words: list) -> list[DiscardedTake]:
    """Um take descartado por palma ativa, delimitado pela pausa anterior."""
    takes: list[DiscardedTake] = []
    for clap in claps:
        if not clap.enabled:
            continue
        start = phrase_start_before(env, clap.start)
        end = resume_point_after(env, clap.end, words)
        if end <= start:
            end = clap.end
        # não engolir um take já descartado por uma palma anterior
        if takes and start < takes[-1].end:
            start = takes[-1].end
        if end - start < 0.05:
            continue
        takes.append(DiscardedTake(
            id=uuid.uuid4().hex[:10],
            start=round(start, 3),
            end=round(end, 3),
            clap_id=clap.id,
            clap_time=clap.time,
            text=_words_text(words, start, end),
        ))
    return takes


def _words_text(words: list, start: float, end: float) -> str:
    out = []
    for w in words:
        ws = float(w["start"] if isinstance(w, dict) else w.start)
        we = float(w["end"] if isinstance(w, dict) else w.end)
        overlap = min(we, end) - max(ws, start)
        if overlap > 0.5 * (we - ws):
            out.append((w["text"] if isinstance(w, dict) else w.text).strip())
    return " ".join(out).strip()
