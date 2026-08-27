"""FastAPI: API local + arquivos estáticos do frontend.

Nada sobe para lugar nenhum. O servidor só escuta em 127.0.0.1 e lê os
arquivos direto do disco do usuário, por caminho.
"""
from __future__ import annotations

import asyncio
import mimetypes
import platform
import sys
from pathlib import Path

from fastapi import (Body, FastAPI, HTTPException, Query, Request, WebSocket,
                     WebSocketDisconnect)
from fastapi.responses import (FileResponse, HTMLResponse, PlainTextResponse,
                               Response, StreamingResponse)
from fastapi.staticfiles import StaticFiles

from . import db, presets as presets_mod, projects as svc, video_analysis
from .config import (STATIC_DIR, AudioParams, CutParams, ExportParams,
                     SpeedParams, SubtitleStyle, ensure_dirs, ffmpeg_available,
                     WHISPER_MODEL)
from .edit import ops
from .edit.audit import apply_fix, audit_edges
from .ffmpeg_utils import hw_encoders
from .jobs import get_queue, hub
from .models import BlurRegion, Clip, Cutaway, Overlay
from .subtitles import ass as ass_mod

app = FastAPI(title="Editor de Vídeo", docs_url="/api/docs", redoc_url=None)
CHUNK = 1024 * 512


@app.on_event("startup")
async def _startup() -> None:
    ensure_dirs()
    db.connect()
    hub.bind(asyncio.get_running_loop())
    get_queue()


# ------------------------------------------------------------------ helpers
def _project(pid: str) -> svc.Project:
    try:
        return svc.load(pid)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


def _env_or_404(project: svc.Project):
    env = project.envelope()
    if env is None:
        raise HTTPException(400, "rode a análise antes desta operação")
    return env


def _range_response(path: Path, request: Request) -> Response:
    """Serve arquivo grande com suporte a Range — o player precisa disso."""
    if not path.exists():
        raise HTTPException(404, f"arquivo não encontrado: {path}")
    size = path.stat().st_size
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    range_header = request.headers.get("range")
    if not range_header:
        return FileResponse(path, media_type=mime,
                            headers={"Accept-Ranges": "bytes"})
    try:
        units, _, rng = range_header.partition("=")
        start_s, _, end_s = rng.partition("-")
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else size - 1
    except ValueError:
        raise HTTPException(416, "range inválido")
    start = max(0, min(start, size - 1))
    end = max(start, min(end, size - 1))
    length = end - start + 1

    def iterator():
        with open(path, "rb") as fh:
            fh.seek(start)
            left = length
            while left > 0:
                chunk = fh.read(min(CHUNK, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    return StreamingResponse(iterator(), status_code=206, media_type=mime,
                             headers={
                                 "Content-Range": f"bytes {start}-{end}/{size}",
                                 "Accept-Ranges": "bytes",
                                 "Content-Length": str(length),
                             })


# -------------------------------------------------------------------- saúde
@app.get("/api/health")
def health() -> dict:
    ok, detail = ffmpeg_available()
    try:
        from .transcribe import detect_device
        device = detect_device().to_dict()
    except Exception as exc:  # noqa: BLE001
        device = {"device": "?", "detail": str(exc)}
    try:
        import faster_whisper  # noqa: F401
        whisper_ok = True
    except ImportError:
        whisper_ok = False
    return {
        "ffmpeg": {"ok": ok, "detail": detail},
        "faster_whisper": whisper_ok,
        "whisper_model": WHISPER_MODEL,
        "device": device,
        "hw_encoders": hw_encoders(),
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()}",
        "data_dir": str(ensure_dirs() or ""),
    }


@app.get("/api/browse")
def browse(path: str = Query("")) -> dict:
    """Navegador de arquivos local — nada de upload."""
    target = Path(path).expanduser() if path else Path.home()
    if not target.exists():
        raise HTTPException(404, f"não existe: {target}")
    if target.is_file():
        return {"file": str(target.resolve())}
    entries = []
    try:
        for item in sorted(target.iterdir(),
                           key=lambda p: (not p.is_dir(), p.name.lower())):
            if item.name.startswith("."):
                continue
            try:
                is_dir = item.is_dir()
                size = 0 if is_dir else item.stat().st_size
            except OSError:
                continue
            entries.append({"name": item.name, "path": str(item.resolve()),
                            "dir": is_dir, "size": size})
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"path": str(target.resolve()),
            "parent": str(target.parent.resolve()) if target.parent != target else None,
            "entries": entries[:2000]}


SEARCH_DIRS = ("Videos", "Vídeos", "Movies", "Desktop", "Área de Trabalho",
               "Downloads", "Documents", "Documentos", "OneDrive")


