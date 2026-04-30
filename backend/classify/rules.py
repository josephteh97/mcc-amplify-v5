"""Tier 1 — filename keyword classifier (PLAN.md §5.1).

Order is load-bearing per §16:
  - PERSPECTIVE rule MUST precede ELEVATION (perspectives share TD-A-130-…
    prefix and would otherwise leak into ELEVATION and break Stage 3B).
  - The structural -00 vs -01..04 patterns must precede the SECTION/ELEVATION
    word matches in case a structural sheet's filename happens to contain
    those words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from backend.classify.types import (
    ClassificationResult,
    ClassifierTier,
    DrawingClass,
)


@dataclass(frozen=True)
class FilenameRule:
    pattern:       str
    drawing_class: DrawingClass


# Defaults are project-agnostic. A meta.yaml.classifier_rules block overrides
# this list entirely (so users can pin TGCH-specific patterns without losing
# determinism). Patterns are evaluated case-insensitively against the file's
# stem (no .pdf suffix) — anchors like ^/$ apply to that stem.
DEFAULT_FILENAME_RULES: list[FilenameRule] = [
    FilenameRule(r"PERSPECTIVE",          DrawingClass.DISCARD),
    FilenameRule(r"-S-\d{3}-.*-00$",      DrawingClass.STRUCT_PLAN_OVERALL),
    FilenameRule(r"-S-\d{3}-.*-0[1-4]$",  DrawingClass.STRUCT_PLAN_ENLARGED),
    FilenameRule(r"SECTION",              DrawingClass.SECTION),
    FilenameRule(r"ELEVATION",            DrawingClass.ELEVATION),
]


def classify_filename(
    filename: str,
    rules: list[FilenameRule] | None = None,
) -> ClassificationResult | None:
    """Return a high-confidence result if any filename rule fires; else None."""
    rules = rules if rules is not None else DEFAULT_FILENAME_RULES
    stem  = Path(filename).stem
    for r in rules:
        if re.search(r.pattern, stem, re.IGNORECASE):
            return ClassificationResult(
                drawing_class = r.drawing_class,
                tier          = ClassifierTier.FILENAME,
                confidence    = 1.0,
                reason        = f"filename matches {r.pattern!r}",
                signals       = {"pattern": r.pattern, "stem": stem},
            )
    return None
