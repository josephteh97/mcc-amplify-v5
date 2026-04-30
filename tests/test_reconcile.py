"""Step 8 regression tests for Stage 4 — Reconcile (PLAN.md §7).

Coverage:
  - Per-storey: page-offset computation via shared axis labels
                (synthetic + L3 fixture)
  - Per-storey: cross-link matching, label conflict detection,
                missing-label flag
  - Per-project: elevation level merge with disagreement flag
  - Per-project: meta.yaml.levels override precedence
  - Slow integration: full L3 pipeline reconcile end-to-end
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.core.meta_yaml          import (
    AliasesMeta,
    LevelMeta,
    MetaYaml,
    ProjectMeta,
    SlabsMeta,
)
from backend.reconcile.project       import (
    _build_alias_resolver,
    _merge_elevation_levels,
    reconcile_project,
)
from backend.reconcile.storey        import (
    _compute_offsets,
    _label_to_mm,
    reconcile_storey,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE   = REPO_ROOT / "tests" / "fixtures" / "sample_uploaded_documents" / "FLOOR FRAMING PLANS"
ELEV_FIX  = REPO_ROOT / "tests" / "fixtures" / "sample_uploaded_documents" / "04 130 - BLDG ELEVATIONS"

fixture_required = pytest.mark.skipif(
    not FIXTURE.exists(),
    reason="reference fixture symlink missing — see PLAN.md §3.1",
)


# ── Page-offset computation ──────────────────────────────────────────────────

def test_label_to_mm_lookup() -> None:
    axes = [
        {"label": "1", "mm": 0.0},
        {"label": "2", "mm": 8400.0},
        {"label": "21", "mm": 168000.0},
    ]
    assert _label_to_mm(axes, "1")  == 0.0
    assert _label_to_mm(axes, "21") == 168000.0
    assert _label_to_mm(axes, "99") is None


def test_compute_offsets_zero_when_first_label_shared() -> None:
    overall_grid = {
        "x_axes": [{"label": "1", "mm": 0}, {"label": "2", "mm": 8400}],
        "y_axes": [{"label": "DD", "mm": 0}, {"label": "CC", "mm": 8400}],
    }
    enlarged_grid = {
        "x_axes": [{"label": "1", "mm": 0}, {"label": "2", "mm": 8400}],
        "y_axes": [{"label": "DD", "mm": 0}, {"label": "CC", "mm": 8400}],
    }
    x_off, y_off, notes = _compute_offsets(overall_grid, enlarged_grid)
    assert x_off == 0.0
    assert y_off == 0.0
    assert notes == []


def test_compute_offsets_upper_right_quadrant() -> None:
    """-02 starts at label '21' which is at mm 168000 in -00 (= 20 × 8400)."""
    overall_grid = {
        "x_axes": [{"label": str(i + 1), "mm": float(i * 8400)} for i in range(42)],
        "y_axes": [{"label": "DD", "mm": 0}, {"label": "CC", "mm": 8400}],
    }
    enlarged_grid = {
        "x_axes": [{"label": "21", "mm": 0}, {"label": "22", "mm": 8400}],
        "y_axes": [{"label": "DD", "mm": 0}, {"label": "CC", "mm": 8400}],
    }
    x_off, y_off, notes = _compute_offsets(overall_grid, enlarged_grid)
    assert x_off == 168000.0
    assert y_off == 0.0


def test_compute_offsets_no_shared_label() -> None:
    overall_grid = {
        "x_axes": [{"label": "1", "mm": 0}],
        "y_axes": [{"label": "A", "mm": 0}],
    }
    enlarged_grid = {
        "x_axes": [{"label": "99", "mm": 0}],
        "y_axes": [{"label": "ZZZ", "mm": 0}],
    }
    x_off, y_off, notes = _compute_offsets(overall_grid, enlarged_grid)
    assert x_off is None
    assert y_off is None
    assert "no_shared_x_label_with_overall" in notes
    assert "no_shared_y_label_with_overall" in notes


# ── Synthetic per-storey reconcile ───────────────────────────────────────────

def _write_synthetic_overall(path: Path, columns: list[dict]) -> None:
    payload = {
        "storey_id":   "TEST",
        "source_pdf":  "test_overall.pdf",
        "page_index":  0,
        "grid": {
            "x_axes": [{"label": "1", "mm": 0}, {"label": "2", "mm": 8400}],
            "y_axes": [{"label": "DD", "mm": 0}, {"label": "CC", "mm": 8400}],
        },
        "columns_canonical": columns,
    }
    path.write_text(json.dumps(payload))


def _write_synthetic_enlarged(path: Path, columns: list[dict],
                              first_x_label: str = "1",
                              first_y_label: str = "DD") -> None:
    payload = {
        "storey_id":     "TEST",
        "page_number":   1,
        "page_region":   "upper-left",
        "source_pdf":    path.name,
        "page_index":    0,
        "grid": {
            "x_axes": [{"label": first_x_label, "mm": 0},
                       {"label": "next",       "mm": 8400}],
            "y_axes": [{"label": first_y_label, "mm": 0},
                       {"label": "next",        "mm": 8400}],
        },
        "columns": columns,
    }
    path.write_text(json.dumps(payload))


def test_reconcile_storey_attaches_label(tmp_path: Path) -> None:
    """One canonical column, one enlarged column 30 mm away with label C2."""
    overall = tmp_path / "TEST.overall.json"
    enlarged = tmp_path / "TEST-01.enlarged.json"
    _write_synthetic_overall(overall, [{
        "centre_grid_mm": [10000.0, 20000.0],
        "bbox_grid_mm":   [9700.0, 19700.0, 10300.0, 20300.0],
        "confidence":     0.95,
    }])
    _write_synthetic_enlarged(enlarged, [{
        "label": "C2", "shape": "square",
        "dim_along_x_mm": 800, "dim_along_y_mm": 800,
        "diameter_mm": None, "is_steel": False,
        "grid_mm_xy": [10030.0, 20020.0],   # 36 mm away — well under 250 tol
        "yolo_confidence": 0.92,
    }])
    r = reconcile_storey(overall, [enlarged], tmp_path)
    assert len(r.columns) == 1
    c = r.columns[0]
    assert c.label  == "C2"
    assert c.shape  == "square"
    assert c.dim_along_x_mm == 800
    assert "label_missing" not in c.flags


def test_reconcile_storey_label_missing(tmp_path: Path) -> None:
    overall = tmp_path / "TEST.overall.json"
    enlarged = tmp_path / "TEST-01.enlarged.json"
    _write_synthetic_overall(overall, [{
        "centre_grid_mm": [10000.0, 20000.0],
        "confidence": 0.95,
    }])
    _write_synthetic_enlarged(enlarged, [])
    r = reconcile_storey(overall, [enlarged], tmp_path)
    assert r.columns[0].label is None
    assert "label_missing" in r.columns[0].flags


def test_reconcile_storey_label_conflict(tmp_path: Path) -> None:
    """Two enlarged candidates within tol carrying *distinct* labelled tuples."""
    overall   = tmp_path / "TEST.overall.json"
    enlarged1 = tmp_path / "TEST-01.enlarged.json"
    enlarged2 = tmp_path / "TEST-02.enlarged.json"
    _write_synthetic_overall(overall, [{
        "centre_grid_mm": [10000.0, 20000.0],
        "confidence": 0.95,
    }])
    _write_synthetic_enlarged(enlarged1, [{
        "label": "C2", "shape": "square",
        "dim_along_x_mm": 800, "dim_along_y_mm": 800,
        "diameter_mm": None, "is_steel": False,
        "grid_mm_xy": [10010.0, 20010.0],
        "yolo_confidence": 0.92,
    }])
    _write_synthetic_enlarged(enlarged2, [{
        "label": "C9", "shape": "rectangular",
        "dim_along_x_mm": 1000, "dim_along_y_mm": 1200,
        "diameter_mm": None, "is_steel": False,
        "grid_mm_xy": [10050.0, 20050.0],
        "yolo_confidence": 0.88,
    }])
    r = reconcile_storey(overall, [enlarged1, enlarged2], tmp_path)
    c = r.columns[0]
    assert any(f.startswith("label_conflict") for f in c.flags)
    # Both distinct tuples preserved.
    assert len(c.label_candidates) == 2


# ── Per-project elevation merge ───────────────────────────────────────────────

def _write_synthetic_elev(path: Path, levels: list[dict]) -> None:
    payload = {
        "source_pdf": path.name,
        "levels":     levels,
        "floor_to_floor_mm": [],
        "flags":      [],
    }
    path.write_text(json.dumps(payload))


def test_merge_elevation_levels_groups_and_flags(tmp_path: Path) -> None:
    e1 = tmp_path / "elev1.elev.json"
    e2 = tmp_path / "elev2.elev.json"
    _write_synthetic_elev(e1, [
        {"name": "BASEMENT 1", "rl_mm": 3500, "source_pdf": "elev1.pdf"},
        {"name": "1ST STOREY", "rl_mm": 9500, "source_pdf": "elev1.pdf"},
    ])
    _write_synthetic_elev(e2, [
        {"name": "BASEMENT 1", "rl_mm": 3520, "source_pdf": "elev2.pdf"},   # 20 mm — under tol
        {"name": "1ST STOREY", "rl_mm": 9700, "source_pdf": "elev2.pdf"},   # 200 mm — over tol
    ])
    levels, flags = _merge_elevation_levels([e1, e2])
    by = {l["name"]: l for l in levels}
    assert by["BASEMENT 1"]["rl_mm"] in (3510, 3500, 3520)   # median of {3500, 3520}
    # 1ST STOREY varies 9500 vs 9700 — beyond LEVEL_AGREEMENT_TOL_MM=25.
    assert any("1ST STOREY" in f and "disagreement" in f for f in flags)


# ── meta.yaml.aliases.levels — name normalisation ────────────────────────────

def test_alias_resolver_collapses_both_directions() -> None:
    meta = MetaYaml(
        project = ProjectMeta(id="X"),
        aliases = AliasesMeta(levels={
            "BASEMENT 1": "B1",
            "1ST STOREY": "L1",
        }),
    )
    resolve, fmap = _build_alias_resolver(meta)
    # Forward: arch full-name → structural code.
    assert resolve("BASEMENT 1") == "B1"
    assert resolve("basement 1") == "B1"     # case-insensitive lookup
    # Reverse: target side recognised as canonical (no-op resolution).
    assert resolve("B1") == "B1"
    # Unmapped names pass through unchanged.
    assert resolve("ROOF") == "ROOF"
    assert resolve("") == ""


def test_alias_collapses_arch_and_structural_levels(tmp_path: Path) -> None:
    """Architectural BASEMENT 1 and structural B1 both at RL 3500 must
    collapse into a single B1 entry, not show up as two duplicate levels."""
    e1 = tmp_path / "arch.elev.json"
    e2 = tmp_path / "struct.elev.json"
    _write_synthetic_elev(e1, [
        {"name": "BASEMENT 1", "rl_mm": 3500, "source_pdf": "arch.pdf"},
        {"name": "1ST STOREY", "rl_mm": 9500, "source_pdf": "arch.pdf"},
    ])
    _write_synthetic_elev(e2, [
        {"name": "B1", "rl_mm": 3500, "source_pdf": "struct.pdf"},
        {"name": "L1", "rl_mm": 9500, "source_pdf": "struct.pdf"},
    ])
    meta = MetaYaml(
        project = ProjectMeta(id="X"),
        aliases = AliasesMeta(levels={
            "BASEMENT 1": "B1",
            "1ST STOREY": "L1",
        }),
    )
    levels, flags = _merge_elevation_levels([e1, e2], meta=meta)
    by_name = {l["name"]: l for l in levels}
    # Two unique levels, not four.
    assert set(by_name.keys()) == {"B1", "L1"}
    assert by_name["B1"]["rl_mm"] == 3500
    assert by_name["L1"]["rl_mm"] == 9500
    # Both PDFs contributed → n_pdfs == 2.
    assert by_name["B1"]["n_pdfs"] == 2
    # Provenance: which raw spellings collapsed in.
    assert "BASEMENT 1" in by_name["B1"]["aliased_from"]
    # alias_normalisation flag emitted.
    assert any(f.startswith("alias_normalisation_applied:") for f in flags)


def test_alias_does_not_affect_unmapped_names(tmp_path: Path) -> None:
    e1 = tmp_path / "e.elev.json"
    _write_synthetic_elev(e1, [
        {"name": "ROOF", "rl_mm": 30000, "source_pdf": "e.pdf"},
    ])
    meta = MetaYaml(
        project = ProjectMeta(id="X"),
        aliases = AliasesMeta(levels={"BASEMENT 1": "B1"}),
    )
    levels, _ = _merge_elevation_levels([e1], meta=meta)
    assert levels[0]["name"] == "ROOF"
    assert "aliased_from" not in levels[0]   # no alias applied


def test_meta_override_resolves_through_alias(tmp_path: Path) -> None:
    """Override declared as 'BASEMENT 1' must hit the merged 'B1' entry."""
    e1 = tmp_path / "e.elev.json"
    _write_synthetic_elev(e1, [
        {"name": "B1", "rl_mm": 3500, "source_pdf": "e.pdf"},
    ])
    meta = MetaYaml(
        project = ProjectMeta(id="X"),
        levels  = {"BASEMENT 1": LevelMeta(rl_mm=3700.0, source="manual")},
        aliases = AliasesMeta(levels={"BASEMENT 1": "B1"}),
    )
    r = reconcile_project([e1], [], tmp_path, meta=meta)
    by_name = {l["name"]: l for l in r.levels}
    assert "B1" in by_name
    assert by_name["B1"]["rl_mm"] == 3700        # override wins
    assert by_name["B1"]["source"] == "meta.yaml"


def test_meta_levels_override(tmp_path: Path) -> None:
    elev = tmp_path / "e.elev.json"
    _write_synthetic_elev(elev, [
        {"name": "BASEMENT 1", "rl_mm": 3500, "source_pdf": "e.pdf"},
    ])
    meta = MetaYaml(
        project=ProjectMeta(id="X", name="X"),
        levels={"BASEMENT 1": LevelMeta(rl_mm=4000.0, source="manual")},
    )
    r = reconcile_project([elev], [], tmp_path, meta=meta)
    by = {l["name"]: l for l in r.levels}
    assert by["BASEMENT 1"]["rl_mm"] == 4000
    assert by["BASEMENT 1"]["source"] == "meta.yaml"


def test_project_slabs_default_thickness_from_meta(tmp_path: Path) -> None:
    sect = tmp_path / "s.section.json"
    sect.write_text(json.dumps({
        "source_pdf": "s.pdf", "section_ids": ["A", "B"], "sections": [], "flags": [],
    }))
    meta = MetaYaml(
        project=ProjectMeta(id="X", name="X"),
        slabs=SlabsMeta(default_thickness_mm=180.0),
    )
    r = reconcile_project([], [sect], tmp_path, meta=meta)
    assert r.slabs["default_thickness_mm"] == 180.0
    assert r.slabs["all_slabs_use_fallback"] is True
    assert sorted(r.slabs["section_ids"]) == ["A", "B"]


# ── Slow integration on the full L3 storey ────────────────────────────────────

@pytest.mark.slow
@fixture_required
def test_reconcile_storey_l3_real(tmp_path: Path) -> None:
    """Full L3 pipeline: -00 + -01..04 → reconcile. Expect ≥85% labelled
    after the neighbour-inference post-pass (real run hits 91%)."""
    from backend.extract.plan_enlarged import extract_enlarged
    from backend.extract.plan_overall  import extract_overall

    base = FIXTURE
    overall_dir = tmp_path / "po"
    enl_dir     = tmp_path / "pe"
    rec_dir     = tmp_path / "rc"

    o = extract_overall(base / "TGCH-TD-S-200-L3-00.pdf", 0, overall_dir, run_yolo=True)
    enl = []
    for n in (1, 2, 3, 4):
        e = extract_enlarged(base / f"TGCH-TD-S-200-L3-0{n}.pdf", 0, enl_dir, run_yolo=True)
        enl.append(e.payload_path)

    r = reconcile_storey(o.payload_path, enl, rec_dir)
    payload = json.loads(r.payload_path.read_text())
    s = payload["summary"]
    labelled_pct = s["labelled"] / max(s["canonical_total"], 1)
    assert labelled_pct >= 0.85, (
        f"L3 reconcile labelled rate {labelled_pct:.1%} below 85% — "
        f"summary={s}"
    )
    # All four enlarged pages should produce a non-None offset.
    for po in payload["page_offsets"]:
        assert po["x_offset_mm"] is not None
        assert po["y_offset_mm"] is not None


@pytest.mark.parametrize("label, shape, dim_x, dim_y, dia", [
    ("C2",   "square",      800,  800,  None),
    ("C9",   "rectangular", 1150, 800,  None),    # asymmetric — proves no axis swap mishandling
    ("RD1",  "round",       None, None, 1130),    # round — propagates diameter
    ("H-C2", "steel",       600,  600,  None),    # steel — propagates is_steel
])
def test_reconcile_neighbour_inference(
    tmp_path: Path,
    label: str, shape: str,
    dim_x: int | None, dim_y: int | None, dia: int | None,
) -> None:
    """An unlabelled column surrounded by 4 identical labelled neighbours —
    post-pass infers whatever those neighbours carry, regardless of value.
    Parametrised so we don't accidentally encode 'C2 800×800' as a magic
    truth: the inference logic just propagates the donor tuple."""
    overall  = tmp_path / "TEST.overall.json"
    enlarged = tmp_path / "TEST-01.enlarged.json"
    centre_xy: tuple[float, float] = (10000.0, 20000.0)
    bay_mm = 8400.0
    neighbours_xy: list[tuple[float, float]] = [
        (centre_xy[0] + bay_mm, centre_xy[1]),
        (centre_xy[0] - bay_mm, centre_xy[1]),
        (centre_xy[0], centre_xy[1] + bay_mm),
        (centre_xy[0], centre_xy[1] - bay_mm),
    ]
    _write_synthetic_overall(overall, [
        {"centre_grid_mm": list(centre_xy), "confidence": 0.9},
        *[{"centre_grid_mm": list(n),       "confidence": 0.9} for n in neighbours_xy],
    ])
    is_steel = label.startswith("H-")
    _write_synthetic_enlarged(enlarged, [
        {
            "label": label, "shape": shape,
            "dim_along_x_mm": dim_x, "dim_along_y_mm": dim_y,
            "diameter_mm": dia, "is_steel": is_steel,
            "grid_mm_xy": list(n), "yolo_confidence": 0.92,
        }
        for n in neighbours_xy
    ])
    r = reconcile_storey(overall, [enlarged], tmp_path)
    centre = next(c for c in r.columns if c.canonical_idx == 0)

    assert centre.label          == label
    assert centre.shape          == shape
    assert centre.dim_along_x_mm == dim_x
    assert centre.dim_along_y_mm == dim_y
    assert centre.diameter_mm    == dia
    assert centre.is_steel       == is_steel
    assert any(f.startswith("label_inferred_from_neighbour") for f in centre.flags)
    assert "label_missing" not in centre.flags
