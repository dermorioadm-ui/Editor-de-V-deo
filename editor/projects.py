"""Serviço de projeto: análise, plano, legendas, exportação e validação."""
from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import db, presets as presets_mod
from .audio.clap import build_discarded_takes, detect_claps
from .audio.envelope import Envelope, compute_envelope
from .config import (PROJECTS_DIR, AudioParams, CutParams, ExportParams,
                     SpeedParams, SubtitleStyle, ensure_dirs)
from .edit.audit import audit_edges, audit_summary
from .edit.plan_builder import build_auto_plan
from .edit.timeline import Timeline
from .ffmpeg_utils import (MediaInfo, extract_wav, hw_encoders, probe,
                           read_wav_mono)
from .models import EditPlan, Subtitle
from .render.export import export_project
from .render.validate import validate_export
from .subtitles.corrections import apply_corrections
from .subtitles.fillers import annotate as annotate_fillers
from .subtitles.linebreak import build_cues
from .subtitles.remap import remap_words

_envelope_cache: dict[str, Envelope] = {}


# ------------------------------------------------------------------ projeto
class Project:
    def __init__(self, row) -> None:
        self.id = row["id"]
        self.name = row["name"]
        self.source_path = row["source_path"]
        self.preset = row["preset"]
        self.status = row["status"]
        self.info = MediaInfo(**{k: v for k, v in db.jloads(row["info_json"]).items()
                                 if k in MediaInfo.__dataclass_fields__}) \
            if row["info_json"] else None
        self.analysis = db.jloads(row["analysis_json"], {})
        self.plan = EditPlan.from_dict(db.jloads(row["plan_json"], {}))
        self.plan.project_id = self.id

    @property
    def dir(self) -> Path:
        d = PROJECTS_DIR / self.id
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def wav(self) -> Path:
        return self.dir / "audio16k.wav"

    @property
    def envelope_file(self) -> Path:
        return self.dir / "envelope.npy"

    @property
    def words(self) -> list[dict]:
        return self.analysis.get("words", [])

    def envelope(self) -> Envelope | None:
        cached = _envelope_cache.get(self.id)
        if cached is not None:
            return cached
        if not self.envelope_file.exists():
            return None
        db_array = np.load(self.envelope_file)
        meta = self.analysis.get("envelope", {})
        env = Envelope(db_array, meta.get("hop", 0.010),
                       meta.get("sample_rate", 16000))
        _envelope_cache[self.id] = env
        return env

    def save_plan(self) -> None:
        db.ex("UPDATE projects SET plan_json=?, updated_at=? WHERE id=?",
              (db.jdumps(self.plan.to_dict()), time.time(), self.id))

    def save_analysis(self) -> None:
        db.ex("UPDATE projects SET analysis_json=?, updated_at=? WHERE id=?",
              (db.jdumps(self.analysis), time.time(), self.id))

    def set_status(self, status: str) -> None:
        self.status = status
        db.ex("UPDATE projects SET status=?, updated_at=? WHERE id=?",
              (status, time.time(), self.id))

    def to_dict(self, full: bool = False) -> dict:
        out = {
            "id": self.id, "name": self.name, "source_path": self.source_path,
            "preset": self.preset, "status": self.status,
            "info": self.info.to_dict() if self.info else None,
            "media": list_media(self.id),
        }
        if full:
            out["analysis"] = self.analysis
            out["plan"] = self.plan.to_dict()
            out["timeline"] = timeline_summary(self)
        return out


def create(source_path: str, name: str = "", preset: str = "VSL") -> Project:
    ensure_dirs()
    src = Path(source_path).expanduser()
    if not src.exists():
        raise FileNotFoundError(f"arquivo não encontrado: {src}")
    info = probe(src)
    pid = uuid.uuid4().hex[:12]
    plan = EditPlan(project_id=pid, preset=preset)
    apply_preset_to_plan(plan, preset)
    now = time.time()
    db.ex(
        "INSERT INTO projects(id, name, source_path, preset, status, info_json, "
        "plan_json, analysis_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (pid, name or src.stem, str(src.resolve()), preset, "novo",
         db.jdumps(info.to_dict()), db.jdumps(plan.to_dict()), "{}", now, now),
    )
    return load(pid)


