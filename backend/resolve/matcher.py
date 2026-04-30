"""Type matcher (PLAN.md §8 — Stage 5A core).

Per reconciled column, walk the four-tier algorithm and return a
``MatchOutcome``:

  1. ``MATCHED_EXACT``  — same shape + dims within ±TYPE_DIM_TOL_MM (5 mm).
  2. ``MATCHED_LABEL``  — same label code AND dims agree within tol;
                           the actual delta is recorded for the audit trail.
  3. ``CREATED``        — no match; auto-duplicate with a canonical
                           ``<label>_<shape_code>_<dims>`` name. Registered
                           in the inventory so subsequent same-(shape, dims)
                           reuse it via tier 1.
  4. ``REJECTED``       — shape unknown / dims None / shape is L or T
                           (deferred). Skips placement, surfaces in the
                           review queue.

Strict-mode (PLAN §11): never round dims, never substitute label,
never coerce shape. Either match exactly, duplicate-and-create, or
reject.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from backend.core.grid_mm     import TYPE_DIM_TOL_MM
from backend.resolve.inventory import (
    DEFAULT_FAMILY_NAMES,
    Family,
    FamilyInventory,
    FamilyType,
)


REJECT_SHAPES_DEFERRED = {"l_section", "t_section", "L", "T"}


class MatchTier(str, Enum):
    EXACT    = "MATCHED_EXACT"
    LABEL    = "MATCHED_LABEL"
    CREATED  = "CREATED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class MatchOutcome:
    tier:         MatchTier
    type_id:      str | None
    type_name:    str | None
    family_name:  str | None
    audit:        str                      # PLAN §8 audit-trail string
    dim_delta_mm: int | None     = None    # populated for MATCHED_LABEL
    flags:        list[str]      = field(default_factory=list)
    reason:       str | None     = None    # populated for REJECTED


# ── Helpers ───────────────────────────────────────────────────────────────────

def shape_code(shape: str) -> str:
    """Canonical 1-2 char shape code used in synthetic type names."""
    return {
        "rectangular": "R",
        "square":      "S",
        "round":       "RD",
        "steel":       "H",
    }.get(shape, "X")


def canonical_type_name(
    label:       str | None,
    shape:       str,
    dim_x_mm:    int | None = None,
    dim_y_mm:    int | None = None,
    diameter_mm: int | None = None,
) -> str:
    """``<label>_<shape_code>_<dims>`` per PLAN §8 (e.g. ``C2_R_1150x800``).

    When the label is missing we fall back to ``UNLABELED`` so the
    canonical form still carries the dim signature.
    """
    lbl = (label or "UNLABELED").strip().upper()
    sc  = shape_code(shape)
    if shape == "round":
        return f"{lbl}_{sc}_{int(diameter_mm or 0)}"
    if shape == "square":
        return f"{lbl}_{sc}_{int(dim_x_mm or 0)}"
    return f"{lbl}_{sc}_{int(dim_x_mm or 0)}x{int(dim_y_mm or 0)}"


def _is_rejectable(shape: str, dim_x: int | None, dim_y: int | None,
                   dia: int | None) -> tuple[bool, str | None]:
    """Return (reject, reason). Strict per PLAN §8 tier 4."""
    if shape in REJECT_SHAPES_DEFERRED:
        return True, f"shape_{shape}_deferred"
    if shape in (None, "", "unknown"):
        return True, "shape_unknown"
    if shape == "round":
        if dia is None:
            return True, "diameter_missing"
    else:
        if dim_x is None or dim_y is None:
            return True, "dims_missing"
    return False, None


# ── Public API ────────────────────────────────────────────────────────────────

def match_column(
    inventory:    FamilyInventory,
    label:        str | None,
    shape:        str,
    dim_x_mm:     int | None,
    dim_y_mm:     int | None,
    diameter_mm:  int | None,
    tol_mm:       float = TYPE_DIM_TOL_MM,
    family_name_override: str | None = None,
) -> MatchOutcome:
    """Run tiers 1-4 on one reconciled column and return the outcome.

    The inventory is mutated when tier 3 fires (auto-duplicate appends a
    new ``FamilyType``). Caller is responsible for persisting the
    inventory if it wants the additions to survive across runs.
    """
    reject, reason = _is_rejectable(shape, dim_x_mm, dim_y_mm, diameter_mm)
    if reject:
        return MatchOutcome(
            tier        = MatchTier.REJECTED,
            type_id     = None,
            type_name   = None,
            family_name = None,
            audit       = f"REJECTED({reason})",
            reason      = reason,
            flags       = [reason or "rejected"],
        )

    # Tier 1 — exact dims match
    t = inventory.lookup_by_dims(shape, dim_x_mm, dim_y_mm, diameter_mm, tol_mm)
    if t is not None:
        f = inventory.find_family_for_shape(shape)
        return MatchOutcome(
            tier        = MatchTier.EXACT,
            type_id     = t.type_id,
            type_name   = t.type_name,
            family_name = f.family_name if f else None,
            audit       = f"MATCHED_EXACT({f.family_name if f else '?'},{t.type_name})",
        )

    # Tier 2 — label-only match (still requires dims agree within tol)
    if label:
        hit = inventory.lookup_by_label(shape, label, dim_x_mm, dim_y_mm, diameter_mm, tol_mm)
        if hit is not None:
            t, delta = hit
            f = inventory.find_family_for_shape(shape)
            return MatchOutcome(
                tier         = MatchTier.LABEL,
                type_id      = t.type_id,
                type_name    = t.type_name,
                family_name  = f.family_name if f else None,
                audit        = f"MATCHED_LABEL({f.family_name if f else '?'},{t.type_name},Δ={delta}mm)",
                dim_delta_mm = delta,
            )

    # Tier 3 — auto-duplicate
    new_name = canonical_type_name(label, shape, dim_x_mm, dim_y_mm, diameter_mm)
    fam_name = (
        family_name_override
        or DEFAULT_FAMILY_NAMES.get(shape, f"Generic-{shape.title()}")
    )
    new_t = inventory.add_type(
        shape       = shape,
        type_name   = new_name,
        label       = label,
        dim_x_mm    = dim_x_mm,
        dim_y_mm    = dim_y_mm,
        diameter_mm = diameter_mm,
        family_name = fam_name,
    )
    f = inventory.find_family_for_shape(shape)
    return MatchOutcome(
        tier        = MatchTier.CREATED,
        type_id     = new_t.type_id,
        type_name   = new_t.type_name,
        family_name = f.family_name if f else fam_name,
        audit       = f"CREATED({f.family_name if f else fam_name},{new_name})",
        flags       = ["auto_duplicated"],
    )
