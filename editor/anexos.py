"""Trava determinística sobre tudo que entra como anexo no vídeo.

Um anexo — corte-cobertura (cutaway) ou sobreposição (overlay) — pode vir de
três lugares: da mão do usuário, do arrasto na linha do tempo, ou da IA. Os
três passam por aqui, e por aqui as regras inegociáveis são conferidas UMA vez,
em código, em vez de serem confiadas a quem pediu.

O motivo de existir deste arquivo é um caminho específico que quebrava a regra
3 em silêncio:

    Um cutaway cuja mídia é MAIS CURTA que a janela pedida faz o ffmpeg entregar
    um segmento curto. render_video_segments grava a duração medida, export
    soma essa duração menor, build_audio_track pede um alvo menor e
    _resample_exact corta o PCM em samples[:alvo]. O fim da frase simplesmente
    some. O único sintoma era um aviso de texto invertido; nenhum teste pegava.

As demais travas seguem o mesmo princípio: o render resolve todo conflito
calado (cutaway sobre foto é descartado, cutaway sobreposto é truncado, mídia
inexistente vira o take principal de novo), e o usuário perde o anexo sem
saber por quê. Aqui o problema vira erro na hora de pedir, com o motivo escrito.
"""
from __future__ import annotations

from dataclasses import dataclass

MIN_JANELA = 0.20        # anexo mais curto que isto vira sujeira no concat
FOLGA = 0.02             # tolerância de fronteira, igual à do renderer


class AnexoInvalido(ValueError):
    """O pedido não pode ser atendido — com o motivo em português."""


@dataclass
class Janela:
    out_start: float
    out_end: float
    media_start: float
    speed: float
    ajustes: list[str]


def _duracao(midia: dict) -> float | None:
    info = midia.get("info") or {}
    d = info.get("duration")
    try:
        d = float(d)
    except (TypeError, ValueError):
        return None
    return d if d > 0 else None


def validar(midias: list[dict], media_id: str, kind_esperado: str) -> dict:
    """A mídia existe e serve para este trilho?

    ``kind_esperado`` é "video" (cutaway: cobre o quadro inteiro), "image"
    (só imagem) ou "any" (sobreposição: imagem OU vídeo, como janela por
    cima do quadro — o vídeo entra pelo ffmpeg com -ss/-t, sem áudio). Imagem
    usada como cutaway vira um quadro só; áudio não entra em trilho visual.
    Nada disso dava erro — dava resultado errado.
    """
    if not media_id:
        raise AnexoInvalido("faltou dizer qual mídia usar")
    m = next((x for x in midias if x.get("id") == media_id), None)
    if m is None:
        raise AnexoInvalido("essa mídia não está no projeto (foi removida?)")
    kind = m.get("kind")
    if kind not in ("video", "image"):
        raise AnexoInvalido(
            f"'{m.get('name') or media_id}' é {kind or 'de um tipo desconhecido'}: "
            f"só imagem e vídeo entram por cima do quadro.")
    if kind_esperado == "video" and kind == "image":
        raise AnexoInvalido(
            f"'{m.get('name') or media_id}' é uma imagem. Imagem entra como "
            f"sobreposição ou como foto inserida — como cobertura de vídeo ela "
            f"vira um quadro congelado de um frame só.")
    if kind_esperado == "image" and kind == "video":
        raise AnexoInvalido(
            f"'{m.get('name') or media_id}' é um vídeo, e aqui só cabe imagem.")
    if kind == "video" and not (m.get("info") or {}).get("duration"):
        raise AnexoInvalido(
            f"não consegui ler '{m.get('name') or media_id}' (o ffprobe não "
            f"devolveu duração). Sem saber quanto dura, não dá para garantir "
            f"que ele preenche a janela inteira.")
    return m


def encaixar(midia: dict, out_start: float, out_end: float,
             media_start: float = 0.0, speed: float = 1.0,
             limite: float | None = None) -> Janela:
    """Ajusta a janela para o que a mídia REALMENTE cobre.

    É aqui que a regra 3 é defendida. Se a mídia não dá conta da janela, a
    janela encolhe — nunca o contrário. Um segmento mais curto que o pedido
    encurta a trilha de áudio e come o fim da frase.
    """
    ajustes: list[str] = []
    speed = max(0.1, min(4.0, float(speed)))
    a, b = float(out_start), float(out_end)
    if b < a:
        a, b = b, a
        ajustes.append("início e fim estavam trocados")
    if a < 0:
        a = 0.0
        ajustes.append("começava antes do vídeo")
    if limite is not None and limite > 0:
        if a >= limite - MIN_JANELA:
            raise AnexoInvalido(
                f"esse instante ({a:.1f} s) está fora do vídeo, que termina em "
                f"{limite:.1f} s")
        if b > limite:
            b = limite
            ajustes.append("passava do fim do vídeo")

    ms = max(0.0, float(media_start))
    dur_midia = _duracao(midia)
    if dur_midia is not None:
        if ms >= dur_midia - 0.05:
            ms = 0.0
            ajustes.append("o ponto de entrada estava além do fim da mídia")
        # o quanto da MÍDIA a janela consome, já contando a velocidade
        precisa = (b - a) * speed
        sobra = dur_midia - ms
        if precisa > sobra + FOLGA:
            # ENCOLHE a janela. Deixar passar entregava um segmento curto, e o
            # áudio era cortado junto — o fim da frase sumia.
            b = a + sobra / speed
            ajustes.append(
                f"a mídia tem {dur_midia:.1f} s e a janela pedia "
                f"{precisa:.1f} s: encurtei para {b - a:.1f} s em vez de "
                f"deixar o corte comer o fim da frase")

    if b - a < MIN_JANELA:
        raise AnexoInvalido(
            f"sobrou uma janela de {max(0.0, b - a):.2f} s, curta demais para "
            f"virar um pedaço de vídeo (o mínimo é {MIN_JANELA:.2f} s)")
    return Janela(round(a, 4), round(b, 4), round(ms, 4), speed, ajustes)


def sem_sobreposicao(existentes: list, a: float, b: float,
                     ignorar: str = "") -> None:
    """Dois cutaways no mesmo lugar: o segundo era truncado ou sumia.

    O renderer resolve a colisão sozinho — trunca o que passa do cursor e
    descarta o que fica inteiramente dentro de outro — mas em silêncio. O
    usuário via o anexo na trilha e não via no vídeo.
    """
    for c in existentes:
        if getattr(c, "id", None) == ignorar or not getattr(c, "enabled", True):
            continue
        ca, cb = float(c.out_start), float(c.out_end)
        if min(b, cb) - max(a, ca) > FOLGA:
            raise AnexoInvalido(
                f"já existe uma cobertura de {ca:.1f} s a {cb:.1f} s aí. Duas "
                f"no mesmo lugar não cabem: mova uma das duas ou apague a outra.")
