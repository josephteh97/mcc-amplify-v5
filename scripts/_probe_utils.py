"""Shared helpers for the per-extractor probes (Step 3 / PLAN.md §14.3).

Probes are investigative scripts that walk the reference fixture, extract raw
PyMuPDF text/geometry, and produce catalogs that inform the Stage 3 regex
design. They do NOT implement any parser logic themselves — that's Steps 4-7.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import fitz  # type: ignore[import-untyped]


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


FIXTURE = REPO_ROOT / "tests" / "fixtures" / "sample_uploaded_documents"
REPORTS = REPO_ROOT / "data" / "probe_reports"


@dataclass(frozen=True)
class TextItem:
    text:     str
    bbox:     tuple[float, float, float, float]
    rotation: float       # degrees; 0 unless the writing direction is rotated
    page:    int


def fixture_pdfs() -> list[Path]:
    return sorted(p for p in FIXTURE.rglob("*.pdf") if p.is_file())


def filter_by_class(pdfs: list[Path], drawing_class: str) -> list[Path]:
    """Use the filename classifier to select only PDFs of a given class.

    Falls back to a simple substring rule if the classifier returns None
    (e.g. ARCH zone-plans don't filename-match — but we don't probe those
    anyway since they're DISCARDed in Stage 2).
    """
    from backend.classify.rules import classify_filename

    out: list[Path] = []
    for p in pdfs:
        r = classify_filename(p.name)
        if r is not None and r.drawing_class.value == drawing_class:
            out.append(p)
    return out


def iter_text_items(pdf_path: Path) -> Iterable[TextItem]:
    """Yield every text span on every page of a PDF.

    PyMuPDF's "dict" mode preserves bbox + rotation per span — important for
    the §3A-2 enlarged-plan probe, which has to deal with vertical labels.
    """
    with fitz.open(pdf_path) as doc:
        for page_idx, page in enumerate(doc):
            d = page.get_text("dict") or {}
            for block in d.get("blocks", []):
                for line in block.get("lines", []):
                    rotation = line.get("dir", (1.0, 0.0))
                    rot_deg = _dir_to_degrees(rotation)
                    for span in line.get("spans", []):
                        text = (span.get("text") or "").strip()
                        if not text:
                            continue
                        bbox = tuple(span.get("bbox", (0, 0, 0, 0)))
                        yield TextItem(
                            text     = text,
                            bbox     = bbox,
                            rotation = rot_deg,
                            page     = page_idx,
                        )


def _dir_to_degrees(direction: tuple[float, float]) -> float:
    """PyMuPDF returns a unit vector for line direction; convert to degrees.

    Horizontal left-to-right is (1, 0) → 0°. Vertical bottom-to-top is (0, -1)
    → 90°. Most labels are 0° or 90°.
    """
    import math

    if not direction or len(direction) < 2:
        return 0.0
    dx, dy = direction[0], direction[1]
    return round(math.degrees(math.atan2(-dy, dx)), 1)


def write_report(name: str, payload: dict) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    p = REPORTS / f"{name}.json"
    with open(p, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=False, default=str)
    return p


def print_top(counter: Counter, label: str, n: int = 20) -> None:
    print(f"\n--- top {n} {label} ---")
    for value, count in counter.most_common(n):
        print(f"  {count:5}  {value}")
