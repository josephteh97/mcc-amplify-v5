"""Probe: column W×H orientation convention (Step 3 / PLAN.md §3A-2).

The §3A-2 strategy says: for an unequal "W×H" annotation, the larger annotation
dim should map to the longer bbox axis. But which is "first" — the X-extent or
the longer side — is consultant-specific. Some firms write `width × height`
(geometric, X×Y on page); some write `longer × shorter`.

Strategy: pair each asymmetric annotation with the nearest small filled rect
path on the same page (these are columns in the vector layer of -01..04
enlarged plans). Compare aspect ratios two ways:
  • "X×Y" hypothesis  — annotation_first ↔ bbox dx
  • "L×S" hypothesis  — annotation_first ↔ bbox max(dx,dy)
For each pair, score which hypothesis is consistent within tolerance. Tally
across the fixture; whichever hypothesis dominates is THIS consultant's
convention. Mixed results = convention varies, and the VLM in Step 5 must
disambiguate per-element.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

import fitz  # type: ignore[import-untyped]

from _probe_utils import (
    fixture_pdfs,
    filter_by_class,
    print_top,
    write_report,
)


RECT_DIM_RE = re.compile(r"^(\d{3,4})\s*[xX]\s*(\d{3,4})$")
ASPECT_TOL  = 0.15   # PLAN.md §13


def _bbox_center(bbox):
    return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)


def _euclidean(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _column_candidate_paths(page) -> list[dict]:
    """Filled rect paths plausibly representing column glyphs.

    Tuned against the fixture (TGCH structural enlarged plans) where columns
    render as 11–80 pt filled rects. Excludes very small (text background)
    and very long (walls / hatching strokes).
    """
    out = []
    for d in page.get_drawings():
        if not d.get("fill"):
            continue
        r = d.get("rect")
        if r is None:
            continue
        dx, dy = r.width, r.height
        if not (8 <= dx <= 100 and 8 <= dy <= 100):
            continue
        # Exclude near-line strokes (one dimension nearly zero).
        if min(dx, dy) < 4:
            continue
        out.append({
            "rect":   tuple(r),
            "dx":     dx,
            "dy":     dy,
            "center": ((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2),
        })
    return out


def main() -> None:
    pdfs = filter_by_class(fixture_pdfs(), "STRUCT_PLAN_ENLARGED")
    print(f"Probing {len(pdfs)} STRUCT_PLAN_ENLARGED page(s) for column orientation…")

    asymmetric_total = 0
    pair_attempts    = 0
    pair_succeeded   = 0

    xy_consistent  = 0
    ls_consistent  = 0
    both_consistent = 0
    neither_consistent = 0

    by_page_xy = defaultdict(int)
    by_page_ls = defaultdict(int)
    examples: list[dict] = []
    annotation_first_vs_second = Counter()  # {"first>second": n, "first<second": n}

    for pdf in pdfs:
        with fitz.open(pdf) as doc:
            page  = doc[0]
            paths = _column_candidate_paths(page)

            text_dict = page.get_text("dict") or {}
            for block in text_dict.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        t = (span.get("text") or "").strip()
                        m = RECT_DIM_RE.match(t)
                        if not m:
                            continue
                        a, b = int(m.group(1)), int(m.group(2))
                        if a == b:
                            continue
                        asymmetric_total += 1
                        annotation_first_vs_second[
                            "first>second" if a > b else "first<second"
                        ] += 1

                        bbox = span.get("bbox")
                        if not bbox:
                            continue
                        text_center = _bbox_center(bbox)

                        # Find the nearest column-candidate path within a
                        # generous radius (3× the text bbox diagonal).
                        text_diag = _euclidean(
                            (bbox[0], bbox[1]), (bbox[2], bbox[3]),
                        )
                        radius = max(text_diag * 3.0, 30.0)
                        nearest = None
                        for p in paths:
                            d = _euclidean(text_center, p["center"])
                            if nearest is None or d < nearest[0]:
                                nearest = (d, p)
                        if nearest is None or nearest[0] > radius:
                            continue
                        pair_attempts += 1
                        d_path = nearest[1]
                        dx, dy = d_path["dx"], d_path["dy"]

                        # Hypothesis 1: X × Y on page.
                        ann_ratio_xy  = a / b
                        bbox_ratio_xy = dx / dy
                        agree_xy = abs(
                            (ann_ratio_xy - bbox_ratio_xy) / max(ann_ratio_xy, bbox_ratio_xy)
                        ) <= ASPECT_TOL

                        # Hypothesis 2: longer × shorter (size order).
                        ann_long, ann_short = max(a, b), min(a, b)
                        bbox_long, bbox_short = max(dx, dy), min(dx, dy)
                        ann_ratio_ls  = ann_long / ann_short
                        bbox_ratio_ls = bbox_long / bbox_short
                        agree_ls = abs(
                            (ann_ratio_ls - bbox_ratio_ls) / max(ann_ratio_ls, bbox_ratio_ls)
                        ) <= ASPECT_TOL

                        pair_succeeded += 1
                        if agree_xy:
                            xy_consistent += 1
                            by_page_xy[pdf.name] += 1
                        if agree_ls:
                            ls_consistent += 1
                            by_page_ls[pdf.name] += 1
                        if agree_xy and agree_ls:
                            both_consistent += 1
                        if not (agree_xy or agree_ls):
                            neither_consistent += 1

                        if len(examples) < 30:
                            examples.append({
                                "pdf":           pdf.name,
                                "annotation":    f"{a}x{b}",
                                "ann_ratio_xy":  round(ann_ratio_xy, 3),
                                "ann_ratio_ls":  round(ann_ratio_ls, 3),
                                "bbox_dx":       round(dx, 1),
                                "bbox_dy":       round(dy, 1),
                                "bbox_ratio_xy": round(bbox_ratio_xy, 3),
                                "bbox_ratio_ls": round(bbox_ratio_ls, 3),
                                "agree_xy":      agree_xy,
                                "agree_ls":      agree_ls,
                                "distance_pt":   round(nearest[0], 2),
                            })

    payload = {
        "scanned":               {"pdfs": len(pdfs)},
        "asymmetric_total":      asymmetric_total,
        "pair_attempts":         pair_attempts,
        "pair_succeeded":        pair_succeeded,
        "annotation_first_vs_second": dict(annotation_first_vs_second),
        "verdict_counts": {
            "xy_consistent":      xy_consistent,
            "ls_consistent":      ls_consistent,
            "both_consistent":    both_consistent,
            "neither_consistent": neither_consistent,
        },
        "verdict_rates_pct": {
            "xy_consistent":      round(100 * xy_consistent      / max(pair_succeeded, 1), 1),
            "ls_consistent":      round(100 * ls_consistent      / max(pair_succeeded, 1), 1),
            "both_consistent":    round(100 * both_consistent    / max(pair_succeeded, 1), 1),
            "neither_consistent": round(100 * neither_consistent / max(pair_succeeded, 1), 1),
        },
        "by_page_xy_top": Counter(by_page_xy).most_common(10),
        "by_page_ls_top": Counter(by_page_ls).most_common(10),
        "examples":       examples,
    }
    out = write_report("column_orientation", payload)

    print(f"\nAsymmetric annotations seen: {asymmetric_total}")
    print(f"  first > second: {annotation_first_vs_second['first>second']}")
    print(f"  first < second: {annotation_first_vs_second['first<second']}")
    print(f"\nPair attempts (label paired with a vector rect path): {pair_attempts}")
    print(f"Pairs scored:                                          {pair_succeeded}")
    print(f"\nHypothesis verdicts:")
    print(f"  X×Y      consistent: {xy_consistent:5}  ({payload['verdict_rates_pct']['xy_consistent']}%)")
    print(f"  L×S      consistent: {ls_consistent:5}  ({payload['verdict_rates_pct']['ls_consistent']}%)")
    print(f"  both     consistent: {both_consistent:5}  ({payload['verdict_rates_pct']['both_consistent']}%)")
    print(f"  neither  consistent: {neither_consistent:5}  ({payload['verdict_rates_pct']['neither_consistent']}%)")
    print(f"\nReport written → {out}")


if __name__ == "__main__":
    main()
