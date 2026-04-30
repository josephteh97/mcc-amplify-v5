"""Step 5 regression tests for STRUCT_PLAN_ENLARGED extraction (PLAN.md §3A-2).

Coverage:
  - Label regex unit tests (TYPE_CODE widening for C1A, RECT_DIM, DIA)
  - Orientation decider unit tests (XY / SWAP / EQUAL / AMBIGUOUS)
  - Filename → storey + page parser
  - extract_labels on real L3-01 fixture (regex coverage on real text)
  - Slow: full extract_enlarged on L3-01..04 with YOLO
"""

from __future__ import annotations

import json
from pathlib import Path

import fitz  # type: ignore[import-untyped]
import pytest

from backend.extract.plan_enlarged.labels      import (
    DIA_RE,
    Label,
    LabelKind,
    RECT_DIM_RE,
    TYPE_CODE_RE,
    extract_labels,
)
from backend.extract.plan_enlarged.orientation import (
    OrientationVerdict,
    decide_orientation,
)
from backend.extract.plan_enlarged.extract     import (
    EnlargedExtractResult,
    extract_enlarged,
    parse_filename,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE   = REPO_ROOT / "tests" / "fixtures" / "sample_uploaded_documents" / "FLOOR FRAMING PLANS"

fixture_required = pytest.mark.skipif(
    not FIXTURE.exists(),
    reason="reference fixture symlink missing — see PLAN.md §3.1",
)


# ── Regexes ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, ok", [
    ("C2",     True),
    ("C9",     True),
    ("C1A",    True),       # widened-regex case (PROBE §3A-2)
    ("H-C2",   True),
    ("H-C9",   True),
    ("RCB1",   True),
    ("H-RCB1", True),
    ("SB1",    True),
    ("NSP2",   True),
    ("LSB3",   True),
    ("8400",   False),
    ("",       False),
    ("CD",     False),      # no digit
    ("123",    False),      # no letter
])
def test_type_code_re(text: str, ok: bool) -> None:
    assert bool(TYPE_CODE_RE.match(text)) is ok


@pytest.mark.parametrize("text, ok", [
    ("800x800",  True),
    ("1150x800", True),
    ("390x800",  True),
    ("100x100",  True),    # 3-digit each — minimum
    ("12x12",    False),   # too short
    ("8400",     False),
    ("",         False),
])
def test_rect_dim_re(text: str, ok: bool) -> None:
    assert bool(RECT_DIM_RE.match(text)) is ok


@pytest.mark.parametrize("text, ok", [
    ("Ø1000",    True),
    ("D1200",    True),
    ("1130 Ø",   True),
    ("800 DIA",  True),
    ("1000",     False),
    ("Ø",        False),
])
def test_dia_re(text: str, ok: bool) -> None:
    assert bool(DIA_RE.match(text)) is ok


# ── Filename parser ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("name, expected_storey, expected_page", [
    ("TGCH-TD-S-200-L3-01.pdf", "L3", 1),
    ("TGCH-TD-S-200-L3-04.pdf", "L3", 4),
    ("TGCH-TD-S-200-B1-02.pdf", "B1", 2),
    ("TGCH-TD-S-200-RF-03.pdf", "RF", 3),
    ("not-a-fixture.pdf",       "not-a-fixture", 0),
])
def test_parse_filename(name: str, expected_storey: str, expected_page: int) -> None:
    storey, page = parse_filename(name)
    assert storey == expected_storey
    assert page   == expected_page


# ── Orientation decider ───────────────────────────────────────────────────────

def test_orientation_xy_fits() -> None:
    """1150 mm wide × 800 mm tall column on a bbox where dx > dy by ratio 1.4."""
    d = decide_orientation(140.0, 100.0, 1150, 800)
    assert d.verdict        == OrientationVerdict.XY
    assert d.dim_along_x_mm == 1150
    assert d.dim_along_y_mm == 800


def test_orientation_swap_fits() -> None:
    """Same annotation 1150x800 but bbox is taller → swap."""
    d = decide_orientation(100.0, 140.0, 1150, 800)
    assert d.verdict        == OrientationVerdict.SWAP
    assert d.dim_along_x_mm == 800
    assert d.dim_along_y_mm == 1150


def test_orientation_equal() -> None:
    d = decide_orientation(50.0, 50.0, 800, 800)
    assert d.verdict == OrientationVerdict.EQUAL


def test_orientation_ambiguous() -> None:
    """Bbox is square but annotation is asymmetric — neither fits."""
    d = decide_orientation(50.0, 50.0, 800, 300)
    assert d.verdict        == OrientationVerdict.AMBIGUOUS
    assert d.dim_along_x_mm is None
    assert d.dim_along_y_mm is None
    # Both errors should be high
    assert d.err_xy   > 0.15
    assert d.err_swap > 0.15


