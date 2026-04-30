"""Step 10 regression tests for Stage 5B — Geometry Emitter (PLAN.md §9).

Coverage:
  - gates: pass/fail per gate, hard vs warn severity
  - gltf: per-shape mesh count + bounding box sanity
  - revit_transaction: v4 recipe schema + slab synthesis
  - RevitClient: health + HTTP success/failure with mocked httpx
  - emit_storey: end-to-end on a synthetic storey
  - slow integration: full L3 pipeline through emission
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import trimesh    # type: ignore[import-untyped]

from backend.emit.gates              import GateResult, validate_storey_gates
from backend.emit.gltf               import emit_storey_gltf
from backend.emit.revit_client       import RevitClient, RvtBuildResult
from backend.emit.revit_transaction  import emit_revit_transaction
from backend.emit.runner             import emit_storey


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE   = REPO_ROOT / "tests" / "fixtures" / "sample_uploaded_documents" / "FLOOR FRAMING PLANS"

fixture_required = pytest.mark.skipif(
    not FIXTURE.exists(),
    reason="reference fixture symlink missing — see PLAN.md §3.1",
)


# ── Fixture helpers ──────────────────────────────────────────────────────────

def _typing_payload(placements: list[dict]) -> dict:
    return {
        "storey_id":  "TEST",
        "summary":    {"column_count": len(placements), "placements": len(placements),
                       "rejected": 0, "tier_counts": {}},
        "placements": placements,
    }


def _placement(label, shape, x, y, *, dim_x=None, dim_y=None, d=None) -> dict:
    src = {"d": d} if shape == "round" else {"x": dim_x, "y": dim_y}
    return {
        "grid_mm_xy":   [x, y],
        "type_id":      f"t-{label}",
        "type_name":    f"{label}_T",
        "family_name":  "Concrete-Rectangular-Column",
        "rotation_deg": 0,
        "comments":     label,
        "source_label": label,
        "source_dims":  src,
        "shape":        shape,
        "is_steel":     False,
        "audit":        f"MATCHED_EXACT(...,{label}_T)",
        "tier":         "MATCHED_EXACT",
        "dim_delta_mm": None,
        "flags":        [],
    }


def _project_levels(rls: list[tuple[str, int]]) -> list[dict]:
    return sorted(
        [{"name": n, "rl_mm": rl, "source": "manual"} for n, rl in rls],
        key=lambda l: l["rl_mm"],
    )


def _inventory_payload(shapes: list[str]) -> dict:
    return {
        "families": [
            {"family_name": f"Family-{s}", "shape": s, "types": [
                {"type_id": f"starter:{s}", "type_name": f"STARTER_{s}",
                 "shape": s, "label": None,
                 "dim_x_mm": 800, "dim_y_mm": 800, "diameter_mm": 800,
                 "is_synthetic": False},
            ]}
            for s in shapes
        ],
    }


# ── Gate logic ───────────────────────────────────────────────────────────────

def test_all_gates_pass_minimal_inputs() -> None:
    typ = _typing_payload([_placement("C2", "square", 0.0, 0.0,
                                      dim_x=800, dim_y=800)])
    rec = {"columns": [{"label": "C2", "flags": []}]}
    g = validate_storey_gates(
        storey_id          = "L3",
        overall_payload    = {"affine_residual_px": 0.15, "grid": {}},
        reconciled_payload = rec,
        typing_payload     = typ,
        project_levels     = _project_levels([("L3", 8100), ("L4", 12600)]),
        inventory_shapes   = {"square"},
        slab_default_mm    = 200.0,
    )
    assert g.all_passed
    assert g.base_rl_mm == 8100
    assert g.top_rl_mm  == 12600
    assert g.storey_height_mm == 4500


def test_uncovered_columns_warn_not_fail() -> None:
    """PLAN §11: missing-column coverage is a WARN, not a hard fail —
    emission still proceeds; uncovered columns go to the review queue."""
    typ = _typing_payload([_placement("C2", "square", 0.0, 0.0,
                                      dim_x=800, dim_y=800)])
    rec = {"columns": [
        {"label": "C2",  "flags": []},
        {"label": None,  "flags": ["label_missing"]},
    ]}
    g = validate_storey_gates(
        storey_id          = "L3",
        overall_payload    = {"affine_residual_px": 0.15, "grid": {}},
        reconciled_payload = rec,
        typing_payload     = typ,
        project_levels     = _project_levels([("L3", 0), ("L4", 4500)]),
        inventory_shapes   = {"square"},
        slab_default_mm    = 200.0,
    )
    assert g.all_passed                  # hard gates still all pass
    assert any(gate.name == "enlarged_coverage" and gate.severity == "warn"
               and not gate.passed for gate in g.gates)
    assert g.warnings, "expected the coverage gate to register a warning"


def test_missing_base_level_hard_fails() -> None:
    typ = _typing_payload([])
    g = validate_storey_gates(
        storey_id          = "L3",
        overall_payload    = {"affine_residual_px": 0.15, "grid": {}},
        reconciled_payload = {"columns": []},
        typing_payload     = typ,
        project_levels     = _project_levels([]),         # no L3 entry
        inventory_shapes   = {"square"},
        slab_default_mm    = 200.0,
    )
    assert not g.all_passed
    assert any(f.name == "base_level_present" for f in g.hard_failures)


def test_missing_slab_thickness_hard_fails() -> None:
    typ = _typing_payload([])
    g = validate_storey_gates(
        storey_id          = "L3",
        overall_payload    = {"affine_residual_px": 0.15, "grid": {}},
        reconciled_payload = {"columns": []},
        typing_payload     = typ,
        project_levels     = _project_levels([("L3", 0), ("L4", 4500)]),
        inventory_shapes   = {"square"},
        slab_default_mm    = None,                 # gate fail
    )
    assert not g.all_passed
    assert any(f.name == "slab_thickness_present" for f in g.hard_failures)


def test_missing_starter_family_hard_fails() -> None:
    typ = _typing_payload([_placement("RD1", "round", 0.0, 0.0, d=1130)])
    g = validate_storey_gates(
        storey_id          = "L3",
        overall_payload    = {"affine_residual_px": 0.15, "grid": {}},
        reconciled_payload = {"columns": [{"label": "RD1", "flags": []}]},
        typing_payload     = typ,
        project_levels     = _project_levels([("L3", 0), ("L4", 4500)]),
        inventory_shapes   = {"square"},                 # no "round"
        slab_default_mm    = 200.0,
    )
    assert not g.all_passed
    fail = next(f for f in g.hard_failures if f.name == "starter_family_for_each_shape")
    assert "round" in fail.detail


def test_top_level_fallback_when_no_above() -> None:
    typ = _typing_payload([_placement("C2", "square", 0.0, 0.0,
                                      dim_x=800, dim_y=800)])
    g = validate_storey_gates(
        storey_id          = "RF",
        overall_payload    = {"affine_residual_px": 0.15, "grid": {}},
        reconciled_payload = {"columns": [{"label": "C2", "flags": []}]},
        typing_payload     = typ,
        project_levels     = _project_levels([("RF", 30000)]),    # no level above
        inventory_shapes   = {"square"},
        slab_default_mm    = 200.0,
    )
    assert g.all_passed
    assert g.top_rl_mm == 30000 + 4500           # fallback height


# ── GLTF emit ────────────────────────────────────────────────────────────────

def test_gltf_emits_one_mesh_per_column(tmp_path: Path) -> None:
    placements = [
        _placement("C2", "square",      0.0, 0.0, dim_x=800,  dim_y=800),
        _placement("C9", "rectangular", 8400.0, 0.0, dim_x=1150, dim_y=800),
        _placement("RD1", "round",      0.0, 8400.0, d=1130),
    ]
    typ = _typing_payload(placements)
    r = emit_storey_gltf(
        storey_id   = "TEST",
        typing_payload = typ,
        base_rl_mm  = 0,
        top_rl_mm   = 4500,
        out_dir     = tmp_path,
        include_slab = False,
    )
    assert r.column_count == 3
    assert r.skipped == 0
    assert r.gltf_path.exists()
    # Round-trip parse — bbox should fit the three columns.
    scene = trimesh.load(r.gltf_path)
    assert len(list(scene.geometry.values())) == 3
    bb = scene.bounds
    # Columns span ~9 m × 9 m × 4.5 m
    assert bb[1][0] - bb[0][0] > 0.5
    assert bb[1][1] - bb[0][1] > 0.5
    assert bb[1][2] - bb[0][2] == pytest.approx(4.5, abs=0.05)


def test_gltf_skips_columns_with_missing_dims(tmp_path: Path) -> None:
    placements = [
        _placement("C2", "square", 0.0, 0.0, dim_x=800, dim_y=800),
        _placement("X",  "rectangular", 8400.0, 0.0),   # no dims
    ]
    r = emit_storey_gltf("TEST", _typing_payload(placements), 0, 4500,
                         tmp_path, include_slab=False)
    assert r.column_count == 1
    assert r.skipped == 1


# ── Revit transaction (v4 recipe) ────────────────────────────────────────────

def test_revit_transaction_carries_full_detected_level_stack(tmp_path: Path) -> None:
    """Recipe shape mirrors v4's contract, BUT the levels array carries the
    full *detected* project level stack (not a hardcoded Level 0 / 1)."""
    placements = [
        _placement("C2", "square",      0.0, 0.0, dim_x=800, dim_y=800),
        _placement("C9", "rectangular", 8400.0, 0.0, dim_x=1150, dim_y=800),
        _placement("RD1", "round",      0.0, 8400.0, d=1130),
    ]
    project_levels = _project_levels([
        ("BASEMENT 3", -6700),
        ("BASEMENT 2", -2500),
        ("BASEMENT 1",  3500),
        ("1ST STOREY", 9500),
        ("2ND STOREY", 15500),
        ("L3",          8100),
        ("L4",         12600),
    ])
    r = emit_revit_transaction(
        storey_id          = "L3",
        typing_payload     = _typing_payload(placements),
        base_rl_mm         = 8100,
        top_rl_mm          = 12600,
        base_level_name    = "L3",
        top_level_name     = "L4",
        project_levels     = project_levels,
        slab_thickness_mm  = 200.0,
        slab_zones         = {},
        out_dir            = tmp_path,
    )
    recipe = json.loads(r.transaction_path.read_text())
    for key in ("job_id", "levels", "grids", "columns", "structural_framing",
                "walls", "core_walls", "stairs", "lifts", "slabs", "metadata"):
        assert key in recipe, f"missing top-level key {key!r}"
    assert recipe["job_id"] == "L3"

    # All detected levels present, sorted by elevation, no Level 0/1 hardcode.
    level_names      = [l["name"] for l in recipe["levels"]]
    level_elevations = [l["elevation"] for l in recipe["levels"]]
    assert "BASEMENT 1" in level_names
    assert "1ST STOREY" in level_names
    assert "L3" in level_names
    assert "Level 0" not in level_names
    assert "Level 1" not in level_names
    assert level_elevations == sorted(level_elevations)

    # Each column references THIS storey's resolved level names.
    for col in recipe["columns"]:
        assert col["Parameters"]["Level"]    == "L3"
        assert col["Parameters"]["TopLevel"] == "L4"
        assert col["level"]     == "L3"
        assert col["top_level"] == "L4"

    # Per-column shape mapping unchanged.
    rd = next(c for c in recipe["columns"] if c["type_mark"] == "RD1")
    assert rd["shape"] == "circular"
    assert rd["width"] == rd["depth"] == 1130

    # One plan-extent slab anchored on the resolved base level.
    assert len(recipe["slabs"]) == 1
    assert recipe["slabs"][0]["level"] == "L3"


def test_revit_transaction_uses_architectural_names_when_provided(tmp_path: Path) -> None:
    """When the elevation extractor surfaces architectural names (BASEMENT 1
    etc.), the per-column level refs use those, not the structural storey id."""
    placements = [_placement("C2", "square", 0.0, 0.0, dim_x=800, dim_y=800)]
    r = emit_revit_transaction(
        storey_id          = "B1_storey",
        typing_payload     = _typing_payload(placements),
        base_rl_mm         = 3500,
        top_rl_mm          = 9500,
        base_level_name    = "BASEMENT 1",
        top_level_name     = "1ST STOREY",
        project_levels     = _project_levels([
            ("BASEMENT 1",  3500),
            ("1ST STOREY",  9500),
            ("2ND STOREY", 15500),
        ]),
        slab_thickness_mm  = 200.0,
        slab_zones         = {},
        out_dir            = tmp_path,
    )
    recipe = json.loads(r.transaction_path.read_text())
    assert recipe["columns"][0]["Parameters"]["Level"]    == "BASEMENT 1"
    assert recipe["columns"][0]["Parameters"]["TopLevel"] == "1ST STOREY"
    assert recipe["metadata"]["base_level_name"] == "BASEMENT 1"
    assert recipe["metadata"]["top_level_name"]  == "1ST STOREY"


def test_revit_transaction_skips_columns_without_dims(tmp_path: Path) -> None:
    placements = [
        _placement("C2", "square", 0.0, 0.0, dim_x=800, dim_y=800),
        _placement("X",  "rectangular", 8400.0, 0.0),    # no dims
    ]
    r = emit_revit_transaction(
        storey_id          = "L3",
        typing_payload     = _typing_payload(placements),
        base_rl_mm         = 0,
        top_rl_mm          = 4500,
        base_level_name    = "L3",
        top_level_name     = "L4",
        project_levels     = _project_levels([("L3", 0), ("L4", 4500)]),
        slab_thickness_mm  = 200.0,
        slab_zones         = {},
        out_dir            = tmp_path,
    )
    assert r.column_count == 1
    assert r.skipped == 1


# ── RevitClient ──────────────────────────────────────────────────────────────

def test_revit_client_health_unreachable_is_false() -> None:
    """Pointing at a port that's almost certainly closed should fail health
    fast and report unhealthy — we DON'T want exceptions escaping."""
    c = RevitClient(server_url="http://127.0.0.1:1", timeout_s=2)
    assert c.is_healthy() is False