@app.post("/api/locate")
def locate(payload: dict = Body(...)) -> dict:
    """Acha o caminho real de um arquivo arrastado para a página.

    O navegador entrega só o nome e o tamanho — nunca o caminho. Em vez de
    subir 2 GB por HTTP, procuramos o arquivo nas pastas óbvias e usamos o
    caminho de verdade. O arquivo não sai do lugar.
    """
    name = str(payload.get("name", "")).strip()
    size = int(payload.get("size", 0) or 0)
    if not name:
        raise HTTPException(400, "informe o nome do arquivo")
    home = Path.home()
    roots = [home, *(home / d for d in SEARCH_DIRS)]
    extra = payload.get("hints") or []
    roots += [Path(h).expanduser() for h in extra if h]
    seen: set[str] = set()
    matches: list[dict] = []
    for root in roots:
        if not root.exists() or str(root) in seen:
            continue
        seen.add(str(root))
        try:
            for depth, pattern in ((0, name), (1, f"*/{name}"), (2, f"*/*/{name}")):
                for hit in root.glob(pattern):
                    if not hit.is_file():
                        continue
                    stat = hit.stat()
                    matches.append({
                        "path": str(hit.resolve()), "size": stat.st_size,
                        "exact": size == 0 or stat.st_size == size,
                        "depth": depth,
                    })
        except (PermissionError, OSError):
            continue
    matches.sort(key=lambda m: (not m["exact"], m["depth"]))
    exact = [m for m in matches if m["exact"]]
    return {"found": bool(exact), "matches": matches[:12],
            "path": exact[0]["path"] if exact else None,
            "searched": [str(r) for r in seen]}


# ----------------------------------------------------------------- projetos
@app.get("/api/projects")
def api_projects() -> list[dict]:
    return svc.list_projects()


@app.post("/api/projects")
def api_create(payload: dict = Body(...)) -> dict:
    path = payload.get("source_path", "").strip()
    if not path:
        raise HTTPException(400, "informe o caminho do vídeo")
    try:
        project = svc.create(path, payload.get("name", ""),
                             payload.get("preset", "VSL"))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return project.to_dict(full=True)


@app.get("/api/projects/{pid}")
def api_project(pid: str) -> dict:
    return _project(pid).to_dict(full=True)


@app.delete("/api/projects/{pid}")
def api_delete(pid: str) -> dict:
    svc.delete_project(pid)
    return {"ok": True}


@app.get("/api/projects/{pid}/source")
def api_source(pid: str, request: Request):
    return _range_response(Path(_project(pid).source_path), request)


@app.get("/api/projects/{pid}/media/{mid}/file")
def api_media_file(pid: str, mid: str, request: Request):
    for m in svc.list_media(pid):
        if m["id"] == mid:
            return _range_response(Path(m["path"]), request)
    raise HTTPException(404, "mídia não encontrada")


@app.get("/api/projects/{pid}/download/{name}")
def api_download(pid: str, name: str, request: Request):
    project = _project(pid)
    path = project.dir / "exports" / Path(name).name
    return _range_response(path, request)


@app.get("/api/projects/{pid}/envelope")
def api_envelope(pid: str, points: int = 4000) -> dict:
    project = _project(pid)
    env = _env_or_404(project)
    return env.to_dict(points)


# --------------------------------------------------------------------- jobs
def _run(kind: str, pid: str, fn) -> dict:
    job = get_queue().submit(kind, pid, fn)
    return job.to_dict()


@app.post("/api/projects/{pid}/analyze")
def api_analyze(pid: str) -> dict:
    project = _project(pid)
    return _run("analise", pid, lambda ctx: svc.analyze(project, ctx))


@app.post("/api/projects/{pid}/autoedit")
def api_autoedit(pid: str) -> dict:
    project = _project(pid)
    return _run("edicao", pid, lambda ctx: svc.auto_edit(project, ctx))


@app.post("/api/projects/{pid}/oneclick")
def api_oneclick(pid: str, payload: dict = Body(default={})) -> dict:
    project = _project(pid)
    if payload.get("preset"):
        svc.apply_preset_to_plan(project.plan, payload["preset"])
        db.ex("UPDATE projects SET preset=? WHERE id=?", (payload["preset"], pid))
        project.save_plan()
    return _run("clique-unico", pid, lambda ctx: svc.one_click(project, ctx))


@app.post("/api/projects/{pid}/export")
def api_export(pid: str, payload: dict = Body(default={})) -> dict:
    project = _project(pid)
    return _run("exportacao", pid, lambda ctx: svc.export(project, ctx, payload))


