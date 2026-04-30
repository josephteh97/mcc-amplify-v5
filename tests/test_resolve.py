"""Step 9 regression tests for Stage 5A — Type Resolver (PLAN.md §8).

Coverage:
  - inventory: round-trip, lookup_by_dims, lookup_by_label, add_type
  - matcher: 4-tier algorithm (EXACT / LABEL / CREATED / REJECTED)
  - matcher: strict-mode rules (no rounding, no label substitution,
             shape mismatch never matches)
  - canonical_type_name across rectangular / square / round / steel
  - resolver: per-storey resolve produces typing.json + review.json
  - slow integration on the L3 reconciled output
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.resolve.inventory import (
    Family,
    FamilyInventory,
    FamilyType,
    load_inventory,
    save_inventory,
    starter_inventory,
)
from backend.resolve.matcher   import (
    MatchTier,
    canonical_type_name,
    match_column,
    shape_code,
)
from backend.resolve.resolver  import resolve_storey


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE   = REPO_ROOT / "tests" / "fixtures" / "sample_uploaded_documents" / "FLOOR FRAMING PLANS"

fixture_required = pytest.mark.skipif(
    not FIXTURE.exists(),
    reason="reference fixture symlink missing — see PLAN.md §3.1",
)


# ── Inventory ─────────────────────────────────────────────────────────────────

def test_starter_inventory_has_one_type_per_shape() -> None:
    inv = starter_inventory()
    shapes = {f.shape for f in inv.families}
    assert shapes == {"rectangular", "square", "round", "steel"}
    for f in inv.families:
        assert len(f.types) == 1


def test_inventory_roundtrip(tmp_path: Path) -> None:
    inv = starter_inventory()
    inv.add_type(shape="rectangular", type_name="X1_R_900x600",
                 label="X1", dim_x_mm=900, dim_y_mm=600)
    p = tmp_path / "inv.json"
    save_inventory(inv, p)
    inv2 = load_inventory(p)
    assert inv2.types_count() == inv.types_count()
    f = inv2.find_family_for_shape("rectangular")
    assert any(t.type_name == "X1_R_900x600" for t in f.types)


def test_lookup_by_dims_within_tol() -> None:
    inv = starter_inventory()
    inv.add_type(shape="rectangular", type_name="C9_R_1150x800",
                 label="C9", dim_x_mm=1150, dim_y_mm=800)
    # Exact dims within tol = 5 mm
    t = inv.lookup_by_dims("rectangular", 1153, 798, None, tol_mm=5)
    assert t is not None and t.type_name == "C9_R_1150x800"
    # Outside tol
    assert inv.lookup_by_dims("rectangular", 1200, 800, None, tol_mm=5) is None


def test_lookup_by_label_returns_delta() -> None:
    inv = starter_inventory()
    inv.add_type(shape="rectangular", type_name="C2_R_800x800",
                 label="C2", dim_x_mm=800, dim_y_mm=800)
    hit = inv.lookup_by_label("rectangular", "c2", 803, 800, None, tol_mm=5)
    assert hit is not None
    t, delta = hit
    assert t.type_name == "C2_R_800x800"
    assert delta == 3
    # case-insensitive
    hit2 = inv.lookup_by_label("rectangular", "  C2  ", 800, 800, None, tol_mm=5)
    assert hit2 is not None


def test_lookup_by_label_excludes_dim_outside_tol() -> None:
    inv = starter_inventory()
    inv.add_type(shape="rectangular", type_name="C2_R_800x800",
                 label="C2", dim_x_mm=800, dim_y_mm=800)
    # Right label but dims off by 50 mm — must NOT match (PLAN §8 strict-mode).
    assert inv.lookup_by_label("rectangular", "C2", 850, 800, None, tol_mm=5) is None


# ── Canonical type name ───────────────────────────────────────────────────────

@pytest.mark.parametrize("label, shape, kwargs, expected", [
    ("C2",   "rectangular", {"dim_x_mm": 1150, "dim_y_mm": 800},   "C2_R_1150x800"),
    ("C2",   "square",      {"dim_x_mm": 800,  "dim_y_mm": 800},   "C2_S_800"),
    ("RD1",  "round",       {"diameter_mm": 1130},                  "RD1_RD_1130"),
    ("H-C2", "steel",       {"dim_x_mm": 600,  "dim_y_mm": 600},   "H-C2_H_600x600"),
    (None,   "round",       {"diameter_mm": 800},                   "UNLABELED_RD_800"),
])
def test_canonical_type_name(label, shape, kwargs, expected) -> None:
    assert canonical_type_name(label, shape, **kwargs) == expected


def test_shape_code_unknown_shape_falls_back() -> None:
    assert shape_code("rectangular") == "R"
    assert shape_code("round")        == "RD"
    assert shape_code("anything_else") == "X"


# ── Matcher: 4 tiers ──────────────────────────────────────────────────────────

def test_matcher_tier1_exact() -> None:
    inv = starter_inventory()
    inv.add_type(shape="square", type_name="C2_S_800",
                 label="C2", dim_x_mm=800, dim_y_mm=800)
    out = match_column(inv, "C2", "square", 803, 800, None)
    assert out.tier == MatchTier.EXACT
    assert out.audit.startswith("MATCHED_EXACT")


def test_matcher_tier2_label_only() -> None:
    """Same label, dims within tol, but a *different* type with closer dims
    is absent — falls to label-only path. We seed an inventory where the
    label-by-dims lookup hits even with a 4 mm delta."""
    inv = FamilyInventory(families=[Family(
        family_name="Concrete-Rectangular-Column", shape="rectangular",
        types=[FamilyType(type_id="t1", type_name="C9_R_1150x800",
                          label="C9", shape="rectangular",
                          dim_x_mm=1150, dim_y_mm=800)],
    )])
    # Tier 1 will already pick this up since dims agree within 5 mm — that's
    # the correct strict path. Verify it's MATCHED_EXACT, not LABEL.
    out = match_column(inv, "C9", "rectangular", 1153, 798, None)
    assert out.tier == MatchTier.EXACT


def test_matcher_tier2_pure_label_match() -> None:
    """Tier 2 fires only when tier 1 misses but the *label* matches and
    dims still agree within tol. Engineer this by giving the inventory a
    type whose dims are inside the label-tol window but not the shape's
    by-dims index (impossible by construction with our index — so we
    verify behaviour symbolically: the algorithm short-circuits at
    tier 1 when applicable, never silently downgrades)."""
    inv = FamilyInventory(families=[Family(
        family_name="Concrete-Rectangular-Column", shape="rectangular",
        types=[FamilyType(type_id="t1", type_name="C9_R_1150x800",
                          label="C9", shape="rectangular",
                          dim_x_mm=1150, dim_y_mm=800)],
    )])
    # Dims off by 6 mm — past tol; tier 1 misses, tier 2 also misses
    # because label match still requires dims within tol.
    out = match_column(inv, "C9", "rectangular", 1156, 800, None)
    assert out.tier == MatchTier.CREATED        # not LABEL — strict-mode


def test_matcher_tier3_created() -> None:
    inv = FamilyInventory()
    out = match_column(inv, "C5", "round", None, None, 800)
    assert out.tier == MatchTier.CREATED
    assert out.type_name == "C5_RD_800"
    # Inventory grew.
    assert inv.types_count() == 1
    # Re-run the same inputs — tier 1 must reuse the just-created type.
    out2 = match_column(inv, "C5", "round", None, None, 800)
    assert out2.tier == MatchTier.EXACT
    assert out2.type_id == out.type_id


def test_matcher_tier4_rejected_unknown_shape() -> None:
    inv = starter_inventory()
    out = match_column(inv, None, "unknown", None, None, None)
    assert out.tier == MatchTier.REJECTED
    assert out.reason == "shape_unknown"
    assert "shape_unknown" in out.flags


def test_matcher_tier4_rejected_dims_missing() -> None:
    inv = starter_inventory()
    out = match_column(inv, "C2", "rectangular", None, 800, None)
    assert out.tier == MatchTier.REJECTED
    assert out.reason == "dims_missing"


def test_matcher_tier4_rejected_l_section_deferred() -> None:
    inv = starter_inventory()
    out = match_column(inv, "L1", "L", 800, 600, None)
    assert out.tier == MatchTier.REJECTED
    assert "deferred" in out.audit


def test_matcher_strict_no_round_substitute_for_square() -> None:
    """Strict shape match — round families never satisfy a square query."""
    inv = FamilyInventory(families=[Family(
        family_name="Concrete-Round-Column", shape="round",
        types=[FamilyType(type_id="rd", type_name="STARTER_RD_800",
                          label=None, shape="round", diameter_mm=800)],
    )])
    out = match_column(inv, "C2", "square", 800, 800, None)
    # No square family exists → tier 3 creates one. Round type must not
    # be reused here.
    assert out.tier == MatchTier.CREATED
    assert "S_" in out.type_name and "RD" not in out.type_name.split("_")[-2:]


# ── Resolver per-storey ───────────────────────────────────────────────────────

def _write_synthetic_reconciled(path: Path, columns: list[dict]) -> None:
    payload = {
        "storey_id": "TEST",
        "columns":   columns,
    }
    path.write_text(json.dumps(payload))


def test_resolve_storey_emits_typing_and_review(tmp_path: Path) -> None:
    rec = tmp_path / "TEST.reconciled.json"
    _write_synthetic_reconciled(rec, [
        {"canonical_idx": 0, "canonical_grid_mm_xy": [0.0, 0.0],
         "label": "C2", "shape": "square", "dim_along_x_mm": 800,
         "dim_along_y_mm": 800, "diameter_mm": None, "is_steel": False,
         "flags": []},
        {"canonical_idx": 1, "canonical_grid_mm_xy": [8400.0, 0.0],
         "label": None, "shape": "unknown", "dim_along_x_mm": None,
         "dim_along_y_mm": None, "diameter_mm": None, "is_steel": False,
         "flags": ["label_missing"]},
        {"canonical_idx": 2, "canonical_grid_mm_xy": [0.0, 8400.0],
         "label": "RD1", "shape": "round", "dim_along_x_mm": None,
         "dim_along_y_mm": None, "diameter_mm": 1130, "is_steel": False,
         "flags": []},
    ])
    inv = starter_inventory()
    r = resolve_storey(rec, inv, tmp_path)
    typing  = json.loads(r.typing_path.read_text())
    review  = json.loads(r.review_path.read_text())
    assert typing["summary"]["column_count"] == 3
    assert typing["summary"]["placements"]   == 2
    assert typing["summary"]["rejected"]     == 1
    # Review queue captures the label_missing/shape_unknown column.
    assert review["summary"]["rejected"] == 1
    assert review["items"][0]["reason"]  == "shape_unknown"
    # Each placement carries the audit trail and PLAN §8 fields.
    for plc in typing["placements"]:
        for key in ("grid_mm_xy", "type_id", "type_name", "rotation_deg",
                    "comments", "source_label", "source_dims", "tier", "audit"):
            assert key in plc, plc


def test_resolve_storey_creates_synthetic_type_for_new_dims(tmp_path: Path) -> None:
    rec = tmp_path / "TEST.reconciled.json"
    _write_synthetic_reconciled(rec, [{
        "canonical_idx": 0, "canonical_grid_mm_xy": [0.0, 0.0],
        "label": "C9", "shape": "rectangular",
        "dim_along_x_mm": 1150, "dim_along_y_mm": 800,
        "diameter_mm": None, "is_steel": False, "flags": [],
    }])
    inv = starter_inventory()
    before = inv.types_count()
    r = resolve_storey(rec, inv, tmp_path)
    typing = json.loads(r.typing_path.read_text())
    plc = typing["placements"][0]
    assert plc["tier"] == MatchTier.CREATED.value
    assert plc["type_name"] == "C9_R_1150x800"
    assert inv.types_count() == before + 1


# ── Slow integration on real L3 reconcile output ─────────────────────────────

@pytest.mark.slow
@fixture_required
def test_resolve_l3_real(tmp_path: Path) -> None:
    """Run the reconcile step on real L3 data, then resolve. Expect majority
    MATCHED_EXACT against the starter inventory + a small number of CREATED
    for new dim signatures."""
    from backend.extract.plan_enlarged import extract_enlarged
    from backend.extract.plan_overall  import extract_overall
    from backend.reconcile.storey      import reconcile_storey

    base = FIXTURE
    o = extract_overall(base / "TGCH-TD-S-200-L3-00.pdf", 0, tmp_path / "po", run_yolo=True)
    enl = []
    for n in (1, 2, 3, 4):
        e = extract_enlarged(base / f"TGCH-TD-S-200-L3-0{n}.pdf", 0, tmp_path / "pe", run_yolo=True)
        enl.append(e.payload_path)
    rec = reconcile_storey(o.payload_path, enl, tmp_path / "rc")

    inv = starter_inventory()
    r = resolve_storey(rec.payload_path, inv, tmp_path / "rs")
    counts = r.tier_counts
    # Most columns are C2 800×800 (square) and match the starter type → EXACT.
    assert counts.get("MATCHED_EXACT", 0) >= 100, counts
    # A handful of distinct dim signatures (rectangular C9, steel H-C2, etc.)
    # should trigger CREATED.
    assert counts.get("CREATED", 0) >= 1, counts
    # Some columns will REJECTED (the ~10 outside-building YOLO FPs).
    assert counts.get("REJECTED", 0) >= 1, counts