def test_revit_client_build_unreachable_returns_error(tmp_path: Path) -> None:
    """build() never raises into the caller; failure surfaces as error!=None."""
    transaction = tmp_path / "tx.json"
    transaction.write_text(json.dumps({"job_id": "X"}))
    c = RevitClient(server_url="http://127.0.0.1:1", timeout_s=2)
    r = c.build(transaction, "X", tmp_path / "out")
    assert isinstance(r, RvtBuildResult)
    assert r.rvt_path is None
    assert r.error is not None


def test_revit_client_http_success_with_mock(tmp_path: Path, monkeypatch) -> None:
    """Stub httpx.Client to return a fake .rvt and verify build saves it."""
    from backend.emit import revit_client as rc
    transaction = tmp_path / "tx.json"
    transaction.write_text(json.dumps({"job_id": "L3", "columns": []}))

    rvt_bytes = rc._RVT_MAGIC + b"\x00" * 4096    # plausible OLE container

    class _FakeResponse:
        status_code = 200
        content     = rvt_bytes
        headers     = {"x-revit-warnings": "[]", "x-revit-warnings-version": "1"}

    class _FakeClient:
        def __init__(self, *a, **kw):       pass
        def __enter__(self):                return self
        def __exit__(self, *a):             return False
        def post(self, *a, **kw):           return _FakeResponse()

    monkeypatch.setattr(rc.httpx, "Client", _FakeClient)
    c = RevitClient(server_url="http://stub", timeout_s=5)
    out = tmp_path / "out"
    r = c.build(transaction, "L3", out)
    assert r.error is None
    assert r.rvt_path is not None and r.rvt_path.exists()
    assert r.rvt_path.read_bytes()[:4] == rc._RVT_MAGIC
    assert r.warnings == []


