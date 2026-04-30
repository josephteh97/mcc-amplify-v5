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
from backend.core.grid_mm import (
    FLAG_LABEL_CONFLICT_PFX,
    FLAG_LABEL_MISSING,
    GATE_SEVERITY_HARD,
    GATE_SEVERITY_WARN,
)
from backend.core.workspace import Workspace

router = APIRouter()


def _read_json(path: Path, label: str) -> dict | None:
    """Read + parse a JSON report, returning None on missing/corrupt.

    Centralises the parse-or-warn pattern that every artefact reader on
    this module needs (one /review request can pull from six files).
    Callers decide whether ``None`` translates to 404 or empty payload.
    """
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception as exc:                          # noqa: BLE001
        logger.warning(f"failed to parse {label} at {path}: {exc}")
        return None


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

    emit = _read_json(job.workspace_root / "output" / "_emit_report.json", "emit report")
    if emit is None:
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

    emit = _read_json(job.workspace_root / "output" / "_emit_report.json", "emit report")
    if emit is None:
        raise HTTPException(404, "Stage 5B has not run yet")

    gltf_path: Path | None = None
    for s in emit.get("storeys", []):
        if str(s.get("storey_id")) == storey_id and s.get("gltf_path"):
            gltf_path = Path(s["gltf_path"])
            break
    if gltf_path is None or not gltf_path.exists():
        raise HTTPException(404, f"No GLTF for storey {storey_id!r}")
    # No `filename=` — that adds Content-Disposition: attachment which
    # makes the browser download the file instead of streaming it into
    # the in-page R3F viewer.
    return FileResponse(gltf_path, media_type="model/gltf+json")


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


def _read_classification_review(out_dir: Path) -> dict:
    discarded:  list[dict] = []
    unresolved: list[dict] = []
    cls = _read_json(out_dir / "_classification_report.json", "classification")
    if cls is None:
        return {"discarded": discarded, "unresolved": unresolved}
    for it in cls.get("items", []):
        row = {k: it.get(k) for k in ("pdf", "page_index", "tier", "confidence", "reason")}
        if it.get("class") == "DISCARD":
            discarded.append(row)
        if it.get("tier") == "unresolved" or it.get("class") == "UNKNOWN":
            unresolved.append(row)
    return {"discarded": discarded, "unresolved": unresolved}


def _read_reconcile_review(rec_dir: Path) -> list[dict]:
    if not rec_dir.exists():
        return []
    out: list[dict] = []
    for path in sorted(rec_dir.glob("*.reconciled.json")):
        d = _read_json(path, f"reconcile {path.name}")
        if d is None:
            continue
        cols = d.get("columns", [])
        conflicts = [
            {"canonical_idx":        c.get("canonical_idx"),
             "canonical_grid_mm_xy": c.get("canonical_grid_mm_xy"),
             "label_candidates":     c.get("label_candidates", []),
             "flags":                c.get("flags", [])}
            for c in cols
            if any(f.startswith(FLAG_LABEL_CONFLICT_PFX) for f in (c.get("flags") or []))
        ]
        missing = [
            {"canonical_idx":        c.get("canonical_idx"),
             "canonical_grid_mm_xy": c.get("canonical_grid_mm_xy")}
            for c in cols
            if FLAG_LABEL_MISSING in (c.get("flags") or [])
        ]
        out.append({
            "storey_id": d.get("storey_id"),
            "summary":   d.get("summary", {}),
            "conflicts": conflicts,
            "missing":   missing,
        })
    return out


def _read_resolve_review(out_dir: Path) -> list[dict]:
    if not out_dir.exists():
        return []
    out: list[dict] = []
    for path in sorted(out_dir.glob("*_review.json")):
        d = _read_json(path, f"resolve {path.name}")
        if d is None:
            continue
        out.append({
            "storey_id": d.get("storey_id"),
            "summary":   d.get("summary", {}),
            "rejected":  d.get("items", []),
        })
    return out


def _read_emit_review(out_dir: Path) -> list[dict]:
    emit = _read_json(out_dir / "_emit_report.json", "emit report")
    if emit is None:
        return []
    out: list[dict] = []
    for s in emit.get("storeys", []):
        gates = (s.get("gates") or {}).get("gates", [])
        hard_failures = [g for g in gates if not g.get("passed") and g.get("severity") == GATE_SEVERITY_HARD]
        warnings      = [g for g in gates if not g.get("passed") and g.get("severity") == GATE_SEVERITY_WARN]
        out.append({
            "storey_id":      s.get("storey_id"),
            "succeeded":      s.get("succeeded"),
            "skipped_reason": s.get("skipped_reason"),
            "hard_failures":  hard_failures,
            "warnings":       warnings,
            "rvt_error":      s.get("rvt_error"),
            "rvt_path":       s.get("rvt_path"),
            "gltf_path":      s.get("gltf_path"),
        })
    return out


def _build_review_payload(workspace_root: Path) -> dict:
    """Read the on-disk reports and compose the review aggregator payload."""
    out_dir       = workspace_root / "output"
    extracted_dir = workspace_root / "extracted"
    return {
        "classification": _read_classification_review(out_dir),
        "reconcile":      {"storeys": _read_reconcile_review(extracted_dir / "reconcile")},
        "resolve":        {"storeys": _read_resolve_review(out_dir)},
        "emit":           {"storeys": _read_emit_review(out_dir)},
    }


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
