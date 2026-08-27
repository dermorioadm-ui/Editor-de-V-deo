"""Jogo de zoom nos cortes (o corte "seco" de VSL).

Um corte de silêncio sem troca de enquadramento aparece como um salto na
imagem — parece defeito de arquivo. O mesmo corte com o enquadramento
mudando vira linguagem: é assim que VSL é montada, e é o que faltava para o
vídeo sair pronto em vez de sair cru.

O zoom é sempre um CROP central seguido de scale de volta ao tamanho do
vídeo, aplicado no mesmo encode do bloco. Não custa geração nenhuma, não muda
a resolução de saída e não toca no áudio.
"""
from __future__ import annotations

from ..config import ZoomParams
from ..models import Clip

CONTIGUOUS_EPS = 0.002


def assign_zoom(clips: list[Clip], params: ZoomParams) -> int:
    """Distribui os níveis de zoom pelos blocos. Devolve quantos ficaram != 1.

    A troca acontece no CORTE, nunca no meio de um trecho contínuo: dois
    blocos que só existem porque a velocidade muda continuam com o mesmo
    enquadramento, senão a imagem pula sem que nada tenha sido cortado.
    """
    main = [c for c in clips if c.enabled and c.source == "main"]
    if not params.enabled or not main:
        for c in clips:
            c.zoom = 1.0
        return 0

    levels = [min(float(z), params.max_level) for z in (params.levels or (1.0,))]
    if not levels:
        levels = [1.0]

    step = 0
    current = levels[0]
    for i, clip in enumerate(main):
        prev = main[i - 1] if i else None
        contiguo = bool(prev and abs(prev.src_end - clip.src_start) < CONTIGUOUS_EPS)
        curto = clip.src_duration < params.min_block
        if i == 0:
            current = levels[0]
        elif contiguo or curto:
            # nada foi cortado aqui (ou o bloco é curto demais para respirar):
            # mantém o enquadramento do vizinho
            current = prev.zoom if prev else current
        else:
            step += 1
            current = levels[step % len(levels)]
        clip.zoom = round(float(current), 4)

    if params.hook_punch and len(main) > 1:
        # a virada do gancho para o corpo é o ponto onde o espectador decide
        # ficar: entrar fechado ali segura mais do que qualquer legenda
        for i in range(1, len(main)):
            if main[i].section != main[0].section:
                if main[i].src_duration >= params.min_block:
                    main[i].zoom = round(min(max(levels), params.max_level), 4)
                break

    for c in clips:
        if c.source != "main" or not c.enabled:
            c.zoom = 1.0
    return sum(1 for c in main if abs(c.zoom - 1.0) > 1e-3)


def zoom_chain(zoom: float, width: int, height: int, bias_y: float) -> str:
    """crop central -> scale de volta. Vazio quando não há zoom."""
    if zoom is None or abs(float(zoom) - 1.0) <= 1e-3:
        return ""
    z = max(1.0, float(zoom))
    # dimensões pares: encoder yuv420p recusa ímpar
    cw = f"trunc(iw/{z:.6f}/2)*2"
    ch = f"trunc(ih/{z:.6f}/2)*2"
    x = f"trunc((iw-{cw})/2/2)*2"
    y = f"trunc((ih-{ch})*{max(0.0, min(1.0, bias_y)):.4f}/2)*2"
    return f"crop={cw}:{ch}:{x}:{y},scale={width}:{height}:flags=lanczos"
