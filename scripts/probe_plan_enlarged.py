"""Probe: STRUCT_PLAN_ENLARGED label/dim catalog (Step 3 / PLAN.md §3A-2).

Walks every -01..04 page and enumerates:
  - vector text content + rotation distribution
  - matches against TYPE_CODE_RE / SECTION_RE / DIA_RE
  - candidate label/dim pairs by spatial proximity
  - text that LOOKS like a label/dim but doesn't match — feedback to Step 5
    when we widen the regexes.

Run:
    python scripts/probe_plan_enlarged.py
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

from _probe_utils import (
    fixture_pdfs,
    filter_by_class,
    iter_text_items,
    print_top,
    write_report,
)

# Pulled from grid_mm.py — duplicated locally so probes don't carry an import
# coupling that would force scripts to be re-run on backend changes.
TYPE_CODE_RE       = re.compile(r"^(H-)?[A-Z]{1,3}\d+$")
RECT_DIM_RE        = re.compile(r"^\d{3,4}\s*[xX]\s*\d{3,4}$")
DIA_RE             = re.compile(r"^[ØøD]\s*\d{3,4}$|^\d{3,4}\s*(?:DIA|dia|Ø|ø)$")

# A wider net to surface candidates that the strict regex misses.
LOOSE_TYPE_RE      = re.compile(r"^[A-Z]{1,4}[-_]?\d+[A-Z]?$")           # e.g. C2A, RCB-12, B7
LOOSE_RECT_RE      = re.compile(r"^\d{2,5}\s*[xX×]\s*\d{2,5}$")          # 50x100, 1500×800
LOOSE_DIA_RE       = re.compile(r"\b\d{2,5}\s*(?:Ø|ø|DIA|dia|D)\b|\bØ\s*\d{2,5}\b")
NUMERIC_ONLY_RE    = re.compile(r"^\d{2,5}$")


def _bbox_diagonal(bbox: tuple[float, float, float, float]) -> float:
    x0, y0, x1, y1 = bbox
    return ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5


def _bbox_center(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    x0, y0, x1, y1 = bbox
    return ((x0 + x1) / 2, (y0 + y1) / 2)


def _euclidean(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def main() -> None:
    pdfs = filter_by_class(fixture_pdfs(), "STRUCT_PLAN_ENLARGED")
    print(f"Probing {len(pdfs)} STRUCT_PLAN_ENLARGED page(s)…")

    text_total = 0
    by_rotation: Counter[float] = Counter()

    type_codes_strict: Counter[str] = Counter()
    type_codes_loose:  Counter[str] = Counter()
    rect_dims_strict:  Counter[str] = Counter()
    rect_dims_loose:   Counter[str] = Counter()
    dias_strict:       Counter[str] = Counter()
    dias_loose:        Counter[str] = Counter()
    numeric_only:      Counter[str] = Counter()
    other_short:       Counter[str] = Counter()   # short text that didn't match anything

    # label → set of dim strings observed within proximity, per page
    label_to_dims: dict[str, set[str]] = defaultdict(set)
    pair_examples: list[dict] = []

    for pdf in pdfs:
        page_items: list = list(iter_text_items(pdf))
        text_total += len(page_items)

        # Per-page bookkeeping for proximity pairing.
        codes_per_page = []
        dims_per_page  = []

        for item in page_items:
            t = item.text
            by_rotation[item.rotation] += 1

            if TYPE_CODE_RE.match(t):
                type_codes_strict[t] += 1
                codes_per_page.append(item)
            elif LOOSE_TYPE_RE.match(t):
                type_codes_loose[t] += 1

            if RECT_DIM_RE.match(t):
                rect_dims_strict[t] += 1
                dims_per_page.append(("rect", t, item))
            elif LOOSE_RECT_RE.match(t):
                rect_dims_loose[t] += 1

            if DIA_RE.match(t):
                dias_strict[t] += 1
                dims_per_page.append(("dia", t, item))
            elif LOOSE_DIA_RE.search(t):
                dias_loose[t] += 1

            if NUMERIC_ONLY_RE.match(t):
                numeric_only[t] += 1
            elif (1 < len(t) <= 8
                  and not TYPE_CODE_RE.match(t)
                  and not RECT_DIM_RE.match(t)
                  and not DIA_RE.match(t)
                  and not NUMERIC_ONLY_RE.match(t)):
                other_short[t] += 1

        # Naive proximity pair: each label paired to the nearest dim on the
        # same page. Real associator (Step 5) uses bbox diagonal × multiplier;
        # this is a probe just to estimate hit-rate.
        for code_item in codes_per_page:
            code_center = _bbox_center(code_item.bbox)
            best = None
            for kind, dim_text, dim_item in dims_per_page:
                d = _euclidean(code_center, _bbox_center(dim_item.bbox))
                if best is None or d < best[0]:
                    best = (d, kind, dim_text, dim_item)
            if best is not None and best[0] < _bbox_diagonal(code_item.bbox) * 4.0:
                label_to_dims[code_item.text].add(best[2])
                if len(pair_examples) < 30:
                    pair_examples.append({
                        "pdf":      pdf.name,
                        "label":    code_item.text,
                        "dim":      best[2],
                        "dim_kind": best[1],
                        "distance_pt": round(best[0], 2),
                        "label_diagonal_pt": round(_bbox_diagonal(code_item.bbox), 2),
                    })

    payload = {
        "scanned": {
            "pdfs":  len(pdfs),
            "text_items": text_total,
        },
        "rotation_distribution": dict(by_rotation),
        "matches": {
            "TYPE_CODE_RE_strict":   {"hits": sum(type_codes_strict.values()), "uniques": len(type_codes_strict)},
            "TYPE_CODE_loose_only":  {"hits": sum(type_codes_loose.values()),  "uniques": len(type_codes_loose)},
            "RECT_DIM_RE_strict":    {"hits": sum(rect_dims_strict.values()),  "uniques": len(rect_dims_strict)},
            "RECT_DIM_loose_only":   {"hits": sum(rect_dims_loose.values()),   "uniques": len(rect_dims_loose)},
            "DIA_RE_strict":         {"hits": sum(dias_strict.values()),       "uniques": len(dias_strict)},
            "DIA_loose_only":        {"hits": sum(dias_loose.values()),        "uniques": len(dias_loose)},
            "numeric_only":          {"hits": sum(numeric_only.values()),      "uniques": len(numeric_only)},
        },
        "label_to_dims":  {k: sorted(v) for k, v in sorted(label_to_dims.items())},
        "pair_examples":  pair_examples,
        "top_strict_type_codes":  type_codes_strict.most_common(40),
        "top_loose_type_codes":   type_codes_loose.most_common(20),
        "top_strict_rect_dims":   rect_dims_strict.most_common(40),
        "top_loose_rect_dims":    rect_dims_loose.most_common(20),
        "top_strict_dias":        dias_strict.most_common(40),
        "top_loose_dias":         dias_loose.most_common(20),
        "top_other_short":        other_short.most_common(40),
    }

    out = write_report("plan_enlarged", payload)

    print(f"\nText items scanned: {text_total} across {len(pdfs)} pages")
    print(f"Rotation distribution: {dict(by_rotation)}")
    print(f"\nMatches:")
    for k, v in payload["matches"].items():
        print(f"  {k:28} hits={v['hits']:5}  uniques={v['uniques']:4}")
    print(f"\nLabel→dims pairs found: {len(label_to_dims)} unique labels")
    print_top(type_codes_strict, "strict TYPE_CODE matches", 15)
    print_top(rect_dims_strict,  "strict RECT_DIM matches", 15)
    print_top(dias_strict,       "strict DIA matches", 15)
    print_top(type_codes_loose,  "LOOSE-ONLY type codes (regex misses)", 15)
    print_top(rect_dims_loose,   "LOOSE-ONLY rect dims  (regex misses)", 15)
    print(f"\nReport written → {out}")


if __name__ == "__main__":
    main()
