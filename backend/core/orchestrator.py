"""Stage runner per PLAN.md §2.

Builds out one stage at a time as each lands per §14:
  Step 1a (current) — ingest only
  Step 2            — + classify
  Step 3..7         — + extract/{plan_overall, plan_enlarged, elevation, section}
  Step 8            — + reconcile
  Step 9            — + resolve (5A)
  Step 10           — + emit (5B) — RVT + GLTF
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from backend.core.meta_yaml import MetaYaml
from backend.core.workspace import Workspace
from backend.ingest.ingest import IngestedFile, ingest, walk_uploads


@dataclass
class JobResult:
    workspace: Workspace
    manifest:  list[IngestedFile]


def run(upload_root: Path, workspace_root: Path, meta_path: Path | None = None) -> JobResult:
    """One job = one upload = one output (§2). Rerun = full reprocess."""
    ws = Workspace.fresh(workspace_root)

    if meta_path is not None and meta_path.exists():
        meta = MetaYaml.load(meta_path)
        meta.save(ws.meta_path)
        logger.info(f"Loaded meta.yaml for project {meta.project.id!r}")
    else:
        logger.info("No meta.yaml provided; using built-in defaults")

    logger.info(f"Stage 1 — ingest: {upload_root}")
    pdfs = walk_uploads(upload_root)
    manifest = ingest(pdfs)
    page_total = sum(f.n_pages for f in manifest)
    logger.info(f"Stage 1 done — {len(manifest)} PDF(s), {page_total} page(s)")

    return JobResult(workspace=ws, manifest=manifest)