def test_revit_client_rejects_non_rvt_response(tmp_path: Path, monkeypatch) -> None:
    from backend.emit import revit_client as rc
    transaction = tmp_path / "tx.json"
    transaction.write_text("{}")

    class _FakeResponse:
        status_code = 200
        content     = b"<html>oops</html>"
        headers     = {}

    class _FakeClient:
        def __init__(self, *a, **kw):       pass
        def __enter__(self):                return self
        def __exit__(self, *a):             return False
        def post(self, *a, **kw):           return _FakeResponse()

    monkeypatch.setattr(rc.httpx, "Client", _FakeClient)
    c = RevitClient(server_url="http://stub", timeout_s=5)
    r = c.build(transaction, "X", tmp_path / "out")
    assert r.rvt_path is None
    assert r.error and "look like .rvt" in r.error


# ── Runner end-to-end ─────────────────────────────────────────────────────────

def test_emit_storey_succeeds_minimal(tmp_path: Path) -> None:
    typ = _typing_payload([_placement("C2", "square", 0.0, 0.0,
                                      dim_x=800, dim_y=800)])
    inv = _inventory_payload(["square"])
    r = emit_storey(
        storey_id          = "L3",
        overall_payload    = {"affine_residual_px": 0.15, "grid": {}},
        reconciled_payload = {"columns": [{"label": "C2", "flags": []}]},
        typing_payload     = typ,
        project_levels     = _project_levels([("L3", 8100), ("L4", 12600)]),
        slab_default_mm    = 200.0,
        slab_zones         = {},
        inventory_payload  = inv,
        out_dir            = tmp_path,
    )
    assert r.succeeded
    assert r.gltf is not None        and r.gltf.gltf_path.exists()
    assert r.transaction is not None and r.transaction.transaction_path.exists()
    # No revit_client supplied → no RVT build attempted.
    assert r.rvt_build is None


