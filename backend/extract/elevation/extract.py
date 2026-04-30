"""Per-PDF elevation extractor (PLAN.md §3B).

A single elevation PDF typically shows 2–4 elevation views on the same
page (north / south / east / west, or just left/right). Each view repeats
the same level set, so the same level name appears 2–4 times.

Pipeline:

  1. extract_level_and_rl_spans on every page of the PDF.
  2. Pair each LevelSpan with its nearest RLSpan in screen-space — the
     fixture has the level name above the FFL by ~10 pt, both at the
     drawing's right edge.
  3. Deduplicate: group pairs by canonical name, take the median RL.
     Flag if any pair within a group disagrees beyond
     LEVEL_AGREEMENT_TOL_MM (25 mm) — strict-mode (PLAN §11).
  4. Sort by rl_mm ascending and compute ``floor_to_floor_mm[i] =
     rl[i+1] − rl[i]``.
  5. Emit ``extracted/elevation/<pdf_stem>.elev.json``.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

import fitz  # type: ignore[import-untyped]
from loguru import logger

from backend.core.grid_mm                  import LEVEL_AGREEMENT_TOL_MM
from backend.extract.elevation.labels      import (
    LevelSpan,
    RLSpan,
    extract_level_and_rl_spans,
)


PAIR_MAX_DIST_PT = 30.0       # name-to-RL search radius. Fixture: ~10 pt typical.
PAIR_MIN_DY_PT   = 3.0        # the FFL must sit *below* the name by at least this
                              # — same-line spans like a stray "BASEMENT 1" tag at
                              # y=829.6 next to FFL+9.50 at y=829.8 would otherwise
                              # mispair (see TD-A-130-01-01 spread of 6000 mm).
PAIR_MAX_DX_PT   = 25.0       # name and FFL share an X column on the drawing


@dataclass
class ElevationExtractResult:
    pdf_path:       Path
    pdf_stem:       str
    page_count:     int
    level_count:    int          # uniques after dedupe
    payload_path:   Path | None
    error:          str | None    = None
    flags:          list[str]     = field(default_factory=list)


def _euclid(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _pair_levels_with_rls(
    levels: list[LevelSpan],
    rls:    list[RLSpan],
    radius_pt: float = PAIR_MAX_DIST_PT,
) -> list[tuple[LevelSpan, RLSpan, float]]:
    """Greedy nearest-neighbour pairing. Returns (level, rl, distance) tuples.

    A LevelSpan with no RL within ``radius_pt`` is dropped. The same RL
    can satisfy multiple level spans only if they're equidistant (rare on
    real elevations); we don't enforce uniqueness of RL→level since the
    deduper downstream collapses by level name anyway.
    """
    out: list[tuple[LevelSpan, RLSpan, float]] = []
    for lvl in levels:
        best: tuple[RLSpan, float] | None = None
        for rl in rls:
            dx = abs(rl.centre_pt[0] - lvl.centre_pt[0])
            dy = rl.centre_pt[1] - lvl.centre_pt[1]   # signed: +ve = RL below name
            if dx > PAIR_MAX_DX_PT:
                continue
            if dy < PAIR_MIN_DY_PT:
                continue
            d = _euclid(lvl.centre_pt, rl.centre_pt)
            if d > radius_pt:
                continue
            if best is None or d < best[1]:
                best = (rl, d)
        if best is not None:
            out.append((lvl, best[0], best[1]))
    return out


def _dedupe_levels(
    pairs: list[tuple[LevelSpan, RLSpan, float]],
    tol_mm: float = LEVEL_AGREEMENT_TOL_MM,
) -> tuple[list[dict], list[str]]:
    """Group pairs by canonical level name, return (levels, flags).

    Each output dict:
      {"name": str, "rl_mm": int, "n_views": int, "rl_spread_mm": int}

    Flags emitted when the spread within a group exceeds ``tol_mm`` —
    strict-mode disagreement signal that goes into the review queue.
    """
    by_name: dict[str, list[int]] = defaultdict(list)
    for lvl, rl, _d in pairs:
        by_name[lvl.name.upper()].append(rl.rl_mm)

    out: list[dict] = []
    flags: list[str] = []
    for name, vals in by_name.items():
        med = int(round(median(vals)))
        spread = max(vals) - min(vals)
        if spread > tol_mm:
            flags.append(
                f"level_disagreement: {name!r} spans {spread} mm "
                f"across {len(vals)} views (>{int(tol_mm)} mm tol)"
            )
        out.append({
            "name":         name,
            "rl_mm":        med,
            "n_views":      len(vals),
            "rl_spread_mm": int(spread),
        })
    out.sort(key=lambda r: r["rl_mm"])
    return out, flags


def _build_payload(
    pdf_path:           Path,
    page_count:         int,
    levels:             list[dict],
    flags:              list[str],
    raw_level_count:    int,
    raw_rl_count:       int,
    paired_count:       int,
) -> dict:
    floor_to_floor_mm = [
        levels[i + 1]["rl_mm"] - levels[i]["rl_mm"]
        for i in range(len(levels) - 1)
    ]
    return {
        "source_pdf":         pdf_path.name,
        "pdf_stem":           pdf_path.stem,
        "page_count":         page_count,
        "stats": {
            "raw_level_spans": raw_level_count,
            "raw_rl_spans":    raw_rl_count,
            "paired_pairs":    paired_count,
            "unique_levels":   len(levels),
        },
        "levels":             [{"name": l["name"],
                                "rl_mm": l["rl_mm"],
                                "n_views": l["n_views"],
                                "rl_spread_mm": l["rl_spread_mm"],
                                "source_pdf": pdf_path.name}
                               for l in levels],
        "floor_to_floor_mm":  floor_to_floor_mm,
        "flags":              flags,
    }


def extract_elevation(pdf_path: Path, out_dir: Path) -> ElevationExtractResult:
    """Run elevation extraction across every page of one elevation PDF.

    Always writes ``out_dir/<pdf_stem>.elev.json``. Failures surface as
    flags + level_count=0 — never raised exceptions (orchestrator runs
    this best-effort per PDF).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    flags: list[str] = []
    all_levels: list[LevelSpan] = []
    all_rls:    list[RLSpan]    = []
    page_count = 0

    with fitz.open(pdf_path) as doc:
        page_count = doc.page_count
        for page in doc:
            lvls, rls = extract_level_and_rl_spans(page)
            all_levels.extend(lvls)
            all_rls.extend(rls)

    pairs = _pair_levels_with_rls(all_levels, all_rls)
    if not pairs:
        flags.append("no_level_rl_pairs")
        logger.warning(
            f"{pdf_path.name}: extracted {len(all_levels)} levels and "
            f"{len(all_rls)} RLs but none paired within {PAIR_MAX_DIST_PT} pt"
        )

    unique_levels, dedupe_flags = _dedupe_levels(pairs)
    flags.extend(dedupe_flags)

    payload = _build_payload(
        pdf_path        = pdf_path,
        page_count      = page_count,
        levels          = unique_levels,
        flags           = flags,
        raw_level_count = len(all_levels),
        raw_rl_count    = len(all_rls),
        paired_count    = len(pairs),
    )
    payload_path = out_dir / f"{pdf_path.stem}.elev.json"
    with open(payload_path, "w") as f:
        json.dump(payload, f, indent=2)

    return ElevationExtractResult(
        pdf_path     = pdf_path,
        pdf_stem     = pdf_path.stem,
        page_count   = page_count,
        level_count  = len(unique_levels),
        payload_path = payload_path,
        error        = None,
        flags        = flags,
    )
