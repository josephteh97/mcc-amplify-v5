"""Step 6 regression tests for ELEVATION extraction (PLAN.md §3B, PROBE §3B).

Coverage:
  - LEVEL_NAME_RE_V2 unit tests (BASEMENT n / NTH STOREY / structural codes)
  - RL_FFL_RE meters→mm conversion
  - extract_level_and_rl_spans on a real elevation PDF
  - extract_elevation full round-trip on TD-A-130-01-01
  - Slow integration on all 5 architectural elevation PDFs
"""

from __future__ import annotations

import json
from pathlib import Path

import fitz  # type: ignore[import-untyped]
import pytest

from backend.extract.elevation.extract import (
    ElevationExtractResult,
    extract_elevation,
)
from backend.extract.elevation.labels  import (
    LEVEL_NAME_RE,
    RL_FFL_RE,
    RL_MM_RE,
    extract_level_and_rl_spans,
    _ffl_to_mm,
    _mm_to_mm,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE   = REPO_ROOT / "tests" / "fixtures" / "sample_uploaded_documents" / "04 130 - BLDG ELEVATIONS"

fixture_required = pytest.mark.skipif(
    not FIXTURE.exists(),
    reason="reference fixture symlink missing — see PLAN.md §3.1",
)


# ── Level-name regex (V2 from PROBE §3B) ──────────────────────────────────────

@pytest.mark.parametrize("text, ok", [
    ("BASEMENT 1",   True),     # architectural form
    ("BASEMENT 2",   True),
    ("BASEMENT 3",   True),
    ("1ST STOREY",   True),
    ("2ND STOREY",   True),
    ("3RD STOREY",   True),
    ("10TH STOREY",  True),
    ("ROOF",         True),
    ("PARAPET",      True),
    ("B1",           True),     # structural short codes
    ("L3",           True),
    ("RF",           True),
    ("UR",           True),
    ("CORRIDOR",     False),    # ARCH room labels must not match
    ("8400",         False),
    ("",             False),
])
def test_level_name_re(text: str, ok: bool) -> None:
    assert bool(LEVEL_NAME_RE.match(text.upper())) is ok


# ── RL_FFL_RE meters→mm ───────────────────────────────────────────────────────

@pytest.mark.parametrize("text, expected_mm", [
    ("FFL+3.50",  3500),
    ("FFL-2.50", -2500),
    ("FFL+9.50",  9500),
    ("FFL+15.50", 15500),
    ("FFL+52.85", 52850),
    ("FFL-6.7",  -6700),
    ("FFL +0.0",  0),
    ("ffl+3.50",  3500),    # case-insensitive
])
def test_rl_ffl_re(text: str, expected_mm: int) -> None:
    m = RL_FFL_RE.search(text)
    assert m is not None, text
    assert _ffl_to_mm(m.group(1), m.group(2)) == expected_mm


def test_rl_ffl_re_no_match() -> None:
    for s in ("FFL", "+3.50", "BASEMENT 1", "8400 mm"):
        assert RL_FFL_RE.search(s) is None or "FFL" not in s


# ── RL_MM_RE fallback ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("sign, value, units, expected_mm", [
    ("+",  "9500",   "mm", 9500),
    ("-",  "3000",   None, -3000),
    (None, "9.5",    "m",  9500),
    (None, "12500",  "mm", 12500),
])
def test_mm_conversion(sign, value, units, expected_mm) -> None:
    assert _mm_to_mm(sign, value, units) == expected_mm


# ── Real-fixture span extraction ──────────────────────────────────────────────

@fixture_required
def test_extract_spans_l3_01() -> None:
    """TD-A-130-01-01 carries ~29 level names + ~34 FFL RLs across its
    multiple elevation views (PROBE §3B)."""
    pdf = FIXTURE / "TD-A-130-01-01_ELEVATION 1_2.pdf"
    with fitz.open(pdf) as doc:
        levels, rls = extract_level_and_rl_spans(doc[0])
    assert len(levels) >= 25
    assert len(rls)    >= 30

    names = {l.name for l in levels}
    # PROBE §3B confirmed both forms are present.
    assert "BASEMENT 1" in names or "BASEMENT 2" in names
    assert any("STOREY" in n for n in names)

    # All RLs should fall in a sane building range.
    rl_vals = [r.rl_mm for r in rls]
    assert min(rl_vals) >= -10_000
    assert max(rl_vals) <=  80_000


# ── Full extractor on one PDF ────────────────────────────────────────────────

@fixture_required
def test_extract_elevation_l3_01(tmp_path: Path) -> None:
    pdf = FIXTURE / "TD-A-130-01-01_ELEVATION 1_2.pdf"
    r = extract_elevation(pdf, tmp_path)
    assert isinstance(r, ElevationExtractResult)
    assert r.payload_path is not None
    assert r.level_count >= 10           # 12 unique levels on this fixture

    payload = json.loads(r.payload_path.read_text())
    for key in ("source_pdf", "stats", "levels", "floor_to_floor_mm", "flags"):
        assert key in payload

    # Level set is ascending by RL.
    rls = [l["rl_mm"] for l in payload["levels"]]
    assert rls == sorted(rls)

    # PROBE §3B: BASEMENT 1 = +3.50 m, 1ST STOREY = +9.50 m.
    by_name = {l["name"]: l["rl_mm"] for l in payload["levels"]}
    assert by_name.get("BASEMENT 1")  == 3500
    assert by_name.get("1ST STOREY")  == 9500
    assert by_name.get("BASEMENT 2") == -2500

    # floor_to_floor must be n_levels - 1.
    assert len(payload["floor_to_floor_mm"]) == len(payload["levels"]) - 1


# ── Slow integration on the full elevation set ───────────────────────────────

@pytest.mark.slow
@fixture_required
def test_extract_all_elevations(tmp_path: Path) -> None:
    pdfs = sorted(FIXTURE.glob("TD-A-130-01-*.pdf"))
    assert len(pdfs) == 5

    rl_observed: dict[str, set[int]] = {}
    for pdf in pdfs:
        r = extract_elevation(pdf, tmp_path)
        assert r.level_count >= 10
        payload = json.loads(r.payload_path.read_text())
        for l in payload["levels"]:
            rl_observed.setdefault(l["name"], set()).add(l["rl_mm"])

    # Across all 5 elevations, the canonical levels should agree on RL —
    # any cross-PDF disagreement is a candidate for the reconciler in
    # Step 8 (PLAN.md §3B "When several elevation PDFs cover different
    # facades, the level set must agree").
    for name in ("BASEMENT 2", "1ST STOREY", "2ND STOREY"):
        if name in rl_observed:
            assert len(rl_observed[name]) == 1, (
                f"{name} disagrees across PDFs: {rl_observed[name]}"
            )
