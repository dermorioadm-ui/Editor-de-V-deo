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

                       assobio          resto (fala, vogal, fricativa, palma)
  concentração no pico 0,464–0,966      0,057–0,103      <- separa sozinho
  energia < 500 Hz     0,000–0,032      0,044–0,890
  frequência do pico   848–3800 Hz      217–4838 Hz
  estabilidade do pico 0,002–0,053      0,000–0,383

A CONCENTRAÇÃO é o critério decisivo: quanto da potência cabe numa faixa de ±4%
em volta da frequência de pico. É a definição direta de "isto é um tom", e a
margem é de 4,5x entre o pior assobio (0,464, já quase só sopro) e o caso
não-assobio mais alto (a fala, 0,103). Palma fica em 0,073–0,081 e as
fricativas /s/, /f/ e /ʃ/ em 0,057–0,077.

Duas correções vieram de medição posterior e valem ser ditas:

1. A energia grave é medida sobre a banda ACIMA DE 150 Hz, não sobre o total.
   Medido sobre o total, o rumble da sala (ar-condicionado, rua) empurra a razão
   de 0,0007 para 0,0224 a 19 dB de SNR e o assobio deixa de ser achado. Medida
   acima de 150 Hz, ela fica em 0,0009 mesmo a 10 dB de SNR — o rumble mora
   abaixo de 150 Hz e sai da conta dos dois lados.

2. A planura espectral NÃO serve aqui: ela mede o piso de ruído da gravação, não
   a tonalidade. O mesmo assobio vai de 0,006 (sem sala) a 0,43 (com sala),
   atravessando de ponta a ponta a faixa da voz. Por isso ela não aparece.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass

import numpy as np

from .envelope import Envelope, _mask_runs

# --- limiares, no meio da distância medida ------------------------------------
MIN_CONCENTRACAO = 0.42  # potência em ±4% do pico (assobio 0,464+, fala 0,103)
MAX_GRAVE = 0.020        # energia < 500 Hz sobre a banda ACIMA de 150 Hz
FREQ_MIN = 800.0         # abaixo disso é voz, não assobio
FREQ_MAX = 4200.0
MAX_DERIVA = 0.05        # variação relativa da frequência entre quadros
MIN_DURACAO = 0.18       # assobio mais curto que isto não é intencional
MAX_DURACAO = 4.0
GRAVE_ATE = 500.0
GRAVE_DESDE = 150.0      # o rumble da sala mora abaixo disto e sai da conta

WIN = 0.046              # janela de análise
HOP = 0.020
# A STFT inteira de uma vez pedia 637 MB para 12 min e 2,1 GB para 40 min, num
# app que já segura um fonte de 500 MB a 2 GB na mesma máquina. Em fatias de
# 60 s o pico fica abaixo de 60 MB e o resultado é bit a bit o mesmo: o passo
# é fixo e as fatias se sobrepõem numa janela inteira.
FATIA = 60.0


@dataclass
class WhistleEvent:
    id: str
    time: float                 # meio do assobio
    start: float
    end: float
    duration: float
    freq: float                 # frequência mediana do assobio
    grave: float                # energia < 500 Hz sobre a banda > 150 Hz
    concentracao: float         # potência em ±4% do pico (0 a 1)
    deriva: float               # o quanto a frequência oscilou
    peak_db: float
    enabled: bool = True        # o usuário pode desligar um assobio
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _quadros_fatia(bloco: np.ndarray, sr: int, nwin: int, nhop: int
                   ) -> tuple[np.ndarray, ...]:
    """Uma fatia: pico, grave, concentração e energia, quadro a quadro."""
    n = 1 + (len(bloco) - nwin) // nhop
    passos = bloco.strides[0]
    # vista deslizante: sem copiar o áudio n vezes
    blocos = np.lib.stride_tricks.as_strided(
        bloco, shape=(n, nwin), strides=(passos * nhop, passos), writeable=False)
    janela = np.hanning(nwin).astype(np.float32)
    energia = np.sqrt((blocos.astype(np.float32) ** 2).mean(axis=1))

    freqs = np.fft.rfftfreq(nwin, 1.0 / sr)
    banda = freqs >= 200.0
    desloc = int((~banda).sum())
    grave_sel = (freqs < GRAVE_ATE) & (freqs >= GRAVE_DESDE)
    base_sel = freqs >= GRAVE_DESDE

    spec = np.abs(np.fft.rfft(blocos * janela, axis=1)).astype(np.float32) + 1e-12
    pot = spec ** 2
    # grave sobre a banda ACIMA de 150 Hz: o rumble da sala sai dos dois lados
    grave = pot[:, grave_sel].sum(axis=1) / np.maximum(pot[:, base_sel].sum(axis=1), 1e-12)

    idx = np.argmax(spec[:, banda], axis=1) + desloc
    pico = freqs[idx]
    # concentração: potência dentro de ±4% do pico sobre o total do quadro.
    # É a medida de "isto é um tom" — e não depende do piso de ruído.
    perto = np.abs(freqs[None, :] - pico[:, None]) <= 0.04 * pico[:, None]
    conc = (pot * perto).sum(axis=1) / np.maximum(pot.sum(axis=1), 1e-12)
    return (pico.astype(np.float32), grave.astype(np.float32),
            conc.astype(np.float32), energia)


def _quadros(samples: np.ndarray, sr: int) -> tuple[np.ndarray, ...]:
    """Pico, grave, concentração e energia — em fatias, para caber na memória.

    A STFT inteira de uma vez pedia 637 MB num vídeo de 12 min. Em fatias de
    ``FATIA`` segundos com uma janela de sobreposição, o resultado é idêntico
    quadro a quadro (o passo é fixo, então as fronteiras caem sempre no mesmo
    lugar) e o pico de memória fica em dezenas de MB.
    """
    nwin = int(WIN * sr)
    nhop = int(HOP * sr)
    vazio = (np.zeros(0, np.float32),) * 4
    if len(samples) < nwin:
        return vazio
    total = 1 + (len(samples) - nwin) // nhop

    # quantos quadros por fatia, e onde cada fatia começa em amostras
    por_fatia = max(1, int(FATIA / HOP))
    partes: list[tuple[np.ndarray, ...]] = []
    q = 0
    while q < total:
        n = min(por_fatia, total - q)
        i0 = q * nhop
        i1 = i0 + (n - 1) * nhop + nwin
        partes.append(_quadros_fatia(
            np.ascontiguousarray(samples[i0:i1]), sr, nwin, nhop))
        q += n
    if not partes:
        return vazio
    return tuple(np.concatenate([p[i] for p in partes]) for i in range(4))


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
    pico, grave, conc, energia = _quadros(x, sample_rate)
    if not pico.size:
        return []

    lo, hi = FREQ_MIN, FREQ_MAX
    if freq_alvo:
        lo = max(FREQ_MIN, float(freq_alvo) * (1.0 - tolerancia))
        hi = min(FREQ_MAX, float(freq_alvo) * (1.0 + tolerancia))

    piso = 10 ** (env.silence_threshold / 20.0)
    candidato = ((conc >= MIN_CONCENTRACAO) & (grave < MAX_GRAVE)
                 & (pico >= lo) & (pico <= hi) & (energia > piso))

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
            concentracao=round(float(np.median(conc[a:b])), 4),
            deriva=round(deriva, 4),
            peak_db=round(pdb, 2),
            reason=(f"tom de {mediana:.0f} Hz por {dur:.2f} s, "
                    f"{np.median(conc[a:b]) * 100:.0f}% da energia no tom"),
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
