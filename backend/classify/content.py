"""Tier 3 — content heuristics (PLAN.md §5.3).

Lightweight vector-geometry / text checks. The heavy decisions are the LLM
judge's job (tier 4); this tier only catches obvious cases that are cheap to
detect from PyMuPDF metadata alone.
"""

from __future__ import annotations

import re
from pathlib import Path

import fitz  # type: ignore[import-untyped]

from backend.classify.types import (
    ClassificationResult,
    ClassifierTier,
    DrawingClass,
)


_SECTION_CUTLINE_RE = re.compile(r"\bSECTION\s+[A-Z]\s*[-–]\s*[A-Z]\b", re.IGNORECASE)


def _all_text(page: "fitz.Page") -> str:
    return page.get_text("text") or ""


def _long_horizontal_line_ratio(page: "fitz.Page") -> float:
    """Fraction of horizontal vector lines that span ≥60% of the page width.

    PLAN.md §5.3: vertical level lines spanning ≥60% of page width → ELEVATION.
    The plan's wording says "vertical level lines" but elevation level lines
    are HORIZONTAL on the page (a level is a horizontal datum drawn across the
    elevation view). Implementing the geometric reality.
    """
    width = page.rect.width
    if width <= 0:
        return 0.0
    long_lines = 0
    total_lines = 0
    for path in page.get_drawings():
        for item in path.get("items", []):
            if not item or item[0] != "l":
                continue
            p1, p2 = item[1], item[2]
            dx = abs(p2.x - p1.x)
            dy = abs(p2.y - p1.y)
            # Treat as horizontal if dy is small relative to dx.
            if dx == 0 or dy / max(dx, 1.0) > 0.05:
                continue
            total_lines += 1
            if dx >= 0.6 * width:
                long_lines += 1
    return long_lines / total_lines if total_lines else 0.0


def classify_content(
    pdf_path: Path,
    page_index: int = 0,
) -> ClassificationResult | None:
    with fitz.open(pdf_path) as doc:
        page = doc[page_index]
        text = _all_text(page).upper()

        m = _SECTION_CUTLINE_RE.search(text)
        if m:
            return ClassificationResult(
                drawing_class = DrawingClass.SECTION,
                tier          = ClassifierTier.CONTENT,
                confidence    = 0.8,
                reason        = "cut-line label pattern (SECTION X-Y) detected",
                signals       = {"match": m.group(0)},
            )

        long_ratio = _long_horizontal_line_ratio(page)
        if long_ratio >= 0.3:
            return ClassificationResult(
                drawing_class = DrawingClass.ELEVATION,
                tier          = ClassifierTier.CONTENT,
                confidence    = 0.7,
                reason        = f"≥30% of horizontal lines span ≥60% of page width "
                                f"(ratio={long_ratio:.2f})",
                signals       = {"long_line_ratio": long_ratio},
            )

    return None
