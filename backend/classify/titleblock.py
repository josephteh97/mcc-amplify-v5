"""Tier 2 — title-block parse (PLAN.md §5.2).

Extracts text from the bottom-right quadrant of the page (the conventional
title-block location on construction drawings) and looks for the same
keywords the filename tier checks. Catches sheets whose filenames don't
follow naming conventions but whose title block does.
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


# (keyword, class) — same order-sensitivity as the filename rules:
# PERSPECTIVE before ELEVATION.
_TITLE_KEYWORDS: list[tuple[str, DrawingClass]] = [
    ("PERSPECTIVE", DrawingClass.DISCARD),
    ("SECTION",     DrawingClass.SECTION),
    ("ELEVATION",   DrawingClass.ELEVATION),
]


def extract_title_block_text(pdf_path: Path, page_index: int = 0) -> str:
    """Return the bottom-right quadrant text. Empty string if PDF unreadable."""
    with fitz.open(pdf_path) as doc:
        page = doc[page_index]
        r = page.rect
        clip = fitz.Rect(r.width * 0.5, r.height * 0.5, r.width, r.height)
        return page.get_text("text", clip=clip) or ""


def classify_titleblock(
    pdf_path: Path,
    page_index: int = 0,
) -> ClassificationResult | None:
    text = extract_title_block_text(pdf_path, page_index).upper()
    if not text.strip():
        return None
    for keyword, drawing_class in _TITLE_KEYWORDS:
        if re.search(rf"\b{keyword}\b", text):
            return ClassificationResult(
                drawing_class = drawing_class,
                tier          = ClassifierTier.TITLE_BLOCK,
                confidence    = 0.85,
                reason        = f"title block contains {keyword!r}",
                signals       = {"keyword": keyword, "snippet": text[:200].strip()},
            )
    return None
