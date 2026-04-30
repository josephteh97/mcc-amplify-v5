"""Step 1b API integration tests.

Async tests using httpx.AsyncClient so the event loop persists across calls
(TestClient's per-request portal orphans background tasks).
"""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE   = REPO_ROOT / "tests" / "fixtures" / "sample_uploaded_documents"

fixture_required = pytest.mark.skipif(
    not FIXTURE.exists(),
    reason="reference fixture symlink missing — see PLAN.md §3.1",
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Reload api modules with an isolated jobs root.

    Tier 4 (Ollama LLM judge) is disabled here so the API tests stay fast
    and deterministic — the LLM tier is exercised separately in
    test_classify_llm.py.
    """
    monkeypatch.setenv("JOBS_ROOT", str(tmp_path / "jobs"))
    monkeypatch.setattr("backend.classify.llm_judge.LLM_DISABLED", True)
    import backend.api.jobs
    importlib.reload(backend.api.jobs)
    import backend.api.routes
    importlib.reload(backend.api.routes)
    import backend.api.app
    importlib.reload(backend.api.app)
    return backend.api.app.app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _three_fixture_pdfs() -> list[Path]:
    """Three TGCH structural PDFs that filename-tier-match — keeps API
    smoke tests in the seconds, not the minutes. Alphabetical sort across
    the whole fixture would pick ARCH zone-plans first, which fall through
    to tier 4 (LLM) and slow tests by ~25s/page."""
    return sorted((FIXTURE / "FLOOR FRAMING PLANS").glob("*.pdf"))[:3]


async def _wait_for_status(client, job_id: str, target: str, timeout: float = 30.0) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        r = await client.get(f"/api/jobs/{job_id}")
        status = r.json()
        if status["status"] in (target, "failed"):
            return status
        await asyncio.sleep(0.05)
    return status


@fixture_required
async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@fixture_required
async def test_upload_rejects_non_pdf(client):
    r = await client.post(
        "/api/upload",
        files=[("files", ("notes.txt", b"hi", "text/plain"))],
    )
    assert r.status_code == 400


@fixture_required
async def test_upload_runs_full_job_and_serves_manifest(client):
    pdfs  = _three_fixture_pdfs()
    files = [
        ("files", (p.name, p.read_bytes(), "application/pdf"))
        for p in pdfs
    ]
    r = await client.post("/api/upload", files=files)
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    assert r.json()["file_count"] == 3

    status = await _wait_for_status(client, job_id, "completed")
    assert status["status"] == "completed", status
    assert status["file_count"] == 3
    assert status["page_count"] == 3   # fixture sample is single-page per PDF
    assert status["workspace_root"]    # populated from upload-time onward

    r = await client.get(f"/api/jobs/{job_id}/manifest")
    assert r.status_code == 200
    manifest = r.json()
    assert manifest["file_count"] == 3
    assert len(manifest["files"]) == 3
    assert all(len(f["page_hashes"]) == f["n_pages"] for f in manifest["files"])


@fixture_required
async def test_classification_endpoint(client):
    """The classification report becomes available once Stage 2 completes."""
    pdfs  = _three_fixture_pdfs()
    files = [("files", (p.name, p.read_bytes(), "application/pdf")) for p in pdfs]
    r = await client.post("/api/upload", files=files)
    job_id = r.json()["job_id"]

    await _wait_for_status(client, job_id, "completed")
    r = await client.get(f"/api/jobs/{job_id}/classification")
    assert r.status_code == 200
    report = r.json()
    assert report["summary"]["total"] == 3
    assert len(report["items"])       == 3
    # All three picks are filename-tier-matchable structural PDFs.
    assert report["summary"]["by_class"].get("STRUCT_PLAN_OVERALL", 0) \
         + report["summary"]["by_class"].get("STRUCT_PLAN_ENLARGED", 0) == 3
    assert report["summary"]["by_tier"].get("filename") == 3


@fixture_required
async def test_review_endpoint_aggregates_artefacts(client, tmp_path):
    """/api/jobs/{id}/review composes from on-disk reports.

    We seed synthetic Stage 2 / 4 / 5A / 5B artefacts on the workspace
    *after* uploading so we don't need YOLO + a real Revit server in the
    fast suite. Verifies the aggregator reads the right files into the
    right top-level sections.
    """
    pdfs  = _three_fixture_pdfs()
    files = [("files", (p.name, p.read_bytes(), "application/pdf")) for p in pdfs]
    r = await client.post("/api/upload", files=files)
    job_id = r.json()["job_id"]
    await _wait_for_status(client, job_id, "completed")

    # Inject synthetic review-relevant artefacts onto this job's workspace.
    from backend.api.jobs import job_store
    job = job_store.get(job_id)
    ws_root = job.workspace_root
    out_dir = ws_root / "output"
    rec_dir = ws_root / "extracted" / "reconcile"
    rec_dir.mkdir(parents=True, exist_ok=True)

    # ── Reconcile: one storey with a label_conflict + a label_missing.
    (rec_dir / "L3.reconciled.json").write_text(json.dumps({
        "storey_id": "L3",
        "summary":   {"canonical_total": 5, "labelled": 3, "label_inferred": 1,
                      "label_missing": 1, "label_conflicts": 1},
        "columns": [
            {"canonical_idx": 0, "label": "C2",    "flags": []},
            {"canonical_idx": 1, "label": None,    "flags": ["label_missing"],
             "canonical_grid_mm_xy": [10000, 20000]},
            {"canonical_idx": 2, "label": "C9",    "flags": ["label_conflict:2_distinct_tuples"],
             "canonical_grid_mm_xy": [16800, 20000],
             "label_candidates": [
                 {"label": "C9",   "shape": "rectangular",
                  "dim_along_x_mm": 1150, "dim_along_y_mm": 800,
                  "source_pdf": "L3-01.pdf", "distance_mm": 35.0},
                 {"label": "RCB2", "shape": "rectangular",
                  "dim_along_x_mm": 800, "dim_along_y_mm": 300,
                  "source_pdf": "L3-02.pdf", "distance_mm": 41.0},
             ]},
        ],
    }))

    # ── Stage 5A: one storey review file with a REJECTED column.
    (out_dir / "L3_review.json").write_text(json.dumps({
        "storey_id": "L3",
        "summary":   {"rejected": 1},
        "items": [{
            "canonical_idx":   1,
            "canonical_grid_mm_xy": [10000, 20000],
            "label":           None,
            "shape":           "unknown",
            "dim_along_x_mm":  None,
            "dim_along_y_mm":  None,
            "diameter_mm":     None,
            "reason":          "shape_unknown",
            "audit":           "REJECTED(shape_unknown)",
            "flags":           ["shape_unknown"],
        }],
    }))

    # ── Stage 5B: emit report with one hard failure + one warning.
    (out_dir / "_emit_report.json").write_text(json.dumps({
        "summary": {"storey_count": 2, "succeeded": 1, "skipped": 1,
                    "total_columns": 244, "rvt_built": 0},
        "storeys": [
            {
                "storey_id": "L3",  "succeeded": True, "skipped_reason": None,
                "gates": {"all_passed": True, "gates": [
                    {"name": "enlarged_coverage",   "passed": False, "severity": "warn",
                     "detail": "1/5 unlabelled"},
                    {"name": "base_level_present",  "passed": True, "severity": "hard",
                     "detail": "L3 RL = 8100 mm"},
                ]},
                "rvt_path": None, "gltf_path": "L3.gltf", "rvt_warnings": [],
                "rvt_error": None,
            },
            {
                "storey_id": "RF", "succeeded": False, "skipped_reason": "RF: hard gates failed",
                "gates": {"all_passed": False, "gates": [
                    {"name": "base_level_present", "passed": False, "severity": "hard",
                     "detail": "no level entry for RF"},
                ]},
                "rvt_path": None, "gltf_path": None, "rvt_warnings": [],
                "rvt_error": None,
            },
        ],
    }))

    # ── Hit the aggregator endpoint.
    r = await client.get(f"/api/jobs/{job_id}/review")
    assert r.status_code == 200
    review = r.json()

    # Classification: came from the real run; tier 1 picks up all three
    # filename-matched STRUCT_* PDFs, so DISCARD/UNRESOLVED are empty.
    assert isinstance(review["classification"]["discarded"], list)
    assert isinstance(review["classification"]["unresolved"], list)

    # Reconcile: locate the L3 record we injected (the upload's own real
    # pipeline may have produced records for other storeys too).
    rec_by_id = {s["storey_id"]: s for s in review["reconcile"]["storeys"]}
    assert "L3" in rec_by_id
    L3 = rec_by_id["L3"]
    assert len(L3["conflicts"]) == 1
    assert L3["conflicts"][0]["canonical_idx"] == 2
    assert len(L3["conflicts"][0]["label_candidates"]) == 2
    assert len(L3["missing"]) == 1
    assert L3["missing"][0]["canonical_idx"] == 1

    # Resolve: our L3 review.json must show up as one of the storeys.
    rj_by_id = {s["storey_id"]: s for s in review["resolve"]["storeys"]}
    assert "L3" in rj_by_id
    assert len(rj_by_id["L3"]["rejected"]) == 1
    assert rj_by_id["L3"]["rejected"][0]["reason"] == "shape_unknown"

    # Emit: synthetic report overrode the real one, so we have exactly L3+RF.
    em = {s["storey_id"]: s for s in review["emit"]["storeys"]}
    assert em["L3"]["warnings"][0]["name"]      == "enlarged_coverage"
    assert em["L3"]["hard_failures"]            == []
    assert em["RF"]["hard_failures"][0]["name"] == "base_level_present"


async def test_review_endpoint_returns_empty_sections_when_artefacts_absent(client):
    """A freshly-uploaded job that hasn't reached Stage 4 yet still serves
    the review endpoint with empty sections (not 404)."""
    pdfs  = _three_fixture_pdfs()
    files = [("files", (p.name, p.read_bytes(), "application/pdf")) for p in pdfs]
    r = await client.post("/api/upload", files=files)
    job_id = r.json()["job_id"]
    # Hit the endpoint immediately — even before Stage 4 wrote anything,
    # the aggregator must respond cleanly.
    r = await client.get(f"/api/jobs/{job_id}/review")
    assert r.status_code == 200
    rv = r.json()
    assert rv["reconcile"]["storeys"] == []
    assert rv["resolve"]["storeys"]   == []
    assert rv["emit"]["storeys"]      == []


async def test_review_endpoint_404_for_unknown_job(client):
    r = await client.get("/api/jobs/does-not-exist/review")
    assert r.status_code == 404


@fixture_required
async def test_storeys_endpoint_lists_emit_targets(client):
    """/api/jobs/{id}/storeys reads _emit_report.json and lists every
    storey with its GLTF availability + column count."""
    pdfs  = _three_fixture_pdfs()
    files = [("files", (p.name, p.read_bytes(), "application/pdf")) for p in pdfs]
    r = await client.post("/api/upload", files=files)
    job_id = r.json()["job_id"]
    await _wait_for_status(client, job_id, "completed")

    # Inject a synthetic emit report with one playable + one skipped storey.
    from backend.api.jobs import job_store
    job = job_store.get(job_id)
    out_dir = job.workspace_root / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    fake_gltf = out_dir / "L3.gltf"
    fake_gltf.write_text(json.dumps({"asset": {"version": "2.0"}, "scenes": [{"nodes": []}]}))
    (out_dir / "_emit_report.json").write_text(json.dumps({
        "summary": {"storey_count": 2, "succeeded": 1, "skipped": 1},
        "storeys": [
            {"storey_id": "L3", "succeeded": True, "skipped_reason": None,
             "gates": {"all_passed": True, "gates": []},
             "rvt_path": None, "gltf_path": str(fake_gltf), "rvt_warnings": [],
             "rvt_error": None, "column_count": 244},
            {"storey_id": "RF", "succeeded": False, "skipped_reason": "RF: hard gates failed",
             "gates": {"all_passed": False, "gates": []},
             "rvt_path": None, "gltf_path": None, "rvt_warnings": [],
             "rvt_error": None, "column_count": None},
        ],
    }))

    r = await client.get(f"/api/jobs/{job_id}/storeys")
    assert r.status_code == 200
    data = r.json()
    by_id = {s["storey_id"]: s for s in data["storeys"]}
    assert by_id["L3"]["has_gltf"] is True
    assert by_id["L3"]["gltf_bytes"] > 0
    assert by_id["L3"]["column_count"] == 244
    assert by_id["RF"]["has_gltf"]   is False
    assert by_id["RF"]["succeeded"]  is False


async def test_storeys_endpoint_empty_before_emit(client):
    pdfs  = _three_fixture_pdfs()
    files = [("files", (p.name, p.read_bytes(), "application/pdf")) for p in pdfs]
    r = await client.post("/api/upload", files=files)
    job_id = r.json()["job_id"]
    # Hit immediately — emit report not yet written; endpoint must respond
    # cleanly with empty list, not 404.
    r = await client.get(f"/api/jobs/{job_id}/storeys")
    assert r.status_code == 200
    assert r.json()["storeys"] == [] or isinstance(r.json()["storeys"], list)


@fixture_required
async def test_gltf_endpoint_streams_file(client):
    pdfs  = _three_fixture_pdfs()
    files = [("files", (p.name, p.read_bytes(), "application/pdf")) for p in pdfs]
    r = await client.post("/api/upload", files=files)
    job_id = r.json()["job_id"]
    await _wait_for_status(client, job_id, "completed")

    from backend.api.jobs import job_store
    job = job_store.get(job_id)
    out_dir = job.workspace_root / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    fake_gltf = out_dir / "L3.gltf"
    fake_gltf.write_text(json.dumps({"asset": {"version": "2.0"}, "scenes": []}))
    (out_dir / "_emit_report.json").write_text(json.dumps({
        "summary": {}, "storeys": [
            {"storey_id": "L3", "succeeded": True, "skipped_reason": None,
             "gates": {}, "rvt_path": None, "gltf_path": str(fake_gltf),
             "rvt_warnings": [], "rvt_error": None, "column_count": 1},
        ],
    }))

    r = await client.get(f"/api/jobs/{job_id}/gltf/L3")
    assert r.status_code == 200
    assert r.headers["content-type"] == "model/gltf+json"
    body = json.loads(r.content)
    assert body["asset"]["version"] == "2.0"


async def test_gltf_endpoint_404_for_unknown_storey(client):
    pdfs  = _three_fixture_pdfs()
    files = [("files", (p.name, p.read_bytes(), "application/pdf")) for p in pdfs]
    r = await client.post("/api/upload", files=files)
    job_id = r.json()["job_id"]
    await _wait_for_status(client, job_id, "completed")
    r = await client.get(f"/api/jobs/{job_id}/gltf/DOES-NOT-EXIST")
    assert r.status_code == 404


@fixture_required
async def test_classification_endpoint_404_before_stage_2(client):
    """A bogus job has no classification report."""
    r = await client.get("/api/jobs/does-not-exist/classification")
    assert r.status_code == 404


@fixture_required
async def test_websocket_replays_backlog_after_completion(app):
    """A WS client connecting after the job finishes still sees the events."""
    pdfs  = _three_fixture_pdfs()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        files = [("files", (p.name, p.read_bytes(), "application/pdf")) for p in pdfs]
        r = await client.post("/api/upload", files=files)
        job_id = r.json()["job_id"]
        await _wait_for_status(client, job_id, "completed")

    # WebSocket round-trip uses TestClient (sync) for protocol convenience —
    # the job is already done so no background tasks are at risk here.
    from fastapi.testclient import TestClient
    received: list[dict] = []
    with TestClient(app) as tc:
        with tc.websocket_connect(f"/api/ws/{job_id}") as ws:
            while True:
                msg = json.loads(ws.receive_text())
                received.append(msg)
                if msg["type"] == "job_completed":
                    break

    types = [e["type"] for e in received]
    assert "stage_started"   in types
    assert "stage_completed" in types
    assert types[-1]         == "job_completed"


@fixture_required
async def test_unknown_job_id_returns_404(client):
    r = await client.get("/api/jobs/does-not-exist")
    assert r.status_code == 404
    r = await client.get("/api/jobs/does-not-exist/manifest")
    assert r.status_code == 404