def load(pid: str) -> Project:
    row = db.q1("SELECT * FROM projects WHERE id=?", (pid,))
    if not row:
        raise KeyError(f"projeto {pid} não encontrado")
    return Project(row)


def list_projects() -> list[dict]:
    rows = db.q("SELECT * FROM projects ORDER BY updated_at DESC")
    out = []
    for r in rows:
        info = db.jloads(r["info_json"], {})
        out.append({
            "id": r["id"], "name": r["name"], "source_path": r["source_path"],
            "preset": r["preset"], "status": r["status"],
            "duration": info.get("duration", 0), "width": info.get("width"),
            "height": info.get("height"), "updated_at": r["updated_at"],
        })
    return out


def delete_project(pid: str) -> None:
    db.ex("DELETE FROM projects WHERE id=?", (pid,))
    db.ex("DELETE FROM media WHERE project_id=?", (pid,))
    _envelope_cache.pop(pid, None)
    shutil.rmtree(PROJECTS_DIR / pid, ignore_errors=True)


def apply_preset_to_plan(plan: EditPlan, preset_name: str) -> None:
    data = presets_mod.get_preset(preset_name)
    if not data:
        return
    plan.preset = preset_name
    plan.cut = CutParams(**data.get("cut", {}))
    plan.speed = SpeedParams(**data.get("speed", {}))
    plan.style = SubtitleStyle(**data.get("style", {}))
    plan.audio = AudioParams(**data.get("audio", {}))
    plan.export = ExportParams(**data.get("export", {}))


# -------------------------------------------------------------------- mídia
def list_media(pid: str) -> list[dict]:
    return [{"id": r["id"], "path": r["path"], "kind": r["kind"],
             "name": r["name"], "info": db.jloads(r["info_json"], {})}
            for r in db.q("SELECT * FROM media WHERE project_id=? ORDER BY created_at",
                          (pid,))]


def add_media(pid: str, path: str, kind: str = "video",
              name: str = "") -> dict:
    src = Path(path).expanduser()
    if not src.exists():
        raise FileNotFoundError(f"arquivo não encontrado: {src}")
    info = {}
    if kind in ("video", "audio"):
        info = probe(src).to_dict()
    elif kind == "image":
        try:
            info = probe(src).to_dict()
        except Exception:  # noqa: BLE001
            info = {}
    mid = uuid.uuid4().hex[:10]
    db.ex("INSERT INTO media(id, project_id, path, kind, name, info_json, created_at) "
          "VALUES (?,?,?,?,?,?,?)",
          (mid, pid, str(src.resolve()), kind, name or src.name,
           db.jdumps(info), time.time()))
    return {"id": mid, "path": str(src.resolve()), "kind": kind,
            "name": name or src.name, "info": info}


def sources_for(project: Project) -> dict:
    sources = {"main": {"path": project.source_path, "info": project.info}}
    for m in list_media(project.id):
        info = None
        if m["info"]:
            info = MediaInfo(**{k: v for k, v in m["info"].items()
                                if k in MediaInfo.__dataclass_fields__})
        sources[m["id"]] = {"path": m["path"], "info": info, "kind": m["kind"]}
    return sources


