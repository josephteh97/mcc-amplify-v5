"""GLTF emitter (PLAN.md §9).

Produces ``output/<storey>.gltf`` from a Stage 5A typing payload + the
storey's vertical extents (gates G3/G4).

  - Rectangular / square columns → axis-aligned box with footprint
    ``dim_x_mm × dim_y_mm`` and height = storey_height_mm.
  - Round columns → cylinder of radius=diameter/2, same height.
  - Steel columns → treated as rectangular for v5.3 (PLAN §3A-2).

Coordinate units: glTF default is meters, so every mm value is divided
by 1000 at export time. The Y-axis on screen maps to the building's
vertical (RL); the building's plan-XY becomes the glTF X- and Z-axes.
This matches typical viewer expectations (Three.js, Google Model
Viewer).

Stage 5B emits visualisation-grade geometry. The actual ``.rvt`` file
is produced inside Revit by the pyRevit script that consumes
``<storey>_revit_manifest.json``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh    # type: ignore[import-untyped]
from loguru import logger


MM_TO_M = 0.001


@dataclass(frozen=True)
class GltfEmitResult:
    storey_id:    str
    gltf_path:    Path
    column_count: int
    skipped:      int
    bbox_m:       tuple[tuple[float, float, float], tuple[float, float, float]] | None


def _column_mesh(plc: dict, height_m: float) -> trimesh.Trimesh | None:
    """Build a single column mesh from a placement payload."""
    shape = plc.get("shape")
    src   = plc.get("source_dims") or {}
    if shape == "round":
        d_mm = src.get("d")
        if not d_mm:
            return None
        radius_m = (float(d_mm) / 2.0) * MM_TO_M
        mesh = trimesh.creation.cylinder(radius=radius_m, height=height_m, sections=24)
    else:
        dx_mm = src.get("x")
        dy_mm = src.get("y")
        if not dx_mm or not dy_mm:
            return None
        mesh = trimesh.creation.box(extents=(
            float(dx_mm) * MM_TO_M,
            float(dy_mm) * MM_TO_M,
            height_m,
        ))
    return mesh


def _place(mesh: trimesh.Trimesh, x_m: float, y_m: float, base_rl_m: float,
           top_rl_m: float) -> trimesh.Trimesh:
    """Translate the column mesh so its base sits at base_rl_m and its
    centroid is at (x_m, y_m). trimesh primitives are centred at the
    origin, so we translate by (x, y, mid_height)."""
    mid = (base_rl_m + top_rl_m) / 2.0
    mesh.apply_translation((x_m, y_m, mid))
    return mesh


def emit_storey_gltf(
    storey_id:        str,
    typing_payload:   dict,
    base_rl_mm:       int,
    top_rl_mm:        int,
    out_dir:          Path,
    include_slab:     bool  = True,
    slab_thickness_mm: float = 200.0,
) -> GltfEmitResult:
    """Build per-column meshes, wrap them in a Scene, export to gltf.

    Skipped columns (missing dims) are counted in the result; the
    overall report flags them, but they don't block the export.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    height_m  = max(1.0, (top_rl_mm - base_rl_mm)) * MM_TO_M
    base_m    = base_rl_mm * MM_TO_M
    top_m     = top_rl_mm  * MM_TO_M

    scene = trimesh.Scene()
    n_cols = 0
    skipped = 0
    for plc in typing_payload.get("placements", []):
        xy = plc.get("grid_mm_xy")
        if not xy:
            skipped += 1
            continue
        mesh = _column_mesh(plc, height_m)
        if mesh is None:
            skipped += 1
            continue
        mesh = _place(mesh, xy[0] * MM_TO_M, xy[1] * MM_TO_M, base_m, top_m)
        rot_deg = plc.get("rotation_deg") or 0
        if rot_deg:
            R = trimesh.transformations.rotation_matrix(
                angle=np.radians(rot_deg),
                direction=(0.0, 0.0, 1.0),
                point=(xy[0] * MM_TO_M, xy[1] * MM_TO_M, (base_m + top_m) / 2.0),
            )
            mesh.apply_transform(R)
        scene.add_geometry(mesh, node_name=plc.get("type_name") or f"col_{n_cols}")
        n_cols += 1

    if include_slab and n_cols > 0:
        # Plan-extent slab at the top of the storey.
        try:
            xs = [plc["grid_mm_xy"][0] for plc in typing_payload["placements"]
                  if plc.get("grid_mm_xy")]
            ys = [plc["grid_mm_xy"][1] for plc in typing_payload["placements"]
                  if plc.get("grid_mm_xy")]
            if xs and ys:
                margin_m = 1.0
                cx = (min(xs) + max(xs)) / 2.0 * MM_TO_M
                cy = (min(ys) + max(ys)) / 2.0 * MM_TO_M
                ex = (max(xs) - min(xs)) * MM_TO_M + 2 * margin_m
                ey = (max(ys) - min(ys)) * MM_TO_M + 2 * margin_m
                slab = trimesh.creation.box(extents=(ex, ey, slab_thickness_mm * MM_TO_M))
                slab.apply_translation((cx, cy, top_m + (slab_thickness_mm * MM_TO_M / 2.0)))
                scene.add_geometry(slab, node_name=f"slab_{storey_id}")
        except Exception as exc:                      # noqa: BLE001
            logger.warning(f"{storey_id}: slab plane skipped ({exc})")

    out_path = out_dir / f"{storey_id}.gltf"
    if n_cols == 0:
        # Empty scene — write a minimal stub so downstream consumers
        # don't need to special-case absence.
        empty_path = out_path.with_suffix(".gltf")
        empty_path.write_text(json.dumps({
            "asset": {"version": "2.0"},
            "scenes": [{"nodes": []}],
            "scene": 0,
        }))
        bbox = None
    else:
        # trimesh exports either .gltf+.bin or .glb. We want a single
        # human-inspectable .gltf with embedded buffer.
        scene.export(out_path, file_type="gltf")
        bb = scene.bounds
        bbox = (tuple(bb[0]), tuple(bb[1])) if bb is not None else None

    return GltfEmitResult(
        storey_id    = storey_id,
        gltf_path    = out_path,
        column_count = n_cols,
        skipped      = skipped,
        bbox_m       = bbox,
    )
