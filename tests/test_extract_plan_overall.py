"""Step 4 regression tests for STRUCT_PLAN_OVERALL extraction (PLAN.md §3A-1).

Coverage:
  - Affine solver  (unit, no fixture)
  - Storey id parser
  - detect_grid + solve_affine round-trip on L3-00
  - extract_overall payload schema + perimeter-band filter (no "SB" in V/H labels)
  - Sweep all 14 -00 storeys (slow): every page must produce a valid affine
"""

from __future__ import annotations

import json
from pathlib import Path

import fitz  # type: ignore[import-untyped]
import pytest

from backend.extract.plan_overall.affine     import (
    Affine2D,
    AffineSolveError,
    _AxisFit,
    _cumulative_mm,
    _fit_axis,
    solve_affine,
)
from backend.extract.plan_overall.detector   import detect_grid, GridResult
from backend.extract.plan_overall.extract    import (
    OverallExtractResult,
    extract_overall,
    storey_id_from_filename,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE   = REPO_ROOT / "tests" / "fixtures" / "sample_uploaded_documents" / "FLOOR FRAMING PLANS"

fixture_required = pytest.mark.skipif(
    not FIXTURE.exists(),
    reason="reference fixture symlink missing — see PLAN.md §3.1",
)


# ── Storey id parser ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name, expected", [
    ("TGCH-TD-S-200-L3-00.pdf", "L3"),
    ("TGCH-TD-S-200-B1-00.pdf", "B1"),
    ("TGCH-TD-S-200-RF-00.pdf", "RF"),
    ("TGCH-TD-S-200-UR-00.pdf", "UR"),
    ("TGCH-TD-S-200-L9-04.pdf", "L9"),     # -04 still parses; classifier gates use
    ("random.pdf",              "random"), # falls back to stem
])
def test_storey_id_from_filename(name: str, expected: str) -> None:
    assert storey_id_from_filename(name) == expected


# ── Affine: unit ──────────────────────────────────────────────────────────────

def test_cumulative_mm() -> None:
    assert _cumulative_mm([8400, 8400, 6000]) == [0, 8400, 16800, 22800]


def test_fit_axis_perfect() -> None:
    pxs = [100.0, 200.0, 300.0, 400.0]
    mms = [0.0, 8400.0, 16800.0, 25200.0]
    fit = _fit_axis(pxs, mms)
    assert fit.residual_px < 1e-9
    assert fit.slope_px_per_mm == pytest.approx(100.0 / 8400.0)
    # Round-trip
    assert fit.px_to_mm(fit.mm_to_px(12345.6)) == pytest.approx(12345.6)


def test_fit_axis_rejects_short_input() -> None:
    with pytest.raises(AffineSolveError):
        _fit_axis([100.0], [0.0])


def test_solve_affine_residual_gate() -> None:
    """Forge a GridResult whose y-axis has a >1 px residual; solver must reject."""
    # x_lines: perfect; y_lines: third point off by 5 px
    grid = GridResult(
        x_lines_px    = [100.0, 200.0, 300.0],
        y_lines_px    = [100.0, 200.0, 305.0],
        x_labels      = ["1", "2", "3"],
        y_labels      = ["A", "B", "C"],
        x_spacings_mm = [8400.0, 8400.0],
        y_spacings_mm = [8400.0, 8400.0],
        page_rotation = 0,
        img_w_px      = 1000,
        img_h_px      = 1000,
        dpi           = 150.0,
        has_grid      = True,
        source        = "text_labels",
    )
    with pytest.raises(AffineSolveError, match="residual"):
        solve_affine(grid)


def test_solve_affine_passes_under_gate() -> None:
    grid = GridResult(
        x_lines_px    = [100.0, 200.0, 300.0],
        y_lines_px    = [100.0, 200.0, 300.0],
        x_labels      = ["1", "2", "3"],
        y_labels      = ["A", "B", "C"],
        x_spacings_mm = [8400.0, 8400.0],
        y_spacings_mm = [8400.0, 8400.0],
        page_rotation = 0,
        img_w_px      = 1000,
        img_h_px      = 1000,
        dpi           = 150.0,
        has_grid      = True,
        source        = "text_labels",
    )
    a = solve_affine(grid)
    assert a.residual_px < 1e-9
    # Round-trip a known grid intersection
    assert a.px_to_mm(200.0, 200.0) == pytest.approx((8400.0, 8400.0))


def test_solve_affine_rejects_no_grid() -> None:
    grid = GridResult(
        x_lines_px=[], y_lines_px=[], x_labels=[], y_labels=[],
        x_spacings_mm=[], y_spacings_mm=[],
        page_rotation=0, img_w_px=10, img_h_px=10, dpi=150.0,
        has_grid=False, source="fallback",
    )
    with pytest.raises(AffineSolveError, match="has_grid=False"):
        solve_affine(grid)


# ── Detector + extractor on real fixture ──────────────────────────────────────

