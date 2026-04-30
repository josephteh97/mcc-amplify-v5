"""HTTP + WebSocket endpoints for the v5.3 single-shot pipeline."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from loguru import logger

from backend.api.jobs import JOBS_ROOT, job_store, run_job
from backend.api.websocket import broadcaster
from backend.core.workspace import Workspace

router = APIRouter()


@router.post("/upload")
async def upload(files: list[UploadFile] = File(...)) -> dict:
    """Accept N PDFs, create a job + workspace, kick off the pipeline.

    PLAN.md §4: single-shot, no zip handling, the build agent must not rely on
    folder structure. We accept any flat list of .pdf uploads.
    """
    pdfs = [f for f in files if (f.filename or "").lower().endswith(".pdf")]
    if not pdfs:
        raise HTTPException(400, "Upload at least one .pdf file")

    job = job_store.create()
    ws  = Workspace.fresh(JOBS_ROOT / job.job_id)

    for f in pdfs:
        dest = ws.uploads / Path(f.filename or "unnamed.pdf").name
        with open(dest, "wb") as out:
            out.write(await f.read())

    job.file_count     = len(pdfs)
    job.workspace_root = ws.root
    logger.info(f"Job {job.job_id} created — {len(pdfs)} PDF(s) staged at {ws.uploads}")

    asyncio.create_task(run_job(job, ws, broadcaster))

    return {"job_id": job.job_id, "file_count": len(pdfs)}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job.to_status_payload()


@router.get("/jobs/{job_id}/manifest")
async def get_manifest(job_id: str):
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.status != "completed":
        raise HTTPException(409, f"Job not completed (status={job.status})")
    manifest_path = Path(job.result["manifest_path"])
    if not manifest_path.exists():
        raise HTTPException(500, "Manifest path recorded but file missing on disk")
    return FileResponse(manifest_path, media_type="application/json")


@router.get("/jobs/{job_id}/classification")
async def get_classification(job_id: str):
    """The Stage 2 classifier report (PLAN.md §5).

    Available from the moment Stage 2 finishes — accessible while the job is
    still running. Returns 404 only if the job doesn't exist or hasn't reached
    Stage 2 yet (the classifier writes the report synchronously).
    """
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")

    if job.workspace_root is None:
        raise HTTPException(404, "Classification report not available yet")
    report_path = job.workspace_root / "output" / "_classification_report.json"
    if not report_path.exists():
        raise HTTPException(404, "Classification report not available yet")
    return FileResponse(report_path, media_type="application/json")


@router.get("/jobs")
async def list_jobs() -> dict:
    return {"jobs": [j.to_status_payload() for j in job_store.all()]}


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.websocket("/ws/{job_id}")
async def ws_progress(ws: WebSocket, job_id: str) -> None:
    job = job_store.get(job_id)
    if job is None:
        await ws.close(code=4404)
        return

    backlog = list(job.events)
    if job.status == "completed" and job.result is not None:
        backlog.append({"type": "job_completed", "result": job.result})
    elif job.status == "failed":
        backlog.append({"type": "error", "message": job.error})

    await broadcaster.subscribe(job_id, ws, backlog)
    try:
        while True:
            # Server is push-only; we just hold the connection open and react
            # to client-side close. Any message from the client is ignored.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        broadcaster.unsubscribe(job_id, ws)