def test_emit_storey_aborts_on_hard_gate_failure(tmp_path: Path) -> None:
    typ = _typing_payload([])
    r = emit_storey(
        storey_id          = "L3",
        overall_payload    = {"affine_residual_px": 0.15, "grid": {}},
        reconciled_payload = {"columns": []},
        typing_payload     = typ,
        project_levels     = _project_levels([]),     # no L3 → hard fail
        slab_default_mm    = 200.0,
        slab_zones         = {},
        inventory_payload  = _inventory_payload(["square"]),
        out_dir            = tmp_path,
    )
    assert not r.succeeded
    assert r.gltf is None
    assert r.transaction is None
    assert r.skipped_reason and "base_level_present" in r.skipped_reason


# ── Slow integration ─────────────────────────────────────────────────────────

@pytest.mark.slow
@fixture_required
def test_emit_l3_real(tmp_path: Path) -> None:
    """Run the full chain Stage 3 → 4 → 5A → 5B on real L3 fixtures.
    Verify the storey emits a non-trivial gltf + transaction recipe."""
    from backend.extract.plan_enlarged import extract_enlarged
    from backend.extract.plan_overall  import extract_overall
    from backend.reconcile.storey      import reconcile_storey
    from backend.resolve.inventory     import starter_inventory
    from backend.resolve.resolver      import resolve_storey

    base = FIXTURE
    o = extract_overall(base / "TGCH-TD-S-200-L3-00.pdf", 0,
                        tmp_path / "po", run_yolo=True)
    enl = []
    for n in (1, 2, 3, 4):
        e = extract_enlarged(base / f"TGCH-TD-S-200-L3-0{n}.pdf", 0,
                             tmp_path / "pe", run_yolo=True)
        enl.append(e.payload_path)
    rec = reconcile_storey(o.payload_path, enl, tmp_path / "rc")
    inv = starter_inventory()
    rs  = resolve_storey(rec.payload_path, inv, tmp_path / "rs")

    er = emit_storey(
        storey_id          = "L3",
        overall_payload    = json.loads(o.payload_path.read_text()),
        reconciled_payload = json.loads(rec.payload_path.read_text()),
        typing_payload     = json.loads(rs.typing_path.read_text()),
        project_levels     = _project_levels([("L3", 8100), ("L4", 12600)]),
        slab_default_mm    = 200.0,
        slab_zones         = {},
        inventory_payload  = json.loads((tmp_path / "rs" / "_inventory.json").read_text())
                              if (tmp_path / "rs" / "_inventory.json").exists()
                              else inv.to_dict(),
        out_dir            = tmp_path / "out",
    )
    assert er.succeeded, f"gates: {er.gates.failures}"
    assert er.gltf is not None
    assert er.transaction is not None
    assert er.transaction.transaction_path.exists()
    # L3 has hundreds of columns — gltf should be non-trivially sized.
    assert er.gltf.column_count > 100
    assert er.gltf.gltf_path.stat().st_size > 10_000

    # Verify the recipe carries v4 column-entry shape AND uses the detected
    # level names (passed in via project_levels) — not Level 0 / Level 1.
    recipe = json.loads(er.transaction.transaction_path.read_text())
    assert recipe["job_id"] == "L3"
    assert "Parameters" in recipe["columns"][0]
    assert "Properties" in recipe["columns"][0]
    # Resolved against project_levels = [("L3", 8100), ("L4", 12600)] above.
    assert recipe["columns"][0]["Parameters"]["Level"]    == "L3"
    assert recipe["columns"][0]["Parameters"]["TopLevel"] == "L4"
    # The full level stack appears in the levels array.
    assert any(l["name"] == "L3" for l in recipe["levels"])
    assert any(l["name"] == "L4" for l in recipe["levels"])
    # No revit_client passed → no RVT was attempted.
    assert er.rvt_build is None