@fixture_required
def test_detect_grid_l3() -> None:
    """L3-00 is the canonical reference (also used in classifier tests)."""
    pdf = FIXTURE / "TGCH-TD-S-200-L3-00.pdf"
    with fitz.open(pdf) as doc:
        g = detect_grid(doc[0])
    assert g.has_grid is True
    assert g.page_rotation == 90
    # PROBE §3A-1 says digits 17-33+ — TGCH actually goes 1-42 on L3.
    assert all(lbl.isdigit() for lbl in g.x_labels)
    assert all(lbl.isalpha() for lbl in g.y_labels)
    assert len(g.x_labels) >= 15
    assert len(g.y_labels) >= 15
    # Perimeter-band filter must eliminate the interior "SB" / "WB" annotations
    # that PROBE §3A-1 logged as the dominant interior FP class.
    assert "SB" not in g.y_labels
    assert "WB" not in g.y_labels
    assert "TY" not in g.y_labels
    # x-spacings must be all positive
    assert all(sp > 0 for sp in g.x_spacings_mm)
    assert all(sp > 0 for sp in g.y_spacings_mm)


@fixture_required
def test_extract_overall_l3_no_yolo(tmp_path: Path) -> None:
    """End-to-end extractor (grid + affine) — YOLO disabled to keep test fast."""
    pdf = FIXTURE / "TGCH-TD-S-200-L3-00.pdf"
    r = extract_overall(pdf, page_index=0, out_dir=tmp_path, run_yolo=False)
    assert isinstance(r, OverallExtractResult)
    assert r.storey_id == "L3"
    assert r.has_grid is True
    assert r.affine_residual_px is not None
    assert r.affine_residual_px <= 1.0    # PLAN.md §3A-1 gate

    payload = json.loads(r.payload_path.read_text())
    # Schema match — every key PLAN.md §3A-1 names.
    for key in ("grid", "columns_canonical", "beams_canonical",
                "slabs_canonical", "affine_residual_px"):
        assert key in payload
    assert "x_axes" in payload["grid"] and "y_axes" in payload["grid"]
    assert payload["affine_residual_px"] is not None
    # YOLO step skipped explicitly.
    assert "yolo_columns_skipped" in payload["flags"]
    assert payload["columns_canonical"] == []
    # x_axes are monotonically increasing in mm.
    xmm = [a["mm"] for a in payload["grid"]["x_axes"]]
    assert xmm == sorted(xmm)
    ymm = [a["mm"] for a in payload["grid"]["y_axes"]]
    assert ymm == sorted(ymm)


@fixture_required
def test_extract_overall_b2_outlier_filter(tmp_path: Path) -> None:
    """B2-00 has interior 'SB'/'WB' labels and a stray 'TY' in the right margin.

    The detector's extreme-X anchor and spacing-outlier filter must drop them
    so the affine residual stays under 1 px.
    """
    pdf = FIXTURE / "TGCH-TD-S-200-B2-00.pdf"
    r = extract_overall(pdf, page_index=0, out_dir=tmp_path, run_yolo=False)
    assert r.has_grid is True
    assert r.affine_residual_px is not None
    assert r.affine_residual_px <= 1.0
    payload = json.loads(r.payload_path.read_text())
    y_labels = [a["label"] for a in payload["grid"]["y_axes"]]
    assert "TY" not in y_labels
    assert "SB" not in y_labels
    assert "WB" not in y_labels


@pytest.mark.slow
@fixture_required
def test_extract_overall_all_storeys(tmp_path: Path) -> None:
    """Sweep all 14 -00 pages — every storey must produce a valid affine."""
    pdfs = sorted(FIXTURE.glob("*-00.pdf"))
    assert len(pdfs) == 14, "fixture should ship 14 storeys (B3..UR)"
    for pdf in pdfs:
        r = extract_overall(pdf, page_index=0, out_dir=tmp_path, run_yolo=False)
        assert r.has_grid is True, f"{r.storey_id}: {r.flags}"
        assert r.affine_residual_px is not None
        assert r.affine_residual_px <= 1.0, (
            f"{r.storey_id}: residual {r.affine_residual_px:.3f} px > 1.0 gate"
        )


@pytest.mark.slow
@fixture_required
def test_extract_overall_l3_with_yolo(tmp_path: Path) -> None:
    """Single-page YOLO smoke test — we just want the wiring to produce
    SOME column detections at non-trivial confidence and mm coords inside
    the building footprint. Not asserting an exact count (model recall is
    not the focus of this test)."""
    pdf = FIXTURE / "TGCH-TD-S-200-L3-00.pdf"
    r = extract_overall(pdf, page_index=0, out_dir=tmp_path, run_yolo=True)
    assert r.has_grid is True

    payload = json.loads(r.payload_path.read_text())
    cols = payload["columns_canonical"]
    if not cols:
        # YOLO weights/torch missing in CI — accept the skip flag instead.
        assert any(f.startswith("yolo_columns_") for f in payload["flags"])
        return
    assert len(cols) > 50, f"expected many columns on L3, got {len(cols)}"
    for c in cols[:5]:
        assert 0 < c["confidence"] <= 1.0
        assert 0 < c["aspect"] <= 1.0
        bb = c["bbox_grid_mm"]
        assert bb[2] > bb[0] and bb[3] > bb[1]
