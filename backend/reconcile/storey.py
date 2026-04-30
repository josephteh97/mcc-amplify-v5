"""Per-storey reconciler (PLAN.md §7).

For each storey, cross-link the canonical columns from
``<storey>.overall.json`` with the labelled detections from every
``<storey>-NN.enlarged.json`` covering that storey's quadrants. Steps 4
and 5 currently emit column positions in *page-local* grid-mm; this is
where they get translated into a single global frame anchored at -00's
first axis label.

Algorithm (PLAN §7):

  1. Translate every enlarged-page column into the global -00 mm frame.
     The shared axis labels (e.g. "1", "C") are the canonical anchor —
     for each enlarged page, look up the mm of its first axis label in
     the -00 grid; the difference is the page's offset. Try labels in
     order until a shared one is found (handles edge cases where the
     enlarged page's first label happens to be missing from the -00
     extraction's label set).
  2. For each canonical -00 column, gather enlarged candidates within
     DEDUPE_TOL_MM = 50 grid-mm.
  3. If any candidate has a label, attach the (label, shape, dims) tuple
     from the closest one. If multiple candidates carry *distinct*
     labelled tuples (e.g. a column on the boundary of -01 and -02 with
     conflicting reads), strict-mode keeps all distinct tuples and
     flags ``label_conflict`` (PLAN §7, §11).
  4. Unmatched canonical columns emit ``label_missing`` for the review
     queue.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from backend.core.grid_mm import DEDUPE_TOL_MM


# Neighbour inference (PLAN §11 strict-mode-with-provenance):
# For canonical columns that picked up no labelled -01..04 candidate, look
# at the labelled columns within ~1.5× bay spacing and adopt their type
# tuple if they agree. Real structural plans repeat the same column type
# across a grid (TGCH L3 is dominated by C2 800x800), so this recovers
# most of the boundary / YOLO-missed cases. Inference is *separately
# flagged* — never silently overwritten — so the review queue sees the
# provenance.
NEIGHBOUR_INFER_RADIUS_MM:   float = 12600.0   # 1.5 × 8400 mm bay
NEIGHBOUR_INFER_MIN_AGREE:   int   = 3         # at least N labelled neighbours
                                               # must agree on the same tuple
NEIGHBOUR_INFER_MIN_FRAC:    float = 0.75      # of those neighbours, ≥ this
                                               # fraction must share the tuple


@dataclass(frozen=True)
class ReconciledColumn:
    canonical_idx:           int
    canonical_grid_mm_xy:    tuple[float, float]
    canonical_bbox_grid_mm:  list[float] | None
    canonical_confidence:    float

    label:           str | None
    is_steel:        bool
    shape:           str
    dim_along_x_mm:  int | None
    dim_along_y_mm:  int | None
    diameter_mm:     int | None

    n_enlarged_candidates: int
    label_candidates:      list[dict]    # all distinct labelled tuples seen
    flags:                 list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "canonical_idx":           self.canonical_idx,
            "canonical_grid_mm_xy":    list(self.canonical_grid_mm_xy),
            "canonical_bbox_grid_mm":  self.canonical_bbox_grid_mm,
            "canonical_confidence":    self.canonical_confidence,
            "label":                   self.label,
            "is_steel":                self.is_steel,
            "shape":                   self.shape,
            "dim_along_x_mm":          self.dim_along_x_mm,
            "dim_along_y_mm":          self.dim_along_y_mm,
            "diameter_mm":             self.diameter_mm,
            "n_enlarged_candidates":   self.n_enlarged_candidates,
            "label_candidates":        self.label_candidates,
            "flags":                   self.flags,
        }


@dataclass
class StoreyReconcileResult:
    storey_id:           str
    overall_path:        Path
    enlarged_paths:      list[Path]
    columns:             list[ReconciledColumn]
    page_offsets:        list[dict]
    payload_path:        Path | None
    flags:               list[str] = field(default_factory=list)


def _label_to_mm(axes: list[dict], label: str) -> float | None:
    """Look up an axis label's mm value in a grid axis list (case-insensitive)."""
    target = label.upper()
    for ax in axes:
        if str(ax["label"]).upper() == target:
            return float(ax["mm"])
    return None


