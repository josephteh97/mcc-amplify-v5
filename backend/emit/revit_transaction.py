"""Revit transaction-recipe emitter (PLAN.md §9).

Produces ``output/<storey>_transaction.json`` matching the v4 contract that
the Windows-side ``RevitModelBuilderAddin.dll`` already consumes:

  - HTTP mode: POSTed as ``transaction_json`` to ``/build-model``.
  - File-drop mode: written as ``pending.json`` in the shared directory.

Top-level shape::

    {
      "job_id":   "<storey_id>",
      "levels":   [{"name": <detected>, "elevation": <rl_mm>}, ...],   # full stack
      "grids":    [],
      "columns":  [...],                  # see _column_entry
      "structural_framing": [],
      "walls":    [], "core_walls": [],
      "stairs":   [], "lifts": [],
      "slabs":    [...],                  # one plan-extent rect at fallback thickness
      "metadata": {...}
    }

Level names come from the elevation extractor / meta.yaml — never
hardcoded. The recipe carries the *full* level stack so a Revit document
opening from this recipe gets every storey, not just two; per-column
``Parameters.Level`` and ``Parameters.TopLevel`` reference the resolved
base + next-higher names for that storey.

Per-column entry mirrors v4 verbatim — both the capitalised
``Parameters``/``Properties`` blocks (the add-in's primary parser) and the
lowercase aliases (``location``, ``width``, ``depth``, ``height``, ``shape``,
``material``, ``level``, ``top_level``) the v4 add-in still reads as
fallbacks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


SLAB_MARGIN_MM  = 1000.0


@dataclass(frozen=True)
class TransactionEmitResult:
    storey_id:        str
    transaction_path: Path
    column_count:     int
    skipped:          int
    slab_count:       int


def _shape_to_v4(shape: str) -> str:
    """v4 uses 'circular' / 'rectangular' only. Map our shape vocabulary."""
    if shape == "round":
        return "circular"
    if shape in ("square", "steel", "rectangular"):
        return "rectangular"
    return "rectangular"


def _material(plc: dict) -> str:
    return "steel" if plc.get("is_steel") else "concrete"


def _column_entry(
    plc:               dict,
    storey_height_mm:  int,
    base_level_name:   str,
    top_level_name:    str,
) -> dict | None:
    """One column → v4 recipe entry. None when dims are missing."""
    xy = plc.get("grid_mm_xy")
    if not xy:
        return None
    src   = plc.get("source_dims") or {}
    shape = _shape_to_v4(plc.get("shape", "rectangular"))
    if shape == "circular":
        d = src.get("d") or src.get("diameter")
        if not d:
            return None
        width  = float(d)
        depth  = float(d)
    else:
        w = src.get("x")
        h = src.get("y")
        if not w or not h:
            return None
        width  = float(w)
        depth  = float(h)

    cx_mm, cy_mm  = float(xy[0]), float(xy[1])
    family_name   = plc.get("family_name") or "Concrete-Rectangular-Column"
    type_name     = plc.get("type_name")   or "STARTER"
    material      = _material(plc)

    return {
        "id":          plc.get("canonical_idx"),
        "type_mark":   plc.get("source_label"),
        "Parameters": {
            "Family":   family_name,
            "Symbol":   type_name,
            "Location": {"X": cx_mm, "Y": cy_mm, "Z": 0.0},
            "Level":    base_level_name,
            "TopLevel": top_level_name,
        },
        "Properties": {
            "Width":    round(width, 1),
            "Depth":    round(depth, 1),
            "Material": material,
        },
        # Legacy lowercase aliases (v4 add-in reads these as fallback).
        "family_type": type_name,
        "location":    {"x": cx_mm, "y": cy_mm, "z": 0.0},
        "width":       round(width, 1),
        "depth":       round(depth, 1),
        "height":      int(storey_height_mm),
        "shape":       shape,
        "material":    material,
        "level":       base_level_name,
        "top_level":   top_level_name,
        # Provenance (Stage 5A audit) — informational, ignored by the add-in.
        "audit":       plc.get("audit"),
        "tier":        plc.get("tier"),
    }


def _levels_array(project_levels: list[dict]) -> list[dict]:
    """Sort and project the full level stack into v4's {name, elevation} form."""
    out = sorted(project_levels, key=lambda l: int(l["rl_mm"]))
    return [
        {"name": str(l["name"]), "elevation": int(l["rl_mm"])}
        for l in out
    ]


