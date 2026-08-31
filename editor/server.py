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

from . import anexos, config as _config, db, presets as presets_mod, \
    projects as svc, video_analysis
from .config import (STATIC_DIR, AudioParams, CutParams, ExportParams,
                     SpeedParams, SubtitleStyle, ZoomParams, ensure_dirs,
                     ffmpeg_available, WHISPER_MODEL)
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
    modelo = WHISPER_MODEL
    try:
        from .transcribe import detect_device, resolve_model

        info = detect_device()
        device = info.to_dict()
        modelo = resolve_model(WHISPER_MODEL, info.device)
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
        "whisper_model": modelo,
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


# Música ficou de fora na primeira versão, e era justamente a pasta de quem
# arrasta uma trilha para o trilho A1: o arquivo estava em ~/Música e o app
# dizia "não achei nas pastas conhecidas".
SEARCH_DIRS = ("Videos", "Vídeos", "Movies", "Desktop", "Área de Trabalho",
               "Downloads", "Documents", "Documentos",
               "Music", "Música", "Musicas", "Músicas", "Pictures", "Imagens",
               "OneDrive", "OneDrive/Vídeos", "OneDrive/Videos",
               "OneDrive/Desktop", "OneDrive/Documentos", "OneDrive/Documents",
               "OneDrive/Música", "OneDrive/Music", "OneDrive/Imagens",
               "OneDrive/Pictures")


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
            for depth, pattern in ((0, name), (1, f"*/{name}"),
                                   (2, f"*/*/{name}"), (3, f"*/*/*/{name}")):
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
    # um export em andamento continuaria enchendo a pasta "apagada" de
    # gigabytes (e no Windows o rmtree nem consegue remover arquivo aberto)
    import time as _time

    queue = get_queue()
    for job in queue.list(pid):
        if job.status in ("fila", "rodando"):
            queue.cancel(job.id)
    # espera o worker soltar os arquivos (no Windows, arquivo aberto pelo
    # ffmpeg não é removível e a pasta viraria zumbi)
    for _ in range(50):
        if not any(j.status == "rodando" for j in queue.list(pid)):
            break
        _time.sleep(0.1)
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
    """Serve o arquivo exportado — esteja ele onde estiver.

    A exportação final grava na pasta de Vídeos (foi pedido: o usuário não
    achava o arquivo dentro do AppData) e a prévia grava dentro do projeto.
    Esta rota só olhava dentro do projeto, então o botão "baixar o vídeo"
    devolvia 404 justamente para o arquivo que interessa.
    """
    from .config import output_dir

    project = _project(pid)
    seguro = Path(name).name          # sem "..", sem barra: só o nome
    for base in (project.dir / "exports", output_dir()):
        alvo = base / seguro
        if alvo.exists():
            return _range_response(alvo, request)
    raise HTTPException(404, f"arquivo não encontrado: {seguro}")


@app.get("/api/projects/{pid}/envelope")
def api_envelope(pid: str, points: int = 4000) -> dict:
    project = _project(pid)
    env = _env_or_404(project)
    return env.to_dict(points)


# --------------------------------------------------------------------- jobs
def _run(kind: str, pid: str, fn) -> dict:
    # dois cliques no mesmo botão não podem virar dois jobs: o segundo
    # re-analisaria tudo e apagaria as decisões manuais feitas após o primeiro
    for existing in get_queue().list(pid):
        if existing.kind == kind and existing.status in ("fila", "rodando"):
            return existing.to_dict()
    job = get_queue().submit(kind, pid, fn)
    return job.to_dict()


@app.post("/api/projects/{pid}/proxy")
def api_proxy(pid: str) -> dict:
    """Gera a cópia leve da fonte, para a prévia tocar sem engasgo."""
    _project(pid)
    return _run("proxy", pid, lambda ctx: svc.build_proxy_job(svc.load(pid), ctx))


@app.get("/api/projects/{pid}/proxy")
def api_proxy_file(pid: str, request: Request):
    """Serve o proxy. 404 quando não existe — o player cai na fonte."""
    project = _project(pid)
    if not project.proxy_ok:
        raise HTTPException(404, "sem prévia leve")
    return _range_response(project.proxy_file, request)


@app.get("/api/projects/{pid}/proxy-status")
def api_proxy_status(pid: str) -> dict:
    from .render.proxy import vale_a_pena

    project = _project(pid)
    precisa, motivo = (vale_a_pena(project.info) if project.info
                       else (False, "sem informação do arquivo"))
    return {"ok": project.proxy_ok, "precisa": precisa, "detail": motivo,
            "size_bytes": (project.proxy_file.stat().st_size
                           if project.proxy_ok else 0)}


@app.post("/api/projects/{pid}/analyze")
def api_analyze(pid: str) -> dict:
    _project(pid)
    return _run("analise", pid, lambda ctx: svc.analyze(svc.load(pid), ctx))


@app.post("/api/projects/{pid}/autoedit")
def api_autoedit(pid: str) -> dict:
    _project(pid)
    return _run("edicao", pid, lambda ctx: svc.auto_edit(svc.load(pid), ctx))