def _compute_offsets(
    overall_grid:  dict,
    enlarged_grid: dict,
) -> tuple[float | None, float | None, list[str]]:
    """Find offsets that map enlarged-mm → global -00 mm.

    Returns (x_offset, y_offset, notes). Either offset is None if no
    shared axis label exists between overall and enlarged; the caller
    skips that page and flags it.
    """
    notes: list[str] = []
    x_off: float | None = None
    y_off: float | None = None
    for ax in enlarged_grid["x_axes"]:
        gm = _label_to_mm(overall_grid["x_axes"], str(ax["label"]))
        if gm is not None:
            x_off = gm - float(ax["mm"])
            break
    for ax in enlarged_grid["y_axes"]:
        gm = _label_to_mm(overall_grid["y_axes"], str(ax["label"]))
        if gm is not None:
            y_off = gm - float(ax["mm"])
            break
    if x_off is None:
        notes.append("no_shared_x_label_with_overall")
    if y_off is None:
        notes.append("no_shared_y_label_with_overall")
    return x_off, y_off, notes


def _euclid(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _label_tuple(col: dict) -> tuple[Any, ...]:
    """Hashable key identifying a labelled detection's (label, shape, dims)."""
    return (
        col.get("label"),
        col.get("shape"),
        col.get("dim_along_x_mm"),
        col.get("dim_along_y_mm"),
        col.get("diameter_mm"),
        bool(col.get("is_steel")),
    )


def _infer_from_neighbours(
    columns: list["ReconciledColumn"],
    radius_mm: float = NEIGHBOUR_INFER_RADIUS_MM,
    min_agree: int   = NEIGHBOUR_INFER_MIN_AGREE,
    min_frac:  float = NEIGHBOUR_INFER_MIN_FRAC,
) -> list["ReconciledColumn"]:
    """Fill in `label_missing` columns from agreeing labelled neighbours.

    For each missing column, gather labelled neighbours within ``radius_mm``;
    if at least ``min_agree`` of them share the same labelled-tuple AND
    they make up ≥ ``min_frac`` of the neighbour pool, adopt that tuple
    and tag the column with ``label_inferred_from_neighbour``. Otherwise
    leave the column as label_missing.
    """
    from collections import Counter

    labelled_pool = [(c.canonical_grid_mm_xy, c) for c in columns if c.label]
    if not labelled_pool:
        return columns

    out: list["ReconciledColumn"] = []
    for c in columns:
        if c.label is not None or "label_missing" not in c.flags:
            out.append(c)
            continue
        nbrs: list["ReconciledColumn"] = []
        for cxy, lc in labelled_pool:
            if _euclid(c.canonical_grid_mm_xy, cxy) <= radius_mm:
                nbrs.append(lc)
        if len(nbrs) < min_agree:
            out.append(c)
            continue
        tuples = Counter(_label_tuple_from_reconciled(n) for n in nbrs)
        best_tuple, best_n = tuples.most_common(1)[0]
        if best_n < min_agree or best_n / len(nbrs) < min_frac:
            out.append(c)
            continue
        # Reach into the original ReconciledColumn for one example matching
        # this tuple — we adopt its (label, shape, dims, is_steel).
        donor = next(n for n in nbrs if _label_tuple_from_reconciled(n) == best_tuple)
        new_flags = [f for f in c.flags if f != "label_missing"]
        new_flags.append(
            f"label_inferred_from_neighbour:n={best_n}/{len(nbrs)}"
        )
        out.append(ReconciledColumn(
            canonical_idx          = c.canonical_idx,
            canonical_grid_mm_xy   = c.canonical_grid_mm_xy,
            canonical_bbox_grid_mm = c.canonical_bbox_grid_mm,
            canonical_confidence   = c.canonical_confidence,
            label                  = donor.label,
            is_steel               = donor.is_steel,
            shape                  = donor.shape,
            dim_along_x_mm         = donor.dim_along_x_mm,
            dim_along_y_mm         = donor.dim_along_y_mm,
            diameter_mm            = donor.diameter_mm,
            n_enlarged_candidates  = c.n_enlarged_candidates,
            label_candidates       = c.label_candidates,    # still empty — provenance is "inferred"
            flags                  = new_flags,
        ))
    return out


def _label_tuple_from_reconciled(c: "ReconciledColumn") -> tuple:
    return (c.label, c.shape, c.dim_along_x_mm, c.dim_along_y_mm,
            c.diameter_mm, bool(c.is_steel))


def _candidate_summary(global_xy: tuple[float, float], col: dict, source_pdf: str,
                       page_id: int, page_region: str, distance_mm: float) -> dict:
    return {
        "label":           col.get("label"),
        "shape":           col.get("shape"),
        "dim_along_x_mm":  col.get("dim_along_x_mm"),
        "dim_along_y_mm":  col.get("dim_along_y_mm"),
        "diameter_mm":     col.get("diameter_mm"),
        "is_steel":        bool(col.get("is_steel")),
        "global_grid_mm":  list(global_xy),
        "distance_mm":     round(distance_mm, 2),
        "source_pdf":      source_pdf,
        "page_id":         page_id,
        "page_region":     page_region,
        "yolo_confidence": col.get("yolo_confidence"),
    }


def reconcile_storey(
    overall_path:    Path,
    enlarged_paths:  list[Path],
    out_dir:         Path,
    dedupe_tol_mm:   float = DEDUPE_TOL_MM,
) -> StoreyReconcileResult:
    """Cross-link one storey. Always writes a payload — flags surface failures."""
    out_dir.mkdir(parents=True, exist_ok=True)
    overall = json.loads(overall_path.read_text())
    storey_id = overall["storey_id"]
    overall_grid = overall["grid"]
    canonical = overall.get("columns_canonical", [])
    flags: list[str] = []

    # --- 1. Translate every enlarged column into the global mm frame ---
    all_enlarged: list[dict] = []
    page_offsets: list[dict] = []
    for ep in enlarged_paths:
        d = json.loads(ep.read_text())
        x_off, y_off, notes = _compute_offsets(overall_grid, d["grid"])
        page_offsets.append({
            "source_pdf":  d.get("source_pdf"),
            "page_id":     d.get("page_number"),
            "page_region": d.get("page_region"),
            "x_offset_mm": x_off,
            "y_offset_mm": y_off,
            "n_columns":   len(d.get("columns", [])),
            "notes":       notes,
        })
        if x_off is None or y_off is None:
            flags.append(f"page_offset_unresolved: {ep.name}")
            continue
        for col in d.get("columns", []):
            xy = col.get("grid_mm_xy")
            if not xy:
                continue
            global_xy = (xy[0] + x_off, xy[1] + y_off)
            all_enlarged.append({
                "col":         col,
                "source_pdf":  d.get("source_pdf", ep.name),
                "page_id":     d.get("page_number", 0),
                "page_region": d.get("page_region", "unknown"),
                "global_xy":   global_xy,
            })

    # --- 2 & 3. Match canonical to enlarged candidates, attach labels ---
    reconciled: list[ReconciledColumn] = []
    for ci, c in enumerate(canonical):
        cxy_raw = c.get("centre_grid_mm")
        if cxy_raw is None:
            continue
        cxy = (float(cxy_raw[0]), float(cxy_raw[1]))

        cands: list[tuple[float, dict]] = []
        for ec in all_enlarged:
            d = _euclid(cxy, ec["global_xy"])
            if d <= dedupe_tol_mm:
                cands.append((d, ec))
        cands.sort(key=lambda p: p[0])

        labelled = [(d, ec) for d, ec in cands if ec["col"].get("label")]

        if not labelled:
            reconciled.append(ReconciledColumn(
                canonical_idx          = ci,
                canonical_grid_mm_xy   = cxy,
                canonical_bbox_grid_mm = c.get("bbox_grid_mm"),
                canonical_confidence   = float(c.get("confidence", 0.0)),
                label                  = None,
                is_steel               = False,
                shape                  = "unknown",
                dim_along_x_mm         = None,
                dim_along_y_mm         = None,
                diameter_mm            = None,
                n_enlarged_candidates  = len(cands),
                label_candidates       = [],
                flags                  = ["label_missing"] if not cands
                                         else ["candidates_present_but_unlabelled"],
            ))
            continue

        # Group labelled candidates by their distinct (label, shape, dims) tuple.
        seen: dict[tuple, dict] = {}
        for d, ec in labelled:
            key = _label_tuple(ec["col"])
            if key not in seen:
                seen[key] = _candidate_summary(
                    global_xy   = ec["global_xy"],
                    col         = ec["col"],
                    source_pdf  = ec["source_pdf"],
                    page_id     = ec["page_id"],
                    page_region = ec["page_region"],
                    distance_mm = d,
                )
        distinct_tuples = list(seen.values())

        col_flags: list[str] = []
        if len(distinct_tuples) > 1:
            col_flags.append(f"label_conflict:{len(distinct_tuples)}_distinct_tuples")

        # Promote the closest labelled candidate as the primary tuple.
        primary = labelled[0][1]["col"]
        reconciled.append(ReconciledColumn(
            canonical_idx          = ci,
            canonical_grid_mm_xy   = cxy,
            canonical_bbox_grid_mm = c.get("bbox_grid_mm"),
            canonical_confidence   = float(c.get("confidence", 0.0)),
            label                  = primary.get("label"),
            is_steel               = bool(primary.get("is_steel")),
            shape                  = primary.get("shape", "unknown"),
            dim_along_x_mm         = primary.get("dim_along_x_mm"),
            dim_along_y_mm         = primary.get("dim_along_y_mm"),
            diameter_mm            = primary.get("diameter_mm"),
            n_enlarged_candidates  = len(cands),
            label_candidates       = distinct_tuples,
            flags                  = col_flags,
        ))

    # --- 3b. Neighbour inference for label_missing columns ---
    reconciled = _infer_from_neighbours(reconciled)

    # --- 4. Write payload ---
    payload = {
        "storey_id":     storey_id,
        "overall_pdf":   overall.get("source_pdf"),
        "enlarged_pdfs": [pp["source_pdf"] for pp in page_offsets],
        "page_offsets":  page_offsets,
        "summary": {
            "canonical_total":     len(canonical),
            "reconciled_columns":  len(reconciled),
            "labelled":            sum(1 for r in reconciled if r.label),
            "label_missing":       sum(1 for r in reconciled if "label_missing" in r.flags),
            "label_inferred":      sum(1 for r in reconciled if any(
                f.startswith("label_inferred_from_neighbour") for f in r.flags
            )),
            "label_conflicts":     sum(1 for r in reconciled if any(
                f.startswith("label_conflict") for f in r.flags
            )),
        },
        "columns": [r.to_dict() for r in reconciled],
        "flags":   flags,
    }
    payload_path = out_dir / f"{storey_id}.reconciled.json"
    with open(payload_path, "w") as f:
        json.dump(payload, f, indent=2)

    logger.info(
        f"  {storey_id}: canonical={payload['summary']['canonical_total']} "
        f"labelled={payload['summary']['labelled']} "
        f"inferred={payload['summary']['label_inferred']} "
        f"missing={payload['summary']['label_missing']} "
        f"conflicts={payload['summary']['label_conflicts']}"
    )

    return StoreyReconcileResult(
        storey_id       = storey_id,
        overall_path    = overall_path,
        enlarged_paths  = enlarged_paths,
        columns         = reconciled,
        page_offsets    = page_offsets,
        payload_path    = payload_path,
        flags           = flags,
    )