# ------------------------------------------------------------------ análise
def analyze(project: Project, ctx) -> dict:
    """Fase 1: áudio -> envelope -> transcrição -> palmas -> takes."""
    from .transcribe import detect_device, transcribe

    info = project.info or probe(project.source_path)
    ctx.stage("audio", "extraindo áudio (WAV mono 16 kHz)")
    extract_wav(project.source_path, project.wav, 16000, 1,
                on_progress=lambda f: ctx.progress(0.02 + f * 0.10,
                                                   "extraindo áudio"),
                duration=info.duration)

    ctx.stage("envelope", "calculando o envelope de energia")
    samples, sr = read_wav_mono(project.wav)
    env = compute_envelope(samples, sr)
    np.save(project.envelope_file, env.db)
    _envelope_cache[project.id] = env
    ctx.progress(0.16, f"piso de ruído em {env.noise_floor:.1f} dB")

    ctx.stage("palmas", "procurando palmas")
    claps = detect_claps(samples, sr, env)
    ctx.progress(0.20, f"{len(claps)} candidato(s) a palma")

    ctx.stage("transcricao", "transcrevendo")
    device = detect_device()
    result = transcribe(
        samples, info.duration, silence=env.all_silence_runs(0.5),
        on_progress=lambda f, m: ctx.progress(0.22 + f * 0.68, m),
        device_info=device,
    )
    words = result["words"]

    ctx.stage("takes", "aplicando a regra do take")

    fillers = annotate_fillers(words, env)
    previous = project.analysis or {}
    # decisões do usuário sobrevivem a uma reanálise: palma confirmada ou
    # descartada é casada pelo instante do pico; take recuperado, pelo início
    for clap in claps:
        for old in previous.get("claps", []):
            if abs(float(old.get("time", -1)) - clap.time) < 0.05                     and not old.get("suspect", True) is None:
                if old.get("suspect") is False and clap.suspect:
                    clap.suspect = False
                    clap.confirmed = bool(old.get("confirmed", clap.confirmed))
                    clap.enabled = bool(old.get("enabled", clap.enabled))
                elif old.get("enabled") is False:
                    clap.enabled = False
                    clap.confirmed = False
                    clap.suspect = False
    takes = [t.to_dict() for t in build_discarded_takes(env, claps, words)]
    for take in takes:
        for old in previous.get("takes", []):
            if abs(float(old.get("start", -9)) - take["start"]) < 0.2                     and old.get("restored"):
                take["restored"] = True
    project.analysis = {
        "duration": info.duration,
        "words": words,
        "segments": result.get("segments", []),
        "device": result.get("device", {}),
        "model": result.get("model"),
        "language": result.get("language"),
        "claps": [c.to_dict() for c in claps],
        "takes": takes,
        "fillers": fillers,
        "envelope": {"hop": env.hop, "sample_rate": env.sample_rate,
                     "noise_floor": env.noise_floor,
                     "silence_threshold": env.silence_threshold,
                     "speech_threshold": env.speech_threshold,
                     "audit_threshold": env.audit_threshold,
                     "duration": env.duration},
        "manual_removed_word_ids": previous.get("manual_removed_word_ids", []),
        "analyzed_at": time.time(),
    }
    project.save_analysis()
    project.set_status("analisado")
    ctx.progress(1.0, f"{len(words)} palavras, {len(claps)} palma(s), "
                      f"{len(takes)} take(s) descartado(s)")
    return {
        "words": len(words), "claps": len(claps), "takes": len(takes),
        "fillers": len(fillers), "device": result.get("device", {}),
        "noise_floor": round(env.noise_floor, 2),
    }


def auto_edit(project: Project, ctx) -> dict:
    """Fase 2: proposta de cortes, velocidades e legendas."""
    env = project.envelope()
    if env is None:
        raise RuntimeError("rode a análise antes")
    words = project.words
    takes = project.analysis.get("takes", [])
    # remoções feitas à mão (pelo texto) sobrevivem à reedição automática
    manual_removed = set(project.analysis.get("manual_removed_word_ids", []))
    ctx.stage("cortes", "propondo cortes com encaixe no vale de energia")
    result = build_auto_plan(words, env, project.plan.cut, project.plan.speed,
                             takes, extra_removed=manual_removed)
    plan = project.plan
    from .edit.ops import remap_output_items
    fps = project.info.fps if project.info else None
    old_tl = Timeline(plan.active_clips, fps)
    plan.clips = result["clips"]
    plan.removed = result["removed"]
    plan.discarded_takes = takes
    plan.claps = project.analysis.get("claps", [])
    # NÃO zerar plan.subtitles aqui: os textos editados à mão são casados de
    # volta pelo rebuild (por palavra), e cutaways/overlays/desfoques são
    # reancorados pela fonte — refazer a edição não pode custar trabalho manual
    new_tl = Timeline(plan.active_clips, fps)
    remap_output_items(plan, old_tl, new_tl)
    project.analysis["removed_word_ids"] = result["removed_word_ids"]
    project.analysis["plan_notes"] = result["notes"]

    ctx.progress(0.55, f"{len(plan.clips)} blocos propostos")
    ctx.stage("auditoria", "auditando as bordas de corte")
    issues = audit_edges(plan.clips, env, words, set(result["removed_word_ids"]))
    plan.audit = issues

    ctx.stage("legendas", "gerando legendas")
    cues = rebuild_subtitles(project)
    project.save_analysis()
    project.save_plan()
    project.set_status("editado")
    ctx.progress(1.0, f"{len(plan.clips)} blocos, {len(cues)} legendas, "
                      f"{len(issues)} alerta(s) de borda")
    return {
        "clips": len(plan.clips), "subtitles": len(cues),
        "audit": audit_summary(issues),
        "duration": round(plan.duration, 2),
        "notes": result["notes"],
    }