@app.post("/api/projects/{pid}/oneclick")
def api_oneclick(pid: str, payload: dict = Body(default={})) -> dict:
    """Dispara o clique único — e a RECEITA da primeira tela entra aqui.

    Era aqui que a primeira tela morria: o preset era reaplicado por cima do
    plano SEMPRE, e `apply_preset_to_plan` reconstrói cut/speed/style/audio/
    export/zoom do zero. Tudo que a tela tinha acabado de gravar por /params
    — velocidade, intensidade de zoom, tamanho de legenda, resolução — voltava
    ao padrão do preset milissegundos antes de o vídeo ser montado. Os sliders
    existiam e não mudavam nada no arquivo entregue.

    Agora o preset só é reaplicado quando MUDOU, e a receita é aplicada DEPOIS
    dele, na mesma chamada — a ordem deixa de depender de quem gravou primeiro.
    """
    project = _project(pid)
    preset = payload.get("preset")
    mudou = bool(preset) and preset != project.preset
    if mudou:
        svc.apply_preset_to_plan(project.plan, preset)
        svc.escalar_legenda(project.plan, project.info)
        db.ex("UPDATE projects SET preset=? WHERE id=?", (preset, pid))
    receita = payload.get("receita")
    if isinstance(receita, dict) and receita:
        aplicar_receita(project, receita)
    if mudou or receita:
        project.save_plan()

    def pipeline(ctx) -> dict:
        res = svc.one_click(svc.load(pid), ctx)
        # e o MP4 final continua sendo gerado — por baixo, sem segurar a tela.
        # O botão de baixar no editor acende sozinho quando este job termina.
        try:
            job = get_queue().submit(
                "exportacao", pid,
                lambda c: svc.exportar_final(svc.load(pid), c))
            res["final_job"] = job.id
        except Exception as exc:  # noqa: BLE001 — o corte está pronto de todo jeito
            res["final_job"] = None
            res["final_erro"] = str(exc)
        return res

    return _run("clique-unico", pid, pipeline)


@app.post("/api/projects/{pid}/export")
def api_export(pid: str, payload: dict = Body(default={})) -> dict:
    _project(pid)
    return _run("exportacao", pid,
                lambda ctx: svc.export(svc.load(pid), ctx, payload))


@app.post("/api/projects/{pid}/export-final")
def api_export_final(pid: str) -> dict:
    """Refaz o arquivo final por baixo — é o que roda quando você para de mexer.

    Sempre no mesmo arquivo e reaproveitando o cache por trecho: só o que o
    retoque tocou é reencodado.
    """
    _project(pid)
    return _run("exportacao", pid,
                lambda ctx: svc.exportar_final(svc.load(pid), ctx))


@app.post("/api/projects/{pid}/validate")
def api_validate(pid: str, payload: dict = Body(default={})) -> dict:
    _project(pid)
    return _run("validacao", pid,
                lambda ctx: svc.validate(svc.load(pid), ctx, payload.get("output")))


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
PARAMS_MAP = {"cut": (CutParams, "cut"), "speed": (SpeedParams, "speed"),
              "style": (SubtitleStyle, "style"), "audio": (AudioParams, "audio"),
              "export": (ExportParams, "export"), "zoom": (ZoomParams, "zoom")}


def aplicar_receita(project, payload: dict) -> None:
    """Escreve a receita no plano (sem salvar) — campo a campo, sem apagar o resto."""
    plan = project.plan
    for key, (cls, attr) in PARAMS_MAP.items():
        if key in payload and isinstance(payload[key], dict):
            current = getattr(plan, attr).__dict__.copy()
            current.update({k: v for k, v in payload[key].items() if k in current})
            if "levels" in current:
                current["levels"] = tuple(float(x) for x in current["levels"])
            setattr(plan, attr, cls(**current))
    if "look" in payload:
        from .render.looks import BY_ID

        if str(payload["look"]) in BY_ID:
            plan.look = str(payload["look"])
    # tamanho de legenda RELATIVO ao que o formato pede. O tamanho certo já é
    # calculado sozinho na criação do projeto (o preset é medido numa altura
    # de 1024 e reescalado para a altura real); isto aqui é só "um pouco maior"
    # ou "um pouco menor", e recalcula do zero para não acumular.
    # formatos extras: só os que existem, e o principal nunca entra na lista
    if isinstance(payload.get("export"), dict) and "extras" in payload["export"]:
        from .render.renderer import PROPORCOES

        pedidos = payload["export"].get("extras") or []
        plan.export.extras = tuple(
            dict.fromkeys(a for a in pedidos
                          if a in PROPORCOES and a != "fonte"))
    escala = (payload.get("style") or {}).get("fontsize_scale")
    if escala is not None:
        plan.style.fontsize = svc.fontsize_do_formato(plan, project.info, float(escala))
    if "zoom" in payload and isinstance(payload["zoom"], dict):
        # mudou o jogo de zoom: redistribui pelos blocos na hora
        svc.recalcular_zoom(project)


