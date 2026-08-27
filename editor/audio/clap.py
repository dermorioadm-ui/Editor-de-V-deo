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

# --- o que separa uma palma de uma palavra forte ---------------------------
# Os critérios de envelope (pico, salto, duração, ataque) NÃO separam: uma
# palavra enfática logo depois de uma pausa passa em todos os quatro, porque a
# própria pausa é o "silêncio" de onde o ataque vem. Palma e "Pá!" têm o mesmo
# envelope; o que difere é o timbre. Medido em fala real contra palma real:
#
#                       palma          fala forte
#   subida (10→90%)     0,1–1,5 ms     43–325 ms
#   spectral flatness   0,84–0,86      0,05–0,08
#   agudo/grave         4,2–6,3        0,03–0,39
#
# Os limiares abaixo ficam no meio dessa distância, com folga para o áudio
# comprimido do WhatsApp (que borra o transiente e corta agudo).
MAX_RISE_MS = 12.0          # palma sobe quase instantaneamente
MIN_FLATNESS = 0.25         # palma é ruído de banda larga; voz é harmônica
MIN_HF_RATIO = 1.0          # energia 2–7 kHz sobre 150–1200 Hz
JUMP_MIN_DB = 30.0          # salto sobre a mediana do entorno
CONTEXT = 1.0               # 1 s antes e depois formam o "entorno"
MAX_DURATION = 0.60         # palma é curta
ATTACK_WINDOW = 0.20        # 200 ms antes têm que cair pro piso
PHRASE_PAUSE = 0.70         # fronteira de frase
# 0,40 s era pouco: quem respira no meio da frase tinha só a metade
# dela descartada, e a outra metade ficava no vídeo dobrando o texto.
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
    confirmed: bool             # passou em todos os critérios
    suspect: bool               # passou no timbre mas não no ataque
    attack_floor_db: float      # menor nível nos 200 ms anteriores
    rise_ms: float = 0.0        # tempo de subida 10→90% do pico
    flatness: float = 0.0       # planura espectral (ruído x harmônico)
    hf_ratio: float = 0.0       # razão agudo/grave
    timbre_score: int = 0       # quantos dos 3 critérios de timbre passaram
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


def timbre_features(samples: np.ndarray, sample_rate: int,
                    t_start: float, t_end: float) -> dict:
    """Mede o TIMBRE do transiente — é isto que distingue palma de fala."""
    i0 = max(0, int(t_start * sample_rate) - int(0.01 * sample_rate))
    i1 = min(len(samples), int(t_end * sample_rate) + int(0.02 * sample_rate))
    x = np.asarray(samples[i0:i1], dtype=np.float32)
    out = {"rise_ms": 99.0, "flatness": 0.0, "hf_ratio": 0.0}
    if x.size < 128:
        return out

    peak_i = int(np.argmax(np.abs(x)))
    peak = float(abs(x[peak_i])) or 1e-9

    # subida: do primeiro ponto com 10% do pico até o primeiro com 90%
    pre = np.abs(x[: peak_i + 1])
    a = np.flatnonzero(pre >= 0.1 * peak)
    b = np.flatnonzero(pre >= 0.9 * peak)
    if a.size and b.size:
        out["rise_ms"] = float(b[0] - a[0]) / sample_rate * 1000.0

    win = x[max(0, peak_i - int(0.005 * sample_rate)):
            peak_i + int(0.055 * sample_rate)]
    if win.size < 128:
        return out
    spec = np.abs(np.fft.rfft(win * np.hanning(win.size))) + 1e-12
    freqs = np.fft.rfftfreq(win.size, 1.0 / sample_rate)
    # planura: média geométrica sobre a aritmética. Ruído -> perto de 1,
    # voz (picos nos harmônicos) -> perto de 0.
    out["flatness"] = float(np.exp(np.mean(np.log(spec))) / np.mean(spec))

    def band(lo: float, hi: float) -> float:
        sel = (freqs >= lo) & (freqs < hi)
        return float((spec[sel] ** 2).sum()) if sel.any() else 0.0

    out["hf_ratio"] = band(2000, 7000) / max(band(150, 1200), 1e-12)
    return out


