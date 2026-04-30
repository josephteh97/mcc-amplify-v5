"""Stage 3B — ELEVATION extractor (PLAN.md §3B).

Reduced scope per v5.3 plan: extracts level names + reduced levels (RLs)
only. Column continuity is deferred. Result is a per-PDF JSON of
``{levels: [{name, rl_mm, source_pdf}], floor_to_floor_mm: [...]}``
that the reconciler in Step 8 merges with the structural-plan storey list.
"""

from backend.extract.elevation.extract import (
    ElevationExtractResult,
    extract_elevation,
)
from backend.extract.elevation.labels  import (
    LEVEL_NAME_RE,
    RL_FFL_RE,
    RL_MM_RE,
    LevelSpan,
    RLSpan,
    extract_level_and_rl_spans,
)


__all__ = [
    "ElevationExtractResult",
    "extract_elevation",
    "LEVEL_NAME_RE",
    "RL_FFL_RE",
    "RL_MM_RE",
    "LevelSpan",
    "RLSpan",
    "extract_level_and_rl_spans",
]
