"""Per-page extractor for STRUCT_PLAN_OVERALL (PLAN.md §3A-1).

Glue between detector.py and affine.py:
  1. Open the PDF page.
  2. detect_grid → GridResult (text-based, perimeter-band filtered).
  3. solve_affine → Affine2D, gated at residual ≤ 1 px (PLAN.md §3A-1).
  4. Emit a JSON-serialisable payload matching the §3A-1 schema.

YOLO column / framing detection lands in Step 4d as an additive layer; for
now the columns/beams/slabs lists are emitted empty with a `flags` entry
recording that they're not yet populated.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # type: ignore[import-untyped]
from loguru import logger

from backend.extract.plan_overall.affine       import Affine2D, AffineSolveError, solve_affine
from backend.extract.plan_overall.detector     import GridResult, detect_grid
from backend.extract.plan_overall.yolo_columns import ColumnDetection, detect_columns


# Filename pattern: TGCH-TD-S-200-{storey}-00 (PLAN.md §3.1 / §5.2).
# Storey tokens observed: B3, B2, B1, L1..L9, RF, UR.
_STOREY_RE = re.compile(r"-(B\d|L\d+|RF|UR|MEZZ|GF|GL)-0[0-4]\b", re.IGNORECASE)


@dataclass
class OverallExtractResult:
    storey_id:           str
    pdf_path:            Path
    page_index:          int
    has_grid:            bool
    affine_residual_px:  float | None
    payload_path:        Path | None
    error:               str | None             = None
    flags:               list[str]              = field(default_factory=list)


def storey_id_from_filename(name: str) -> str:
    """Extract the storey token (e.g. 'L3') from a STRUCT_PLAN_OVERALL filename.

    Falls back to the filename stem when no canonical match is found — the
    payload still names the file but the storey id will be the raw stem.
    """
    m = _STOREY_RE.search(name)
    if m:
        return m.group(1).upper()
    return Path(name).stem


def _grid_payload(grid: GridResult) -> dict:
    x_axes: list[dict] = []
    cum = 0.0
    for i, lbl in enumerate(grid.x_labels):
        x_axes.append({"label": lbl, "mm": round(cum, 3)})
        if i < len(grid.x_spacings_mm):
            cum += grid.x_spacings_mm[i]
    y_axes: list[dict] = []
    cum = 0.0
    for i, lbl in enumerate(grid.y_labels):
        y_axes.append({"label": lbl, "mm": round(cum, 3)})
        if i < len(grid.y_spacings_mm):
            cum += grid.y_spacings_mm[i]
    return {"x_axes": x_axes, "y_axes": y_axes}


def _columns_payload(detections: list[ColumnDetection]) -> list[dict]:
    return [
        {
            "bbox_grid_mm":   list(d.bbox_grid_mm),
            "centre_grid_mm": list(d.centre_grid_mm),
            "aspect":         round(d.aspect, 4),
            "confidence":     round(d.confidence, 4),
            "bbox_px":        list(d.bbox_px),
        }
        for d in detections
    ]


def _build_payload(
    storey_id:  str,
    pdf_path:   Path,
    page_index: int,
    grid:       GridResult,
    affine:     Affine2D | None,
    columns:    list[ColumnDetection],
    flags:      list[str],
) -> dict:
    return {
        "storey_id":          storey_id,
        "source_pdf":         pdf_path.name,
        "page_index":         page_index,
        "page_rotation":      grid.page_rotation,
        "image":              {"width_px": grid.img_w_px, "height_px": grid.img_h_px, "dpi": grid.dpi},
        "grid":               _grid_payload(grid),
        "x_spacings_mm":      list(grid.x_spacings_mm),
        "y_spacings_mm":      list(grid.y_spacings_mm),
        "affine":             None if affine is None else {
            "x":           {"slope_px_per_mm": affine.x_axis.slope_px_per_mm,
                            "intercept_px":    affine.x_axis.intercept_px,
                            "residual_px":     affine.x_axis.residual_px},
            "y":           {"slope_px_per_mm": affine.y_axis.slope_px_per_mm,
                            "intercept_px":    affine.y_axis.intercept_px,
                            "residual_px":     affine.y_axis.residual_px},
        },
        "affine_residual_px": None if affine is None else affine.residual_px,
        "columns_canonical":  _columns_payload(columns),
        "beams_canonical":    [],
        "slabs_canonical":    [],
        "flags":              flags,
        "detector_notes":     list(grid.notes),
    }


def extract_overall(
    pdf_path:    Path,
    page_index:  int,
    out_dir:     Path,
    run_yolo:    bool = True,
) -> OverallExtractResult:
    """Run grid detection + affine solve + YOLO columns on one OVERALL page.

    Always writes a payload file under ``out_dir/<storey>.overall.json`` so
    downstream stages have something to introspect even when the grid is
    rejected. A failure surfaces as `has_grid=False` + non-empty flags.
    YOLO is gated on `run_yolo` (tests can disable to keep runtime tight)
    and additionally short-circuits when the affine is rejected — without
    a valid pixel→mm transform a column bbox in mm has no meaning.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    storey_id = storey_id_from_filename(pdf_path.name)
    flags: list[str] = []
    affine: Affine2D | None = None
    columns: list[ColumnDetection] = []

    with fitz.open(pdf_path) as doc:
        if page_index >= doc.page_count:
            raise ValueError(
                f"{pdf_path.name}: page_index {page_index} out of range (n_pages={doc.page_count})",
            )
        page = doc[page_index]
        grid = detect_grid(page)

        if grid.has_grid:
            try:
                affine = solve_affine(grid)
            except AffineSolveError as exc:
                flags.append(f"affine_rejected: {exc}")
                logger.warning(f"{pdf_path.name}: {exc}")
        else:
            flags.append("grid_not_detected")

        if not run_yolo:
            flags.append("yolo_columns_skipped")
        elif affine is None:
            flags.append("yolo_columns_skipped_no_affine")
        else:
            crashed = False
            try:
                columns = detect_columns(page, affine, dpi=grid.dpi)
            except Exception as exc:                       # noqa: BLE001
                logger.exception(f"{pdf_path.name}: YOLO column step crashed: {exc}")
                flags.append(f"yolo_columns_crashed: {type(exc).__name__}")
                crashed = True
            if not crashed and not columns:
                # Empty result still warrants a flag — could be missing weight,
                # missing ultralytics, or genuinely zero columns. yolo_columns.py
                # already logs the cause; record the disposition for review.
                flags.append("yolo_columns_empty")

    payload = _build_payload(
        storey_id  = storey_id,
        pdf_path   = pdf_path,
        page_index = page_index,
        grid       = grid,
        affine     = affine,
        columns    = columns,
        flags      = flags,
    )
    payload_path = out_dir / f"{storey_id}.overall.json"
    with open(payload_path, "w") as f:
        json.dump(payload, f, indent=2)

    return OverallExtractResult(
        storey_id          = storey_id,
        pdf_path           = pdf_path,
        page_index         = page_index,
        has_grid           = grid.has_grid and affine is not None,
        affine_residual_px = None if affine is None else affine.residual_px,
        payload_path       = payload_path,
        error              = None if affine is not None else flags[0],
        flags              = flags,
    )