def one_click(project: Project, ctx) -> dict:
    """O clique único (Parte 1)."""
    ctx.progress(0.0, "iniciando")
    a = _scoped(ctx, 0.0, 0.86, lambda c: analyze(project, c))
    b = _scoped(ctx, 0.86, 1.0, lambda c: auto_edit(project, c))
    return {"analysis": a, "edit": b}


class _ScopedCtx:
    def __init__(self, ctx, lo: float, hi: float) -> None:
        self._ctx, self._lo, self._hi = ctx, lo, hi

    def progress(self, f: float, message: str = "", stage: str = "") -> None:
        self._ctx.progress(self._lo + (self._hi - self._lo) * f, message, stage)

    def stage(self, name: str, message: str = "") -> None:
        self._ctx.stage(name, message)

    def check(self) -> None:
        self._ctx.check()

    def cancelled(self) -> bool:
        return self._ctx.cancelled()


def _scoped(ctx, lo: float, hi: float, fn: Callable) -> Any:
    return fn(_ScopedCtx(ctx, lo, hi))


# ----------------------------------------------------------------- legendas
def corrected_words(project: Project) -> tuple[list[dict], list[dict]]:
    rules = db.list_corrections()
    return apply_corrections(project.words, rules)


def rebuild_subtitles(project: Project, timeline: Timeline | None = None) -> list[dict]:
    """Palavras corrigidas -> remapeadas -> quebradas em legendas."""
    plan = project.plan
    words, log = corrected_words(project)
    removed = set(project.analysis.get("removed_word_ids", []))
    words = [w for w in words if w.get("src_i", w["i"]) not in removed]
    fps = project.info.fps if project.info else None
    tl = timeline or Timeline(plan.active_clips, fps)
    mapped = remap_words(words, tl)
    manual = [s for s in plan.subtitles if s.edited]
    cues = build_cues(mapped, plan.style, limit=tl.duration)
    plan.subtitles = [
        Subtitle(start=c["start"], end=c["end"], text=c["text"],
                 word_ids=[i for i in c["word_ids"] if i is not None])
        for c in cues
    ]
    # o texto editado segue as PALAVRAS, não o relógio: cortes anteriores
    # deslocam todos os tempos, mas os índices das palavras não mudam
    claimed: set[int] = set()
    for sub in plan.subtitles:
        ids = set(sub.word_ids)
        for k, old in enumerate(manual):
            if k in claimed or not ids & set(old.word_ids):
                continue
            sub.text = old.text
            sub.edited = True
            claimed.add(k)
            break
    project.analysis["correction_log"] = log
    return [s.to_dict() for s in plan.subtitles]


def cue_list(project: Project) -> list[dict]:
    return [{"start": s.start, "end": s.end, "text": s.text}
            for s in project.plan.subtitles]


