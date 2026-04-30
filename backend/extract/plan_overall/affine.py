"""Pixel → grid-mm affine solver (PLAN.md §3A-1).

Grid bubble pixel positions and the cumulative-mm position of each axis line
are an over-determined linear system: for each V-line we know image_x_px and
mm_x; for each H-line we know image_y_px and mm_y. The two axes are
independent, so we solve two 1D least-squares fits.

Residual gate (PLAN.md §11 strict-mode): if the maximum per-point residual on
either axis exceeds 1 pixel, the affine is rejected — the caller treats the
plan as having no usable grid and flags the page for review rather than
shipping a wrong coordinate transform.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.extract.plan_overall.detector import GridResult


MAX_RESIDUAL_PX = 1.0   # PLAN.md §3A-1


class AffineSolveError(ValueError):
    """Raised when the solver cannot produce a transform within the residual gate."""


@dataclass(frozen=True)
class _AxisFit:
    slope_px_per_mm: float    # px = slope * mm + intercept
    intercept_px:    float
    residual_px:     float    # max abs per-point residual

    def mm_to_px(self, mm: float) -> float:
        return self.slope_px_per_mm * mm + self.intercept_px

    def px_to_mm(self, px: float) -> float:
        return (px - self.intercept_px) / self.slope_px_per_mm


@dataclass(frozen=True)
class Affine2D:
    x_axis: _AxisFit
    y_axis: _AxisFit
    residual_px: float    # max(x.residual, y.residual)

    def px_to_mm(self, px: float, py: float) -> tuple[float, float]:
        return self.x_axis.px_to_mm(px), self.y_axis.px_to_mm(py)

    def mm_to_px(self, mx: float, my: float) -> tuple[float, float]:
        return self.x_axis.mm_to_px(mx), self.y_axis.mm_to_px(my)


def _fit_axis(pxs: list[float], mms: list[float]) -> _AxisFit:
    """Least-squares fit of px = slope * mm + intercept and report max residual.

    With N ≥ 2 grid lines this is well-conditioned; mm values are guaranteed
    distinct (cumulative sum of strictly positive spacings).
    """
    if len(pxs) != len(mms) or len(pxs) < 2:
        raise AffineSolveError(
            f"need ≥2 calibration points, got {len(pxs)}",
        )
    n = len(pxs)
    sx  = sum(mms)
    sy  = sum(pxs)
    sxx = sum(m * m for m in mms)
    sxy = sum(m * p for m, p in zip(mms, pxs))
    denom = n * sxx - sx * sx
    if denom == 0:
        raise AffineSolveError("degenerate calibration — all mm values equal")
    slope     = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    if slope == 0:
        raise AffineSolveError("zero slope — calibration collapsed")

    residual = max(abs(p - (slope * m + intercept)) for m, p in zip(mms, pxs))
    return _AxisFit(slope_px_per_mm=slope, intercept_px=intercept, residual_px=residual)


def _cumulative_mm(spacings: list[float]) -> list[float]:
    """Convert n-1 bay spacings into n cumulative grid-line positions starting at 0."""
    out = [0.0]
    for sp in spacings:
        out.append(out[-1] + sp)
    return out


def solve_affine(grid: GridResult, max_residual_px: float = MAX_RESIDUAL_PX) -> Affine2D:
    """Solve the pixel→grid-mm affine for a detected grid.

    Raises AffineSolveError when has_grid=False, when the calibration is
    degenerate, or when either axis residual exceeds max_residual_px.
    """
    if not grid.has_grid:
        raise AffineSolveError("grid was not detected (has_grid=False)")
    if len(grid.x_spacings_mm) != len(grid.x_lines_px) - 1:
        raise AffineSolveError(
            f"x: {len(grid.x_lines_px)} lines vs {len(grid.x_spacings_mm)} spacings",
        )
    if len(grid.y_spacings_mm) != len(grid.y_lines_px) - 1:
        raise AffineSolveError(
            f"y: {len(grid.y_lines_px)} lines vs {len(grid.y_spacings_mm)} spacings",
        )

    x_mms = _cumulative_mm(grid.x_spacings_mm)
    y_mms = _cumulative_mm(grid.y_spacings_mm)

    x_axis = _fit_axis(grid.x_lines_px, x_mms)
    y_axis = _fit_axis(grid.y_lines_px, y_mms)

    residual = max(x_axis.residual_px, y_axis.residual_px)
    if residual > max_residual_px:
        raise AffineSolveError(
            f"affine residual {residual:.3f} px exceeds gate {max_residual_px} px "
            f"(x={x_axis.residual_px:.3f}, y={y_axis.residual_px:.3f})",
        )

    return Affine2D(x_axis=x_axis, y_axis=y_axis, residual_px=residual)
