"""Stage 4 — Reconcile (PLAN.md §7).

Cross-link the parallel outputs of Stages 3A-1 / 3A-2 / 3B / 3C into one
unified storey + project model:

  - Per storey: -00 column positions + -01..04 type/dim/shape labels.
  - Per project: elevation levels + section slab-source map.

Both transforms resolve into the same global grid-mm anchored at -00's
first axis label. -00 is *truth-of-existence*; -01..04 is *truth-of-type*.
"""

from backend.reconcile.project import ProjectReconcileResult, reconcile_project
from backend.reconcile.storey  import (
    ReconciledColumn,
    StoreyReconcileResult,
    reconcile_storey,
)


__all__ = [
    "ProjectReconcileResult",
    "reconcile_project",
    "ReconciledColumn",
    "StoreyReconcileResult",
    "reconcile_storey",
]