def test_orientation_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        decide_orientation(0.0, 50.0, 800, 600)
    with pytest.raises(ValueError):
        decide_orientation(50.0, 50.0, 0, 600)


def test_orientation_perfect_fit_residual_zero() -> None:
    """A 1150 mm × 800 mm bbox with the matching annotation has err_xy ≈ 0."""
    d = decide_orientation(1150.0, 800.0, 1150, 800)
    assert d.verdict == OrientationVerdict.XY
    assert d.err_xy  == pytest.approx(0.0, abs=1e-9)


# ── Real-fixture label extraction ────────────────────────────────────────────

@fixture_required
def test_extract_labels_l3_01() -> None:
    """L3-01 is the canonical -01 reference. PROBE §3A-2 drove the regex set;
    we expect ≥200 type codes and ≥50 rect dims.
    """
    pdf = FIXTURE / "TGCH-TD-S-200-L3-01.pdf"
    with fitz.open(pdf) as doc:
        labels = extract_labels(doc[0])
    types = [l for l in labels if l.kind == LabelKind.TYPE]
    rects = [l for l in labels if l.kind == LabelKind.RECT_DIM]
    assert len(types) >= 200
    assert len(rects) >= 50

    # Steel detection (H- prefix) — PROBE §3A-2 lists 344 H-C2 and 78 H-C9.
    steels = [l for l in types if l.is_steel]
    assert len(steels) > 0

    # Common type codes from PROBE §3A-2.
    code_set = {l.text for l in types}
    assert {"C2", "SB1"} & code_set


# ── Slow end-to-end ──────────────────────────────────────────────────────────

@pytest.mark.slow
@fixture_required
def test_extract_enlarged_l3_quadrants(tmp_path: Path) -> None:
    """Run all four L3 quadrants — every page must produce a valid affine
    and non-trivial column counts."""
    for i, page_n in enumerate(("01", "02", "03", "04"), start=1):
        pdf = FIXTURE / f"TGCH-TD-S-200-L3-{page_n}.pdf"
        r = extract_enlarged(pdf, page_index=0, out_dir=tmp_path, run_yolo=True)
        assert r.has_grid is True, f"L3-{page_n} flags: {r.flags}"
        assert r.affine_residual_px is not None
        assert r.affine_residual_px <= 1.0
        assert r.page_number == i
        assert r.page_region in ("upper-left", "upper-right", "lower-left", "lower-right")
        # YOLO recall on enlarged plans is high; expect at least some columns.
        if r.column_count == 0:
            # Allow zero only when a flag explains it (e.g. weight missing).
            assert any(f.startswith("yolo_columns_") for f in r.flags)


@pytest.mark.slow
@fixture_required
def test_extract_enlarged_l3_01_payload_schema(tmp_path: Path) -> None:
    """Payload schema check (PLAN.md §3A-2)."""
    pdf = FIXTURE / "TGCH-TD-S-200-L3-01.pdf"
    r = extract_enlarged(pdf, page_index=0, out_dir=tmp_path, run_yolo=True)
    payload = json.loads(r.payload_path.read_text())
    for key in ("storey_id", "page_number", "page_region", "grid", "columns",
                "summary", "affine_residual_px"):
        assert key in payload
    assert payload["page_region"] == "upper-left"

    if not payload["columns"]:
        return                                # YOLO unavailable in CI
    c = payload["columns"][0]
    for key in ("type", "label", "shape", "dim_along_x_mm", "dim_along_y_mm",
                "diameter_mm", "bbox_grid_mm", "grid_mm_xy", "page_id",
                "page_region", "flags"):
        assert key in c, f"missing key {key!r} on column"

    # Summary counts add up.
    s = payload["summary"]
    sum_shapes = (s["shape_rectangular"] + s["shape_square"] + s["shape_round"]
                  + s["shape_steel"] + s["shape_unknown"])
    assert sum_shapes == s["column_count"]


@pytest.mark.slow
@fixture_required
def test_extract_enlarged_no_yolo_skip_flag(tmp_path: Path) -> None:
    """run_yolo=False keeps the pipeline running but emits an explicit skip flag."""
    pdf = FIXTURE / "TGCH-TD-S-200-L3-01.pdf"
    r = extract_enlarged(pdf, page_index=0, out_dir=tmp_path, run_yolo=False)
    assert r.has_grid is True
    assert r.column_count == 0
    payload = json.loads(r.payload_path.read_text())
    assert payload["columns"] == []
    assert "yolo_columns_skipped" in payload["flags"]
    # Labels still get extracted — the orchestrator can use them downstream
    # (e.g. for label-only resolvers) even without YOLO.
    assert payload["label_counts"].get("type", 0) > 0
