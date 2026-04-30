"""Per-element X×Y vs swap orientation decider (PLAN.md §3A-2, §11).

Background (PROBE §3A-2 cont.): for an unequal `W×H` annotation, the
larger annotation dim should map to the longer bbox axis — but the
convention direction (geometric X×Y vs size-order L×S) is *consultant-
specific* and even varies element-by-element on this fixture. 84/469
asymmetric annotations are written `first < second`, ruling out a pure
`longer × shorter` rule.

Strategy (PLAN.md §3A-2):

  - Treat each rectangular column as its own decision.
  - With a confident YOLO bbox in pt-space (dx, dy), and a regex-parsed
    annotation `(a, b)` with a ≠ b:

        ratio_box   = dx / dy
        ratio_xy    = a  / b
        ratio_swap  = b  / a

    Compute the relative error to each hypothesis. Whichever sits within
    `ASPECT_TOL` (0.15) wins. If both fit (square-ish bbox or noisy YOLO),
    XY wins by convention. If neither fits, signal `AMBIGUOUS` and the
    caller defers to the LLM checker.

PLAN §11 strict-mode: never coerce. Wrong column dimension is an
unacceptable failure mode — `AMBIGUOUS` is the safe outcome and the
review queue flags it for VLM disambiguation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from backend.core.grid_mm import ASPECT_TOL


class OrientationVerdict(str, Enum):
    XY        = "xy"          # dim_along_x = a, dim_along_y = b
    SWAP      = "swap"         # dim_along_x = b, dim_along_y = a
    EQUAL     = "equal"        # square (a == b) — orientation irrelevant
    AMBIGUOUS = "ambiguous"    # neither hypothesis fits the bbox


@dataclass(frozen=True)
class OrientationDecision:
    verdict:        OrientationVerdict
    dim_along_x_mm: int | None
    dim_along_y_mm: int | None
    err_xy:         float
    err_swap:       float
    notes:          str


def _relative_error(r_box: float, r_hyp: float) -> float:
    if r_box <= 0 or r_hyp <= 0:
        return float("inf")
    return abs(r_box - r_hyp) / max(r_box, r_hyp)


def decide_orientation(
    bbox_dx_pt: float,
    bbox_dy_pt: float,
    ann_a_mm:   int,
    ann_b_mm:   int,
    tol:        float = ASPECT_TOL,
) -> OrientationDecision:
    """Decide whether annotation ``(a, b)`` reads X×Y or H×W on this bbox.

    Inputs are unit-agnostic — only ratios matter — so the bbox can be
    in any consistent unit (pt, px, mm). Both annotation values must be
    positive and the bbox must have non-zero extent on both axes.
    """
    if ann_a_mm <= 0 or ann_b_mm <= 0:
        raise ValueError(f"annotation must be positive, got ({ann_a_mm}, {ann_b_mm})")
    if bbox_dx_pt <= 0 or bbox_dy_pt <= 0:
        raise ValueError(f"bbox must be non-empty, got ({bbox_dx_pt}, {bbox_dy_pt})")

    if ann_a_mm == ann_b_mm:
        return OrientationDecision(
            verdict        = OrientationVerdict.EQUAL,
            dim_along_x_mm = ann_a_mm,
            dim_along_y_mm = ann_b_mm,
            err_xy         = 0.0,
            err_swap       = 0.0,
            notes          = "square annotation — orientation undefined but unambiguous",
        )

    r_box  = bbox_dx_pt / bbox_dy_pt
    r_xy   = ann_a_mm   / ann_b_mm
    r_swap = ann_b_mm   / ann_a_mm
    err_xy   = _relative_error(r_box, r_xy)
    err_swap = _relative_error(r_box, r_swap)

    fit_xy   = err_xy   <= tol
    fit_swap = err_swap <= tol

    if fit_xy and fit_swap:
        # Tie — pick the smaller error; XY wins on exact tie.
        if err_xy <= err_swap:
            return OrientationDecision(
                verdict        = OrientationVerdict.XY,
                dim_along_x_mm = ann_a_mm,
                dim_along_y_mm = ann_b_mm,
                err_xy         = err_xy,
                err_swap       = err_swap,
                notes          = "both fit — XY tighter",
            )
        return OrientationDecision(
            verdict        = OrientationVerdict.SWAP,
            dim_along_x_mm = ann_b_mm,
            dim_along_y_mm = ann_a_mm,
            err_xy         = err_xy,
            err_swap       = err_swap,
            notes          = "both fit — SWAP tighter",
        )

    if fit_xy:
        return OrientationDecision(
            verdict        = OrientationVerdict.XY,
            dim_along_x_mm = ann_a_mm,
            dim_along_y_mm = ann_b_mm,
            err_xy         = err_xy,
            err_swap       = err_swap,
            notes          = f"XY fit (err={err_xy:.3f} ≤ tol={tol})",
        )
    if fit_swap:
        return OrientationDecision(
            verdict        = OrientationVerdict.SWAP,
            dim_along_x_mm = ann_b_mm,
            dim_along_y_mm = ann_a_mm,
            err_xy         = err_xy,
            err_swap       = err_swap,
            notes          = f"SWAP fit (err={err_swap:.3f} ≤ tol={tol})",
        )

    return OrientationDecision(
        verdict        = OrientationVerdict.AMBIGUOUS,
        dim_along_x_mm = None,
        dim_along_y_mm = None,
        err_xy         = err_xy,
        err_swap       = err_swap,
        notes          = (
            f"neither fits (err_xy={err_xy:.3f}, err_swap={err_swap:.3f}, tol={tol}) "
            "— defer to LLM checker"
        ),
    )
