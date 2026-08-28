"""Filtros de cinema (o "look" do vídeo).

Tudo aqui é cadeia de filtro do ffmpeg aplicada no MESMO encode dos blocos:
não custa geração nenhuma, não muda resolução e não toca no áudio.

O look vale para o vídeo inteiro. Ele entra DEPOIS do tonemap e do
enquadramento, e ANTES da legenda — legenda queimada não pode ficar sépia
junto com a imagem, senão o contorno preto some.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Look:
    id: str
    label: str
    description: str
    chain: str
    vignette: float = 0.0       # 0 = sem vinheta; 1 = forte


def _curve(shadows: str, mids: str, highs: str) -> str:
    return f"curves=r='{shadows}':g='{mids}':b='{highs}'"


LOOKS: list[Look] = [
    Look("nenhum", "Nenhum", "A imagem como saiu da câmera.", ""),

    Look("pb", "Preto e branco",
         "Sem cor, com contraste um pouco levantado. O clássico de depoimento.",
         "hue=s=0,eq=contrast=1.14:brightness=0.01"),

    Look("pb_duro", "Preto e branco duro",
         "Preto e branco de alto contraste, quase gráfico. Bom para hook.",
         "hue=s=0,eq=contrast=1.42:brightness=-0.02,unsharp=5:5:0.6"),

    Look("quente", "Ambiente quente",
         "Pele mais viva e sombra puxada para o âmbar. Sala de casa, fim de tarde.",
         "eq=saturation=1.10:contrast=1.06,"
         "colorbalance=rs=0.06:gs=0.01:bs=-0.06:rm=0.04:bm=-0.03:rh=0.02:bh=-0.04",
         vignette=0.35),

    Look("frio", "Ambiente frio",
         "Azulado e sóbrio. Escritório, madrugada, tom sério.",
         "eq=saturation=0.94:contrast=1.08,"
         "colorbalance=rs=-0.05:bs=0.08:rm=-0.03:bm=0.05:bh=0.04",
         vignette=0.30),

    Look("teal_orange", "Cinema (teal & orange)",
         "Pele quente contra fundo esverdeado. O look de trailer.",
         "eq=saturation=1.05:contrast=1.12,"
         "colorbalance=rs=-0.06:gs=0.03:bs=0.05:rm=0.07:gm=0.01:bm=-0.05:"
         "rh=0.05:bh=-0.03",
         vignette=0.40),

    Look("vintage", "Vintage",
         "Levemente lavado, com sépia no preto. Parece arquivo antigo.",
         "eq=saturation=0.72:contrast=0.94:brightness=0.03,"
         "colorbalance=rs=0.10:gs=0.04:bs=-0.08:rm=0.05:bm=-0.05,"
         "noise=alls=6:allf=t+u",
         vignette=0.55),

    Look("nitido", "Nítido",
         "Só levanta contraste e definição. Ajuda vídeo de celular chapado.",
         "eq=contrast=1.10:saturation=1.06,unsharp=5:5:0.8:5:5:0.0"),
]

BY_ID = {look.id: look for look in LOOKS}


def vignette_chain(strength: float) -> str:
    """Escurece os cantos. Segura o olho no rosto, que é o ponto do vídeo."""
    if strength <= 0.001:
        return ""
    # ângulo menor = vinheta mais fechada; PI/5 já é bem marcada
    import math

    angle = math.pi / (5.0 + (1.0 - min(1.0, strength)) * 4.0)
    return f"vignette=angle={angle:.4f}:mode=forward"


def look_chain(look_id: str | None, vignette: float | None = None) -> str:
    """Cadeia completa do look. Vazia quando não há look nenhum."""
    look = BY_ID.get(look_id or "nenhum")
    if look is None or (not look.chain and not (vignette or look.vignette)):
        return ""
    partes = [look.chain] if look.chain else []
    v = look.vignette if vignette is None else float(vignette)
    vg = vignette_chain(v)
    if vg:
        partes.append(vg)
    return ",".join(partes)


def catalog() -> list[dict]:
    return [{"id": k.id, "label": k.label, "description": k.description,
             "vignette": k.vignette} for k in LOOKS]
