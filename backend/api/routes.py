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


@router.get("/jobs/{job_id}/storeys")
async def get_storeys(job_id: str) -> dict:
    """Per-storey emission status — drives the 3D viewer's selector.

    Reads ``output/_emit_report.json`` and returns just what the UI needs
    to populate a dropdown: storey id, GLTF availability + size, hard-gate
    status, and column count. Empty list (not 404) when Stage 5B hasn't
    run yet.
    """
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.workspace_root is None:
        return {"storeys": []}

    emit_path = job.workspace_root / "output" / "_emit_report.json"
    if not emit_path.exists():
        return {"storeys": []}

    try:
        emit = json.loads(emit_path.read_text())
    except Exception as exc:
        logger.warning(f"storeys: emit parse failed: {exc}")
        return {"storeys": []}

    out: list[dict] = []
    for s in emit.get("storeys", []):
        gltf_path = s.get("gltf_path")
        gltf_size = None
        if gltf_path and Path(gltf_path).exists():
            try:
                gltf_size = Path(gltf_path).stat().st_size
            except OSError:
                gltf_size = None
        out.append({
            "storey_id":   s.get("storey_id"),
            "succeeded":   s.get("succeeded"),
            "column_count": s.get("column_count"),
            "has_gltf":    gltf_size is not None,
            "gltf_bytes":  gltf_size,
        })
    # Stable order: succeeded first, then by storey_id.
    out.sort(key=lambda r: (not r["succeeded"], str(r["storey_id"] or "")))
    return {"storeys": out}


@router.get("/jobs/{job_id}/gltf/{storey_id}")
async def get_gltf(job_id: str, storey_id: str):
    """Stream a single storey's GLTF preview file.

    The path is sourced from the emit report (so a storey that didn't
    pass Stage 5B's hard gates returns 404). Storey IDs are sanitised
    against the report's known set — we never join the URL parameter
    onto the workspace path directly to avoid traversal.
    """
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.workspace_root is None:
        raise HTTPException(404, "Workspace not staged yet")

    emit_path = job.workspace_root / "output" / "_emit_report.json"
    if not emit_path.exists():
        raise HTTPException(404, "Stage 5B has not run yet")

    try:
        emit = json.loads(emit_path.read_text())
    except Exception as exc:
        raise HTTPException(500, f"Failed to read emit report: {exc}")

    gltf_path: Path | None = None
    for s in emit.get("storeys", []):
        if str(s.get("storey_id")) == storey_id and s.get("gltf_path"):
            gltf_path = Path(s["gltf_path"])
            break
    if gltf_path is None or not gltf_path.exists():
        raise HTTPException(404, f"No GLTF for storey {storey_id!r}")
    return FileResponse(gltf_path, media_type="model/gltf+json",
                        filename=f"{storey_id}.gltf")


@router.get("/jobs/{job_id}/review")
async def get_review(job_id: str):
    """Aggregated review queue for the UI (PLAN.md §11 strict-mode).

    Composes one JSON from the on-disk artefacts of stages 2 / 4 / 5A / 5B:

      classification.discarded      — DISCARD-tier pages from Stage 2
      classification.unresolved     — UNRESOLVED-tier pages from Stage 2
      reconcile.storeys[*].conflicts — label_conflict columns (Stage 4)
      reconcile.storeys[*].missing   — label_missing columns (Stage 4)
      resolve.storeys[*].rejected    — REJECTED placements (Stage 5A)
      emit.storeys[*].gates          — hard failures + warnings (Stage 5B)

    Available as soon as a job has any of these artefacts; missing
    sections come back empty rather than 404 so the frontend renders
    progressively even if late stages haven't completed.
    """
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.workspace_root is None:
        raise HTTPException(404, "Workspace not staged yet")
    return _build_review_payload(job.workspace_root)


