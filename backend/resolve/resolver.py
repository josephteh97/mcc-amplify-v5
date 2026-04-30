"""Per-storey type resolver entry point (PLAN.md §8).

Loads a reconciled storey JSON (Stage 4 output), runs the matcher on
every column, and emits a placement payload:

  - ``output/<storey>_typing.json``  — placement plan for Stage 5B / pyRevit
  - ``output/<storey>_review.json``  — REJECTED + ambiguous columns

Inventory is mutated by the auto-duplicate path; caller passes a
``FamilyInventory`` and we persist the updated version back to disk
when an output path is supplied.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from backend.resolve.inventory import FamilyInventory, save_inventory
from backend.resolve.matcher   import (
    MatchOutcome,
    MatchTier,
    match_column,
)


@dataclass
class StoreyResolveResult:
    storey_id:           str
    reconciled_path:     Path
    typing_path:         Path | None
    review_path:         Path | None
    column_count:        int
    tier_counts:         dict[str, int]
    flags:               list[str] = field(default_factory=list)


def _placement_payload(
    col:     dict,
    outcome: MatchOutcome,
) -> dict:
    """PLAN §8 per-column placement payload."""
    shape = col.get("shape", "unknown")
    if shape == "round":
        source_dims = {"d": col.get("diameter_mm")}
    elif shape in ("rectangular", "square", "steel"):
        source_dims = {"x": col.get("dim_along_x_mm"), "y": col.get("dim_along_y_mm")}
    else:
        source_dims = {}

    return {
        "grid_mm_xy":   col.get("canonical_grid_mm_xy"),
        "type_id":      outcome.type_id,
        "type_name":    outcome.type_name,
        "family_name":  outcome.family_name,
        "rotation_deg": 0,                       # orientation already in dims (§3A-2)
        "comments":     col.get("label"),         # PLAN §8: label → instance Comments
        "source_label": col.get("label"),
        "source_dims":  source_dims,
        "shape":        shape,
        "is_steel":     bool(col.get("is_steel")),
        "audit":        outcome.audit,
        "tier":         outcome.tier.value,
        "dim_delta_mm": outcome.dim_delta_mm,
        "flags":        list(set((col.get("flags") or []) + outcome.flags)),
        "canonical_idx": col.get("canonical_idx"),
    }


def resolve_storey(
    reconciled_path: Path,
    inventory:       FamilyInventory,
    out_dir:         Path,
    inventory_save_path: Path | None = None,
) -> StoreyResolveResult:
    """Resolve one storey's columns into a Revit placement plan.

    Always writes ``<storey>_typing.json`` and ``<storey>_review.json``
    under ``out_dir``. The inventory is mutated in place when tier 3
    fires; pass ``inventory_save_path`` to persist the updated state.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = json.loads(reconciled_path.read_text())
    storey_id = payload.get("storey_id", reconciled_path.stem)
    columns   = payload.get("columns", [])

    placements: list[dict] = []
    review:     list[dict] = []
    counts:     Counter[str] = Counter()

    for col in columns:
        outcome = match_column(
            inventory   = inventory,
            label       = col.get("label"),
            shape       = col.get("shape", "unknown"),
            dim_x_mm    = col.get("dim_along_x_mm"),
            dim_y_mm    = col.get("dim_along_y_mm"),
            diameter_mm = col.get("diameter_mm"),
        )
        counts[outcome.tier.value] += 1
        plc = _placement_payload(col, outcome)
        if outcome.tier == MatchTier.REJECTED:
            review.append({
                "canonical_idx":         col.get("canonical_idx"),
                "canonical_grid_mm_xy":  col.get("canonical_grid_mm_xy"),
                "label":                 col.get("label"),
                "shape":                 col.get("shape"),
                "dim_along_x_mm":        col.get("dim_along_x_mm"),
                "dim_along_y_mm":        col.get("dim_along_y_mm"),
                "diameter_mm":           col.get("diameter_mm"),
                "reason":                outcome.reason,
                "audit":                 outcome.audit,
                "flags":                 plc["flags"],
            })
            continue
        placements.append(plc)

    typing_path = out_dir / f"{storey_id}_typing.json"
    review_path = out_dir / f"{storey_id}_review.json"
    typing_path.write_text(json.dumps({
        "storey_id":   storey_id,
        "summary":     {
            "column_count":   len(columns),
            "placements":     len(placements),
            "rejected":       len(review),
            "tier_counts":    dict(counts),
        },
        "placements":  placements,
    }, indent=2))
    review_path.write_text(json.dumps({
        "storey_id": storey_id,
        "summary":   {"rejected": len(review)},
        "items":     review,
    }, indent=2))

    if inventory_save_path is not None:
        save_inventory(inventory, inventory_save_path)

    logger.info(
        f"  {storey_id}: placements={len(placements)} rejected={len(review)} "
        f"tiers={dict(counts)}"
    )

    return StoreyResolveResult(
        storey_id       = storey_id,
        reconciled_path = reconciled_path,
        typing_path     = typing_path,
        review_path     = review_path,
        column_count    = len(columns),
        tier_counts     = dict(counts),
    )
