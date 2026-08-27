"""Fila de processamento pesado com progresso por WebSocket.

Nada de travar a tela: análise, exportação e validação rodam em worker
thread e reportam progresso.
"""
from __future__ import annotations

import asyncio
import queue
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from . import db
from .config import WORKERS


@dataclass
class Job:
    id: str
    project_id: str
    kind: str
    status: str = "fila"        # fila | rodando | ok | erro | cancelado
    progress: float = 0.0
    stage: str = ""
    message: str = ""
    result: dict = field(default_factory=dict)
    error: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "project_id": self.project_id, "kind": self.kind,
            "status": self.status, "progress": round(self.progress, 4),
            "stage": self.stage, "message": self.message, "result": self.result,
            "error": self.error, "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class Hub:
    """Ponte entre as worker threads e os WebSockets (que são async)."""

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self._clients: set[Any] = set()
        self._lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    async def register(self, ws) -> None:
        with self._lock:
            self._clients.add(ws)

    async def unregister(self, ws) -> None:
        with self._lock:
            self._clients.discard(ws)

    def broadcast(self, payload: dict) -> None:
        if self.loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._send(payload), self.loop)
        except RuntimeError:
            pass

    async def _send(self, payload: dict) -> None:
        with self._lock:
            targets = list(self._clients)
        dead = []
        for ws in targets:
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        if dead:
            with self._lock:
                for ws in dead:
                    self._clients.discard(ws)


hub = Hub()


class JobQueue:
    def __init__(self, workers: int = WORKERS) -> None:
        self._q: queue.Queue = queue.Queue()
        self._jobs: dict[str, Job] = {}
        self._cancel: set[str] = set()
        self._lock = threading.Lock()
        self._threads = [
            threading.Thread(target=self._worker, daemon=True, name=f"job-{i}")
            for i in range(max(1, workers))
        ]
        for t in self._threads:
            t.start()

    # ------------------------------------------------------------- interface
    def submit(self, kind: str, project_id: str,
               fn: Callable[["JobContext"], dict]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], project_id=project_id, kind=kind)
        with self._lock:
            self._jobs[job.id] = job
        self._persist(job)
        self._emit(job)
        self._q.put((job, fn))
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, project_id: str | None = None) -> list[Job]:
        with self._lock:
            jobs = list(self._jobs.values())
        if project_id:
            jobs = [j for j in jobs if j.project_id == project_id]
        return sorted(jobs, key=lambda j: -j.created_at)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status in ("ok", "erro", "cancelado"):
                return False
            self._cancel.add(job_id)
            if job.status == "fila":
                job.status = "cancelado"
                job.message = "cancelado antes de começar"
        self._emit(job)
        return True

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancel

    # --------------------------------------------------------------- interno
    def _worker(self) -> None:
        while True:
            job, fn = self._q.get()
            if self.is_cancelled(job.id):
                job.status = "cancelado"
                self._finish(job)
                self._q.task_done()
                continue
            job.status = "rodando"
            job.updated_at = time.time()
            self._emit(job)
            ctx = JobContext(job, self)
            try:
                result = fn(ctx) or {}
                job.result = result
                job.status = "ok"
                job.progress = 1.0
                job.message = job.message or "concluído"
            except KeyboardInterrupt:
                job.status = "cancelado"
                job.message = "cancelado"
            except Exception as exc:  # noqa: BLE001
                job.status = "erro"
                job.error = f"{type(exc).__name__}: {exc}"
                job.message = str(exc)[:400]
                traceback.print_exc()
            finally:
                self._finish(job)
                self._q.task_done()

    def _finish(self, job: Job) -> None:
        job.updated_at = time.time()
        with self._lock:
            self._cancel.discard(job.id)
        self._persist(job)
        self._emit(job)

    def _persist(self, job: Job) -> None:
        try:
            db.ex(
                "INSERT INTO jobs(id, project_id, kind, status, progress, stage, "
                "message, result_json, error, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
                "progress=excluded.progress, stage=excluded.stage, "
                "message=excluded.message, result_json=excluded.result_json, "
                "error=excluded.error, updated_at=excluded.updated_at",
                (job.id, job.project_id, job.kind, job.status, job.progress,
                 job.stage, job.message, db.jdumps(job.result), job.error,
                 job.created_at, job.updated_at),
            )
        except Exception:  # noqa: BLE001
            pass

    def _emit(self, job: Job) -> None:
        hub.broadcast({"type": "job", "job": job.to_dict()})


class JobContext:
    """O que a função do job recebe para reportar progresso."""

    def __init__(self, job: Job, q: JobQueue) -> None:
        self.job = job
        self._q = q
        self._last = 0.0

    @property
    def project_id(self) -> str:
        return self.job.project_id

    def cancelled(self) -> bool:
        return self._q.is_cancelled(self.job.id)

    def check(self) -> None:
        if self.cancelled():
            raise KeyboardInterrupt("cancelado")

    def progress(self, fraction: float, message: str = "", stage: str = "") -> None:
        self.check()
        self.job.progress = max(0.0, min(1.0, float(fraction)))
        if message:
            self.job.message = message
        if stage:
            self.job.stage = stage
        self.job.updated_at = time.time()
        now = time.time()
        if now - self._last > 0.12 or fraction >= 1.0:
            self._last = now
            self._q._emit(self.job)

    def stage(self, name: str, message: str = "") -> None:
        self.job.stage = name
        self.progress(self.job.progress, message or name, name)

    def scoped(self, lo: float, hi: float, stage: str = ""):
        """Callback de progresso limitado a uma faixa."""
        def cb(fraction: float, message: str = ""):
            self.progress(lo + (hi - lo) * max(0.0, min(1.0, fraction)),
                          message, stage)
        return cb


queue_instance: JobQueue | None = None


def get_queue() -> JobQueue:
    global queue_instance
    if queue_instance is None:
        queue_instance = JobQueue()
    return queue_instance
