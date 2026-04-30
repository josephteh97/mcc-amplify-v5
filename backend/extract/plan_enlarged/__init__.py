"""Stage 3A-2 — STRUCT_PLAN_ENLARGED extractor (PLAN.md §3A-2).

The renovation gap-fill — authoritative for *type, dimension, shape*.
"""

from backend.extract.plan_enlarged.associator  import (
    AssociatedColumn,
    associate_columns,
)
from backend.extract.plan_enlarged.extract     import (
    EnlargedExtractResult,
    extract_enlarged,
)
from backend.extract.plan_enlarged.labels      import (
    DIA_RE,
    Label,
    LabelKind,
    RECT_DIM_RE,
    TYPE_CODE_RE,
    extract_labels,
)
from backend.extract.plan_enlarged.orientation import (
    OrientationVerdict,
    decide_orientation,
)


__all__ = [
    "AssociatedColumn",
    "associate_columns",
    "EnlargedExtractResult",
    "extract_enlarged",
    "DIA_RE",
    "Label",
    "LabelKind",
    "RECT_DIM_RE",
    "TYPE_CODE_RE",
    "extract_labels",
    "OrientationVerdict",
    "decide_orientation",
]
