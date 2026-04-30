"""Step 7 regression tests for SECTION extraction (PLAN.md §3C, PROBE §3C).

Stage 3C is intentionally a stub for v5.3 — the fixture's architectural
sections carry no machine-readable thickness annotations, so the
extractor parses section IDs from the filename and emits empty joints.
Stage 5B reads ``meta.yaml.slabs.default_thickness_mm``.

Coverage:
  - SECTION_FILENAME_RE on the four fixture filenames + edge cases
  - parse_section_ids on multi-letter and singleton forms
  - Real-fixture smoke on TD-A-120-0101 (and slow sweep across all 4)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.extract.section.extract import (
    SectionExtractResult,
    extract_section,
)
from backend.extract.section.labels  import (
    SECTION_FILENAME_RE,
    parse_section_ids,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE   = REPO_ROOT / "tests" / "fixtures" / "sample_uploaded_documents" / "03 120 - BLDG SECTIONS"

fixture_required = pytest.mark.skipif(
    not FIXTURE.exists(),
    reason="reference fixture symlink missing — see PLAN.md §3.1",
)


# ── Filename parsing ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name, expected", [
    ("TD-A-120-0101_SECTION A_B.pdf",          ["A", "B"]),
    ("TD-A-120-0102_SECTION C_D.pdf",          ["C", "D"]),
    ("TD-A-120-0103_SECTION E_F.pdf",          ["E", "F"]),
    ("TD-A-120-0104_SECTION G.pdf",            ["G"]),
    ("foo_SECTION X_Y_Z.pdf",                  ["X", "Y", "Z"]),    # 3-way
    ("foo_section a_b.pdf",                    ["A", "B"]),         # case-insensitive
    ("not-a-section.pdf",                      []),
    ("TD-A-130-01-01_ELEVATION 1_2.pdf",       []),                 # elevation, not section
])
def test_parse_section_ids(name: str, expected: list[str]) -> None:
    assert parse_section_ids(name) == expected


def test_section_filename_re_matches() -> None:
    assert SECTION_FILENAME_RE.search("TD-A-120-0101_SECTION A_B.pdf") is not None
    assert SECTION_FILENAME_RE.search("not-a-section.pdf") is None


# ── Real-fixture round-trip ───────────────────────────────────────────────────

@fixture_required
def test_extract_section_a_b(tmp_path: Path) -> None:
    pdf = FIXTURE / "TD-A-120-0101_SECTION A_B.pdf"
    r = extract_section(pdf, tmp_path)
    assert isinstance(r, SectionExtractResult)
    assert r.section_ids == ["A", "B"]
    assert r.payload_path is not None

    payload = json.loads(r.payload_path.read_text())
    for key in ("source_pdf", "section_ids", "sections", "thickness_hints", "flags"):
        assert key in payload
    # PLAN §3C joints schema — empty in v5.3.
    assert all(s["joints"] == [] for s in payload["sections"])
    # Strict-mode flag set so Stage 5B knows to apply meta.yaml fallback.
    assert "stage_5b_falls_back_to_meta_default" in payload["flags"]
    assert "thickness_extraction_deferred_v5_3"  in payload["flags"]


@fixture_required
def test_extract_section_singleton_g(tmp_path: Path) -> None:
    pdf = FIXTURE / "TD-A-120-0104_SECTION G.pdf"
    r = extract_section(pdf, tmp_path)
    assert r.section_ids == ["G"]
    payload = json.loads(r.payload_path.read_text())
    assert len(payload["sections"]) == 1
    assert payload["sections"][0]["section_id"] == "G"


# ── Slow sweep across all four section PDFs ──────────────────────────────────

@pytest.mark.slow
@fixture_required
def test_extract_section_all(tmp_path: Path) -> None:
    pdfs = sorted(FIXTURE.glob("*.pdf"))
    assert len(pdfs) == 4

    expected = {
        "TD-A-120-0101_SECTION A_B": ["A", "B"],
        "TD-A-120-0102_SECTION C_D": ["C", "D"],
        "TD-A-120-0103_SECTION E_F": ["E", "F"],
        "TD-A-120-0104_SECTION G":   ["G"],
    }
    for pdf in pdfs:
        r = extract_section(pdf, tmp_path)
        assert r.section_ids == expected[pdf.stem], f"{pdf.stem}: got {r.section_ids}"
        # PROBE §3C: 0–2 thickness hits across the fixture. Just sanity-check
        # that the scan ran without crashing and stayed sub-10.
        assert 0 <= r.thickness_hits < 10
