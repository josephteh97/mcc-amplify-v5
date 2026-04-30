"""Stage runner per PLAN.md §2.

Builds out one stage at a time as each lands per §14:
  Step 1a — ingest only
  Step 1b — same, but driven by API + emitting events through a callback
  Step 2  — + classify
  Step 3..7 — + extract/{plan_overall, plan_enlarged, elevation, section}
  Step 8  — + reconcile
  Step 9  — + resolve (5A)
  Step 10 — + emit (5B) — RVT + GLTF
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from loguru import logger

from backend.classify.classifier import (
    ClassifiedItem,
    classify_manifest,
    summarise,
    write_report,
)
from backend.classify.rules import FilenameRule
from backend.classify.types import DrawingClass
from backend.core.meta_yaml import MetaYaml
from backend.core.workspace import Workspace
from backend.extract.plan_overall import OverallExtractResult, extract_overall
from backend.ingest.ingest import IngestedFile, ingest, walk_uploads

# Progress callback signature: (event_type, payload) -> None.
# Synchronous and side-effect-only; the API layer adapts it onto an async
# WebSocket broadcaster. None is acceptable for headless / CLI runs.
ProgressFn = Callable[[str, dict], None] | None


@dataclass
class JobResult:
    workspace:       Workspace
    manifest:        list[IngestedFile]
    classification:  list[ClassifiedItem] | None         = None
    plan_overall:    list[OverallExtractResult] | None   = None


def _emit(progress: ProgressFn, event_type: str, payload: dict) -> None:
    if progress is not None:
        progress(event_type, payload)


def run(
    workspace:           Workspace,
    walk_root:           Path | None      = None,
    meta_path:           Path | None      = None,
    progress:            ProgressFn       = None,
    run_yolo_columns:    bool             = True,
) -> JobResult:
    """One job = one upload = one output (§2). Rerun = full reprocess.

    walk_root defaults to workspace.uploads when omitted; CLI tooling can pass
    a fixture path directly to avoid staging hundreds of PDFs into the
    workspace just to walk them.
    """
    source = walk_root if walk_root is not None else workspace.uploads

    meta: MetaYaml | None = None
    if meta_path is not None and meta_path.exists():
        meta = MetaYaml.load(meta_path)
        meta.save(workspace.meta_path)
        logger.info(f"Loaded meta.yaml for project {meta.project.id!r}")

    _emit(progress, "stage_started", {"stage": "ingest", "source": str(source)})
    logger.info(f"Stage 1 — ingest: {source}")
    pdfs = walk_uploads(source)
    manifest = ingest(pdfs)
    page_total = sum(f.n_pages for f in manifest)
    summary = {"file_count": len(manifest), "page_count": page_total}
    logger.info(f"Stage 1 done — {summary['file_count']} PDF(s), {summary['page_count']} page(s)")
    _emit(progress, "stage_completed", {"stage": "ingest", **summary})

    _persist_manifest(workspace, manifest)

    _emit(progress, "stage_started", {"stage": "classify"})
    logger.info("Stage 2 — classify")
    filename_rules = _filename_rules_from_meta(meta)
    classified = classify_manifest(manifest, filename_rules=filename_rules)
    cls_summary = summarise(classified)
    write_report(classified, workspace.output / "_classification_report.json")
    logger.info(
        f"Stage 2 done — {cls_summary['total']} pages | "
        + " ".join(f"{k}={v}" for k, v in cls_summary["by_class"].items())
    )
    _emit(progress, "stage_completed", {"stage": "classify", **cls_summary})

    overall_results = _run_plan_overall(workspace, classified, progress,
                                        run_yolo=run_yolo_columns)

    return JobResult(
        workspace      = workspace,
        manifest       = manifest,
        classification = classified,
        plan_overall   = overall_results,
    )


def _run_plan_overall(
    workspace:  Workspace,
    classified: list[ClassifiedItem],
    progress:   ProgressFn,
    run_yolo:   bool = True,
) -> list[OverallExtractResult]:
    """Stage 3A-1 — grid + affine on every STRUCT_PLAN_OVERALL page (PLAN.md §3A-1).

    Stops at grid + affine for now; YOLO columns/beams/slabs land in Step 4d.
    """
    overall_items = [
        c for c in classified
        if c.result.drawing_class == DrawingClass.STRUCT_PLAN_OVERALL
    ]
    _emit(progress, "stage_started", {"stage": "extract_plan_overall", "count": len(overall_items)})
    logger.info(f"Stage 3A-1 — extract_plan_overall: {len(overall_items)} page(s)")

    out_dir = workspace.extracted / "plan_overall"
    results: list[OverallExtractResult] = []
    for c in overall_items:
        try:
            r = extract_overall(c.pdf_path, c.page_index, out_dir, run_yolo=run_yolo)
        except Exception as exc:                   # noqa: BLE001 — log + continue
            logger.exception(f"extract_overall failed on {c.pdf_path.name}: {exc}")
            results.append(OverallExtractResult(
                storey_id          = c.pdf_path.stem,
                pdf_path           = c.pdf_path,
                page_index         = c.page_index,
                has_grid           = False,
                affine_residual_px = None,
                payload_path       = None,
                error              = f"{type(exc).__name__}: {exc}",
                flags              = ["extractor_crashed"],
            ))
            continue
        results.append(r)
        logger.info(
            f"  {r.storey_id}: has_grid={r.has_grid} residual="
            f"{r.affine_residual_px if r.affine_residual_px is not None else 'n/a'}",
        )

    summary = {
        "total":            len(results),
        "with_grid":        sum(1 for r in results if r.has_grid),
        "rejected":         sum(1 for r in results if not r.has_grid),
    }
    _write_overall_report(workspace, results, summary)
    _emit(progress, "stage_completed", {"stage": "extract_plan_overall", **summary})
    logger.info(
        f"Stage 3A-1 done — {summary['total']} page(s) | "
        f"with_grid={summary['with_grid']} rejected={summary['rejected']}",
    )
    return results


def _write_overall_report(
    workspace: Workspace,
    results:   list[OverallExtractResult],
    summary:   dict,
) -> None:
    payload = {
        "summary": summary,
        "items": [
            {
                "storey_id":          r.storey_id,
                "pdf":                str(r.pdf_path),
                "page_index":         r.page_index,
                "has_grid":           r.has_grid,
                "affine_residual_px": r.affine_residual_px,
                "payload_path":       None if r.payload_path is None else str(r.payload_path),
                "error":              r.error,
                "flags":              r.flags,
            }
            for r in results
        ],
    }
    out = workspace.output / "_extract_plan_overall_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(payload, f, indent=2)


def _filename_rules_from_meta(meta: MetaYaml | None) -> list[FilenameRule] | None:
    """Promote meta.yaml.project.classifier_rules into runtime FilenameRule objects.

    Returns None when the user hasn't configured rules — the classifier then
    uses its built-in DEFAULT_FILENAME_RULES.
    """
    if meta is None or not meta.project.classifier_rules:
        return None
    out: list[FilenameRule] = []
    for r in meta.project.classifier_rules:
        try:
            cls = DrawingClass(r.cls)
        except ValueError:
            logger.warning(f"meta.yaml classifier_rule has unknown class {r.cls!r}; skipping")
            continue
        out.append(FilenameRule(pattern=r.pattern, drawing_class=cls))
    return out or None


def _persist_manifest(workspace: Workspace, manifest: list[IngestedFile]) -> None:
    """Write the ingest manifest into the workspace so the API can serve it."""
    payload = {
        "file_count": len(manifest),
        "page_count": sum(f.n_pages for f in manifest),
        "files": [
            {
                "pdf":         str(f.pdf_path),
                "n_pages":     f.n_pages,
                "page_hashes": list(f.page_hashes),
            }
            for f in manifest
        ],
    }
    out = workspace.output / "manifest.json"
    with open(out, "w") as f:
        json.dump(payload, f, indent=2)
