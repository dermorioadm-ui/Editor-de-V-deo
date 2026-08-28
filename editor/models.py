"""Plano de edição declarativo.

Regra da Parte 10.1: NENHUMA alteração renderiza nada. O plano é uma estrutura
de dados. Só a exportação encoda, e cada trecho é encodado UMA vez, direto da
fonte original. É isso que impede a segunda e a terceira geração de H.264.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Any

from .config import (AudioParams, CutParams, ExportParams, SpeedParams,
                     SubtitleStyle, ZoomParams)

SECTIONS = {
    "gancho":     {"label": "Gancho / abertura", "speed": (1.00, 1.06), "color": "#38bdf8"},
    "dor":        {"label": "Dor, contexto",     "speed": (1.08, 1.08), "color": "#fb923c"},
    "explicacao": {"label": "Explicação",        "speed": (1.12, 1.18), "color": "#a78bfa"},
    "revelacao":  {"label": "Revelação, clímax", "speed": (1.00, 1.05), "color": "#f472b6"},
    "prova":      {"label": "Prova, números",    "speed": (1.06, 1.12), "color": "#34d399"},
    "oferta":     {"label": "Oferta, preço",     "speed": (1.00, 1.00), "color": "#facc15"},
    "garantia":   {"label": "Garantia, CTA",     "speed": (1.00, 1.00), "color": "#f87171"},
}


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"


def _from_dict(cls, data: Any):
    if data is None:
        return None
    if is_dataclass(cls) and isinstance(data, dict):
        names = {f.name: f for f in fields(cls)}
        kwargs = {}
        for k, v in data.items():
            if k not in names:
                continue
            kwargs[k] = v
        return cls(**kwargs)
    return data


@dataclass
class Clip:
    """Um pedaço contíguo da linha do tempo de SAÍDA."""

    id: str = field(default_factory=lambda: new_id("c_"))
    source: str = "main"            # "main" ou id de uma mídia importada
    src_start: float = 0.0
    src_end: float = 0.0
    speed: float = 1.0
    # velocidade do bloco com o multiplicador global em 1,00x. O speed efetivo
    # é clamp(base × global); sem guardar a base, subir o global até o teto e
    # voltar perdia a velocidade original do bloco (o clamp não tem inversa).
    base_speed: float | None = None
    section: str = "explicacao"
    kind: str = "speech"            # speech | insert | photo
    audio: str = "source"           # source | mute
    enabled: bool = True
    cut_in: bool = True             # a borda de entrada é corte real?
    cut_out: bool = True            # a de saída?
    snap_in: dict | None = None     # SnapResult, para a interface explicar
    snap_out: dict | None = None
    measured_duration: float | None = None   # medida real do render
    zoom: float = 1.0               # jogo de zoom do corte (crop central)
    label: str = ""
    photo: dict | None = None       # {duration, ken_burns, annotations}
    fit: dict | None = None         # {tonemap, brightness, saturation, contrast}

    @property
    def src_duration(self) -> float:
        if self.kind == "photo":
            return float((self.photo or {}).get("duration", 3.0))
        return max(0.0, self.src_end - self.src_start)

    @property
    def out_duration(self) -> float:
        if self.measured_duration is not None:
            return self.measured_duration
        return self.src_duration / max(self.speed, 1e-6)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["src_duration"] = round(self.src_duration, 4)
        d["out_duration"] = round(self.out_duration, 4)
        return d


@dataclass
class RemovedRegion:
    id: str = field(default_factory=lambda: new_id("r_"))
    start: float = 0.0
    end: float = 0.0
    reason: str = "silencio"        # silencio | palma | manual | vicio | texto
    restorable: bool = True
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Cutaway:
    """Substituir: o vídeo entra, o áudio original continua por baixo."""

    id: str = field(default_factory=lambda: new_id("k_"))
    media_id: str = ""
    out_start: float = 0.0
    out_end: float = 0.0
    media_start: float = 0.0
    speed: float = 1.0
    fit: dict = field(default_factory=lambda: {
        "mode": "blur_pad", "tonemap": "auto",
        "tonemap_mode": "transferencia", "npl": 100.0,
        "tonemap_operator": "hable", "desat": 0.0,
        "brightness": 0.0, "saturation": 1.0, "contrast": 1.0,
    })
    enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Overlay:
    """PNG com transparência sobre a linha do tempo de saída (Parte 8)."""

    id: str = field(default_factory=lambda: new_id("o_"))
    media_id: str = ""
    out_start: float = 0.0
    out_end: float = 3.0
    x: float = 0.5                  # 0..1 relativo à largura (centro do PNG)
    y: float = 0.25                 # 0..1 relativo à altura
    scale: float = 1.0
    opacity: float = 1.0
    anim_in: str = "fade"           # fade | slide_left | slide_right | pop | none
    anim_out: str = "fade"
    dur_in: float = 0.35
    dur_out: float = 0.35
    enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BlurRegion:
    """Desfoque retangular — proteção de rosto e documento (Parte 7.3)."""

    id: str = field(default_factory=lambda: new_id("b_"))
    out_start: float = 0.0
    out_end: float = 2.0
    strength: int = 24
    shape: str = "blur"             # blur (gaussiano) | pixel (mosaico)
    keyframes: list = field(default_factory=list)  # [{t,x,y,w,h}] normalizado
    enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Subtitle:
    id: str = field(default_factory=lambda: new_id("s_"))
    start: float = 0.0              # linha do tempo de SAÍDA
    end: float = 0.0
    text: str = ""                  # linhas separadas por "\n"
    word_ids: list = field(default_factory=list)
    edited: bool = False
    # deslocamentos que o usuário aplicou nos botões de tempo. O rebuild
    # recalcula os tempos automáticos e REAPLICA estes deltas — sem eles, o
    # ajuste sumia do burn-in (que sempre rebuilda) enquanto o SRT baixado
    # ainda o mostrava: o usuário revisava um SRT certo e publicava um vídeo
    # errado.
    start_off: float = 0.0
    end_off: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MusicTrack:
    media_id: str = ""
    gain_db: float = -18.0
    ducking: bool = True
    duck_amount: float = 12.0
    fade_in: float = 1.0
    fade_out: float = 2.0
    enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EditPlan:
    project_id: str = ""
    preset: str = "VSL"
    clips: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    discarded_takes: list = field(default_factory=list)
    claps: list = field(default_factory=list)
    cutaways: list = field(default_factory=list)
    overlays: list = field(default_factory=list)
    blurs: list = field(default_factory=list)
    subtitles: list = field(default_factory=list)
    music: dict | None = None
    cut: CutParams = field(default_factory=CutParams)
    speed: SpeedParams = field(default_factory=SpeedParams)
    style: SubtitleStyle = field(default_factory=SubtitleStyle)
    audio: AudioParams = field(default_factory=AudioParams)
    export: ExportParams = field(default_factory=ExportParams)
    zoom: ZoomParams = field(default_factory=ZoomParams)
    look: str = "nenhum"                # filtro de cinema do vídeo inteiro
    look_vignette: float | None = None  # None = a vinheta que o look define
    audit: list = field(default_factory=list)
    audit_fixed: list = field(default_factory=list)   # bordas acertadas sozinho
    repeats: list = field(default_factory=list)       # trechos ditos duas vezes
    version: int = 1

    # ------------------------------------------------------------ serialize
    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "preset": self.preset,
            "clips": [c.to_dict() for c in self.clips],
            "removed": [r.to_dict() for r in self.removed],
            "discarded_takes": [t.to_dict() if hasattr(t, "to_dict") else t
                                for t in self.discarded_takes],
            "claps": [c.to_dict() if hasattr(c, "to_dict") else c for c in self.claps],
            "cutaways": [c.to_dict() for c in self.cutaways],
            "overlays": [o.to_dict() for o in self.overlays],
            "blurs": [b.to_dict() for b in self.blurs],
            "subtitles": [s.to_dict() for s in self.subtitles],
            "music": self.music,
            "cut": asdict(self.cut),
            "speed": asdict(self.speed),
            "style": asdict(self.style),
            "audio": asdict(self.audio),
            "export": asdict(self.export),
            "zoom": asdict(self.zoom),
            "look": self.look,
            "look_vignette": self.look_vignette,
            "audit": self.audit,
            "audit_fixed": self.audit_fixed,
            "repeats": self.repeats,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EditPlan":
        plan = cls(project_id=data.get("project_id", ""),
                   preset=data.get("preset", "VSL"))
        plan.clips = [_from_dict(Clip, c) for c in data.get("clips", [])]
        plan.removed = [_from_dict(RemovedRegion, r) for r in data.get("removed", [])]
        plan.discarded_takes = list(data.get("discarded_takes", []))
        plan.claps = list(data.get("claps", []))
        plan.cutaways = [_from_dict(Cutaway, c) for c in data.get("cutaways", [])]
        plan.overlays = [_from_dict(Overlay, o) for o in data.get("overlays", [])]
        plan.blurs = [_from_dict(BlurRegion, b) for b in data.get("blurs", [])]
        plan.subtitles = [_from_dict(Subtitle, s) for s in data.get("subtitles", [])]
        plan.music = data.get("music")
        plan.cut = _from_dict(CutParams, data.get("cut")) or CutParams()
        plan.speed = _from_dict(SpeedParams, data.get("speed")) or SpeedParams()
        plan.style = _from_dict(SubtitleStyle, data.get("style")) or SubtitleStyle()
        plan.audio = _from_dict(AudioParams, data.get("audio")) or AudioParams()
        plan.export = _from_dict(ExportParams, data.get("export")) or ExportParams()
        zdata = dict(data.get("zoom") or {})
        if "levels" in zdata:
            zdata["levels"] = tuple(float(x) for x in zdata["levels"])
        plan.zoom = ZoomParams(**zdata) if zdata else ZoomParams()
        plan.look = str(data.get("look", "nenhum"))
        lv = data.get("look_vignette")
        plan.look_vignette = None if lv is None else float(lv)
        plan.audit = list(data.get("audit", []))
        plan.audit_fixed = list(data.get("audit_fixed", []))
        plan.repeats = list(data.get("repeats", []))
        plan.version = int(data.get("version", 1))
        return plan

    # ------------------------------------------------------------- consultas
    @property
    def active_clips(self) -> list[Clip]:
        return [c for c in self.clips if c.enabled and c.src_duration > 1e-4]

    @property
    def duration(self) -> float:
        return sum(c.out_duration for c in self.active_clips)
