"""Hard-required-gates validator (PLAN.md §9).

Checks every precondition Stage 5B needs before emitting geometry. Each
failed gate produces a structured message naming exactly what's
missing — per PLAN §9: "L3 lower-left has no -03 page; columns at grid
C/4..G/4 cannot be typed".

Gates per storey (all must pass to proceed):

  G1  overall_present        — Stage 3A-1 produced an overall.json
                                 with has_grid=True
  G2  enlarged_coverage      — every canonical column has at least one
                                 labelled or inferred enlarged
                                 candidate (no `label_missing` survives)
  G3  base_level_present     — storey RL is in project_reconcile.levels
                                 OR meta.yaml.levels
  G4  top_level_present      — next storey above has an RL (or
                                 meta.yaml fallback)
  G5  slab_thickness_present — meta.yaml.slabs.default_thickness_mm or
                                 zone-specific override is set
  G6  starter_family_for_each_shape — every shape encountered in the
                                 storey's typing payload has a family
                                 in the inventory

The strict-mode contract from PLAN §11 says: any G1..G5 failure aborts
the storey's emission. G6 fails loud since auto-duplicate would have
created a synthetic family — if Stage 5A REJECTED everything the
column gets written to the review queue instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GateResult:
    name:     str
    passed:   bool
    detail:   str  = ""
    severity: str  = "hard"     # "hard" → blocks emission, "warn" → logged only


@dataclass(frozen=True)
class StoreyGates:
    storey_id:    str
    gates:        list[GateResult]
    base_rl_mm:   int | None
    top_rl_mm:    int | None
    storey_height_mm: int | None
    slab_thickness_mm: float | None

    @property
    def all_passed(self) -> bool:
        """All HARD gates passed. Warn-severity failures don't block emission."""
        return all(g.passed for g in self.gates if g.severity == "hard")

    @property
    def failures(self) -> list[GateResult]:
        return [g for g in self.gates if not g.passed]

    @property
    def hard_failures(self) -> list[GateResult]:
        return [g for g in self.gates if not g.passed and g.severity == "hard"]

    @property
    def warnings(self) -> list[GateResult]:
        return [g for g in self.gates if not g.passed and g.severity == "warn"]

    def to_dict(self) -> dict:
        return {
            "storey_id":         self.storey_id,
            "all_passed":        self.all_passed,
            "base_rl_mm":        self.base_rl_mm,
            "top_rl_mm":         self.top_rl_mm,
            "storey_height_mm":  self.storey_height_mm,
            "slab_thickness_mm": self.slab_thickness_mm,
            "gates": [
                {"name": g.name, "passed": g.passed,
                 "detail": g.detail, "severity": g.severity}
                for g in self.gates
            ],
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _next_storey_rl(
    storey_id:  str,
    levels:     list[dict],
) -> tuple[str, int] | None:
    """Find the next-higher level by RL.

    levels is sorted ascending by rl_mm (project reconciler emits this).
    The storey's own level is matched by name (uppercase). The "next"
    is the one immediately above.
    """
    target = storey_id.upper()
    by_name = {l["name"].upper(): l for l in levels}
    if target not in by_name:
        return None
    me_rl = by_name[target]["rl_mm"]
    above = sorted([l for l in levels if l["rl_mm"] > me_rl], key=lambda l: l["rl_mm"])
    if not above:
        return None
    nxt = above[0]
    return nxt["name"], int(nxt["rl_mm"])


def _shapes_in_typing(typing_payload: dict) -> set[str]:
    out: set[str] = set()
    for plc in typing_payload.get("placements", []):
        s = plc.get("shape")
        if s:
            out.add(s)
    return out


def _coverage_from_reconciled(reconciled_payload: dict) -> tuple[int, int, int]:
    """Return (canonical_total, labelled_or_inferred, missing).

    A canonical column is "covered" iff it has a label (direct match or
    neighbour-inferred). label_missing flag means uncovered.
    """
    cols = reconciled_payload.get("columns", [])
    total = len(cols)
    missing = sum(1 for c in cols if "label_missing" in (c.get("flags") or []))
    return total, total - missing, missing


# ── Public API ────────────────────────────────────────────────────────────────

def validate_storey_gates(
    storey_id:           str,
    overall_payload:     dict | None,
    reconciled_payload:  dict | None,
    typing_payload:      dict | None,
    project_levels:      list[dict],
    inventory_shapes:    set[str],
    slab_default_mm:     float | None,
    storey_height_fallback_mm: int = 4500,
) -> StoreyGates:
    """Run all Stage 5B gates on one storey. Caller decides whether to abort."""
    gates: list[GateResult] = []

    # G1 — overall_present
    if overall_payload is None:
        gates.append(GateResult("overall_present", False,
                                "no -00 overall payload"))
    elif not overall_payload.get("affine_residual_px") and not overall_payload.get("grid"):
        gates.append(GateResult("overall_present", False,
                                "overall payload missing grid"))
    else:
        gates.append(GateResult("overall_present", True,
                                f"residual_px={overall_payload.get('affine_residual_px')}"))

    # G2 — enlarged_coverage
    # PLAN §11: "Column from -00 not covered by any -0[1-4] → Emit
    # unlabeled, list missing region". So missing-column count is a
    # WARNING, not a hard gate failure — emission proceeds and the
    # uncovered columns end up in the review queue. The hard failure
    # only fires when the reconcile step itself didn't run.
    if reconciled_payload is None:
        gates.append(GateResult("enlarged_coverage", False,
                                "no reconciled payload",
                                severity="hard"))
    else:
        total, covered, missing = _coverage_from_reconciled(reconciled_payload)
        if total == 0:
            gates.append(GateResult("enlarged_coverage", False,
                                    "zero canonical columns",
                                    severity="hard"))
        elif missing > 0:
            gates.append(GateResult(
                "enlarged_coverage",
                False,
                f"{missing}/{total} canonical columns have no label "
                "(emitted unlabeled — see review queue)",
                severity="warn",
            ))
        else:
            gates.append(GateResult("enlarged_coverage", True,
                                    f"{covered}/{total} columns covered"))

    # G3 — base_level_present
    by_name = {l["name"].upper(): l for l in project_levels}
    base_rl: int | None = None
    if storey_id.upper() in by_name:
        base_rl = int(by_name[storey_id.upper()]["rl_mm"])
        gates.append(GateResult("base_level_present", True,
                                f"{storey_id} RL = {base_rl} mm "
                                f"(source={by_name[storey_id.upper()].get('source')})"))
    else:
        gates.append(GateResult("base_level_present", False,
                                f"no level entry for {storey_id} in elevation extract or meta.yaml"))

    # G4 — top_level_present
    top_rl: int | None = None
    storey_height_mm: int | None = None
    nxt = _next_storey_rl(storey_id, project_levels)
    if nxt is not None and base_rl is not None:
        top_name, top_rl = nxt
        storey_height_mm = top_rl - base_rl
        gates.append(GateResult("top_level_present", True,
                                f"top = {top_name} ({top_rl} mm); "
                                f"storey height = {storey_height_mm} mm"))
    elif base_rl is not None:
        top_rl = base_rl + storey_height_fallback_mm
        storey_height_mm = storey_height_fallback_mm
        gates.append(GateResult(
            "top_level_present",
            True,
            f"no level above {storey_id}; using fallback height "
            f"{storey_height_fallback_mm} mm",
        ))
    else:
        gates.append(GateResult("top_level_present", False,
                                f"no base RL for {storey_id} so top RL is undefined"))

    # G5 — slab_thickness_present
    if slab_default_mm is None or slab_default_mm <= 0:
        gates.append(GateResult("slab_thickness_present", False,
                                "no meta.yaml.slabs.default_thickness_mm set"))
    else:
        gates.append(GateResult("slab_thickness_present", True,
                                f"{slab_default_mm} mm (meta.yaml fallback)"))

    # G6 — starter_family_for_each_shape
    if typing_payload is None:
        gates.append(GateResult("starter_family_for_each_shape", False,
                                "no typing payload"))
    else:
        shapes_seen = _shapes_in_typing(typing_payload)
        missing_shapes = shapes_seen - inventory_shapes
        if missing_shapes:
            gates.append(GateResult(
                "starter_family_for_each_shape",
                False,
                f"no starter family for shape(s): {sorted(missing_shapes)} — "
                f"load corresponding .rfa(s)",
            ))
        else:
            gates.append(GateResult(
                "starter_family_for_each_shape",
                True,
                f"shapes covered: {sorted(shapes_seen) or 'none'}",
            ))

    return StoreyGates(
        storey_id         = storey_id,
        gates             = gates,
        base_rl_mm        = base_rl,
        top_rl_mm         = top_rl,
        storey_height_mm  = storey_height_mm,
        slab_thickness_mm = slab_default_mm,
    )