@app.post("/api/projects/{pid}/validate")
def api_validate(pid: str, payload: dict = Body(default={})) -> dict:
    project = _project(pid)
    return _run("validacao", pid,
                lambda ctx: svc.validate(project, ctx, payload.get("output")))


@app.get("/api/jobs")
def api_jobs(project_id: str | None = None) -> list[dict]:
    return [j.to_dict() for j in get_queue().list(project_id)]


@app.post("/api/jobs/{job_id}/cancel")
def api_cancel(job_id: str) -> dict:
    return {"ok": get_queue().cancel(job_id)}


@app.websocket("/ws")
async def ws(socket: WebSocket) -> None:
    await socket.accept()
    await hub.register(socket)
    try:
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        await hub.unregister(socket)


# --------------------------------------------------------------- parâmetros
@app.post("/api/projects/{pid}/params")
def api_params(pid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    plan = project.plan
    mapping = {"cut": (CutParams, "cut"), "speed": (SpeedParams, "speed"),
               "style": (SubtitleStyle, "style"), "audio": (AudioParams, "audio"),
               "export": (ExportParams, "export")}
    for key, (cls, attr) in mapping.items():
        if key in payload and isinstance(payload[key], dict):
            current = getattr(plan, attr).__dict__.copy()
            current.update({k: v for k, v in payload[key].items() if k in current})
            setattr(plan, attr, cls(**current))
    if payload.get("rebuild_subtitles"):
        svc.rebuild_subtitles(project)
    project.save_plan()
    return {"plan": plan.to_dict(), "timeline": svc.timeline_summary(project)}


@app.post("/api/projects/{pid}/preset")
def api_apply_preset(pid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    name = payload.get("name", "")
    if not presets_mod.get_preset(name):
        raise HTTPException(404, "preset não encontrado")
    svc.apply_preset_to_plan(project.plan, name)
    db.ex("UPDATE projects SET preset=? WHERE id=?", (name, pid))
    project.save_plan()
    return {"ok": True, "plan": project.plan.to_dict()}


@app.get("/api/presets")
def api_presets() -> list[dict]:
    return presets_mod.list_presets()


@app.post("/api/presets")
def api_save_preset(payload: dict = Body(...)) -> dict:
    if not payload.get("name"):
        raise HTTPException(400, "o preset precisa de nome")
    payload["builtin"] = False
    return presets_mod.save_preset(payload)


@app.delete("/api/presets/{name}")
def api_delete_preset(name: str) -> dict:
    return {"ok": presets_mod.delete_preset(name)}


# --------------------------------------------------------------- edição
@app.post("/api/projects/{pid}/plan")
def api_replace_plan(pid: str, payload: dict = Body(...)) -> dict:
    """Substitui o plano inteiro — é assim que desfazer/refazer persiste."""
    from .models import EditPlan

    project = _project(pid)
    project.plan = EditPlan.from_dict(payload.get("plan", {}))
    project.plan.project_id = pid
    project.save_plan()
    return {"ok": True, "timeline": svc.timeline_summary(project)}


def _after_edit(project: svc.Project, rebuild: bool = True) -> dict:
    env = project.envelope()
    if env is not None:
        removed = set(project.analysis.get("removed_word_ids", []))
        project.plan.audit = audit_edges(project.plan.clips, env,
                                         project.words, removed)
    if rebuild:
        svc.rebuild_subtitles(project)
    project.save_plan()
    return svc.timeline_summary(project)


@app.post("/api/projects/{pid}/ops/delete-range")
def api_delete_range(pid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    env = _env_or_404(project)
    res = ops.delete_output_range(project.plan, env, float(payload["start"]),
                                  float(payload["end"]), project.plan.cut,
                                  project.words)
    if not res.get("ok"):
        raise HTTPException(400, res.get("reason", "não foi possível deletar"))
    return {**res, "timeline": _after_edit(project)}


@app.post("/api/projects/{pid}/ops/remove-words")
def api_remove_words(pid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    env = _env_or_404(project)
    res = ops.remove_words(project.plan, env, project.words,
                           payload.get("word_ids", []), project.plan.cut)
    removed = set(project.analysis.get("removed_word_ids", []))
    for group in res.get("applied", []):
        if group.get("ok"):
            removed.update(group["words"])
    project.analysis["removed_word_ids"] = sorted(removed)
    project.save_analysis()
    return {**res, "timeline": _after_edit(project)}


@app.post("/api/projects/{pid}/ops/restore-words")
def api_restore_words(pid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    ids = [int(i) for i in payload.get("word_ids", [])]
    words = project.words
    if not ids:
        raise HTTPException(400, "nenhuma palavra informada")
    start = min(words[i]["start"] for i in ids) - 0.12
    end = max(words[i]["end"] for i in ids) + 0.12
    res = ops.restore_range(project.plan, max(0.0, start), end)
    removed = set(project.analysis.get("removed_word_ids", [])) - set(ids)
    project.analysis["removed_word_ids"] = sorted(removed)
    project.save_analysis()
    return {**res, "timeline": _after_edit(project)}


@app.post("/api/projects/{pid}/ops/restore-range")
def api_restore_range(pid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    res = ops.restore_range(project.plan, float(payload["start"]),
                            float(payload["end"]),
                            payload.get("source", "main"))
    if not res.get("ok"):
        raise HTTPException(400, res.get("reason", "não foi possível recuperar"))
    return {**res, "timeline": _after_edit(project)}


@app.post("/api/projects/{pid}/ops/split")
def api_split(pid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    res = ops.split_clip(project.plan, payload["clip_id"], float(payload["time"]))
    if not res.get("ok"):
        raise HTTPException(400, res.get("reason", "não foi possível dividir"))
    return {**res, "timeline": _after_edit(project)}


@app.post("/api/projects/{pid}/ops/merge")
def api_merge(pid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    res = ops.merge_clips(project.plan, payload.get("clip_ids", []))
    if not res.get("ok"):
        raise HTTPException(400, res.get("reason", "não foi possível fundir"))
    return {**res, "timeline": _after_edit(project)}


@app.post("/api/projects/{pid}/ops/speed")
def api_speed(pid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    env = project.envelope()
    if payload.get("global") is not None:
        project.plan.speed.global_multiplier = float(payload["global"])
        base = float(payload["global"])
        for clip in project.plan.clips:
            clip.speed = round(max(project.plan.speed.min_speed,
                                   min(project.plan.speed.max_speed,
                                       clip.speed * base / max(1e-6, payload.get("previous", 1.0)))), 2)
            clip.measured_duration = None
        return {"ok": True, "timeline": _after_edit(project)}
    res = ops.set_speed(project.plan, payload["clip_id"], float(payload["speed"]), env)
    if not res.get("ok"):
        raise HTTPException(400, res.get("reason", "bloco não encontrado"))
    warn = float(payload["speed"]) > project.plan.speed.warn_above
    return {**res, "warn": warn,
            "warn_message": (f"acima de {project.plan.speed.warn_above:.2f}x a fala "
                             f"soa artificial e derruba retenção") if warn else "",
            "timeline": _after_edit(project)}


@app.post("/api/projects/{pid}/ops/section")
def api_section(pid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    for clip in project.plan.clips:
        if clip.id == payload.get("clip_id"):
            clip.section = payload.get("section", clip.section)
            project.save_plan()
            return {"ok": True, "timeline": svc.timeline_summary(project)}
    raise HTTPException(404, "bloco não encontrado")


@app.post("/api/projects/{pid}/ops/audit-fix")
def api_audit_fix(pid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    issues = project.plan.audit
    index = int(payload.get("index", -1))
    if not (0 <= index < len(issues)):
        raise HTTPException(400, "alerta inválido")
    if not apply_fix(project.plan.clips, issues[index]):
        raise HTTPException(400, "não foi possível aplicar a correção")
    return {"ok": True, "timeline": _after_edit(project)}


@app.post("/api/projects/{pid}/ops/take")
def api_take(pid: str, payload: dict = Body(...)) -> dict:
    """Recupera ou volta a descartar um take (Parte 2.4)."""
    project = _project(pid)
    takes = project.analysis.get("takes", [])
    tid = payload.get("take_id")
    restore = bool(payload.get("restored"))
    for t in takes:
        if t["id"] == tid:
            t["restored"] = restore
            break
    else:
        raise HTTPException(404, "take não encontrado")
    project.analysis["takes"] = takes
    project.save_analysis()
    project.plan.discarded_takes = takes
    return {"ok": True, "takes": takes, "hint": "rode a edição automática de novo "
                                                "para o plano refletir a mudança"}


@app.post("/api/projects/{pid}/ops/clap")
def api_clap(pid: str, payload: dict = Body(...)) -> dict:
    """Confirma ou descarta uma palma suspeita."""
    project = _project(pid)
    claps = project.analysis.get("claps", [])
    for c in claps:
        if c["id"] == payload.get("clap_id"):
            c["enabled"] = bool(payload.get("enabled"))
            c["confirmed"] = bool(payload.get("enabled"))
            c["suspect"] = False
            break
    else:
        raise HTTPException(404, "palma não encontrada")
    project.analysis["claps"] = claps
    project.save_analysis()
    project.plan.claps = claps
    project.save_plan()
    return {"ok": True, "claps": claps}


@app.get("/api/projects/{pid}/fillers")
def api_fillers(pid: str) -> list[dict]:
    return _project(pid).analysis.get("fillers", [])


# ------------------------------------------------------------------ legendas
@app.post("/api/projects/{pid}/subtitles/rebuild")
def api_rebuild_subs(pid: str) -> dict:
    project = _project(pid)
    cues = svc.rebuild_subtitles(project)
    project.save_plan()
    return {"subtitles": cues}


@app.put("/api/projects/{pid}/subtitles/{sid}")
def api_edit_sub(pid: str, sid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    for sub in project.plan.subtitles:
        if sub.id == sid:
            if "text" in payload:
                sub.text = str(payload["text"])
                sub.edited = True
            if "start" in payload:
                sub.start = round(float(payload["start"]), 3)
            if "end" in payload:
                sub.end = round(float(payload["end"]), 3)
            project.save_plan()
            return {"ok": True, "subtitle": sub.to_dict()}
    raise HTTPException(404, "legenda não encontrada")


@app.get("/api/projects/{pid}/subtitles.srt")
def api_srt(pid: str) -> PlainTextResponse:
    project = _project(pid)
    return PlainTextResponse(
        ass_mod.build_srt(svc.cue_list(project), project.plan.style.uppercase),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{project.name}.srt"'})


@app.get("/api/projects/{pid}/subtitles.ass")
def api_ass(pid: str) -> PlainTextResponse:
    project = _project(pid)
    w, h = (project.info.display_size if project.info else (1080, 1920))
    return PlainTextResponse(
        ass_mod.build_ass(svc.cue_list(project), project.plan.style, w, h),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{project.name}.ass"'})


@app.post("/api/projects/{pid}/style/calibrate")
def api_calibrate(pid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    w, h = (project.info.display_size if project.info else (1080, 1920))
    sample = payload.get("sample") or "ISSO MUDA TUDO"
    target = int(payload.get("target_px", 726))
    try:
        result = ass_mod.calibrate_fontsize(target, sample, project.plan.style, w, h)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    if payload.get("apply", True):
        project.plan.style.fontsize = result["fontsize"]
        project.plan.style.target_width_px = target
        project.save_plan()
    return result


@app.get("/api/corrections")
def api_corrections() -> list[dict]:
    return db.list_corrections()


@app.post("/api/corrections")
def api_add_correction(payload: dict = Body(...)) -> dict:
    return db.add_correction(payload.get("from", ""), payload.get("to", ""))


@app.put("/api/corrections/{cid}")
def api_update_correction(cid: int, payload: dict = Body(...)) -> dict:
    db.update_correction(cid, payload.get("from", ""), payload.get("to", ""),
                         bool(payload.get("enabled", True)))
    return {"ok": True}


@app.delete("/api/corrections/{cid}")
def api_delete_correction(cid: int) -> dict:
    db.delete_correction(cid)
    return {"ok": True}


# --------------------------------------------------------------------- mídia
@app.post("/api/projects/{pid}/media")
def api_add_media(pid: str, payload: dict = Body(...)) -> dict:
    _project(pid)
    try:
        return svc.add_media(pid, payload["path"], payload.get("kind", "video"),
                             payload.get("name", ""))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/projects/{pid}/cutaways")
def api_cutaway(pid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    cut = Cutaway(media_id=payload["media_id"],
                  out_start=float(payload["out_start"]),
                  out_end=float(payload["out_end"]),
                  media_start=float(payload.get("media_start", 0.0)),
                  speed=float(payload.get("speed", 1.0)))
    if payload.get("fit"):
        cut.fit.update(payload["fit"])
    project.plan.cutaways.append(cut)
    project.save_plan()
    return {"ok": True, "cutaway": cut.to_dict(),
            "timeline": svc.timeline_summary(project)}


@app.put("/api/projects/{pid}/cutaways/{cid}")
def api_cutaway_update(pid: str, cid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    for c in project.plan.cutaways:
        if c.id == cid:
            for k in ("out_start", "out_end", "media_start", "speed"):
                if k in payload:
                    setattr(c, k, float(payload[k]))
            if "enabled" in payload:
                c.enabled = bool(payload["enabled"])
            if payload.get("fit"):
                c.fit.update(payload["fit"])
            project.save_plan()
            return {"ok": True, "cutaway": c.to_dict()}
    raise HTTPException(404, "cutaway não encontrado")


@app.delete("/api/projects/{pid}/cutaways/{cid}")
def api_cutaway_delete(pid: str, cid: str) -> dict:
    project = _project(pid)
    project.plan.cutaways = [c for c in project.plan.cutaways if c.id != cid]
    project.save_plan()
    return {"ok": True, "timeline": svc.timeline_summary(project)}


@app.post("/api/projects/{pid}/insert")
def api_insert(pid: str, payload: dict = Body(...)) -> dict:
    """Inserir vídeo ou foto empurrando o resto (Parte 7.1 modo 'inserir')."""
    project = _project(pid)
    plan = project.plan
    from .edit.timeline import Timeline

    tl = Timeline(plan.active_clips)
    at = float(payload.get("at", tl.duration))
    kind = payload.get("kind", "insert")
    media = next((m for m in svc.list_media(pid)
                  if m["id"] == payload.get("media_id")), None)
    if not media:
        raise HTTPException(404, "mídia não encontrada")

    if kind == "photo":
        clip = Clip(source=media["id"], kind="photo", src_start=0.0,
                    src_end=float(payload.get("duration", 3.0)), speed=1.0,
                    audio="mute",
                    photo={"duration": float(payload.get("duration", 3.0)),
                           "ken_burns": payload.get("ken_burns")
                           or {"enabled": False, "intensity": 0.12,
                               "direction": "in"},
                           "annotations": payload.get("annotations", [])})
    else:
        clip = Clip(source=media["id"], kind="insert",
                    src_start=float(payload.get("media_start", 0.0)),
                    src_end=float(payload.get("media_end",
                                              media["info"].get("duration", 5.0))),
                    speed=float(payload.get("speed", 1.0)),
                    audio=payload.get("audio", "source"),
                    fit=payload.get("fit"))

    placed = tl.at(at)
    if placed is None:
        plan.clips.append(clip)
    else:
        if at - placed.out_start > 0.15 and placed.out_end - at > 0.15:
            ops.split_clip(plan, placed.clip.id, at)
            tl2 = Timeline(plan.active_clips)
            target = tl2.at(at)
            idx = plan.clips.index(target.clip) if target else len(plan.clips)
        else:
            idx = plan.clips.index(placed.clip)
            if at > (placed.out_start + placed.out_end) / 2:
                idx += 1
        plan.clips.insert(idx, clip)
    project.save_plan()
    return {"ok": True, "clip": clip.to_dict(),
            "timeline": _after_edit(project)}


@app.post("/api/projects/{pid}/overlays")
def api_overlay(pid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    o = Overlay(media_id=payload["media_id"],
                out_start=float(payload.get("out_start", 0.0)),
                out_end=float(payload.get("out_end", 3.0)),
                x=float(payload.get("x", 0.5)), y=float(payload.get("y", 0.2)),
                scale=float(payload.get("scale", 1.0)),
                opacity=float(payload.get("opacity", 1.0)),
                anim_in=payload.get("anim_in", "fade"),
                anim_out=payload.get("anim_out", "fade"),
                dur_in=float(payload.get("dur_in", 0.35)),
                dur_out=float(payload.get("dur_out", 0.35)))
    project.plan.overlays.append(o)
    project.save_plan()
    return {"ok": True, "overlay": o.to_dict()}


@app.put("/api/projects/{pid}/overlays/{oid}")
def api_overlay_update(pid: str, oid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    for o in project.plan.overlays:
        if o.id == oid:
            for k, cast in (("out_start", float), ("out_end", float), ("x", float),
                            ("y", float), ("scale", float), ("opacity", float),
                            ("dur_in", float), ("dur_out", float),
                            ("anim_in", str), ("anim_out", str),
                            ("enabled", bool)):
                if k in payload:
                    setattr(o, k, cast(payload[k]))
            project.save_plan()
            return {"ok": True, "overlay": o.to_dict()}
    raise HTTPException(404, "sobreposição não encontrada")


@app.delete("/api/projects/{pid}/overlays/{oid}")
def api_overlay_delete(pid: str, oid: str) -> dict:
    project = _project(pid)
    project.plan.overlays = [o for o in project.plan.overlays if o.id != oid]
    project.save_plan()
    return {"ok": True}


@app.post("/api/projects/{pid}/blurs")
def api_blur(pid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    b = BlurRegion(out_start=float(payload.get("out_start", 0.0)),
                   out_end=float(payload.get("out_end", 2.0)),
                   strength=int(payload.get("strength", 24)),
                   keyframes=payload.get("keyframes")
                   or [{"t": float(payload.get("out_start", 0.0)),
                        "x": 0.35, "y": 0.35, "w": 0.3, "h": 0.3}])
    project.plan.blurs.append(b)
    project.save_plan()
    return {"ok": True, "blur": b.to_dict()}


@app.put("/api/projects/{pid}/blurs/{bid}")
def api_blur_update(pid: str, bid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    for b in project.plan.blurs:
        if b.id == bid:
            if "out_start" in payload:
                b.out_start = float(payload["out_start"])
            if "out_end" in payload:
                b.out_end = float(payload["out_end"])
            if "strength" in payload:
                b.strength = int(payload["strength"])
            if "shape" in payload:
                b.shape = str(payload["shape"])
            if "keyframes" in payload:
                b.keyframes = payload["keyframes"]
            if "enabled" in payload:
                b.enabled = bool(payload["enabled"])
            project.save_plan()
            return {"ok": True, "blur": b.to_dict()}
    raise HTTPException(404, "desfoque não encontrado")


@app.delete("/api/projects/{pid}/blurs/{bid}")
def api_blur_delete(pid: str, bid: str) -> dict:
    project = _project(pid)
    project.plan.blurs = [b for b in project.plan.blurs if b.id != bid]
    project.save_plan()
    return {"ok": True}


@app.post("/api/projects/{pid}/music")
def api_music(pid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    project.plan.music = payload if payload.get("media_id") else None
    project.save_plan()
    return {"ok": True, "music": project.plan.music}


# -------------------------------------------------------------------- áudio
@app.get("/api/projects/{pid}/audio/analysis")
def api_audio_analysis(pid: str) -> dict:
    from .audio.denoise import noise_profile, propose_chain, sibilance, snr
    from .audio.loudness import build_chain, measure_file
    from .ffmpeg_utils import read_wav_mono

    project = _project(pid)
    env = _env_or_404(project)
    samples, sr = read_wav_mono(project.wav)
    profile = noise_profile(samples, sr, env)
    before = measure_file(project.source_path)
    return {
        "loudness_before": before.to_dict(),
        "chain": build_chain(project.plan.audio),
        "noise": profile,
        "proposal": propose_chain(profile),
        "snr": round(snr(samples, env), 2),
        "sibilance": round(sibilance(samples, sr), 4),
        "denoise_enabled": project.plan.audio.denoise_enabled,
    }


@app.post("/api/projects/{pid}/audio/preview")
def api_audio_preview(pid: str, payload: dict = Body(...)) -> dict:
    """Prévia A/B: mede antes e depois antes de aplicar qualquer coisa."""
    from .audio.denoise import sibilance, snr
    from .audio.envelope import compute_envelope
    from .audio.loudness import build_chain, compare, measure_samples
    from .ffmpeg_utils import decode_pcm

    project = _project(pid)
    env = _env_or_404(project)
    params = AudioParams(**{**project.plan.audio.__dict__,
                            **{k: v for k, v in payload.items()
                               if k in AudioParams.__dataclass_fields__}})
    start = float(payload.get("start", 0.0))
    dur = float(payload.get("duration", min(20.0, env.duration)))
    chain = build_chain(params)
    raw = decode_pcm(project.source_path, start, start + dur, 48000, 1)
    processed = decode_pcm(project.source_path, start, start + dur, 48000, 1,
                           filters=chain)
    before = measure_samples(raw)
    after = measure_samples(processed)
    env_b = compute_envelope(raw, 48000)
    env_a = compute_envelope(processed, 48000)
    return {
        "chain": chain,
        "before": before.to_dict(), "after": after.to_dict(),
        "comparison": compare(before, after, params),
        "snr_before": round(snr(raw, env_b), 2),
        "snr_after": round(snr(processed, env_a), 2),
        "sibilance_before": round(sibilance(raw, 48000), 4),
        "sibilance_after": round(sibilance(processed, 48000), 4),
        "sibilance_warning": (
            "a sibilância subiu — aumente o de-esser até voltar ao nível "
            "original, senão os 's' ficam agressivos"
            if sibilance(processed, 48000) > sibilance(raw, 48000) * 1.05 else ""),
    }


@app.get("/api/projects/{pid}/safe-zone")
def api_safe_zone(pid: str) -> dict:
    project = _project(pid)
    band = video_analysis.detect_subtitle_band(project.source_path, project.info)
    return {"band": band,
            "anchor": video_analysis.suggest_anchor(band, project.info)}


@app.get("/api/projects/{pid}/frame")
def api_frame(pid: str, t: float = 0.0, width: int = 360, source: str = "main"):
    """Um quadro do vídeo, para posicionar elementos com guias."""
    project = _project(pid)
    path = project.source_path
    if source != "main":
        media = next((m for m in svc.list_media(pid) if m["id"] == source), None)
        if not media:
            raise HTTPException(404, "mídia não encontrada")
        path = media["path"]
    # o tempo vem da linha do tempo de SAÍDA; converte para a fonte
    if source == "main":
        from .edit.timeline import Timeline

        tl = Timeline(project.plan.active_clips,
                      project.info.fps if project.info else None)
        found = tl.to_source(t)
        if found and found[0] == "main":
            t = found[1]
    try:
        data, _mean = video_analysis.frame_jpeg(path, t, "", width)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)[:300]) from exc
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.put("/api/projects/{pid}/clips/{cid}/photo")
def api_photo(pid: str, cid: str, payload: dict = Body(...)) -> dict:
    """Duração, push-in e anotações de uma foto inserida (Parte 7.2)."""
    project = _project(pid)
    for clip in project.plan.clips:
        if clip.id != cid or clip.kind != "photo":
            continue
        photo = dict(clip.photo or {})
        if "duration" in payload:
            dur = max(0.3, float(payload["duration"]))
            photo["duration"] = dur
            clip.src_start = 0.0
            clip.src_end = dur
        if "ken_burns" in payload:
            photo["ken_burns"] = payload["ken_burns"]
        if "annotations" in payload:
            photo["annotations"] = payload["annotations"]
        clip.photo = photo
        clip.measured_duration = None
        project.save_plan()
        return {"ok": True, "clip": clip.to_dict(),
                "timeline": svc.timeline_summary(project)}
    raise HTTPException(404, "foto não encontrada")


@app.get("/api/projects/{pid}/media/{mid}/tonemap-preview")
def api_tonemap_preview(pid: str, mid: str, t: float = 0.0, npl: float = 100.0,
                        operator: str = "hable", brightness: float = 0.0,
                        saturation: float = 1.0, contrast: float = 1.0,
                        main_time: float = 0.0) -> dict:
    """Comparação lado a lado das conversões HDR -> SDR (Parte 7.1)."""
    import base64

    from .render.filters import color_chain, tonemap_chain

    project = _project(pid)
    media = next((m for m in svc.list_media(pid) if m["id"] == mid), None)
    if not media:
        raise HTTPException(404, "mídia não encontrada")

    color = color_chain({"brightness": brightness, "saturation": saturation,
                         "contrast": contrast})

    def shot(label: str, chain: str, note: str) -> dict:
        full = ",".join(x for x in (chain, color) if x)
        try:
            data, mean = video_analysis.frame_jpeg(media["path"], t, full)
        except RuntimeError as exc:
            return {"label": label, "error": str(exc)[:300], "chain": full}
        return {
            "label": label, "chain": full, "note": note,
            "mean_luma": round(mean, 2),
            "image": "data:image/jpeg;base64," + base64.b64encode(data).decode(),
        }

    variants = [
        shot("sem conversão", "",
             "como o inserto entra se nada for feito"),
        shot("transferência (padrão)", tonemap_chain("transferencia"),
             "só curva e primárias; num teste de ida e volta devolve o "
             "original com erro de 0,22 em 255"),
        shot(f"tonemap {operator} (npl={npl:g})",
             tonemap_chain("operador", npl, operator),
             "comprime os altos; use quando o material realmente estourar o "
             "alcance SDR"),
    ]
    try:
        data, mean = video_analysis.frame_jpeg(project.source_path, main_time)
        main = {"label": "vídeo principal", "mean_luma": round(mean, 2),
                "image": "data:image/jpeg;base64," + base64.b64encode(data).decode()}
    except RuntimeError:
        main = None
    return {"variants": variants, "main": main,
            "media": {"id": mid, "name": media["name"],
                      "is_hdr": bool(media["info"].get("is_hdr"))}}


@app.get("/api/projects/{pid}/bitrate-estimate")
def api_bitrate(pid: str) -> dict:
    from .render.renderer import estimate_bitrate

    project = _project(pid)
    return estimate_bitrate(project.info, project.plan.export)


# ------------------------------------------------------------------ estáticos
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"
                                     if (STATIC_DIR / "assets").exists()
                                     else STATIC_DIR), name="assets")


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(index_file.read_text(encoding="utf-8"))
    return HTMLResponse(
        "<h1>Editor de Vídeo</h1><p>O frontend não foi compilado. "
        "Rode <code>npm install &amp;&amp; npm run build</code> dentro de "
        "<code>frontend/</code>.</p>", status_code=200)


@app.get("/{path:path}", response_class=HTMLResponse)
def spa(path: str) -> HTMLResponse:
    if path.startswith("api/"):
        raise HTTPException(404, "rota não encontrada")
    candidate = STATIC_DIR / path
    if candidate.is_file():
        return FileResponse(candidate)
    return index()
