"""Detecção de assobio — o marcador de "acertei".

Protocolo do usuário, complementar ao da palma:

    PALMA   = errei. Joga fora a frase que eu estava falando.
    ASSOBIO = acertei. Fecha aqui — vou respirar e recomeçar quando quiser.

O assobio NÃO apaga fala nenhuma. Ele só valida o take e manda cortar rente:
todo o silêncio depois dele, até a próxima palavra, sai. É isso que dá ao
usuário liberdade para demorar o quanto quiser antes de continuar.

Por isso um falso assobio é barato (perde-se silêncio) enquanto um falso positivo
de palma é caro (perde-se fala). Os limiares aqui refletem essa assimetria.

--- o que separa assobio de tudo o mais ---------------------------------------
Assobio é o OPOSTO da palma: onde a palma é estouro de banda larga, o assobio é
um tom quase puro, sustentado, sem nada de grave. Medido (assobio sintético com
vibrato e sopro, contra fala do espeak-ng, vogal sustentada, sibilante e palma):

                       assobio          resto (fala, vogal, sibilante, palma)
  energia < 500 Hz     0,000–0,001      0,060–0,810      <- separa sozinho
  frequência do pico   848–3000 Hz      283–3685 Hz
  estabilidade do pico 0,000–0,015      0,000–1,199

A energia abaixo de 500 Hz é o critério decisivo: 96x de margem entre o pior
assobio (0,0009) e o caso não-assobio mais próximo (a vogal "aaaa", 0,0864).
Sobrevive a sala barulhenta (22 dB de SNR) e à compressão AAC 64k do WhatsApp —
nas duas condições ficou em 0,0009 ou menos.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass

import numpy as np

from .envelope import Envelope, _mask_runs

# --- limiares, no meio da distância medida ------------------------------------
MAX_GRAVE = 0.020        # energia abaixo de 500 Hz sobre o total
FREQ_MIN = 800.0         # abaixo disso é voz, não assobio
FREQ_MAX = 4200.0
MAX_DERIVA = 0.05        # variação relativa da frequência entre quadros
MIN_DURACAO = 0.18       # assobio mais curto que isto não é intencional
MAX_DURACAO = 4.0
GRAVE_ATE = 500.0

WIN = 0.046              # janela de análise
HOP = 0.020


@dataclass
class WhistleEvent:
    id: str
    time: float                 # meio do assobio
    start: float
    end: float
    duration: float
    freq: float                 # frequência mediana do assobio
    grave: float                # energia abaixo de 500 Hz (0 a 1)
    deriva: float               # o quanto a frequência oscilou
    peak_db: float
    enabled: bool = True        # o usuário pode desligar um assobio
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _quadros(samples: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Frequência do pico, fração de grave e energia, quadro a quadro."""
    nwin = int(WIN * sr)
    nhop = int(HOP * sr)
    if len(samples) < nwin:
        return (np.zeros(0), np.zeros(0), np.zeros(0))
    n = 1 + (len(samples) - nwin) // nhop
    # vista deslizante: sem copiar o áudio inteiro n vezes
    passos = samples.strides[0]
    blocos = np.lib.stride_tricks.as_strided(
        samples, shape=(n, nwin), strides=(passos * nhop, passos), writeable=False)
    janela = np.hanning(nwin).astype(np.float32)
    energia = np.sqrt((blocos.astype(np.float32) ** 2).mean(axis=1))

    freqs = np.fft.rfftfreq(nwin, 1.0 / sr)
    banda = freqs >= 200.0
    grave_sel = freqs < GRAVE_ATE

    # a FFT de tudo de uma vez é o que torna isto viável num vídeo de 12 min
    spec = np.abs(np.fft.rfft(blocos * janela, axis=1)).astype(np.float32) + 1e-12
    pot = spec ** 2
    total = pot.sum(axis=1)
    grave = pot[:, grave_sel].sum(axis=1) / np.maximum(total, 1e-12)
    idx = np.argmax(spec[:, banda], axis=1) + int((~banda).sum())
    pico = freqs[idx]
    return pico.astype(np.float32), grave.astype(np.float32), energia


def detect_whistles(samples: np.ndarray, sample_rate: int, env: Envelope,
                    freq_alvo: float | None = None,
                    tolerancia: float = 0.25) -> list[WhistleEvent]:
    """Acha os assobios.

    ``freq_alvo`` vem da calibração: quando o usuário grava o assobio dele, a
    busca passa a exigir aquela frequência (mais ou menos ``tolerancia``), o que
    derruba a chance de falso positivo quase a zero.
    """
    x = np.asarray(samples, dtype=np.float32)
    if x.size < int(WIN * sample_rate) * 2:
        return []
    pico, grave, energia = _quadros(x, sample_rate)
    if not pico.size:
        return []

    lo, hi = FREQ_MIN, FREQ_MAX
    if freq_alvo:
        lo = max(FREQ_MIN, float(freq_alvo) * (1.0 - tolerancia))
        hi = min(FREQ_MAX, float(freq_alvo) * (1.0 + tolerancia))

    piso = 10 ** (env.silence_threshold / 20.0)
    candidato = (grave < MAX_GRAVE) & (pico >= lo) & (pico <= hi) & (energia > piso)

    eventos: list[WhistleEvent] = []
    for a, b in _mask_runs(candidato):
        t0 = a * HOP
        t1 = b * HOP + WIN
        dur = t1 - t0
        if dur < MIN_DURACAO or dur > MAX_DURACAO:
            continue
        f = pico[a:b]
        if f.size < 2:
            continue
        mediana = float(np.median(f))
        deriva = float(np.median(np.abs(np.diff(f))) / max(mediana, 1.0))
        if deriva > MAX_DERIVA:
            continue
        i0, i1 = env.slice_indices(t0, t1)
        pdb = float(env.db[i0:i1].max()) if i1 > i0 else -90.0
        eventos.append(WhistleEvent(
            id=uuid.uuid4().hex[:10],
            time=round((t0 + t1) / 2.0, 3),
            start=round(t0, 3),
            end=round(t1, 3),
            duration=round(dur, 3),
            freq=round(mediana, 1),
            grave=round(float(np.median(grave[a:b])), 5),
            deriva=round(deriva, 4),
            peak_db=round(pdb, 2),
            reason=(f"tom de {mediana:.0f} Hz por {dur:.2f} s, "
                    f"{np.median(grave[a:b]) * 100:.1f}% de grave"),
        ))
    return eventos


def calibrar(samples: np.ndarray, sample_rate: int, env: Envelope) -> dict:
    """Mede a frequência do assobio DO USUÁRIO a partir de uma gravação curta.

    Ele aperta um botão, assobia duas ou três vezes, e o app guarda a
    frequência. Assobio varia muito de pessoa para pessoa (uns fazem 1 kHz,
    outros 3 kHz); com a dele guardada, a busca fica bem mais precisa.
    """
    achados = detect_whistles(samples, sample_rate, env)
    if not achados:
        return {"ok": False, "detail": "não ouvi assobio nenhum nessa gravação",
                "freq": None, "amostras": 0}
    freqs = sorted(e.freq for e in achados)
    mediana = freqs[len(freqs) // 2]
    espalha = max(abs(f - mediana) / max(mediana, 1.0) for f in freqs)
    return {
        "ok": True,
        "freq": round(mediana, 1),
        "amostras": len(freqs),
        "espalhamento": round(espalha, 3),
        "detail": (f"{len(freqs)} assobio(s), {mediana:.0f} Hz"
                   + (f" (variação de {espalha * 100:.0f}%)" if espalha > 0.05 else "")),
    }
