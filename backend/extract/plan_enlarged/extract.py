"""Per-page extractor for STRUCT_PLAN_ENLARGED (PLAN.md §3A-2).

Pipeline for one ``-01..04`` page:

  1. Detect grid bubbles (reuse Step 4's detector — enlarged pages have the
     same vector-text bubbles, just on the quadrant subset).
  2. Solve a per-page pixel→grid-mm affine. Page-local mm: grid_mm origin is
     each page's first V-line / first H-line. Stage 8 (Reconcile, PLAN §4)
     translates these into the global -00 grid.
  3. YOLO column detection + extract vector text labels.
  4. Associate each YOLO bbox with its type/dim labels.
  5. Apply the per-element X×Y vs swap orientation algorithm (PLAN §3A-2,
     §11 strict-mode — never coerce; flag `orientation_ambiguous` for the
     Stage 5A LLM checker.)
  6. Emit ``extracted/plan_enlarged/<storey>-<page>.enlarged.json``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # type: ignore[import-untyped]
from loguru import logger

from backend.core.grid_mm                          import PAGE_REGION_MAP
from backend.extract.plan_enlarged.associator      import (
    AssociatedColumn,
    associate_columns,
)
from backend.extract.plan_enlarged.labels          import extract_labels
from backend.extract.plan_overall.affine           import (
    Affine2D,
    AffineSolveError,
    solve_affine,
)
from backend.extract.plan_overall.detector         import detect_grid
from backend.extract.plan_overall.yolo_columns     import detect_columns


# Filename: TGCH-TD-S-200-{storey}-{page} where page ∈ {01..04} for enlarged
_STOREY_RE = re.compile(r"-(B\d|L\d+|RF|UR|MEZZ|GF|GL)-(0[1-4])\b", re.IGNORECASE)


@dataclass
class EnlargedExtractResult:
    storey_id:           str
    page_number:         int                     # 1..4
    page_region:         str                     # PAGE_REGION_MAP value
    pdf_path:            Path
    page_index:          int
    has_grid:            bool
    affine_residual_px:  float | None
    column_count:        int
    payload_path:        Path | None
    error:               str | None              = None
    flags:               list[str]               = field(default_factory=list)


def parse_filename(name: str) -> tuple[str, int]:
    """Return (storey_id, page_number 1..4) from an enlarged-plan filename.

    Falls back to (stem, 0) when the filename doesn't match — the orchestrator
    will still try the page but page_region won't be reliable.
    """
    m = _STOREY_RE.search(name)
    if not m:
        return Path(name).stem, 0
    return m.group(1).upper(), int(m.group(2))


def _column_payload(
    col:    AssociatedColumn,
    affine: Affine2D | None,
    page_id:     int,
    page_region: str,
) -> dict:
    if affine is not None:
        x_mm0, y_mm0 = affine.px_to_mm(col.bbox_px[0], col.bbox_px[1])
        x_mm1, y_mm1 = affine.px_to_mm(col.bbox_px[2], col.bbox_px[3])
        bbox_grid_mm = [
            min(x_mm0, x_mm1), min(y_mm0, y_mm1),
            max(x_mm0, x_mm1), max(y_mm0, y_mm1),
        ]
        cx, cy = affine.px_to_mm(col.centre_px[0], col.centre_px[1])
        grid_mm_xy = [cx, cy]
    else:
        bbox_grid_mm = None
        grid_mm_xy   = None

    return {
        "type":           "column",
        "label":          col.label,
        "is_steel":       col.is_steel,
        "shape":          col.shape,
        "dim_along_x_mm": col.dim_along_x_mm,
        "dim_along_y_mm": col.dim_along_y_mm,
        "diameter_mm":    col.diameter_mm,
        "bbox_grid_mm":   bbox_grid_mm,
        "grid_mm_xy":     grid_mm_xy,
        "page_id":        page_id,
        "page_region":    page_region,
        "yolo_confidence": round(col.yolo_confidence, 4),
        "yolo_aspect":     round(col.yolo_aspect, 4),
        "bbox_px":         list(col.bbox_px),
        "orientation":     None if col.orientation is None else {
            "verdict":  col.orientation.verdict.value,
            "err_xy":   round(col.orientation.err_xy,   4),
            "err_swap": round(col.orientation.err_swap, 4),
            "notes":    col.orientation.notes,
        },
        "type_label_text": None if col.type_label is None else col.type_label.text,
        "dim_label_text":  None if col.dim_label  is None else col.dim_label.text,
        "flags":           col.flags,
    }


def _build_payload(
    storey_id:    str,
    page_number:  int,
    page_region:  str,
    pdf_path:     Path,
    page_index:   int,
    grid:         dict,
    affine:       Affine2D | None,
    columns:      list[AssociatedColumn],
    label_count_by_kind: dict,
    flags:        list[str],
) -> dict:
    return {
        "storey_id":          storey_id,
        "page_number":        page_number,
        "page_region":        page_region,
        "source_pdf":         pdf_path.name,
        "page_index":         page_index,
        "page_rotation":      grid["page_rotation"],
        "image":              grid["image"],
        "grid":               grid["grid"],
        "x_spacings_mm":      grid["x_spacings_mm"],
        "y_spacings_mm":      grid["y_spacings_mm"],
        "affine":             None if affine is None else {
            "x":           {"slope_px_per_mm": affine.x_axis.slope_px_per_mm,
                            "intercept_px":    affine.x_axis.intercept_px,
                            "residual_px":     affine.x_axis.residual_px},
            "y":           {"slope_px_per_mm": affine.y_axis.slope_px_per_mm,
                            "intercept_px":    affine.y_axis.intercept_px,
                            "residual_px":     affine.y_axis.residual_px},
        },
        "affine_residual_px": None if affine is None else affine.residual_px,
        "label_counts":       label_count_by_kind,
        "columns":            [
            _column_payload(c, affine, page_number, page_region) for c in columns
        ],
        "summary": {
            "column_count":         len(columns),
            "labelled":             sum(1 for c in columns if c.label),
            "shape_rectangular":    sum(1 for c in columns if c.shape == "rectangular"),
            "shape_square":         sum(1 for c in columns if c.shape == "square"),
            "shape_round":          sum(1 for c in columns if c.shape == "round"),
            "shape_steel":          sum(1 for c in columns if c.shape == "steel"),
            "shape_unknown":        sum(1 for c in columns if c.shape == "unknown"),
            "orientation_ambiguous": sum(
                1 for c in columns if "orientation_ambiguous" in c.flags
            ),
            "unlabeled":            sum(1 for c in columns if "unlabeled"     in c.flags),
            "dim_missing":          sum(1 for c in columns if "dim_missing"   in c.flags),
        },
        "flags": flags,
    }


def extract_enlarged(
    pdf_path:    Path,
    page_index:  int,
    out_dir:     Path,
    run_yolo:    bool = True,
) -> EnlargedExtractResult:
    """Run grid + YOLO + label association on one ENLARGED page.

    Always writes ``out_dir/<storey>-<page>.enlarged.json``. Failures
    surface as `has_grid=False` or `column_count=0` plus flags — never
    a raised exception (the orchestrator runs this best-effort per page).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    storey_id, page_number = parse_filename(pdf_path.name)
    page_region = PAGE_REGION_MAP.get(page_number, "unknown")
    flags: list[str] = []
    affine: Affine2D | None = None
    columns: list[AssociatedColumn] = []
    label_count: dict = {}

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

        labels = extract_labels(page)
        from collections import Counter
        label_count = dict(Counter(l.kind.value for l in labels))

        if not run_yolo:
            flags.append("yolo_columns_skipped")
        elif affine is None:
            flags.append("yolo_columns_skipped_no_affine")
        else:
            try:
                col_dets = detect_columns(page, affine, dpi=grid.dpi)
            except Exception as exc:                       # noqa: BLE001
                logger.exception(f"{pdf_path.name}: YOLO column step crashed: {exc}")
                flags.append(f"yolo_columns_crashed: {type(exc).__name__}")
                col_dets = []
            if col_dets:
                yolo_tuples = [
                    (*d.bbox_px, d.aspect, d.confidence) for d in col_dets
                ]
                disp_w_pt = float(page.rect.width)
                disp_h_pt = float(page.rect.height)
                scale     = grid.dpi / 72.0
                rotation  = int(page.rotation or 0)
                columns = associate_columns(
                    yolo_tuples,
                    labels,
                    disp_w_pt, disp_h_pt, scale, rotation,
                )

    grid_payload = _grid_summary(grid)
    payload = _build_payload(
        storey_id           = storey_id,
        page_number         = page_number,
        page_region         = page_region,
        pdf_path            = pdf_path,
        page_index          = page_index,
        grid                = grid_payload,
        affine              = affine,
        columns             = columns,
        label_count_by_kind = label_count,
        flags               = flags,
    )
    payload_path = out_dir / f"{storey_id}-{page_number:02d}.enlarged.json"
    with open(payload_path, "w") as f:
        json.dump(payload, f, indent=2)

    return EnlargedExtractResult(
        storey_id          = storey_id,
        page_number        = page_number,
        page_region        = page_region,
        pdf_path           = pdf_path,
        page_index         = page_index,
        has_grid           = grid.has_grid and affine is not None,
        affine_residual_px = None if affine is None else affine.residual_px,
        column_count       = len(columns),
        payload_path       = payload_path,
        error              = None if affine is not None else (flags[0] if flags else None),
        flags              = flags,
    )


def _grid_summary(grid) -> dict:
    """Pack what the payload schema needs from a GridResult."""
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
    return {
        "page_rotation": grid.page_rotation,
        "image": {"width_px": grid.img_w_px, "height_px": grid.img_h_px, "dpi": grid.dpi},
        "grid": {"x_axes": x_axes, "y_axes": y_axes},
        "x_spacings_mm": list(grid.x_spacings_mm),
        "y_spacings_mm": list(grid.y_spacings_mm),
    }
