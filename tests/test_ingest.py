"""Step 1a regression tests.

Anchored to the reference fixture (§3.1). Skip cleanly when the symlink is
missing so CI on a fresh checkout doesn't false-fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.workspace import Workspace
from backend.ingest.ingest import ingest, walk_uploads


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE   = REPO_ROOT / "tests" / "fixtures" / "sample_uploaded_documents"


fixture_required = pytest.mark.skipif(
    not FIXTURE.exists(),
    reason="reference fixture symlink missing — see PLAN.md §3.1",
)


@fixture_required
def test_walk_finds_125_pdfs():
    """Live fixture currently ships 125 PDFs (81 canonical + 44 ARCH zone-plans).

    The classifier in Step 2 will DISCARD the extras; ingest just enumerates.
    """
    pdfs = walk_uploads(FIXTURE)
    assert len(pdfs) == 125
    assert all(p.suffix.lower() == ".pdf" for p in pdfs)


@fixture_required
def test_walk_is_sorted_and_stable():
    a = walk_uploads(FIXTURE)
    b = walk_uploads(FIXTURE)
    assert a == b
    assert a == sorted(a)


@fixture_required
def test_page_hashes_are_deterministic():
    """Re-ingesting the same PDF must produce identical hashes (§4 dedupe)."""
    pdfs = walk_uploads(FIXTURE)[:3]
    a = ingest(pdfs)
    b = ingest(pdfs)
    for x, y in zip(a, b):
        assert x.page_hashes == y.page_hashes
        assert x.n_pages     == len(x.page_hashes)


def test_workspace_fresh_wipes_root(tmp_path: Path):
    root = tmp_path / "job_x"
    (root / "uploads").mkdir(parents=True)
    (root / "uploads" / "stale.pdf").write_bytes(b"old")

    ws = Workspace.fresh(root)
    assert ws.root.exists()
    assert ws.uploads.exists()
    assert ws.extracted.exists()
    assert ws.output.exists()
    assert not (ws.uploads / "stale.pdf").exists()
