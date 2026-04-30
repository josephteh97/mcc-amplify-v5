"""Stage 5A — Type Resolver + Revit Family Manager (PLAN.md §8).

For every reconciled column from Stage 4, pick a Revit family type:
exact dims match → label match → auto-duplicate → reject. Strict on
dimensions (±TYPE_DIM_TOL_MM = 5 mm), tolerant on label, no fuzzy
substitution. Emits ``output/<storey>_typing.json`` for the pyRevit
script (Stage 5B) to consume.
"""

from backend.resolve.inventory import (
    Family,
    FamilyInventory,
    FamilyType,
    load_inventory,
    save_inventory,
    starter_inventory,
)
from backend.resolve.matcher   import (
    MatchOutcome,
    MatchTier,
    canonical_type_name,
    match_column,
    shape_code,
)
from backend.resolve.resolver  import (
    StoreyResolveResult,
    resolve_storey,
)


__all__ = [
    "Family",
    "FamilyInventory",
    "FamilyType",
    "load_inventory",
    "save_inventory",
    "starter_inventory",
    "MatchOutcome",
    "MatchTier",
    "canonical_type_name",
    "match_column",
    "shape_code",
    "StoreyResolveResult",
    "resolve_storey",
]