def _build_review_payload(workspace_root: Path) -> dict:
    """Read the on-disk reports and compose the review aggregator payload."""
    out_dir       = workspace_root / "output"
    extracted_dir = workspace_root / "extracted"
    payload = {
        "classification": {"discarded": [], "unresolved": []},
        "reconcile":      {"storeys":  []},
        "resolve":        {"storeys":  []},
        "emit":           {"storeys":  []},
    }

    # ── Stage 2 — classifier DISCARD + UNRESOLVED ────────────────────────────
    cls_path = out_dir / "_classification_report.json"
    if cls_path.exists():
        try:
            cls = json.loads(cls_path.read_text())
            for it in cls.get("items", []):
                row = {
                    "pdf":         it.get("pdf"),
                    "page_index":  it.get("page_index"),
                    "tier":        it.get("tier"),
                    "confidence":  it.get("confidence"),
                    "reason":      it.get("reason"),
                }
                if it.get("class") == "DISCARD":
                    payload["classification"]["discarded"].append(row)
                if it.get("tier") == "unresolved" or it.get("class") == "UNKNOWN":
                    payload["classification"]["unresolved"].append(row)
        except Exception as exc:
            logger.warning(f"review: classification parse failed: {exc}")

    # ── Stage 4 — reconcile per storey ───────────────────────────────────────
    rec_dir = extracted_dir / "reconcile"
    if rec_dir.exists():
        for path in sorted(rec_dir.glob("*.reconciled.json")):
            try:
                d = json.loads(path.read_text())
            except Exception as exc:
                logger.warning(f"review: failed to read {path.name}: {exc}")
                continue
            cols = d.get("columns", [])
            conflicts = [
                {
                    "canonical_idx":        c.get("canonical_idx"),
                    "canonical_grid_mm_xy": c.get("canonical_grid_mm_xy"),
                    "label_candidates":     c.get("label_candidates", []),
                    "flags":                c.get("flags", []),
                }
                for c in cols
                if any(f.startswith("label_conflict") for f in (c.get("flags") or []))
            ]
            missing = [
                {
                    "canonical_idx":        c.get("canonical_idx"),
                    "canonical_grid_mm_xy": c.get("canonical_grid_mm_xy"),
                }
                for c in cols
                if "label_missing" in (c.get("flags") or [])
            ]
            payload["reconcile"]["storeys"].append({
                "storey_id": d.get("storey_id"),
                "summary":   d.get("summary", {}),
                "conflicts": conflicts,
                "missing":   missing,
            })

    # ── Stage 5A — resolve rejects (per storey review.json) ─────────────────
    if out_dir.exists():
        for path in sorted(out_dir.glob("*_review.json")):
            try:
                d = json.loads(path.read_text())
            except Exception as exc:
                logger.warning(f"review: failed to read {path.name}: {exc}")
                continue
            payload["resolve"]["storeys"].append({
                "storey_id": d.get("storey_id"),
                "summary":   d.get("summary", {}),
                "rejected":  d.get("items", []),
            })

    # ── Stage 5B — emit gate status ─────────────────────────────────────────
    emit_path = out_dir / "_emit_report.json"
    if emit_path.exists():
        try:
            emit = json.loads(emit_path.read_text())
            for s in emit.get("storeys", []):
                gates = (s.get("gates") or {}).get("gates", [])
                hard_failures = [g for g in gates if not g.get("passed") and g.get("severity") == "hard"]
                warnings      = [g for g in gates if not g.get("passed") and g.get("severity") == "warn"]
                payload["emit"]["storeys"].append({
                    "storey_id":      s.get("storey_id"),
                    "succeeded":      s.get("succeeded"),
                    "skipped_reason": s.get("skipped_reason"),
                    "hard_failures":  hard_failures,
                    "warnings":       warnings,
                    "rvt_error":      s.get("rvt_error"),
                    "rvt_path":       s.get("rvt_path"),
                    "gltf_path":      s.get("gltf_path"),
                })
        except Exception as exc:
            logger.warning(f"review: emit parse failed: {exc}")

    return payload


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
