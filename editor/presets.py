"""Presets (Parte 11)."""
from __future__ import annotations

import json
import time
from dataclasses import asdict

from .config import (AudioParams, CutParams, ExportParams, Preset, SpeedParams,
                     SubtitleStyle)


def _preset(name: str, description: str, cut: CutParams, speed: SpeedParams,
            style: SubtitleStyle, audio: AudioParams | None = None,
            export: ExportParams | None = None) -> dict:
    return {
        "name": name, "description": description, "builtin": True,
        "cut": asdict(cut), "speed": asdict(speed), "style": asdict(style),
        "audio": asdict(audio or AudioParams()),
        "export": asdict(export or ExportParams()),
    }


BUILTIN = [
    _preset(
        "VSL", "2 a 3 min. Corte conservador, velocidade contida, legenda padrão.",
        CutParams(silence_min=0.70, air=0.25, margin=0.15, min_block=1.0),
        SpeedParams(ceiling=1.12, max_speed=1.25, warn_above=1.25),
        SubtitleStyle(fontsize=64, max_chars_per_line=24, margin_v=220),
    ),
    _preset(
        "Criativo 60s", "Corte agressivo, até 1,18x, legenda maior.",
        CutParams(silence_min=0.50, air=0.15, margin=0.15, min_block=0.9),
        SpeedParams(ceiling=1.18, max_speed=1.30, warn_above=1.25),
        SubtitleStyle(fontsize=82, max_chars_per_line=20, margin_v=300,
                      outline=5.0),
    ),
    _preset(
        "Story", "30 s, corte máximo, até 1,25x.",
        CutParams(silence_min=0.35, air=0.10, margin=0.12, min_block=0.7,
                  narrative_pause=0.60),
        SpeedParams(ceiling=1.25, max_speed=1.40, warn_above=1.25),
        SubtitleStyle(fontsize=92, max_chars_per_line=18, margin_v=420,
                      outline=6.0, uppercase=True),
    ),
]


def to_objects(data: dict) -> Preset:
    return Preset(
        name=data["name"], description=data.get("description", ""),
        cut=CutParams(**data.get("cut", {})),
        speed=SpeedParams(**data.get("speed", {})),
        style=SubtitleStyle(**data.get("style", {})),
        audio=AudioParams(**data.get("audio", {})),
        export=ExportParams(**data.get("export", {})),
        builtin=bool(data.get("builtin")),
    )


def list_presets() -> list[dict]:
    from . import db

    return [db.jloads(r["data_json"]) for r in
            db.q("SELECT data_json FROM presets ORDER BY builtin DESC, name")]


def get_preset(name: str) -> dict | None:
    from . import db

    row = db.q1("SELECT data_json FROM presets WHERE name=?", (name,))
    return db.jloads(row["data_json"]) if row else None


def save_preset(data: dict) -> dict:
    from . import db

    data = dict(data)
    data.setdefault("builtin", False)
    db.ex("INSERT INTO presets(name, data_json, builtin, updated_at) VALUES (?,?,?,?) "
          "ON CONFLICT(name) DO UPDATE SET data_json=excluded.data_json, "
          "updated_at=excluded.updated_at",
          (data["name"], json.dumps(data, ensure_ascii=False),
           1 if data.get("builtin") else 0, time.time()))
    return data


def delete_preset(name: str) -> bool:
    from . import db

    row = db.q1("SELECT builtin FROM presets WHERE name=?", (name,))
    if not row or row["builtin"]:
        return False
    db.ex("DELETE FROM presets WHERE name=?", (name,))
    return True