def detect_claps(samples: np.ndarray, sample_rate: int, env: Envelope,
                 peak_min_dbfs: float = PEAK_MIN_DBFS) -> list[ClapEvent]:
    """Envelope filtra o candidato; o TIMBRE decide se é palma.

    Os quatro critérios de envelope (pico, salto, duração, ataque) não separam
    palma de palavra forte: uma palavra enfática logo depois de uma pausa passa
    em todos os quatro, porque a própria pausa é o silêncio de onde o ataque
    vem. Quem separa é o timbre — subida, planura espectral e razão
    agudo/grave — e é ele que manda aqui.

    O que passa no timbre é palma e descarta o take SOZINHO. Nada vira
    pergunta: o usuário conserta depois, na lista do que saiu sozinho.
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
        if t_start < 0.06:
            # transiente colado no início do arquivo: não há janela para medir,
            # e antes do arquivo só existe silêncio — o critério vale
            attack_floor = env.noise_floor
            attack_ok = True
        else:
            attack_floor = env.min_db(a0, max(a0, t_start - 0.02))
            attack_ok = attack_floor <= env.silence_threshold

        crit_peak = peak_db >= peak_min_dbfs
        crit_jump = jump >= JUMP_MIN_DB
        crit_dur = duration <= MAX_DURATION
        if not (crit_peak and crit_jump and crit_dur):
            continue

        # TIMBRE — o filtro que realmente separa palma de palavra forte
        tf = timbre_features(samples, sample_rate, t_start, t_end)
        crit_rise = tf["rise_ms"] <= MAX_RISE_MS
        crit_flat = tf["flatness"] >= MIN_FLATNESS
        crit_hf = tf["hf_ratio"] >= MIN_HF_RATIO
        score = int(crit_rise) + int(crit_flat) + int(crit_hf)

        if score <= 1:
            # som de voz, não de mão: nem entra na lista
            continue

        # NUNCA pergunta. Passou no timbre, é palma, e o take vai embora
        # sozinho. Responder "é palma?" dezessete vezes não é editar vídeo.
        # O usuário conserta depois, na lista do que foi removido sozinho —
        # corrigir uma automação é barato; alimentar uma não é.
        confirmed = score == 3
        reasons = []
        if not crit_rise:
            reasons.append(f"sobe em {tf['rise_ms']:.0f} ms (palma sobe em "
                           f"menos de {MAX_RISE_MS:.0f} ms)")
        if not crit_flat:
            reasons.append(f"som harmônico, não de banda larga "
                           f"(planura {tf['flatness']:.2f}, palma fica acima de "
                           f"{MIN_FLATNESS:.2f})")
        if not crit_hf:
            reasons.append(f"pouco agudo para uma palma "
                           f"(razão {tf['hf_ratio']:.2f})")
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
            rise_ms=round(tf["rise_ms"], 2),
            flatness=round(tf["flatness"], 4),
            hf_ratio=round(tf["hf_ratio"], 3),
            timbre_score=score,
            confirmed=confirmed,
            suspect=not confirmed,
            attack_floor_db=round(attack_floor, 2),
            reason="; ".join(reasons) or "estouro seco, agudo e sem harmônico",
            enabled=True,
        ))
    return events


def phrase_start_before(env: Envelope, t: float, words: list | None = None,
                        pause: float = PHRASE_PAUSE) -> float:
    """Início da frase que estava em andamento quando a palma veio.

    A âncora é a ÚLTIMA PALAVRA dita antes da palma, não a palma. Quem bate
    palma respira antes — e essa respirada é uma pausa longa que a busca
    tomava por fronteira de frase, devolvendo o instante da própria palma e
    descartando um trecho vazio. Ancorando na última palavra, a respirada
    deixa de existir para a busca.
    """
    anchor = t
    if words:
        ends = [float(w["end"] if isinstance(w, dict) else w.end) for w in words
                if float(w["end"] if isinstance(w, dict) else w.end) <= t + 0.02]
        if ends:
            anchor = max(ends) - 0.01
    runs = env.silence_runs(max(0.0, anchor - 120.0), anchor, min_duration=pause)
    if runs:
        return runs[-1].end
    return 0.0


def resume_point_after(env: Envelope, clap_end: float, words: list,
                       pause: float = PHRASE_PAUSE) -> float:
    """Onde a refeitura começa: depois da contagem, na próxima pausa.

    A busca de pausa é LIMITADA pela primeira palavra que não é contagem —
    procurar até 6 s adiante podia achar uma pausa DENTRO da refeitura e
    engolir as primeiras palavras dela no take descartado.
    """
    limit = clap_end + RESUME_MAX_AFTER_CLAP
    cursor = clap_end
    first_word = None       # primeira palavra que já é a refeitura
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
        first_word = start
        break
    search_end = min(limit, env.duration)
    if first_word is not None:
        search_end = min(search_end, first_word)
    runs = env.silence_runs(cursor, search_end, min_duration=pause)
    if runs:
        # a pausa mais próxima da refeitura, não a primeira depois da contagem
        return runs[-1].end
    if first_word is not None:
        return max(cursor, first_word - 0.05)
    return cursor


def build_discarded_takes(env: Envelope, claps: list[ClapEvent],
                          words: list) -> list[DiscardedTake]:
    """Um take descartado por palma ativa, delimitado pela pausa anterior."""
    takes: list[DiscardedTake] = []
    for clap in claps:
        if not clap.enabled:
            continue
        start = phrase_start_before(env, clap.start, words)
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