def _plan_extent_slab(
    columns: list[dict],
    base_rl_mm:        int,
    slab_thickness_mm: float,
    base_level_name:   str,
) -> dict | None:
    """Synthesize one plan-extent slab when section extraction is deferred.

    Bounded by the column footprints + ``SLAB_MARGIN_MM``. ``elevation = 0``
    in v4's slab schema means the slab top sits on the level line — Revit
    extrudes downward, so the slab body ends up below ``base_level``.
    """
    xs = [c["location"]["x"] for c in columns]
    ys = [c["location"]["y"] for c in columns]
    if not xs or not ys:
        return None
    x0 = min(xs) - SLAB_MARGIN_MM
    x1 = max(xs) + SLAB_MARGIN_MM
    y0 = min(ys) - SLAB_MARGIN_MM
    y1 = max(ys) + SLAB_MARGIN_MM
    return {
        "id":              f"slab_{base_level_name.replace(' ', '_')}",
        "boundary_points": [
            {"x": x0, "y": y0},
            {"x": x1, "y": y0},
            {"x": x1, "y": y1},
            {"x": x0, "y": y1},
        ],
        "thickness":       float(slab_thickness_mm),
        "elevation":       0.0,
        "level":           base_level_name,
    }


def emit_revit_transaction(
    storey_id:          str,
    typing_payload:     dict,
    base_rl_mm:         int,
    top_rl_mm:          int,
    base_level_name:    str,
    top_level_name:     str,
    project_levels:     list[dict],
    slab_thickness_mm:  float,
    slab_zones:         dict,
    out_dir:            Path,
) -> TransactionEmitResult:
    """Build the v4 recipe JSON for one storey and write it to disk.

    Level names — both the top-level ``levels`` array and the per-column
    ``Level`` / ``TopLevel`` refs — come from the detected project level
    stack. v4 used a hardcoded ``Level 0`` / ``Level 1`` because it was a
    single-floor pipeline; v5 carries the full ``B3 / B2 / B1 / L1 / L2 /
    …`` (or architectural ``BASEMENT 1 / 1ST STOREY / …``) stack so a
    Revit document opened from this recipe gets every storey.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    storey_height_mm = max(1, top_rl_mm - base_rl_mm)

    columns: list[dict] = []
    skipped = 0
    for plc in typing_payload.get("placements", []):
        entry = _column_entry(plc, storey_height_mm, base_level_name, top_level_name)
        if entry is None:
            skipped += 1
            continue
        columns.append(entry)

    slab_entry = _plan_extent_slab(
        columns           = columns,
        base_rl_mm        = base_rl_mm,
        slab_thickness_mm = slab_thickness_mm,
        base_level_name   = base_level_name,
    )
    slabs = [slab_entry] if slab_entry else []

    levels_payload = _levels_array(project_levels)
    if not levels_payload:
        # Defensive fallback when project_levels is empty — at least carry
        # the storey's own pair so the add-in has something to anchor on.
        levels_payload = [
            {"name": base_level_name, "elevation": int(base_rl_mm)},
            {"name": top_level_name,  "elevation": int(top_rl_mm)},
        ]

    recipe = {
        "job_id":  storey_id,
        "levels":  levels_payload,
        "grids":              [],
        "columns":            columns,
        "structural_framing": [],
        "walls":              [],
        "core_walls":         [],
        "stairs":             [],
        "lifts":              [],
        "slabs":              slabs,
        "metadata": {
            "storey_id":          storey_id,
            "base_level_name":    base_level_name,
            "top_level_name":     top_level_name,
            "source":             "amplify-v5",
            "slab_thickness_mm":  float(slab_thickness_mm),
            "slab_zones":         slab_zones or {},
            "column_count":       len(columns),
            "columns_skipped":    skipped,
        },
    }

    out_path = out_dir / f"{storey_id}_transaction.json"
    out_path.write_text(json.dumps(recipe, indent=2))

    return TransactionEmitResult(
        storey_id        = storey_id,
        transaction_path = out_path,
        column_count     = len(columns),
        skipped          = skipped,
        slab_count       = len(slabs),
    )
