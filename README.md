# MCC Amplify v5.3 — Floor Plan to BIM

Single-shot pipeline that ingests a consultant's structural PDF set and emits
Autodesk Revit 2023 RVT + GLTF per storey. Strict-mode, fail-loud, no silent
coercion. Canonical plan: [`docs/PLAN.md`](docs/PLAN.md).

## Status

| Step | What | Status |
|------|------|--------|
| 0    | Layout scaffold per PLAN.md §12                 | done |
| 1a   | Core skeleton + Stage 1 ingest (page-fingerprint) | done |
| 1b   | FastAPI surface — `/upload`, `/jobs/{id}`, `/ws/{id}` | done |
| 1c   | Frontend multi-file upload UI                   | done |
| 2    | Stage 2 — Classifier (filename → title-block → content → Ollama judge → UI) | pending |
| 3    | Per-extractor probe scripts                     | pending |
| 4–7  | Stage 3 extractors (overall, enlarged, elevation, section) | pending |
| 8    | Stage 4 — Reconciler                            | pending |
| 9    | Stage 5A — Type Resolver + Family Manager       | pending |
| 10   | Stage 5B — Geometry Emitter (Revit 2023 + GLTF) | pending |
| 11   | Frontend review queue                            | pending |

## Quick start (dev)

```bash
# One-liner — boots backend and frontend in parallel:
./run.sh

# Or run them separately in two terminals:
python3 -m uvicorn backend.api.app:app --reload --port 8000
cd frontend && npm install && npm run dev    # http://localhost:5173
```

The frontend proxies `/api` and `/ws` to the FastAPI server on `:8000`.

## Layout (PLAN.md §12)

```
mcc-amplify-v5/
├── backend/
│   ├── api/                    FastAPI surface (routes, websocket, jobs)
│   ├── core/                   grid_mm, meta_yaml, workspace, orchestrator
│   ├── ingest/                 Stage 1 (multi-file, page-fingerprint)
│   ├── classify/               Stage 2 (4 classes + DISCARD + Ollama judge)
│   ├── extract/
│   │   ├── plan_overall/       Stage 3A-1 — grid + canonical positions
│   │   ├── plan_enlarged/      Stage 3A-2 — labels + dims + shape
│   │   ├── elevation/          Stage 3B   — RL only
│   │   └── section/            Stage 3C   — slab/beam depth
│   ├── reconcile/              Stage 4
│   ├── resolve/                Stage 5A — type resolver + family manager
│   └── emit/{revit,gltf}/      Stage 5B
├── frontend/                   React + Vite — multi-file upload, progress, manifest
├── ml/weights/                 YOLO models (column @1280, framing @640)
├── revit_scripts/              pyRevit consumer of Stage 5A placement plan
├── revit_server/               Windows-side Revit bridge (kept from v4)
├── tests/
│   ├── test_ingest.py          Stage 1 + Workspace
│   ├── test_api.py             FastAPI + WebSocket integration
│   └── fixtures/sample_uploaded_documents → ~/Documents/sample_uploaded_documents
├── scripts/
│   └── ingest_cli.py           Headless Stage 1 verification
├── docs/
│   └── PLAN.md                 Canonical v5.3 plan (read this first)
├── requirements.txt
└── run.sh                      Boot backend + frontend together
```

## API surface (Step 1b)

| Method | Path                          | Purpose                                       |
|--------|-------------------------------|-----------------------------------------------|
| POST   | `/api/upload`                 | Multi-file PDF upload; creates job + workspace |
| GET    | `/api/jobs/{id}`              | Job status (`pending` \| `running` \| `completed` \| `failed`) |
| GET    | `/api/jobs/{id}/manifest`     | Final ingest manifest JSON                    |
| GET    | `/api/jobs`                   | List all jobs in this server's lifetime       |
| GET    | `/api/health`                 | Liveness                                      |
| WS     | `/api/ws/{id}`                | Live progress events + backlog replay         |

`MAX_CONCURRENT_JOBS=3` (env-overridable). Server restart drops in-flight job
state, but workspaces under `data/jobs/<id>/` survive — completed jobs are
recoverable from `output/manifest.json`.

## Tests

```bash
python3 -m pytest tests/ -v
```

9 tests today (4 ingest + workspace, 5 API + WebSocket), all pinned to the
reference fixture. Run skips cleanly if the symlink is missing.

## Reference data

The 81-PDF canonical TGCH sample (and live 125-PDF superset including ARCH
zone-plans the classifier will DISCARD) lives at
`~/Documents/sample_uploaded_documents/`, symlinked into
`tests/fixtures/sample_uploaded_documents/` for tests. See PLAN.md §3.1.

## Out of scope (v5.3)

Schedules, ARCH plans/elevations/sections as deliverables, MEP, detail
drawings, foundation drawings, DWG/DXF/IFC input, OCR, L/T column shapes,
elevation column-continuity, multi-job project workspaces. Full list in
PLAN.md §17.