# --------------------------------------------------------------- exportação
def export(project: Project, ctx, options: dict | None = None) -> dict:
    options = options or {}
    plan = project.plan
    if options.get("export_override"):
        # só no plano EM MEMÓRIA: o plan_json do banco nunca vê estes valores
        plan.export = ExportParams(**{**plan.export.__dict__,
                                      **options["export_override"]})
    if not plan.active_clips:
        raise RuntimeError("o plano está vazio — rode a edição automática antes")
    sources = sources_for(project)
    out_dir = project.dir / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    name = options.get("filename") or f"{project.name}_editado.mp4"
    dest = out_dir / Path(name).name
    work = project.dir / "work"
    if options.get("restart"):
        shutil.rmtree(work, ignore_errors=True)
    hw = options.get("hw_encoder") or None
    if hw == "auto":
        hw = (hw_encoders() or [None])[0]

    def cues_builder(tl: Timeline) -> list[dict]:
        rebuild_subtitles(project, tl)
        return cue_list(project)

    ctx.stage("exportando", "encodando cada trecho uma única vez")
    result = export_project(
        plan, sources, dest, work, cues_builder,
        on_progress=lambda f, m: ctx.progress(f * 0.98, m),
        cancel=ctx.cancelled, hw=hw,
    )
    # As durações medidas valeram só para esta exportação (mantê-las
    # invalidaria o cache), e o plano NÃO é regravado aqui: este é o snapshot
    # do início do job — regravá-lo apagaria qualquer edição que o usuário
    # salvou enquanto a renderização corria.
    for clip in plan.clips:
        clip.measured_duration = None
    project.set_status("exportado")
    ctx.progress(1.0, f"pronto: {dest.name}")
    payload = result.to_dict()
    payload["download"] = f"/api/projects/{project.id}/download/{dest.name}"
    return payload


def validate(project: Project, ctx, output: str | None = None) -> dict:
    exports = sorted((project.dir / "exports").glob("*.mp4"),
                     key=lambda p: -p.stat().st_mtime)
    target = Path(output) if output else (exports[0] if exports else None)
    if target is None or not target.exists():
        raise RuntimeError("nenhum arquivo exportado para validar")

    words, _ = corrected_words(project)
    removed = set(project.analysis.get("removed_word_ids", []))
    tl = Timeline(project.plan.active_clips,
                  project.info.fps if project.info else None)
    expected = [w["text"] for w in words
                if w.get("src_i", w["i"]) not in removed
                and (tl.covers(float(w["start"])) or tl.covers(float(w["end"])))]

    def transcriber(path: Path) -> list[dict]:
        from .transcribe import transcribe
        from .ffmpeg_utils import extract_wav as ex_wav, read_wav_mono as rd

        tmp = project.dir / "validate.wav"
        ex_wav(path, tmp, 16000, 1)
        samples, _sr = rd(tmp)
        info = probe(path)
        res = transcribe(samples, info.duration,
                         on_progress=lambda f, m: ctx.progress(0.35 + f * 0.45, m))
        tmp.unlink(missing_ok=True)
        return res["words"]

    can_transcribe = True
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        can_transcribe = False

    expected_duration = tl.duration
    last = db.q1("SELECT result_json FROM jobs WHERE project_id=? AND kind='exportacao' "
                 "AND status='ok' ORDER BY updated_at DESC LIMIT 1", (project.id,))
    if last:
        payload = db.jloads(last["result_json"], {})
        if payload.get("output") == str(target) and payload.get("duration"):
            expected_duration = float(payload["duration"])

    report = validate_export(
        target, expected, cue_list(project), project.plan.audio,
        (project.info.v_bitrate or project.info.bitrate) if project.info else 0,
        expected_duration,
        transcriber=transcriber if can_transcribe else None,
        on_progress=lambda f, m: ctx.progress(f, m),
        work=project.dir,
    )
    report["file"] = str(target)
    return report


# -------------------------------------------------------------------- views
def timeline_summary(project: Project) -> dict:
    plan = project.plan
    tl = Timeline(plan.active_clips, project.info.fps if project.info else None)
    blocks = []
    for placed in tl:
        c = placed.clip
        blocks.append({
            **c.to_dict(),
            "out_start": round(placed.out_start, 4),
            "out_end": round(placed.out_end, 4),
        })
    return {
        "duration": round(tl.duration, 3),
        "source_duration": round(project.analysis.get("duration", 0.0), 3),
        "blocks": blocks,
        "removed": [r.to_dict() for r in plan.removed],
        "takes": plan.discarded_takes,
        "claps": plan.claps,
        "subtitles": [s.to_dict() for s in plan.subtitles],
        "audit": plan.audit,
        "cutaways": [c.to_dict() for c in plan.cutaways],
        "overlays": [o.to_dict() for o in plan.overlays],
        "blurs": [b.to_dict() for b in plan.blurs],
        "speed_warn": [b["id"] for b in blocks
                       if b["speed"] > plan.speed.warn_above],
    }
