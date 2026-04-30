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
from backend.extract.elevation     import ElevationExtractResult, extract_elevation
from backend.extract.plan_enlarged import EnlargedExtractResult,  extract_enlarged
from backend.extract.plan_overall  import OverallExtractResult,   extract_overall
from backend.extract.section       import SectionExtractResult,   extract_section
from backend.reconcile             import (
    ProjectReconcileResult,
    StoreyReconcileResult,
    reconcile_project,
    reconcile_storey,
)
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
    plan_overall:    list[OverallExtractResult] | None    = None
    plan_enlarged:   list[EnlargedExtractResult] | None   = None
    elevation:       list[ElevationExtractResult] | None  = None
    section:         list[SectionExtractResult] | None    = None
    reconcile_storeys:  list[StoreyReconcileResult] | None  = None
    reconcile_project_: ProjectReconcileResult | None      = None


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

    enlarged_results = _run_plan_enlarged(workspace, classified, progress,
                                          run_yolo=run_yolo_columns)

    elevation_results = _run_elevation(workspace, classified, progress)

    section_results = _run_section(workspace, classified, progress)

    storey_results, project_result = _run_reconcile(
        workspace,
        overall_results, enlarged_results, elevation_results, section_results,
        meta, progress,
    )

    return JobResult(
        workspace          = workspace,
        manifest           = manifest,
        classification     = classified,
        plan_overall       = overall_results,
        plan_enlarged      = enlarged_results,
        elevation          = elevation_results,
        section            = section_results,
        reconcile_storeys  = storey_results,
        reconcile_project_ = project_result,
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


def _run_reconcile(
    workspace:          Workspace,
    overall_results:    list[OverallExtractResult],
    enlarged_results:   list[EnlargedExtractResult],
    elevation_results:  list[ElevationExtractResult],
    section_results:    list[SectionExtractResult],
    meta:               MetaYaml | None,
    progress:           ProgressFn,
) -> tuple[list[StoreyReconcileResult], ProjectReconcileResult | None]:
    """Stage 4 — Reconcile (PLAN.md §7).

    Per-storey: cross-link -00 canonical columns with -01..04 type/dim
    labels. Per-project: merge elevation levels, build slab fallback map.
    """
    out_dir = workspace.extracted / "reconcile"

    # Group enlarged extracts by storey_id and pair them with their overall.
    enlarged_by_storey: dict[str, list[Path]] = {}
    for er in enlarged_results:
        if er.payload_path is None:
            continue
        enlarged_by_storey.setdefault(er.storey_id, []).append(er.payload_path)

    storey_jobs: list[tuple[str, Path, list[Path]]] = []
    for orr in overall_results:
        if orr.payload_path is None:
            continue
        storey_jobs.append((
            orr.storey_id,
            orr.payload_path,
            sorted(enlarged_by_storey.get(orr.storey_id, [])),
        ))

    _emit(progress, "stage_started", {"stage": "reconcile", "storey_count": len(storey_jobs)})
    logger.info(f"Stage 4 — reconcile: {len(storey_jobs)} storey(s)")

    storey_results: list[StoreyReconcileResult] = []
    for storey_id, overall_path, enlarged_paths in storey_jobs:
        try:
            r = reconcile_storey(overall_path, enlarged_paths, out_dir)
        except Exception as exc:                       # noqa: BLE001
            logger.exception(f"reconcile_storey failed on {storey_id}: {exc}")
            continue
        storey_results.append(r)

    elev_payloads = [er.payload_path for er in elevation_results if er.payload_path is not None]
    sect_payloads = [sr.payload_path for sr in section_results   if sr.payload_path is not None]

    project_result: ProjectReconcileResult | None = None
    try:
        project_result = reconcile_project(elev_payloads, sect_payloads, out_dir, meta=meta)
    except Exception as exc:                           # noqa: BLE001
        logger.exception(f"reconcile_project failed: {exc}")

    summary = {
        "storey_count":         len(storey_results),
        "labelled_columns":     sum(
            sum(1 for c in r.columns if c.label) for r in storey_results
        ),
        "label_missing":        sum(
            sum(1 for c in r.columns if "label_missing" in c.flags) for r in storey_results
        ),
        "label_conflicts":      sum(
            sum(1 for c in r.columns if any(f.startswith("label_conflict") for f in c.flags))
            for r in storey_results
        ),
        "level_count":          0 if project_result is None else len(project_result.levels),
        "section_id_count":     0 if project_result is None else len(project_result.slabs["section_ids"]),
    }
    _write_reconcile_report(workspace, storey_results, project_result, summary)
    _emit(progress, "stage_completed", {"stage": "reconcile", **summary})
    logger.info(
        f"Stage 4 done — storeys={summary['storey_count']} "
        f"labelled_columns={summary['labelled_columns']} "
        f"missing={summary['label_missing']} conflicts={summary['label_conflicts']}"
    )
    return storey_results, project_result


def _write_reconcile_report(
    workspace:       Workspace,
    storey_results:  list[StoreyReconcileResult],
    project_result:  ProjectReconcileResult | None,
    summary:         dict,
) -> None:
    payload = {
        "summary": summary,
        "storeys": [
            {
                "storey_id":       r.storey_id,
                "overall_path":    str(r.overall_path),
                "enlarged_paths":  [str(p) for p in r.enlarged_paths],
                "payload_path":    None if r.payload_path is None else str(r.payload_path),
                "column_count":    len(r.columns),
                "labelled":        sum(1 for c in r.columns if c.label),
                "label_missing":   sum(1 for c in r.columns if "label_missing" in c.flags),
                "label_conflicts": sum(1 for c in r.columns
                                       if any(f.startswith("label_conflict") for f in c.flags)),
                "flags":           r.flags,
            }
            for r in storey_results
        ],
        "project": (
            None if project_result is None else {
                "payload_path": None if project_result.payload_path is None
                                else str(project_result.payload_path),
                "level_count":  len(project_result.levels),
                "slab_section_ids": project_result.slabs.get("section_ids", []),
                "flags":        project_result.flags,
            }
        ),
    }
    out = workspace.output / "_reconcile_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(payload, f, indent=2)


def _run_section(
    workspace:  Workspace,
    classified: list[ClassifiedItem],
    progress:   ProgressFn,
) -> list[SectionExtractResult]:
    """Stage 3C — deferred-stub section extractor (PLAN.md §3C).

    Per-PDF, dedupe on pdf_path. Always emits a stub payload —
    Stage 5B falls back to ``meta.yaml.slabs.default_thickness_mm``
    per PLAN §17.
    """
    seen: set[Path] = set()
    pdfs: list[Path] = []
    for c in classified:
        if c.result.drawing_class != DrawingClass.SECTION:
            continue
        if c.pdf_path in seen:
            continue
        seen.add(c.pdf_path)
        pdfs.append(c.pdf_path)

    _emit(progress, "stage_started", {"stage": "extract_section", "count": len(pdfs)})
    logger.info(f"Stage 3C — extract_section: {len(pdfs)} PDF(s)")

    out_dir = workspace.extracted / "section"
    results: list[SectionExtractResult] = []
    for pdf in pdfs:
        try:
            r = extract_section(pdf, out_dir)
        except Exception as exc:                       # noqa: BLE001
            logger.exception(f"extract_section failed on {pdf.name}: {exc}")
            results.append(SectionExtractResult(
                pdf_path       = pdf,
                pdf_stem       = pdf.stem,
                page_count     = 0,
                section_ids    = [],
                thickness_hits = 0,
                payload_path   = None,
                error          = f"{type(exc).__name__}: {exc}",
                flags          = ["extractor_crashed"],
            ))
            continue
        results.append(r)
        logger.info(
            f"  {r.pdf_stem}: section_ids={r.section_ids} thickness_hits={r.thickness_hits}",
        )

    summary = {
        "total":              len(results),
        "total_section_ids":  sum(len(r.section_ids) for r in results),
        "total_thickness_hits": sum(r.thickness_hits for r in results),
    }
    _write_section_report(workspace, results, summary)
    _emit(progress, "stage_completed", {"stage": "extract_section", **summary})
    logger.info(
        f"Stage 3C done — {summary['total']} PDF(s) | "
        f"section_ids={summary['total_section_ids']} thickness_hits={summary['total_thickness_hits']}",
    )
    return results


def _write_section_report(
    workspace: Workspace,
    results:   list[SectionExtractResult],
    summary:   dict,
) -> None:
    payload = {
        "summary": summary,
        "items": [
            {
                "pdf":             str(r.pdf_path),
                "pdf_stem":        r.pdf_stem,
                "page_count":      r.page_count,
                "section_ids":     r.section_ids,
                "thickness_hits":  r.thickness_hits,
                "payload_path":    None if r.payload_path is None else str(r.payload_path),
                "error":           r.error,
                "flags":           r.flags,
            }
            for r in results
        ],
    }
    out = workspace.output / "_extract_section_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(payload, f, indent=2)


def _run_elevation(
    workspace:  Workspace,
    classified: list[ClassifiedItem],
    progress:   ProgressFn,
) -> list[ElevationExtractResult]:
    """Stage 3B — RL-only elevation extraction (PLAN.md §3B).

    Per-PDF, not per-page: extract_elevation iterates every page of one
    elevation PDF internally. We dedupe on pdf_path so a multi-page
    elevation set runs once per file.
    """
    seen: set[Path] = set()
    pdfs: list[Path] = []
    for c in classified:
        if c.result.drawing_class != DrawingClass.ELEVATION:
            continue
        if c.pdf_path in seen:
            continue
        seen.add(c.pdf_path)
        pdfs.append(c.pdf_path)

    _emit(progress, "stage_started", {"stage": "extract_elevation", "count": len(pdfs)})
    logger.info(f"Stage 3B — extract_elevation: {len(pdfs)} PDF(s)")

    out_dir = workspace.extracted / "elevation"
    results: list[ElevationExtractResult] = []
    for pdf in pdfs:
        try:
            r = extract_elevation(pdf, out_dir)
        except Exception as exc:                       # noqa: BLE001
            logger.exception(f"extract_elevation failed on {pdf.name}: {exc}")
            results.append(ElevationExtractResult(
                pdf_path     = pdf,
                pdf_stem     = pdf.stem,
                page_count   = 0,
                level_count  = 0,
                payload_path = None,
                error        = f"{type(exc).__name__}: {exc}",
                flags        = ["extractor_crashed"],
            ))
            continue
        results.append(r)
        logger.info(f"  {r.pdf_stem}: levels={r.level_count}  flags={len(r.flags)}")

    summary = {
        "total":         len(results),
        "total_levels":  sum(r.level_count for r in results),
        "with_flags":    sum(1 for r in results if r.flags),
    }
    _write_elevation_report(workspace, results, summary)
    _emit(progress, "stage_completed", {"stage": "extract_elevation", **summary})
    logger.info(
        f"Stage 3B done — {summary['total']} PDF(s) | "
        f"levels={summary['total_levels']} with_flags={summary['with_flags']}",
    )
    return results


def _write_elevation_report(
    workspace: Workspace,
    results:   list[ElevationExtractResult],
    summary:   dict,
) -> None:
    payload = {
        "summary": summary,
        "items": [
            {
                "pdf":          str(r.pdf_path),
                "pdf_stem":     r.pdf_stem,
                "page_count":   r.page_count,
                "level_count":  r.level_count,
                "payload_path": None if r.payload_path is None else str(r.payload_path),
                "error":        r.error,
                "flags":        r.flags,
            }
            for r in results
        ],
    }
    out = workspace.output / "_extract_elevation_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(payload, f, indent=2)


def _run_plan_enlarged(
    workspace:  Workspace,
    classified: list[ClassifiedItem],
    progress:   ProgressFn,
    run_yolo:   bool = True,
) -> list[EnlargedExtractResult]:
    """Stage 3A-2 — type/dim/shape on every STRUCT_PLAN_ENLARGED page (PLAN.md §3A-2).

    Page-local grid-mm; Stage 8 (Reconcile) translates to the global -00 grid.
    """
    items = [
        c for c in classified
        if c.result.drawing_class == DrawingClass.STRUCT_PLAN_ENLARGED
    ]
    _emit(progress, "stage_started", {"stage": "extract_plan_enlarged", "count": len(items)})
    logger.info(f"Stage 3A-2 — extract_plan_enlarged: {len(items)} page(s)")

    out_dir = workspace.extracted / "plan_enlarged"
    results: list[EnlargedExtractResult] = []
    for c in items:
        try:
            r = extract_enlarged(c.pdf_path, c.page_index, out_dir, run_yolo=run_yolo)
        except Exception as exc:                       # noqa: BLE001
            logger.exception(f"extract_enlarged failed on {c.pdf_path.name}: {exc}")
            results.append(EnlargedExtractResult(
                storey_id          = c.pdf_path.stem,
                page_number        = 0,
                page_region        = "unknown",
                pdf_path           = c.pdf_path,
                page_index         = c.page_index,
                has_grid           = False,
                affine_residual_px = None,
                column_count       = 0,
                payload_path       = None,
                error              = f"{type(exc).__name__}: {exc}",
                flags              = ["extractor_crashed"],
            ))
            continue
        results.append(r)
        logger.info(
            f"  {r.storey_id}-{r.page_number:02d} ({r.page_region}): "
            f"has_grid={r.has_grid} columns={r.column_count}",
        )

    summary = {
        "total":                 len(results),
        "with_grid":             sum(1 for r in results if r.has_grid),
        "rejected":              sum(1 for r in results if not r.has_grid),
        "total_columns":         sum(r.column_count for r in results),
    }
    _write_enlarged_report(workspace, results, summary)
    _emit(progress, "stage_completed", {"stage": "extract_plan_enlarged", **summary})
    logger.info(
        f"Stage 3A-2 done — {summary['total']} page(s) | "
        f"with_grid={summary['with_grid']} columns={summary['total_columns']}",
    )
    return results


def _write_enlarged_report(
    workspace: Workspace,
    results:   list[EnlargedExtractResult],
    summary:   dict,
) -> None:
    payload = {
        "summary": summary,
        "items": [
            {
                "storey_id":          r.storey_id,
                "page_number":        r.page_number,
                "page_region":        r.page_region,
                "pdf":                str(r.pdf_path),
                "page_index":         r.page_index,
                "has_grid":           r.has_grid,
                "affine_residual_px": r.affine_residual_px,
                "column_count":       r.column_count,
                "payload_path":       None if r.payload_path is None else str(r.payload_path),
                "error":              r.error,
                "flags":              r.flags,
            }
            for r in results
        ],
    }
    out = workspace.output / "_extract_plan_enlarged_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(payload, f, indent=2)


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
