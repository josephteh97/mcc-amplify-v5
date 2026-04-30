"""Step 2a regression tests for the classifier (PLAN.md §5).

Day-one test cases (must pass via tier 1 alone):
  TGCH-TD-S-200-L3-00.pdf            → STRUCT_PLAN_OVERALL
  TGCH-TD-S-200-L3-01.pdf            → STRUCT_PLAN_ENLARGED
  TD-A-120-0101_SECTION A_B.pdf      → SECTION
  TD-A-130-01-01_ELEVATION 1_2.pdf   → ELEVATION
  TD-A-130-0001_PERSPECTIVES 1.pdf   → DISCARD  (must NOT leak into ELEVATION)

Integration on the canonical 81-PDF subset:
  14 STRUCT_PLAN_OVERALL · 56 STRUCT_PLAN_ENLARGED · 5 ELEVATION
   4 SECTION · 2 DISCARD
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.classify.classifier import classify_manifest, summarise
from backend.classify.rules      import classify_filename
from backend.classify.types      import ClassifierTier, DrawingClass
from backend.ingest.ingest       import ingest, walk_uploads


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE   = REPO_ROOT / "tests" / "fixtures" / "sample_uploaded_documents"

fixture_required = pytest.mark.skipif(
    not FIXTURE.exists(),
    reason="reference fixture symlink missing — see PLAN.md §3.1",
)


# ── Day-one filename test cases ───────────────────────────────────────────────

@pytest.mark.parametrize("filename, expected", [
    ("TGCH-TD-S-200-L3-00.pdf",          DrawingClass.STRUCT_PLAN_OVERALL),
    ("TGCH-TD-S-200-L3-01.pdf",          DrawingClass.STRUCT_PLAN_ENLARGED),
    ("TGCH-TD-S-200-L3-04.pdf",          DrawingClass.STRUCT_PLAN_ENLARGED),
    ("TD-A-120-0101_SECTION A_B.pdf",    DrawingClass.SECTION),
    ("TD-A-130-01-01_ELEVATION 1_2.pdf", DrawingClass.ELEVATION),
    ("TD-A-130-0001_PERSPECTIVES 1.pdf", DrawingClass.DISCARD),
])
def test_filename_tier_classifies_dayone(filename, expected):
    r = classify_filename(filename)
    assert r is not None,                       f"{filename}: no rule matched"
    assert r.drawing_class == expected,         f"{filename}: expected {expected}, got {r.drawing_class}"
    assert r.tier == ClassifierTier.FILENAME
    assert r.confidence == 1.0


def test_perspective_rule_precedes_elevation():
    """Critical edge case from PLAN.md §16: PERSPECTIVE share TD-A-130-…
    prefix and would leak into ELEVATION if rule order were wrong."""
    r = classify_filename("TD-A-130-0002_PERSPECTIVES 2.pdf")
    assert r.drawing_class == DrawingClass.DISCARD


def test_struct_overall_pattern_does_not_match_enlarged():
    """The -00 vs -0[1-4] split is exact; no fuzzy slop."""
    r0 = classify_filename("TGCH-TD-S-200-B3-00.pdf")
    r1 = classify_filename("TGCH-TD-S-200-B3-01.pdf")
    assert r0.drawing_class == DrawingClass.STRUCT_PLAN_OVERALL
    assert r1.drawing_class == DrawingClass.STRUCT_PLAN_ENLARGED


def test_unknown_filename_returns_none():
    """Filenames that match no rule must fall through (later tiers will try)."""
    assert classify_filename("random_drawing.pdf") is None
    assert classify_filename("TD-A-111-L101_1ST STOREY PLAN ZONE 1.pdf") is None


# ── Full-fixture integration ─────────────────────────────────────────────────

@pytest.mark.slow
@fixture_required
def test_full_fixture_classification_counts():
    """Live fixture is 125 PDFs. The 81-PDF canonical subset must classify
    by filename alone; the 44 ARCH zone-plans (TD-A-111-*) fall through to
    UNKNOWN until the LLM judge lands in Step 2b."""
    pdfs     = walk_uploads(FIXTURE)
    manifest = ingest(pdfs)
    items    = classify_manifest(manifest)
    s        = summarise(items)

    assert s["by_class"].get("STRUCT_PLAN_OVERALL")  == 14
    assert s["by_class"].get("STRUCT_PLAN_ENLARGED") == 56
    assert s["by_class"].get("ELEVATION")            == 5
    assert s["by_class"].get("SECTION")              == 4
    assert s["by_class"].get("DISCARD")              == 2
    assert s["by_class"].get("UNKNOWN")              == 44

    # Tier accounting must add up.
    assert s["by_tier"].get("filename")   == 81
    assert s["by_tier"].get("unresolved") == 44
    assert sum(s["by_class"].values())    == 125
    assert sum(s["by_tier"].values())     == 125


@pytest.mark.slow
@fixture_required
def test_classification_report_persists_to_workspace(tmp_path):
    """Stage 2 wiring writes _classification_report.json to workspace.output."""
    from backend.core.orchestrator import run as run_pipeline
    from backend.core.workspace    import Workspace

    ws  = Workspace.fresh(tmp_path / "job")
    res = run_pipeline(workspace=ws, walk_root=FIXTURE)

    report = ws.output / "_classification_report.json"
    assert report.exists()

    import json
    data = json.loads(report.read_text())
    assert data["summary"]["total"] == 125
    assert len(data["items"])       == 125
    # Every item carries the four canonical fields.
    for item in data["items"][:5]:
        assert "class" in item and "tier" in item and "confidence" in item and "reason" in item
