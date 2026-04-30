"""Probe: SECTION slab/beam thickness catalog (Step 3 / PLAN.md §3C).

For every SECTION page:
  - find SECTION cut-line patterns (SECTION A-A, A-A, etc.)
  - find slab thickness annotations (e.g. "150 SLAB", "T=200")
  - find beam depth annotations    (e.g. "600 DEEP", "RCB-1 600x300")
  - count horizontal hatched bands as a rough slab-cross-section proxy

Section parsing is the hardest of the four extractors because slab/beam depths
aren't always explicitly labeled — sometimes inferred from drawing geometry.
This probe lists every text candidate so we know what the regex needs to
catch in Step 7.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from _probe_utils import (
    fixture_pdfs,
    filter_by_class,
    iter_text_items,
    print_top,
    write_report,
)


SECTION_LABEL_RE   = re.compile(r"^SECTION\s+([A-Z])\s*[-–]\s*([A-Z])$|^([A-Z])\s*[-–]\s*([A-Z])$",
                                re.IGNORECASE)
THICK_PATTERNS = [
    re.compile(r"^\d{2,4}\s*(SLAB|THK|THICK|MM|mm)$", re.IGNORECASE),
    re.compile(r"^T\s*=\s*\d{2,4}$", re.IGNORECASE),
    re.compile(r"^\d{2,4}\s*DEEP$",  re.IGNORECASE),
]
NUMERIC_RE  = re.compile(r"^\d{2,4}$")
DIM_PAIR_RE = re.compile(r"^\d{2,4}\s*[xX×]\s*\d{2,4}$")
LEVEL_RE    = re.compile(r"^(B\d|L\d+|RF|UR|MEZZ|GF|GL)\b", re.IGNORECASE)


def main() -> None:
    pdfs = filter_by_class(fixture_pdfs(), "SECTION")
    print(f"Probing {len(pdfs)} SECTION page(s)…")

    text_total = 0
    section_labels:  Counter[str] = Counter()
    thickness_annos: Counter[str] = Counter()
    bare_numerics:   Counter[str] = Counter()
    dim_pairs:       Counter[str] = Counter()
    level_refs:      Counter[str] = Counter()
    other_short:     Counter[str] = Counter()

    samples_per_pdf: dict[str, list[str]] = {}

    for pdf in pdfs:
        per_pdf = []
        for it in iter_text_items(pdf):
            text_total += 1
            t = it.text.strip()

            sm = SECTION_LABEL_RE.match(t)
            if sm:
                section_labels[t.upper()] += 1
                continue

            if any(p.match(t) for p in THICK_PATTERNS):
                thickness_annos[t] += 1
                continue

            if DIM_PAIR_RE.match(t):
                dim_pairs[t] += 1
                continue

            if NUMERIC_RE.match(t):
                bare_numerics[t] += 1
                continue

            if LEVEL_RE.match(t):
                level_refs[t.upper()] += 1
                continue

            if 2 < len(t) <= 14:
                other_short[t] += 1
                if len(per_pdf) < 50:
                    per_pdf.append(t)
        samples_per_pdf[pdf.name] = per_pdf

    payload = {
        "scanned": {
            "pdfs":       len(pdfs),
            "text_items": text_total,
        },
        "section_labels":  section_labels.most_common(20),
        "thickness_annos": thickness_annos.most_common(40),
        "dim_pairs":       dim_pairs.most_common(40),
        "bare_numerics":   bare_numerics.most_common(40),
        "level_refs":      level_refs.most_common(40),
        "other_short_top": other_short.most_common(60),
        "per_pdf_sample":  samples_per_pdf,
    }
    out = write_report("section", payload)

    print(f"\nText items scanned: {text_total} across {len(pdfs)} pages")
    print(f"Section labels:     {sum(section_labels.values())} hits / {len(section_labels)} uniques")
    print(f"Thickness annos:    {sum(thickness_annos.values())} hits / {len(thickness_annos)} uniques")
    print(f"Dimension pairs:    {sum(dim_pairs.values())} hits / {len(dim_pairs)} uniques")
    print(f"Bare numerics:      {sum(bare_numerics.values())} hits / {len(bare_numerics)} uniques")
    print(f"Level refs:         {sum(level_refs.values())} hits / {len(level_refs)} uniques")
    print_top(section_labels, "section labels", 10)
    print_top(thickness_annos, "thickness annotations", 15)
    print_top(dim_pairs, "dimension pairs", 15)
    print_top(bare_numerics, "bare numerics (likely thickness/depth refs)", 15)
    print_top(other_short, "other short text (regex widening candidates)", 25)
    print(f"\nReport written → {out}")


if __name__ == "__main__":
    main()