@app.post("/api/projects/{pid}/params")
def api_params(pid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    aplicar_receita(project, payload)
    if payload.get("rebuild_subtitles"):
        svc.rebuild_subtitles(project)
    project.save_plan()
    return {"plan": project.plan.to_dict(),
            "timeline": svc.timeline_summary(project)}


@app.post("/api/projects/{pid}/preset")
def api_apply_preset(pid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    name = payload.get("name", "")
    if not presets_mod.get_preset(name):
        raise HTTPException(404, "preset não encontrado")
    svc.apply_preset_to_plan(project.plan, name)
    svc.escalar_legenda(project.plan, project.info)
    db.ex("UPDATE projects SET preset=? WHERE id=?", (name, pid))
    project.save_plan()
    return {"ok": True, "plan": project.plan.to_dict()}


@app.get("/api/looks")
def api_looks() -> list[dict]:
    """Catálogo dos filtros de cinema."""
    from .render.looks import catalog

    return catalog()


@app.post("/api/projects/{pid}/ops/look")
def api_look(pid: str, payload: dict = Body(...)) -> dict:
    """Escolhe o filtro de cinema do vídeo inteiro."""
    from .render.looks import BY_ID

    project = _project(pid)
    look = str(payload.get("look", "nenhum"))
    if look not in BY_ID:
        raise HTTPException(400, f"filtro desconhecido: {look}")
    project.plan.look = look
    if "vignette" in payload:
        v = payload["vignette"]
        project.plan.look_vignette = None if v is None else max(0.0, min(1.0, float(v)))
    project.save_plan()
    return {"ok": True, "look": look,
            "vignette": project.plan.look_vignette,
            "timeline": svc.timeline_summary(project)}


@app.get("/api/output-dir")
def api_output_dir() -> dict:
    """Onde o vídeo pronto é salvo."""
    from .config import output_dir

    d = output_dir()
    return {"path": str(d), "exists": d.is_dir(),
            "default": str(_config.OUTPUT_DIR)}


@app.post("/api/output-dir")
def api_set_output_dir(payload: dict = Body(...)) -> dict:
    """Muda a pasta de saída."""
    from .config import OUTPUT_DIR, output_dir

    caminho = str(payload.get("path", "")).strip()
    if not caminho:
        db.set_setting("output_dir", None)
        return {"path": str(output_dir())}
    alvo = Path(caminho).expanduser()
    try:
        alvo.mkdir(parents=True, exist_ok=True)
        teste = alvo / ".escrita-ok"
        teste.write_text("ok", encoding="utf-8")
        teste.unlink()
    except OSError as exc:
        raise HTTPException(400, f"não dá para escrever em {alvo}: {exc}") from exc
    db.set_setting("output_dir", str(alvo))
    return {"path": str(alvo), "default": str(OUTPUT_DIR)}


@app.post("/api/reveal")
def api_reveal(payload: dict = Body(...)) -> dict:
    """Abre a pasta do arquivo no explorador de arquivos do sistema.

    O usuário exporta e quer VER o arquivo. Mandar ele procurar um caminho
    escrito na tela não é entregar nada.
    """
    import subprocess

    alvo = Path(str(payload.get("path", ""))).expanduser()
    if not alvo.exists():
        raise HTTPException(404, f"não existe: {alvo}")
    try:
        if sys.platform.startswith("win"):
            if alvo.is_file():
                subprocess.Popen(["explorer", "/select,", str(alvo)])
            else:
                subprocess.Popen(["explorer", str(alvo)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R" if alvo.is_file() else "", str(alvo)]
                             if alvo.is_file() else ["open", str(alvo)])
        else:
            subprocess.Popen(["xdg-open", str(alvo if alvo.is_dir() else alvo.parent)])
    except OSError as exc:
        raise HTTPException(500, f"não deu para abrir: {exc}") from exc
    return {"ok": True, "path": str(alvo)}


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
    # o plano e a lista de palavras removidas andam JUNTOS no histórico:
    # restaurar só o plano deixava a palavra de volta no vídeo mas ainda
    # riscada no texto e fora da legenda
    if "removed_word_ids" in payload:
        project.analysis["removed_word_ids"] = sorted(
            int(i) for i in payload["removed_word_ids"])
        project.analysis["manual_removed_word_ids"] = sorted(
            int(i) for i in payload.get("manual_removed_word_ids", []))
        project.save_analysis()
        svc.rebuild_subtitles(project)
    project.save_plan()
    return {"ok": True, "timeline": svc.timeline_summary(project)}


def _fps(project: svc.Project) -> float | None:
    return project.info.fps if project.info else None


def _remapping(project: svc.Project, fn):
    """Executa uma edição reancorando cutaways/overlays/desfoques (fonte é a âncora)."""
    from .edit.timeline import Timeline

    old = Timeline(project.plan.active_clips, _fps(project))
    result = fn()
    new = Timeline(project.plan.active_clips, _fps(project))
    moved = ops.remap_output_items(project.plan, old, new)
    if moved and isinstance(result, dict):
        result = {**result, "remapped": moved}
    return result


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
    res = _remapping(project, lambda: ops.delete_output_range(
        project.plan, env, float(payload["start"]), float(payload["end"]),
        project.plan.cut, project.words, fps=_fps(project)))
    if not res.get("ok"):
        raise HTTPException(400, res.get("reason", "não foi possível deletar"))
    return {**res, "timeline": _after_edit(project)}


@app.post("/api/projects/{pid}/ops/remove-words")
def api_remove_words(pid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    env = _env_or_404(project)
    res = _remapping(project, lambda: ops.remove_words(
        project.plan, env, project.words, payload.get("word_ids", []),
        project.plan.cut))
    removed = set(project.analysis.get("removed_word_ids", []))
    manual = set(project.analysis.get("manual_removed_word_ids", []))
    for group in res.get("applied", []):
        if group.get("ok"):
            removed.update(group["words"])
            manual.update(group["words"])
    project.analysis["removed_word_ids"] = sorted(removed)
    # remoções manuais sobrevivem ao "refazer edição" — sem esta lista, a
    # reedição automática traria de volta tudo que o usuário tirou pelo texto
    project.analysis["manual_removed_word_ids"] = sorted(manual)
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
    res = _remapping(project,
                     lambda: ops.restore_range(project.plan, max(0.0, start), end))
    removed = set(project.analysis.get("removed_word_ids", [])) - set(ids)
    manual = set(project.analysis.get("manual_removed_word_ids", [])) - set(ids)
    project.analysis["removed_word_ids"] = sorted(removed)
    project.analysis["manual_removed_word_ids"] = sorted(manual)
    project.save_analysis()
    return {**res, "timeline": _after_edit(project)}


@app.post("/api/projects/{pid}/ops/restore-range")
def api_restore_range(pid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    res = _remapping(project, lambda: ops.restore_range(
        project.plan, float(payload["start"]), float(payload["end"]),
        payload.get("source", "main")))
    if not res.get("ok"):
        raise HTTPException(400, res.get("reason", "não foi possível recuperar"))
    return {**res, "timeline": _after_edit(project)}


@app.post("/api/projects/{pid}/ops/split")
def api_split(pid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    res = ops.split_clip(project.plan, payload["clip_id"], float(payload["time"]),
                         fps=_fps(project))
    if not res.get("ok"):
        raise HTTPException(400, res.get("reason", "não foi possível dividir"))
    return {**res, "timeline": _after_edit(project)}


@app.post("/api/projects/{pid}/ops/merge")
def api_merge(pid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    res = _remapping(project,
                     lambda: ops.merge_clips(project.plan, payload.get("clip_ids", [])))
    if not res.get("ok"):
        raise HTTPException(400, res.get("reason", "não foi possível fundir"))
    return {**res, "timeline": _after_edit(project)}


@app.post("/api/projects/{pid}/ops/speed")
def api_speed(pid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    env = project.envelope()
    if payload.get("global") is not None:
        new = float(payload["global"])
        prev = float(project.plan.speed.global_multiplier or 1.0)
        ratio = new / max(1e-6, prev)

        def apply_global():
            sp = project.plan.speed
            for clip in project.plan.clips:
                base = clip.base_speed
                if base is None:
                    base = clip.speed / max(1e-6, prev)
                clip.base_speed = round(base, 4)
                clip.speed = round(max(sp.min_speed,
                                       min(sp.max_speed, base * new)), 2)
                clip.measured_duration = None
            sp.global_multiplier = new
            return {"ok": True, "applied_ratio": round(ratio, 4)}

        res = _remapping(project, apply_global)
        return {**res, "timeline": _after_edit(project)}
    res = _remapping(project, lambda: ops.set_speed(
        project.plan, payload["clip_id"], float(payload["speed"]), env))
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
            # a etapa DECIDE o enquadramento (zoom_base em SECTIONS): trocar a
            # etapa e não recalcular deixava o usuário mudando um rótulo que
            # não mexia em nada na imagem
            svc.recalcular_zoom(project)
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
    removed = set(project.analysis.get("removed_word_ids", []))
    res = _remapping(project, lambda: apply_fix(
        project.plan.clips, issues[index], project.words, removed))
    if not res:
        raise HTTPException(400, "não foi possível aplicar a correção")
    env = project.envelope()
    if env is not None:
        from .edit.plan_builder import resync_removed
        project.plan.removed = resync_removed(project.plan.clips,
                                              project.plan.removed, env.duration)
    return {"ok": True, "timeline": _after_edit(project)}


@app.post("/api/projects/{pid}/ops/music")
def api_music_ajuste(pid: str, payload: dict = Body(...)) -> dict:
    """Volume, mudo e ducking da trilha, sem reescrever o resto.

    A rota /music substitui o objeto inteiro; para mexer só no volume isso
    obrigava o frontend a reenviar tudo e qualquer campo esquecido virava
    padrão em silêncio.
    """
    project = _project(pid)
    m = project.plan.music
    if not m:
        raise HTTPException(404, "não há trilha")
    for campo, conv in (("gain_db", float), ("duck_amount", float),
                        ("fade_in", float), ("fade_out", float),
                        ("ducking", bool), ("enabled", bool), ("muted", bool)):
        if campo in payload:
            m[campo] = conv(payload[campo])
    project.plan.music = m
    project.save_plan()
    return {"ok": True, "music": m, "timeline": svc.timeline_summary(project)}


@app.post("/api/projects/{pid}/ops/resize-removed")
def api_resize_removed(pid: str, payload: dict = Body(...)) -> dict:
    """Arrasta a borda de um trecho já removido, na trilha."""
    project = _project(pid)
    env = _env_or_404(project)
    res = _remapping(project, lambda: ops.resize_removed(
        project.plan,
        float(payload["start"]), float(payload["end"]),
        float(payload["new_start"]), float(payload["new_end"])))
    if not res.get("ok"):
        raise HTTPException(400, res.get("reason", "não deu"))
    from .edit.plan_builder import resync_removed

    project.plan.removed = resync_removed(project.plan.clips,
                                          project.plan.removed, env.duration)
    return {**res, "timeline": _after_edit(project)}


@app.post("/api/projects/{pid}/ops/item")
def api_item(pid: str, payload: dict = Body(...)) -> dict:
    """Move, redimensiona ou apaga um item de trilho.

    Um só endpoint para cutaway, sobreposição, desfoque e trilha — na
    timeline eles são a mesma coisa: um retângulo com começo e fim.
    """
    project = _project(pid)
    kind = str(payload.get("kind", ""))
    iid = str(payload.get("id", ""))
    acao = str(payload.get("action", "move"))
    plan = project.plan

    colecoes = {"cutaway": plan.cutaways, "overlay": plan.overlays,
                "blur": plan.blurs}
    alvo = None
    if kind == "music":
        if not plan.music:
            raise HTTPException(404, "não há trilha")
        alvo = plan.music
    else:
        for item in colecoes.get(kind, []):
            if item.id == iid:
                alvo = item
                break
    if alvo is None:
        raise HTTPException(404, f"item {kind}/{iid} não encontrado")

    def ler(obj, campo, padrao=0.0):
        return float(obj.get(campo, padrao) if isinstance(obj, dict)
                     else getattr(obj, campo, padrao) or padrao)

    def gravar(obj, campo, valor):
        if isinstance(obj, dict):
            obj[campo] = round(valor, 4)
        else:
            setattr(obj, campo, round(valor, 4))

    if acao == "delete":
        if kind == "music":
            plan.music = None
        else:
            colecoes[kind][:] = [i for i in colecoes[kind] if i.id != iid]
        project.save_plan()
        return {"ok": True, "timeline": svc.timeline_summary(project)}

    limite = svc.timeline_summary(project)["duration"]
    if limite <= 0.01 and project.info:
        limite = float(project.info.duration or 0.0)
    a = ler(alvo, "out_start")
    b = ler(alvo, "out_end", limite)
    if acao == "move":
        delta = float(payload.get("delta", 0.0))
        dur = max(0.05, b - a)
        # o item PODE passar do fim do vídeo — o render corta o que sobra.
        # Clampar o fim deixava um item do tamanho da linha inteira imóvel:
        # não sobrava para onde ir, e arrastar não fazia nada.
        novo_a = max(0.0, min(max(0.0, limite - 0.2), a + delta))
        gravar(alvo, "out_start", novo_a)
        gravar(alvo, "out_end", novo_a + dur)
    else:                                    # resize
        side = str(payload.get("side", "end"))
        t = max(0.0, min(limite, float(payload.get("time", b))))
        if side == "start":
            gravar(alvo, "out_start", min(t, b - 0.2))
        else:
            gravar(alvo, "out_end", max(t, a + 0.2))
    project.save_plan()
    return {"ok": True, "timeline": svc.timeline_summary(project)}


@app.post("/api/projects/{pid}/ops/zoom")
def api_zoom(pid: str, payload: dict = Body(...)) -> dict:
    """Ajusta o enquadramento de UM bloco (o jogo de zoom, na mão)."""
    project = _project(pid)
    zoom = float(payload.get("zoom", 1.0))
    # era plan.zoom.max_level — campo que não existe. A rota inteira dava 500,
    # tanto para travar um bloco quanto para ajustar o enquadramento na mão:
    # o controle manual que o usuário pediu nunca funcionou uma vez sequer.
    limite = svc.teto_de_zoom(project)
    zoom = max(1.0, min(zoom, max(limite, 1.0)))
    for clip in project.plan.clips:
        if clip.id == payload.get("clip_id"):
            if "locked" in payload:
                # travado: o recálculo automático não mexe mais neste bloco
                clip.zoom_locked = bool(payload["locked"])
            if "zoom" in payload:
                clip.zoom = round(zoom, 4)
                clip.zoom_locked = True
            project.save_plan()
            return {"ok": True, "zoom": clip.zoom, "locked": clip.zoom_locked,
                    "timeline": svc.timeline_summary(project)}
    raise HTTPException(404, "bloco não encontrado")


@app.post("/api/projects/{pid}/ops/repeat")
def api_repeat(pid: str, payload: dict = Body(...)) -> dict:
    """Recupera (ou volta a remover) um trecho repetido."""
    project = _project(pid)
    repeats = project.analysis.get("repeats", [])
    rid = payload.get("repeat_id")
    for r in repeats:
        if r.get("id") == rid:
            r["restored"] = bool(payload.get("restored"))
            break
    else:
        raise HTTPException(404, "repetição não encontrada")
    project.analysis["repeats"] = repeats
    project.save_analysis()
    project.plan.repeats = repeats
    project.save_plan()
    return {"ok": True, "repeats": repeats}


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


def _sincronizar_comando(project, evento_id: str, enabled: bool) -> None:
    """Um marcador sintético desligado desliga TAMBÉM o comando falado.

    A revisão adversarial provou o buraco: os toggles escreviam só em
    claps/whistles, e analysis["comandos"] ficava com enabled=True para
    sempre. Consequência dupla: a palavra dita ("corta"/"ok") continuava
    removida do vídeo mesmo com a bandeirinha desligada, e a decisão não
    sobrevivia a uma reanálise. Aqui a decisão desce até a raiz — o comando —
    e o conjunto de palavras removidas é recalculado na hora.
    """
    from .audio.comandos import ids_de_comando

    comandos = project.analysis.get("comandos") or []
    mudou = False
    for cmd in comandos:
        if cmd.get("id") == evento_id:
            cmd["enabled"] = enabled
            mudou = True
    if mudou:
        project.analysis["comandos"] = comandos
        project.analysis["command_word_ids"] = sorted(ids_de_comando(comandos))


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
    _sincronizar_comando(project, str(payload.get("clap_id")),
                         bool(payload.get("enabled")))
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
                new_start = max(0.0, round(float(payload["start"]), 3))
                sub.start_off = round(sub.start_off + (new_start - sub.start), 3)
                sub.start = new_start
            if "end" in payload:
                new_end = round(float(payload["end"]), 3)
                sub.end_off = round(sub.end_off + (new_end - sub.end), 3)
                sub.end = new_end
            if sub.end < sub.start + 0.2:
                # nudge não pode produzir legenda invertida/instantânea
                sub.end = round(sub.start + 0.2, 3)
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
    try:
        midia = anexos.validar(svc.list_media(pid),
                               str(payload.get("media_id") or ""), "video")
        janela = anexos.encaixar(
            midia, float(payload.get("out_start", 0.0)),
            float(payload.get("out_end", 0.0)),
            float(payload.get("media_start", 0.0)),
            float(payload.get("speed", 1.0)),
            limite=svc.duracao_de_saida(project))
        anexos.sem_sobreposicao(project.plan.cutaways,
                                janela.out_start, janela.out_end)
    except anexos.AnexoInvalido as exc:
        raise HTTPException(400, str(exc)) from exc
    cut = Cutaway(media_id=midia["id"],
                  out_start=janela.out_start, out_end=janela.out_end,
                  media_start=janela.media_start, speed=janela.speed)
    if payload.get("fit"):
        cut.fit.update(payload["fit"])
    project.plan.cutaways.append(cut)
    project.save_plan()
    return {"ok": True, "cutaway": cut.to_dict(), "ajustes": janela.ajustes,
            "timeline": svc.timeline_summary(project)}


@app.put("/api/projects/{pid}/cutaways/{cid}")
def api_cutaway_update(pid: str, cid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    for c in project.plan.cutaways:
        if c.id == cid:
            if "enabled" in payload:
                c.enabled = bool(payload["enabled"])
            if payload.get("fit"):
                c.fit.update(payload["fit"])
            if any(k in payload for k in
                   ("out_start", "out_end", "media_start", "speed")):
                # arrastar a borda passa pela MESMA trava do POST: encolher a
                # janela para além do que a mídia cobre corta o áudio junto
                try:
                    midia = anexos.validar(svc.list_media(pid), c.media_id, "video")
                    j = anexos.encaixar(
                        midia, float(payload.get("out_start", c.out_start)),
                        float(payload.get("out_end", c.out_end)),
                        float(payload.get("media_start", c.media_start)),
                        float(payload.get("speed", c.speed)),
                        limite=svc.duracao_de_saida(project))
                    anexos.sem_sobreposicao(project.plan.cutaways,
                                            j.out_start, j.out_end, ignorar=c.id)
                except anexos.AnexoInvalido as exc:
                    raise HTTPException(400, str(exc)) from exc
                c.out_start, c.out_end = j.out_start, j.out_end
                c.media_start, c.speed = j.media_start, j.speed
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

    def insert_clip():
        placed = tl.at(at)
        if placed is None:
            plan.clips.append(clip)
            return {"ok": True}
        if at - placed.out_start > 0.15 and placed.out_end - at > 0.15:
            ops.split_clip(plan, placed.clip.id, at, fps=_fps(project))
            tl2 = Timeline(plan.active_clips, _fps(project))
            target = tl2.at(at)
            idx = plan.clips.index(target.clip) if target else len(plan.clips)
        else:
            idx = plan.clips.index(placed.clip)
            if at > (placed.out_start + placed.out_end) / 2:
                idx += 1
        plan.clips.insert(idx, clip)
        return {"ok": True}

    # inserir empurra todo o conteúdo seguinte: overlays, cutaways e blurs
    # posicionados depois do ponto precisam acompanhar o deslocamento
    res = _remapping(project, insert_clip)
    project.save_plan()
    return {**res, "clip": clip.to_dict(), "timeline": _after_edit(project)}


@app.post("/api/projects/{pid}/overlays")
def api_overlay(pid: str, payload: dict = Body(...)) -> dict:
    project = _project(pid)
    try:
        midia = anexos.validar(svc.list_media(pid),
                               str(payload.get("media_id") or ""), "image")
        janela = anexos.encaixar(midia, float(payload.get("out_start", 0.0)),
                                 float(payload.get("out_end", 3.0)),
                                 limite=svc.duracao_de_saida(project))
    except anexos.AnexoInvalido as exc:
        raise HTTPException(400, str(exc)) from exc
    o = Overlay(media_id=midia["id"],
                out_start=janela.out_start,
                out_end=janela.out_end,
                x=float(payload.get("x", 0.5)), y=float(payload.get("y", 0.2)),
                scale=float(payload.get("scale", 1.0)),
                opacity=float(payload.get("opacity", 1.0)),
                anim_in=payload.get("anim_in", "fade"),
                anim_out=payload.get("anim_out", "fade"),
                dur_in=float(payload.get("dur_in", 0.35)),
                dur_out=float(payload.get("dur_out", 0.35)))
    project.plan.overlays.append(o)
    project.save_plan()
    return {"ok": True, "overlay": o.to_dict(), "ajustes": janela.ajustes}


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
    m = dict(payload) if payload.get("media_id") else None
    if m is not None:
        m.setdefault("out_start", 0.0)
        if not m.get("out_end"):
            # "até o fim" fica EXPLÍCITO: item sem fim não tem borda para pegar
            tl = svc.timeline_summary(project)["duration"]
            m["out_end"] = round(tl if tl > 0.01 else float(
                project.info.duration if project.info else 0), 3)
    project.plan.music = m
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


@app.post("/api/projects/{pid}/audio/calibrate-deesser")
def api_deesser(pid: str, payload: dict = Body(default={})) -> dict:
    """Ajusta o de-esser até a sibilância voltar ao nível original (Parte 9.2)."""
    from .audio.denoise import calibrate_deesser
    from .audio.loudness import build_chain

    project = _project(pid)
    env = _env_or_404(project)
    params = AudioParams(**{**project.plan.audio.__dict__,
                            **{k: v for k, v in payload.items()
                               if k in AudioParams.__dataclass_fields__}})
    start = float(payload.get("start", min(2.0, max(0.0, env.duration - 1))))
    duration = float(payload.get("duration", min(20.0, max(2.0, env.duration - start))))
    result = calibrate_deesser(project.source_path, start, duration, params,
                               build_chain)
    if payload.get("apply", True):
        project.plan.audio.presence_gain = params.presence_gain
        project.plan.audio.deesser = result["deesser"]
        project.save_plan()
    return result


@app.post("/api/projects/{pid}/preview")
def api_preview(pid: str, payload: dict = Body(default={})) -> dict:
    """Prévia rápida em 480p, sem tocar na configuração da exportação final.

    Os parâmetros degradados vão como override EM MEMÓRIA dentro do job.
    Persistir antes e restaurar num finally deixava o plano preso em 480p
    para sempre quando o job era cancelado ainda na fila.
    """
    _project(pid)
    override = {
        "scale": str(payload.get("scale", "480")),
        "crf": int(payload.get("crf", 26)),
        "preset": "veryfast",
        "codec": "h264",
        "audio_bitrate": "128k",
    }
    return _run("previa", pid, lambda ctx: svc.export(
        svc.load(pid), ctx,
        {"filename": "previa480.mp4", "restart": True,
         "export_override": override}))


@app.get("/api/projects/{pid}/safe-zone")
def api_safe_zone(pid: str) -> dict:
    project = _project(pid)
    band = video_analysis.detect_subtitle_band(project.source_path, project.info)
    return {"band": band,
            "anchor": video_analysis.suggest_anchor(band, project.info)}


@app.get("/api/projects/{pid}/frame")
def api_frame(pid: str, t: float = 0.0, width: int = 360, source: str = "main",
              look: str = ""):
    """Um quadro do vídeo, para posicionar elementos com guias.

    ``look`` aplica um filtro de cinema no quadro — é assim que o usuário vê o
    filtro antes de exportar, sem renderizar o vídeo inteiro.
    """
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
    from .render.looks import BY_ID, look_chain

    extra = look_chain(look) if look and look in BY_ID else ""
    try:
        data, _mean = video_analysis.frame_jpeg(path, t, extra, width)
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

        def change():
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
            return {"ok": True}

        # mudar a duração da foto desloca tudo depois dela
        res = _remapping(project, change)
        return {**res, "clip": clip.to_dict(),
                "timeline": _after_edit(project)}
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


@app.get("/api/janela")
def api_janela() -> dict:
    """A janela do sistema pode ser aberta nesta máquina?"""
    from . import nativo

    return {"disponivel": nativo.disponivel()}


@app.post("/api/escolher")
def api_escolher(payload: dict = Body(...)) -> dict:
    """Abre a JANELA DO SISTEMA e devolve o caminho escolhido.

    O navegador entrega só nome e tamanho de um arquivo, nunca o caminho —
    por isso existia aqui um explorador próprio, em HTML, que o usuário tinha
    de aprender a usar. Mas o servidor roda na máquina dele: ele pode abrir a
    janela de sempre, a mesma de qualquer programa. O arquivo continua sem
    sair do lugar; o que atravessa é uma string com o caminho.
    """
    from . import nativo

    kind = str(payload.get("kind", "video"))
    try:
        achados = nativo.escolher(kind, bool(payload.get("varios")),
                                  str(payload.get("titulo", "")))
    except nativo.SemJanela as exc:
        # 501: a máquina não tem como abrir a janela. O front cai no explorador
        # de dentro do app, que continua existindo como rede de segurança.
        raise HTTPException(501, str(exc)) from exc
    return {"ok": True, "cancelado": not achados,
            "path": achados[0] if achados else "", "paths": achados}


# ---------------------------------------------------------------- assobio
@app.post("/api/projects/{pid}/whistles/{wid}")
def api_whistle(pid: str, wid: str, payload: dict = Body(...)) -> dict:
    """Liga ou desliga um assobio.

    Desligar não é raro: às vezes um som agudo qualquer entra na conta. A
    decisão sobrevive a uma reanálise (analyze casa pelo instante).
    """
    project = _project(pid)
    achou = False
    for lista in (project.plan.whistles, project.analysis.get("whistles") or []):
        for a in lista:
            if a.get("id") == wid:
                a["enabled"] = bool(payload.get("enabled", True))
                achou = True
    if not achou:
        raise HTTPException(404, "assobio não encontrado")
    _sincronizar_comando(project, wid, bool(payload.get("enabled", True)))
    project.save_plan()
    project.save_analysis()
    return {"ok": True, "timeline": svc.timeline_summary(project)}


@app.post("/api/projects/{pid}/whistle/calibrate")
def api_whistle_calibrate(pid: str) -> dict:
    """Mede a frequência do assobio DO USUÁRIO a partir deste próprio vídeo.

    Assobio varia muito de pessoa para pessoa (uns fazem 1 kHz, outros 3 kHz).
    Em vez de pedir uma gravação separada, a calibração sai de graça do arquivo
    que ele já soltou: os assobios já foram achados, basta guardar a mediana da
    frequência deles. Com ela, a busca fica bem mais estreita e a chance de
    falso positivo cai.
    """
    project = _project(pid)
    freqs = sorted(float(a.get("freq", 0.0))
                   for a in (project.analysis.get("whistles") or [])
                   if a.get("enabled", True) and a.get("freq"))
    if len(freqs) < 2:
        raise HTTPException(400,
            "preciso de pelo menos dois assobios neste vídeo para medir o seu. "
            "Grave assobiando duas ou três vezes e rode a análise.")
    mediana = freqs[len(freqs) // 2]
    espalha = max(abs(f - mediana) / max(mediana, 1.0) for f in freqs)
    project.plan.whistle_freq = round(mediana, 1)
    project.save_plan()
    return {"ok": True, "freq": round(mediana, 1), "amostras": len(freqs),
            "espalhamento": round(espalha, 3),
            "detail": (f"{len(freqs)} assobios, {mediana:.0f} Hz"
                       + (f" (variação de {espalha * 100:.0f}%)"
                          if espalha > 0.05 else ""))}


@app.delete("/api/projects/{pid}/whistle/calibrate")
def api_whistle_uncalibrate(pid: str) -> dict:
    project = _project(pid)
    project.plan.whistle_freq = None
    project.save_plan()
    return {"ok": True}


# ------------------------------------------------------------------------- IA
# A IA OPINA, O CÓDIGO EXECUTA. Ela devolve etapa, ênfase e onde um anexo
# ajuda; quem traduz isso em enquadramento e em janela de anexo é a maquinaria
# determinística de sempre, com todas as invariantes. Ver editor/ai/.
#
# Estas rotas ficam ANTES do catch-all @app.get("/{path:path}") de propósito:
# rota registrada depois dele simplesmente não existe, e o sintoma é um 404
# confuso em vez de um erro de rota.
CHAVE_IA = "gemini_api_key"


def _chave_ia() -> str:
    from .ai.gemini import chave_guardada

    chave = chave_guardada()
    if not chave:
        raise HTTPException(400, "sem chave do Gemini. Cole a sua na tela "
                                 "inicial — uma vez só; ela fica guardada.")
    return chave


@app.get("/api/ai/config")
def api_ia_config() -> dict:
    """O estado da IA — NUNCA a chave.

    A chave fica em texto puro no SQLite (é o que dá para fazer num app local),
    então ela não pode sair por rota nenhuma: o app roda em 127.0.0.1, mas o
    iniciar-rede.bat existe justamente para revisar do celular, e aí qualquer
    um na rede local alcança as rotas.
    """
    from .ai.gemini import chave_guardada

    chave = chave_guardada()
    return {
        "tem_chave": bool(chave),
        "final": chave[-4:] if len(chave) > 8 else "",
        "modelo": db.get_setting("gemini_model", "") or "",
        # o modelo está DECIDIDO E ESCRITO? Enquanto isto for falso com chave
        # presente, o vídeo sairia com o modelo que o programa escolheu
        # sozinho — e a primeira tela segura o botão de gerar.
        "modelo_fixado": bool(chave and db.get_setting("gemini_model", "")),
        # a IA decidindo os cortes no EDITAR — ligada por padrão quando há
        # chave; o usuário desliga aqui se quiser voltar à regra do programa
        "cortes": bool(db.get_setting("ai_cortes", True)),
    }


@app.post("/api/ai/config")
def api_ia_config_set(payload: dict = Body(...)) -> dict:
    """Guarda chave, modelo e liga/desliga — e FIXA o modelo de verdade.

    O modelo tem que ficar decidido ANTES de o vídeo rodar, e escrito. Ele
    ficava vazio: `gemini_model` nascia "" e `escolher_modelo` resolvia o
    vazio em silêncio, toda vez, caindo no primeiro da lista de preferência.
    Ninguém escolheu nada e o vídeo saía com o modelo que o programa achou —
    exatamente a reclamação de estar "usando o gratuito ainda".

    Agora: modelo pedido que a chave não alcança é RECUSADO (com a lista do
    que existe), e chave nova sem modelo pedido fixa o padrão na hora. Depois
    disto, `gemini_model` nunca mais está vazio enquanto houver chave.
    """
    from .ai import gemini as gem

    if "chave" in payload:
        chave = str(payload.get("chave") or "").strip()
        db.set_setting(CHAVE_IA, chave)
        if not chave:
            # sem chave não há modelo a fixar; deixar o antigo escrito faria a
            # tela dizer "vai usar X" sem ter com que usar
            db.set_setting("gemini_model", "")
    if "cortes" in payload:
        db.set_setting("ai_cortes", bool(payload["cortes"]))

    chave = gem.chave_guardada()
    pedido = str(payload.get("modelo") or "").strip() if "modelo" in payload else ""
    if chave and (pedido or not db.get_setting("gemini_model", "")):
        try:
            escolhido = gem.escolher_modelo(chave, pedido)
        except gem.ErroDaIA as exc:
            # Pedido explícito que não deu: o usuário tem que saber. Sem
            # pedido, a fixação do padrão é oportunista — a chave pode estar
            # errada, e quem diz isso com todas as letras é /api/ai/test, não
            # a rota que guarda. Falhar aqui faria colar uma chave ruim voltar
            # um erro sobre modelos, que não é o problema dele.
            if pedido:
                raise HTTPException(400, str(exc)) from exc
            return api_ia_config()
        if pedido and escolhido.get("trocado_de"):
            disponiveis = ", ".join(m["id"] for m in gem.listar_modelos(chave)[:8])
            raise HTTPException(
                400, f"esta chave não alcança '{pedido}'. Ela alcança: "
                     f"{disponiveis}")
        db.set_setting("gemini_model", escolhido["id"])
    return api_ia_config()


@app.get("/api/ai/modelos")
def api_ia_modelos() -> dict:
    """Os modelos que ESTA chave alcança, para a primeira tela escolher.

    A escolha do modelo estava dentro do editor — que só abre DEPOIS do
    processamento, que é justamente quem usa o modelo. Resultado: ele nunca
    era escolhido e o app caía sempre no primeiro da lista de preferência.
    O mesmo erro da chave, repetido. Agora a lista vem para a tela inicial.
    """
    from .ai import gemini as gem

    try:
        lista = gem.listar_modelos(_chave_ia())
    except gem.ErroDaIA as exc:
        raise HTTPException(400, str(exc)) from exc
    escolhido = db.get_setting("gemini_model", "") or ""
    # os que valem a pena aparecer primeiro, na ordem de preferência do app
    ordem = {m: i for i, m in enumerate(gem.PREFERIDOS)}
    lista.sort(key=lambda m: (ordem.get(m["id"], 99), m["id"]))
    return {"modelos": lista, "escolhido": escolhido,
            "padrao": gem.escolher_modelo(_chave_ia(), escolhido)["id"]}


@app.post("/api/ai/test")
def api_ia_test() -> dict:
    """Botão 'testar': diz que modelo esta chave alcança de verdade."""
    from .ai import gemini as gem

    try:
        return gem.testar_chave(_chave_ia(), db.get_setting("gemini_model", "") or "")
    except gem.ErroDaIA as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/projects/{pid}/ai/plan")
def api_ia_plan(pid: str, payload: dict = Body(...)) -> dict:
    """Pede a leitura do roteiro. Roda como JOB, nunca dentro da rota.

    Chamada de rede síncrona no laço de eventos congelaria a barra de progresso
    de TODOS os trabalhos — a exportação em andamento inclusive.
    """
    _project(pid)
    _chave_ia()
    com_anexos = bool(payload.get("anexos", True))
    return _run("ia", pid, lambda ctx: svc.plano_da_ia(svc.load(pid), ctx,
                                                       com_anexos=com_anexos))


@app.post("/api/projects/{pid}/ai/comparar")
def api_ia_comparar(pid: str, payload: dict = Body(default={})) -> dict:
    """Roda DOIS modelos no MESMO vídeo e devolve as duas listas.

    Escolher modelo por opinião é chute; por preço, bobagem (a diferença é de
    centavos por vídeo). O jeito honesto é rodar os dois no vídeo DELE e
    olhar. O teste inteiro custa menos que dez centavos.
    """
    project = _project(pid)
    _chave_ia()
    from .ai import cortes as cortes_ia, gemini

    modelos = payload.get("modelos") or ["gemini-3.7-flash", "gemini-3.1-pro"]
    words = project.analysis.get("words") or []
    if not words:
        raise HTTPException(400, "rode a edição antes: sem transcrição não há "
                                 "o que comparar")
    claps = [c for c in project.analysis.get("claps", []) if c.get("enabled")]
    ass = [a for a in project.analysis.get("whistles", [])
           if a.get("enabled", True)]
    env = project.envelope()
    saida = []
    for m in modelos[:3]:
        try:
            r = cortes_ia.decidir(_chave_ia(), str(m), words, claps, ass, env=env)
            fora = sum(t["end"] - t["start"] for t in r["takes"])
            saida.append({
                "modelo": r.get("modelo", m), "ok": True,
                "cortes": [{"texto": t["text"][:110], "motivo": t["reason"],
                            "tipo": "copy" if t["source"] == "ia_copy" else "refeito",
                            "start": t["start"], "end": t["end"]}
                           for t in r["takes"]],
                "segundos_fora": round(fora, 1),
                "recusados": r.get("recusados", []),
                "leitura": r.get("leitura", ""),
            })
        except gemini.ErroDaIA as exc:
            saida.append({"modelo": m, "ok": False, "erro": str(exc)})
    return {"ok": True, "resultados": saida}


@app.post("/api/projects/{pid}/ai/apply")
def api_ia_apply(pid: str, payload: dict = Body(...)) -> dict:
    """Aplica o que a IA sugeriu — depois de o usuário ver e concordar."""
    project = _project(pid)
    try:
        return svc.aplicar_plano_da_ia(project, payload.get("plano") or {})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/{path:path}", response_class=HTMLResponse)
def spa(path: str) -> HTMLResponse:
    if path.startswith("api/"):
        raise HTTPException(404, "rota não encontrada")
    candidate = STATIC_DIR / path
    if candidate.is_file():
        return FileResponse(candidate)
    return index()
