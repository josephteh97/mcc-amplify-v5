"""Grid bubble detection from PDF vector text (PLAN.md §3A-1, PROBE §3A-1).

This is the v5.3 renovation of v4's grid_detector.py (text-based v26):

  - Grid labels read directly from page.get_text("dict") — no OCR, no image
    processing. PROBE §3A-1 shows 1,967 candidates / 14 pages, fully covered
    by a 1–2 char alphanumeric filter.
  - V-lines (vertical, numbered) live in the top/bottom margin bands.
  - H-lines (horizontal, lettered) live in the left/right margin bands.
  - Each label is paired with its image-pixel coordinate at detection time.
    Position = median pixel coordinate of all occurrences of that label
    (left margin + right margin for H-labels, top + bottom for V-labels).
  - PROBE §3A-1 found 60% interior false positives (e.g. "SB" annotations)
    when filtering by character class alone. We add a coarse 10% perimeter-
    band pre-filter, then v26's median-X / median-Y refinement does the rest.

Spacing detection: v26 modal-based dimension picking (handles mixed
individual + cumulative annotations on the same drawing).

Coordinate transform: rotation-aware (most TGCH structural sheets ship at
rotation=90, displayed landscape).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

import fitz  # type: ignore[import-untyped]
from loguru import logger


DEFAULT_DPI = 150          # ml/yolo_runner contract for STRUCT_PLAN_OVERALL @1280
PERIMETER_BAND_FRAC = 0.20 # PROBE §3A-1 quoted "~10%" but TGCH grid bubbles
                            # actually sit ~18% from the top edge (e.g. iy=868
                            # on a 4966-px L3-00 render). 20% catches both
                            # ends and still kills interior false positives.
MARGIN_X_TOL_PT     = 40.0 # ~83 px @150 DPI — same as v4
LABEL_Y_TOL_PX      = 50.0
DEFAULT_BAY_MM      = 6000
FALLBACK_BAY_MM     = 8400
MAX_GRID_DIGIT      = 99   # v-line label cap (TGCH tops out at 33)

_DIM_RE     = re.compile(r"^\s*(\d{3,5})\s*$")
_H_LABEL_RE = re.compile(r"^[A-Za-z]{1,2}$")


@dataclass(frozen=True)
class _TextSpan:
    text: str
    bbox: tuple[float, float, float, float]  # natural pre-rotation page points


@dataclass(frozen=True)
class GridResult:
    x_lines_px:    list[float]
    y_lines_px:    list[float]
    x_labels:      list[str]
    y_labels:      list[str]
    x_spacings_mm: list[float]
    y_spacings_mm: list[float]
    page_rotation: int
    img_w_px:      int
    img_h_px:      int
    dpi:           float
    has_grid:      bool
    source:        str        # "text_labels" | "fallback"
    notes:         list[str] = field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _spans(page: fitz.Page) -> list[_TextSpan]:
    out: list[_TextSpan] = []
    d = page.get_text("dict") or {}
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                t = (span.get("text") or "").strip()
                bb = span.get("bbox")
                if not t or not bb or len(bb) < 4:
                    continue
                out.append(_TextSpan(text=t, bbox=tuple(bb)))
    return out


def _to_image_coords(
    bbox: tuple[float, float, float, float],
    disp_w_pt: float,
    disp_h_pt: float,
    scale: float,
    rotation: int,
) -> tuple[float, float]:
    """Convert a natural-PDF bbox centre to displayed image-pixel coords."""
    cx = (bbox[0] + bbox[2]) / 2.0
    cy = (bbox[1] + bbox[3]) / 2.0
    if rotation == 90:
        return (disp_w_pt - cy) * scale, cx * scale
    if rotation == 270:
        return cy * scale, (disp_h_pt - cx) * scale
    if rotation == 180:
        return (disp_w_pt - cx) * scale, (disp_h_pt - cy) * scale
    return cx * scale, cy * scale  # rotation == 0


def _median(vals: Iterable[float]) -> float:
    s = sorted(vals)
    return s[len(s) // 2]


def _within_perimeter_band(ix: float, iy: float, img_w: int, img_h: int) -> bool:
    """Coarse filter — within 10% of any page edge.

    PROBE §3A-1 found a strict 1-2-char filter without a perimeter check
    yields ~60% interior FPs (e.g. "SB" structural-beam annotations). The
    median-X / median-Y refinement that follows still does the exact margin
    alignment, but this band cuts the candidate set from 1,967 to ~790 up
    front and prevents an interior cluster of "SB" (60×) from polluting the
    margin median.
    """
    bx = img_w * PERIMETER_BAND_FRAC
    by = img_h * PERIMETER_BAND_FRAC
    near_left   = ix <= bx
    near_right  = ix >= img_w - bx
    near_top    = iy <= by
    near_bottom = iy >= img_h - by
    return near_left or near_right or near_top or near_bottom


# ── Grid label extraction (v26 — pair at detection time) ──────────────────────

def _extract_lines(
    spans:      list[_TextSpan],
    img_w:      int,
    img_h:      int,
    scale:      float,
    disp_w_pt:  float,
    disp_h_pt:  float,
    rotation:   int,
) -> tuple[list[float], list[str], list[float], list[str], list[str]]:
    notes: list[str] = []
    margin_x_tol = MARGIN_X_TOL_PT * scale  # ~83 px @150 dpi

    def to_px(s: _TextSpan) -> tuple[float, float]:
        return _to_image_coords(s.bbox, disp_w_pt, disp_h_pt, scale, rotation)

    def extreme_filter_x(group: list[tuple[str, float, float]], side: str, tol: float):
        """Keep candidates whose X clusters at the page-extreme (left or right).

        Plain median fails when interior false-positive labels (e.g. "SB",
        "WB" on B2-00) cluster more densely than real grid bubbles in the
        same half — the median pulls toward the FPs and we lose the real
        bubbles. Real grid bubbles always sit at the actual page edge, so
        we anchor on min(x) for the left margin and max(x) for the right,
        then keep everything within tol of that anchor.
        """
        if not group:
            return group
        xs = [x for _, x, _ in group]
        anchor = min(xs) if side == "left" else max(xs)
        return [(t, x, y) for t, x, y in group if abs(x - anchor) <= tol]

    def median_filter_y(group: list[tuple[str, float, float]], tol: float = LABEL_Y_TOL_PX):
        if len(group) < 2:
            return group
        med = _median(y for _, _, y in group)
        return [(t, x, y) for t, x, y in group if abs(y - med) <= tol]

    # Step 1 — H-label margin bands ---------------------------------------------
    h_raw: list[tuple[str, float, float]] = []
    for s in spans:
        if not _H_LABEL_RE.match(s.text):
            continue
        ix, iy = to_px(s)
        if not (0 < ix < img_w and 0 < iy < img_h):
            continue
        if not _within_perimeter_band(ix, iy, img_w, img_h):
            continue
        h_raw.append((s.text, ix, iy))

    mid_x   = img_w / 2.0
    left_h  = extreme_filter_x([h for h in h_raw if h[1] <  mid_x], "left",  margin_x_tol)
    right_h = extreme_filter_x([h for h in h_raw if h[1] >= mid_x], "right", margin_x_tol)

    bx0 = int(_median(x for _, x, _ in left_h))  if left_h  else int(img_w * 0.08)
    bx1 = int(_median(x for _, x, _ in right_h)) if right_h else int(img_w * 0.88)

    if not left_h:
        notes.append("no left-margin H-labels — bx0 is fallback")
    if not right_h:
        notes.append("no right-margin H-labels — bx1 is fallback")

    h_ys = sorted(y for _, _, y in (left_h + right_h))
    by0 = int(h_ys[0])  if h_ys else int(img_h * 0.15)
    by1 = int(h_ys[-1]) if h_ys else int(img_h * 0.92)

    # Step 2 — V-label margin band Y bounds -------------------------------------
    v_all: list[tuple[str, float, float]] = []
    for s in spans:
        if not s.text.isdigit():
            continue
        try:
            val = int(s.text)
        except ValueError:
            continue
        if val < 1 or val > MAX_GRID_DIGIT:
            continue
        ix, iy = to_px(s)
        if not (0 < ix < img_w and 0 < iy < img_h):
            continue
        if not _within_perimeter_band(ix, iy, img_w, img_h):
            continue
        if not (bx0 - 200 < ix < bx1 + 200):
            continue
        v_all.append((s.text, ix, iy))

    v_top = median_filter_y([v for v in v_all if v[2] <  img_h * 0.5])
    v_bot = median_filter_y([v for v in v_all if v[2] >= img_h * 0.5])
    v_top_y = int(_median(y for _, _, y in v_top)) if v_top else by0 - 50
    v_bot_y = int(_median(y for _, _, y in v_bot)) if v_bot else by1 + 50

    if not v_top:
        notes.append("no top V-labels — v_top_y is fallback")
    if not v_bot:
        notes.append("no bot V-labels — v_bot_y is fallback")

    # Step 3 — V-lines: median X per unique numeric label -----------------------
    v_xs: dict[str, list[float]] = {}
    for s in spans:
        if not s.text.isdigit():
            continue
        try:
            val = int(s.text)
        except ValueError:
            continue
        if val < 1 or val > MAX_GRID_DIGIT:
            continue
        ix, iy = to_px(s)
        if ix < bx0 or ix > bx1:
            continue
        in_top = abs(iy - v_top_y) <= LABEL_Y_TOL_PX
        in_bot = abs(iy - v_bot_y) <= LABEL_Y_TOL_PX
        if not (in_top or in_bot):
            continue
        v_xs.setdefault(s.text, []).append(ix)

    v_pairs = sorted(((_median(xs), lbl) for lbl, xs in v_xs.items()),
                     key=lambda p: p[0])
    v_positions = [p[0] for p in v_pairs]
    v_labels    = [p[1] for p in v_pairs]

    # Step 4 — H-lines: median Y per unique alpha label -------------------------
    h_ys_per_label: dict[str, list[float]] = {}
    for s in spans:
        if not _H_LABEL_RE.match(s.text):
            continue
        ix, iy = to_px(s)
        if iy < v_top_y or iy > v_bot_y:
            continue
        near_left  = abs(ix - bx0) <= margin_x_tol
        near_right = abs(ix - bx1) <= margin_x_tol
        if not (near_left or near_right):
            continue
        h_ys_per_label.setdefault(s.text, []).append(iy)

    h_pairs = sorted(((_median(ys), lbl) for lbl, ys in h_ys_per_label.items()),
                     key=lambda p: p[0])
    h_positions = [p[0] for p in h_pairs]
    h_labels    = [p[1] for p in h_pairs]

    return v_positions, v_labels, h_positions, h_labels, notes


def _drop_spacing_outliers(
    positions: list[float],
    labels:    list[str],
    hi_factor: float = 1.7,
    lo_factor: float = 0.6,
) -> tuple[list[float], list[str]]:
    """Drop endpoint labels whose neighbour-gap deviates from the median.

    Real grid bubbles are uniformly spaced (TGCH = 8400 mm = ~124 px @150 dpi
    on -00 plans, ~248 px on -01..04 enlarged at 4× scale). Stray non-bubble
    labels caught by the margin filter come in two flavours we need to drop:

      • Far-flung outliers (e.g. legend "TY" past the last real bubble in
        L3-04 with a 317 px gap vs 248 px median) — gap > hi_factor × med.
      • Close-in stragglers (e.g. legend "TY" only 126 px from the last
        real bubble in L3-02) — gap < lo_factor × med.

    Iteratively trim from each end until both endpoints have a gap within
    [lo_factor, hi_factor] of the median.
    """
    if len(positions) < 4:
        return positions, labels
    while len(positions) >= 3:
        gaps = [positions[i + 1] - positions[i] for i in range(len(positions) - 1)]
        med = _median(gaps)
        if med <= 0:
            break
        first_gap = gaps[0]
        last_gap  = gaps[-1]
        if first_gap > hi_factor * med or first_gap < lo_factor * med:
            positions = positions[1:]
            labels    = labels[1:]
            continue
        if last_gap > hi_factor * med or last_gap < lo_factor * med:
            positions = positions[:-1]
            labels    = labels[:-1]
            continue
        break
    return positions, labels


def _sort_lines(positions: list[float], labels: list[str], numeric: bool):
    if not positions:
        return positions, labels
    pairs = sorted(zip(positions, labels), key=lambda p: p[0])
    positions = [p[0] for p in pairs]
    labels    = [p[1] for p in pairs]
    if numeric and all(l.isdigit() for l in labels):
        by_label = sorted(zip(labels, positions), key=lambda p: int(p[0]))
        sorted_lbl = [p[0] for p in by_label]
        sorted_px  = [p[1] for p in by_label]
        if sorted_px == sorted(sorted_px):
            positions, labels = sorted_px, sorted_lbl
    return positions, labels


# ── Spacing detection (v26 modal-based) ───────────────────────────────────────

def _detect_spacings(
    line_positions: list[float],
    axis:           str,
    spans:          list[_TextSpan],
    scale:          float,
    disp_w_pt:      float,
    disp_h_pt:      float,
    rotation:       int,
) -> list[float]:
    n_gaps = max(0, len(line_positions) - 1)
    if n_gaps == 0:
        return []

    gap_candidates: list[list[float]] = [[] for _ in range(n_gaps)]
    for s in spans:
        m = _DIM_RE.match(s.text)
        if not m:
            continue
        dim_mm = int(m.group(1))
        if dim_mm < 300 or dim_mm > 30_000:
            continue
        ix, iy = _to_image_coords(s.bbox, disp_w_pt, disp_h_pt, scale, rotation)
        text_pos = ix if axis == "x" else iy
        for i in range(n_gaps):
            mid    = (line_positions[i] + line_positions[i + 1]) / 2.0
            half   = max(abs(line_positions[i + 1] - line_positions[i]) / 2.0, 1.0)
            if abs(text_pos - mid) < half:
                gap_candidates[i].append(float(dim_mm))
                break

    spacings: list[float | None] = [None] * n_gaps
    plaus_tol = 0.30
    all_vals = [v for cand in gap_candidates for v in cand]
    if all_vals:
        modal_mm = float(Counter(all_vals).most_common(1)[0][0])
        gap_pxs  = [abs(line_positions[i + 1] - line_positions[i]) for i in range(n_gaps)]
        median_gap_px = sorted(gap_pxs)[len(gap_pxs) // 2]
        for i, cand in enumerate(gap_candidates):
            if not cand:
                continue
            expected = modal_mm * gap_pxs[i] / max(median_gap_px, 1.0)
            plausible = [v for v in cand
                         if abs(v - expected) / max(expected, 1.0) <= plaus_tol]
            if plausible:
                spacings[i] = min(plausible, key=lambda v: abs(v - expected))

    annotated = [(abs(line_positions[i + 1] - line_positions[i]), s)
                 for i, s in enumerate(spacings) if s is not None]
    if annotated:
        avg_ratio = sum(px / mm for px, mm in annotated if mm > 0) / len(annotated)
        for i in range(n_gaps):
            if spacings[i] is not None:
                continue
            gap_px = abs(line_positions[i + 1] - line_positions[i])
            best_mm, best_err = None, float("inf")
            for _, mm in annotated:
                expected = mm * avg_ratio
                if expected <= 0:
                    continue
                ratio = gap_px / expected
                if 0.50 <= ratio <= 1.50:
                    err = abs(ratio - 1.0)
                    if err < best_err:
                        best_err, best_mm = err, mm
            if best_mm is not None:
                spacings[i] = best_mm

    for i in range(n_gaps):
        if spacings[i] is None:
            spacings[i] = float(FALLBACK_BAY_MM)

    return [float(s) for s in spacings]


# ── Public API ────────────────────────────────────────────────────────────────

def detect_grid(page: fitz.Page, dpi: float = DEFAULT_DPI) -> GridResult:
    """Detect grid bubble labels + spacings on one PDF page.

    Returns a fallback GridResult (has_grid=False) when fewer than two
    V-lines or H-lines survive — caller decides what to do.
    """
    rotation = int(page.rotation or 0)
    rect = page.rect
    disp_w_pt, disp_h_pt = float(rect.width), float(rect.height)
    scale = dpi / 72.0
    img_w = int(round(disp_w_pt * scale))
    img_h = int(round(disp_h_pt * scale))

    spans = _spans(page)

    v_pos, v_lbl, h_pos, h_lbl, notes = _extract_lines(
        spans, img_w, img_h, scale, disp_w_pt, disp_h_pt, rotation,
    )

    if len(v_pos) < 2 or len(h_pos) < 2:
        logger.warning(
            f"detect_grid: only {len(v_pos)} V-lines / {len(h_pos)} H-lines — "
            "falling back."
        )
        return GridResult(
            x_lines_px    = [],
            y_lines_px    = [],
            x_labels      = [],
            y_labels      = [],
            x_spacings_mm = [],
            y_spacings_mm = [],
            page_rotation = rotation,
            img_w_px      = img_w,
            img_h_px      = img_h,
            dpi           = dpi,
            has_grid      = False,
            source        = "fallback",
            notes         = notes + [
                f"too few lines: V={len(v_pos)}, H={len(h_pos)}",
            ],
        )

    v_pos, v_lbl = _sort_lines(v_pos, v_lbl, numeric=True)
    h_pos, h_lbl = _sort_lines(h_pos, h_lbl, numeric=False)
    v_pos, v_lbl = _drop_spacing_outliers(v_pos, v_lbl)
    h_pos, h_lbl = _drop_spacing_outliers(h_pos, h_lbl)
    if len(v_pos) < 2 or len(h_pos) < 2:
        notes.append("outlier rejection emptied an axis")
        return GridResult(
            x_lines_px=[], y_lines_px=[], x_labels=[], y_labels=[],
            x_spacings_mm=[], y_spacings_mm=[],
            page_rotation=rotation, img_w_px=img_w, img_h_px=img_h, dpi=dpi,
            has_grid=False, source="fallback", notes=notes,
        )

    x_sp = _detect_spacings(v_pos, "x", spans, scale, disp_w_pt, disp_h_pt, rotation)
    y_sp = _detect_spacings(h_pos, "y", spans, scale, disp_w_pt, disp_h_pt, rotation)

    return GridResult(
        x_lines_px    = v_pos,
        y_lines_px    = h_pos,
        x_labels      = v_lbl,
        y_labels      = h_lbl,
        x_spacings_mm = x_sp,
        y_spacings_mm = y_sp,
        page_rotation = rotation,
        img_w_px      = img_w,
        img_h_px      = img_h,
        dpi           = dpi,
        has_grid      = True,
        source        = "text_labels",
        notes         = notes,
    )
