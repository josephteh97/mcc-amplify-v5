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
    """Reload api modules with an isolated jobs root."""
    monkeypatch.setenv("JOBS_ROOT", str(tmp_path / "jobs"))
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
    return sorted(FIXTURE.rglob("*.pdf"))[:3]


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

    r = await client.get(f"/api/jobs/{job_id}/manifest")
    assert r.status_code == 200
    manifest = r.json()
    assert manifest["file_count"] == 3
    assert len(manifest["files"]) == 3
    assert all(len(f["page_hashes"]) == f["n_pages"] for f in manifest["files"])


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
