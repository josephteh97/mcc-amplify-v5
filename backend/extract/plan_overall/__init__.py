"""Stage 3A-1 — STRUCT_PLAN_OVERALL extractor (PLAN.md §3A-1).

The renovation of v4's grid pipeline. Authoritative for *position*, not type.

Public surface:
  - GridResult, detect_grid    (text-based grid bubble detection)
  - Affine2D, solve_affine     (pixel → grid-mm with residual gate)
  - extract_overall            (per-page orchestration → overall.json payload)
"""

from backend.extract.plan_overall.affine   import Affine2D, AffineSolveError, solve_affine
from backend.extract.plan_overall.detector import GridResult, detect_grid
from backend.extract.plan_overall.extract  import OverallExtractResult, extract_overall

__all__ = [
    "GridResult",
    "detect_grid",
    "Affine2D",
    "AffineSolveError",
    "solve_affine",
    "OverallExtractResult",
    "extract_overall",
]
