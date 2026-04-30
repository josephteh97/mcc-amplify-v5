"""Probe: ELEVATION level/RL catalog (Step 3 / PLAN.md §3B).

For every ELEVATION page:
  - find candidates matching LEVEL_NAME_RE  (B1, L1, RF, UR, etc.)
  - find candidates matching RL_RE          (+9.500, -3000, +12500mm, …)
  - report the actual RL form variations found, since notation varies
    (decimal vs whole-number, units suffix, SFL/UFL annotations, …)
  - optional pairing: nearest level-name to each RL, to estimate baseline
    coverage before the real Step 6 extractor.
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


LEVEL_NAME_RE  = re.compile(r"^(B\d|L\d+|RF|UR|MEZZ|GF|GL)\b", re.IGNORECASE)
RL_RE          = re.compile(r"^[+\-]?\d+(?:\.\d+)?\s*(?:mm|m|MM|M)?$")
RL_LOOSE_RE    = re.compile(r"[+\-]?\d+(?:\.\d+)?\s*(?:mm|m|MM|M)?\b.*?(SFL|UFL|FFL|TOC|TOS)?", re.IGNORECASE)


def _bbox_center(bbox):
    return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)


def _euclidean(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def main() -> None:
    pdfs = filter_by_class(fixture_pdfs(), "ELEVATION")
    print(f"Probing {len(pdfs)} ELEVATION page(s)…")

    text_total = 0
    level_names: Counter[str] = Counter()
    rl_strict:   Counter[str] = Counter()
    rl_loose:    Counter[str] = Counter()
    annotations: Counter[str] = Counter()  # SFL/UFL/FFL/TOC suffixes

    pairs: list[dict] = []

    for pdf in pdfs:
        items = list(iter_text_items(pdf))
        text_total += len(items)

        # First pass — collect candidates.
        levels = []
        rls    = []
        for it in items:
            t = it.text.strip()
            m = LEVEL_NAME_RE.match(t)
            if m:
                level_names[m.group(0).upper()] += 1
                levels.append((it, m.group(0).upper()))

            if RL_RE.match(t):
                rl_strict[t] += 1
                rls.append((it, t))
            else:
                lm = RL_LOOSE_RE.search(t)
                if lm and any(c.isdigit() for c in t):
                    rl_loose[t] += 1
                    if lm.group(1):
                        annotations[lm.group(1).upper()] += 1

        # Pair each level with the nearest RL on the same page.
        for lvl_item, lvl_text in levels:
            lvl_c = _bbox_center(lvl_item.bbox)
            best = None
            for rl_item, rl_text in rls:
                d = _euclidean(lvl_c, _bbox_center(rl_item.bbox))
                if best is None or d < best[0]:
                    best = (d, rl_text)
            if best is not None and len(pairs) < 30:
                pairs.append({
                    "pdf":      pdf.name,
                    "level":    lvl_text,
                    "rl":       best[1],
                    "distance_pt": round(best[0], 2),
                })

    payload = {
        "scanned": {
            "pdfs":       len(pdfs),
            "text_items": text_total,
        },
        "level_names":     level_names.most_common(40),
        "rl_strict_top":   rl_strict.most_common(40),
        "rl_loose_top":    rl_loose.most_common(40),
        "rl_annotations":  annotations.most_common(),
        "pair_examples":   pairs,
    }
    out = write_report("elevation", payload)

    print(f"\nText items scanned: {text_total} across {len(pdfs)} pages")
    print(f"Level names: {sum(level_names.values())} hits, {len(level_names)} uniques")
    print(f"RL (strict): {sum(rl_strict.values())} hits, {len(rl_strict)} uniques")
    print(f"RL (loose-only, regex misses): {sum(rl_loose.values())} hits, {len(rl_loose)} uniques")
    if annotations:
        print(f"Suffix annotations seen: {dict(annotations)}")
    print_top(level_names, "level names", 20)
    print_top(rl_strict,   "strict RL values", 20)
    print_top(rl_loose,    "loose-only RL candidates (regex widening hints)", 20)
    print(f"\nReport written → {out}")


if __name__ == "__main__":
    main()
