"""In-memory job registry + the background runner.

Single-shot model (PLAN.md §2): one upload = one job = one workspace under
data/jobs/<job_id>/. Job state is in-memory; the workspace on disk is the
durable record. Server restart drops in-flight jobs but completed workspaces
remain recoverable via the filesystem.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from backend.core.orchestrator import run as run_pipeline
from backend.core.workspace import Workspace
from backend.emit.revit_client import RevitClient

# Bound concurrent pipeline runs so a flood of uploads can't OOM the server.
# Tunable via env; default 3 matches what v4 settled on empirically.
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "3"))
_PIPELINE_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_JOBS)

JOBS_ROOT = Path(os.getenv("JOBS_ROOT", "data/jobs"))


def _build_revit_client() -> RevitClient | None:
    """Construct a RevitClient if the Windows-side env vars are set.

    The pipeline runs perfectly headless without one — Stage 5B always
    writes ``output/<storey>_transaction.json`` so an operator can hand-
    feed the recipe to Revit later. We only construct a client when the
    user has *opted in* by setting WINDOWS_REVIT_SERVER (HTTP mode) or
    REVIT_MODE=file + REVIT_SHARED_DIR (file-drop mode). Set
    REVIT_AUTOBUILD=0 to disable even when those vars are present
    (useful for dry-run jobs).
    """
    if os.getenv("REVIT_AUTOBUILD", "1").lower() in ("0", "false", "no"):
        return None
    mode = os.getenv("REVIT_MODE", "http").lower()
    if mode == "file":
        if not os.getenv("REVIT_SHARED_DIR"):
            return None
        client = RevitClient()
        logger.info(f"Revit client (file-drop) wired → {client.shared_dir}")
        return client
    # http mode — only opt in when the user explicitly set the server URL.
    server_url = os.getenv("WINDOWS_REVIT_SERVER")
    if not server_url:
        return None
    client = RevitClient(server_url=server_url)
    logger.info(f"Revit client (http) wired → {server_url}")
    return client


@dataclass
class JobRecord:
    job_id:         str
    status:         str             = "pending"   # pending | running | completed | failed
    created_at:     float           = field(default_factory=time.time)
    workspace_root: Path | None     = None
    file_count:     int             = 0
    page_count:     int             = 0
    error:          str | None      = None
    events:         list[dict]      = field(default_factory=list)
    result:         dict | None     = None

    def to_status_payload(self) -> dict:
        return {
            "job_id":         self.job_id,
            "status":         self.status,
            "created_at":     self.created_at,
            "workspace_root": str(self.workspace_root) if self.workspace_root else None,
            "file_count":     self.file_count,
            "page_count":     self.page_count,
            "error":          self.error,
            "result":         self.result,
        }


class JobStore:
    """Process-wide registry. Adequate for a single-server deployment."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}

    def create(self) -> JobRecord:
        job = JobRecord(job_id=str(uuid.uuid4()))
        self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> JobRecord | None:
        return self._jobs.get(job_id)

    def all(self) -> list[JobRecord]:
        return list(self._jobs.values())


# Module-level singleton — request handlers and the WS broadcaster share this.
job_store = JobStore()


async def run_job(
    job: JobRecord,
    workspace: Workspace,
    broadcast: "EventBroadcaster",
) -> None:
    """Run the pipeline for one job. Emits events via the broadcaster.

    The pipeline itself is synchronous (CPU-bound PDF parsing); we run it in
    a thread so the event loop stays free to push WS messages. Each emit
    appends to the job's event log AND fans out to live WS subscribers.
    """

    async with _PIPELINE_SEMAPHORE:
        loop = asyncio.get_running_loop()

        def on_progress(event_type: str, payload: dict) -> None:
            event = {"type": event_type, "ts": time.time(), **payload}
            job.events.append(event)
            asyncio.run_coroutine_threadsafe(broadcast.send(job.job_id, event), loop)

        try:
            job.status = "running"
            await broadcast.send(job.job_id, {
                "type": "job_started",
                "ts":   time.time(),
                "job_id": job.job_id,
            })

            revit_client = _build_revit_client()
            result = await asyncio.to_thread(
                run_pipeline,
                workspace    = workspace,
                progress     = on_progress,
                revit_client = revit_client,
            )

            job.file_count = len(result.manifest)
            job.page_count = sum(f.n_pages for f in result.manifest)
            job.status     = "completed"
            job.result     = {
                "workspace":     str(workspace.root),
                "manifest_path": str(workspace.output / "manifest.json"),
                "file_count":    job.file_count,
                "page_count":    job.page_count,
            }
            await broadcast.send(job.job_id, {
                "type":   "job_completed",
                "ts":     time.time(),
                "job_id": job.job_id,
                "result": job.result,
            })
            logger.info(f"Job {job.job_id} completed — {job.file_count} files / {job.page_count} pages")

        except Exception as exc:
            job.status = "failed"
            job.error  = f"{type(exc).__name__}: {exc}"
            logger.exception(f"Job {job.job_id} failed")
            await broadcast.send(job.job_id, {
                "type":    "error",
                "ts":      time.time(),
                "job_id":  job.job_id,
                "message": job.error,
            })


# Forward-declared to avoid a circular import; resolved at runtime.
class EventBroadcaster:  # pragma: no cover  (real implementation in websocket.py)
    async def send(self, job_id: str, event: dict[str, Any]) -> None: ...
