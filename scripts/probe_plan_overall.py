"""Probe: STRUCT_PLAN_OVERALL grid-bubble candidate catalog (Step 3 / PLAN.md §3A-1).

Walks every -00 page and enumerates:
  - all single-character / 1-2 digit text items (grid-bubble candidates)
  - their position relative to the page perimeter (helps validate the
    "grid bubbles around the perimeter" assumption)
  - the count of horizontal vs vertical labels (the two grid axes)

Produces NO geometry — that's Step 4's job. The probe just confirms grid
labels are present in the vector text layer (vs being raster-only or burned
into a path).
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import fitz  # type: ignore[import-untyped]

from _probe_utils import (
    fixture_pdfs,
    filter_by_class,
    iter_text_items,
    print_top,
    write_report,
)

GRID_LABEL_RE = re.compile(r"^[A-Z]{1,2}$|^\d{1,2}$")


def main() -> None:
    pdfs = filter_by_class(fixture_pdfs(), "STRUCT_PLAN_OVERALL")
    print(f"Probing {len(pdfs)} STRUCT_PLAN_OVERALL page(s)…")

    text_total = 0
    grid_label_hits: Counter[str] = Counter()
    grid_labels_per_pdf: dict[str, int] = {}

    perimeter_band_pct = 0.10  # bubbles within 10% of edge are "perimeter"
    perimeter_letter_count = 0
    perimeter_digit_count  = 0
    interior_label_count   = 0

    sample_positions: list[dict] = []

    for pdf in pdfs:
        with fitz.open(pdf) as doc:
            page = doc[0]
            w, h = page.rect.width, page.rect.height

        per_pdf = 0
        for item in iter_text_items(pdf):
            text_total += 1
            t = item.text.strip()
            if GRID_LABEL_RE.match(t):
                grid_label_hits[t] += 1
                per_pdf += 1
                cx = (item.bbox[0] + item.bbox[2]) / 2
                cy = (item.bbox[1] + item.bbox[3]) / 2

                near_left   = cx <  w * perimeter_band_pct
                near_right  = cx >  w * (1 - perimeter_band_pct)
                near_top    = cy <  h * perimeter_band_pct
                near_bottom = cy >  h * (1 - perimeter_band_pct)
                on_perimeter = near_left or near_right or near_top or near_bottom

                if on_perimeter:
                    if t.isalpha():
                        perimeter_letter_count += 1
                    else:
                        perimeter_digit_count += 1
                else:
                    interior_label_count += 1

                if len(sample_positions) < 25 and on_perimeter:
                    sample_positions.append({
                        "pdf":  pdf.name,
                        "text": t,
                        "cx_pct": round(cx / w, 3),
                        "cy_pct": round(cy / h, 3),
                        "rotation": item.rotation,
                    })
        grid_labels_per_pdf[pdf.name] = per_pdf

    payload = {
        "scanned": {
            "pdfs":       len(pdfs),
            "text_items": text_total,
        },
        "grid_label_summary": {
            "total_hits":             sum(grid_label_hits.values()),
            "uniques":                len(grid_label_hits),
            "perimeter_letters":      perimeter_letter_count,
            "perimeter_digits":       perimeter_digit_count,
            "interior_label_count":   interior_label_count,
        },
        "labels_per_pdf":     grid_labels_per_pdf,
        "top_grid_labels":    grid_label_hits.most_common(40),
        "sample_positions":   sample_positions,
    }
    out = write_report("plan_overall", payload)

    print(f"\nText items scanned: {text_total} across {len(pdfs)} pages")
    print(f"Grid-label candidates: {sum(grid_label_hits.values())} hits, "
          f"{len(grid_label_hits)} unique values")
    print(f"  perimeter (letters): {perimeter_letter_count}")
    print(f"  perimeter (digits):  {perimeter_digit_count}")
    print(f"  interior (suspicious): {interior_label_count}")
    print(f"\nLabels per PDF (first 6):")
    for k, v in list(grid_labels_per_pdf.items())[:6]:
        print(f"  {v:4}  {k}")
    print_top(grid_label_hits, "grid-label values", 20)
    print(f"\nReport written → {out}")


if __name__ == "__main__":
    main()
