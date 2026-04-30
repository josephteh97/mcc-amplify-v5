"""Per-project reconciler (PLAN.md §7).

Combines elevation level sets and section section_ids into a project-wide
model. Override precedence (PLAN §7): manual ``meta.yaml`` > extracted
from drawings > fail.

Levels:
  - Collect every (name, rl_mm) emitted by Stage 3B across all elevation
    PDFs. Group by canonical name (uppercase), take median RL across the
    group. Cross-PDF spread > LEVEL_AGREEMENT_TOL_MM flags
    ``cross_pdf_level_disagreement`` for the review queue.
  - meta.yaml.levels (if provided) override extracted values entirely
    for any level whose name appears in both — the ``source`` field on
    each emitted level records the provenance.

Slabs (deferred per PROBE §3C):
  - Extracted joints are empty for v5.3. The slab source map is built
    from section_ids × storeys, every entry tagged
    ``meta.yaml.fallback`` and pointing at
    ``meta.yaml.slabs.default_thickness_mm``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

from loguru import logger

from backend.core.grid_mm   import LEVEL_AGREEMENT_TOL_MM
from backend.core.meta_yaml import MetaYaml


@dataclass
class ProjectReconcileResult:
    levels:        list[dict]
    slabs:         dict
    payload_path:  Path | None
    flags:         list[str] = field(default_factory=list)


def _merge_elevation_levels(
    elevation_paths: list[Path],
    tol_mm:          float = LEVEL_AGREEMENT_TOL_MM,
) -> tuple[list[dict], list[str]]:
    """Group every elevation-extracted level by canonical name, take the
    median RL, flag cross-PDF disagreement beyond ``tol_mm``."""
    by_name: dict[str, list[tuple[int, str]]] = {}     # name → [(rl_mm, source_pdf)]
    for ep in elevation_paths:
        d = json.loads(ep.read_text())
        for lvl in d.get("levels", []):
            name = str(lvl["name"]).upper()
            by_name.setdefault(name, []).append(
                (int(lvl["rl_mm"]), lvl.get("source_pdf", ep.name)),
            )

    out: list[dict] = []
    flags: list[str] = []
    for name, hits in by_name.items():
        rls = [rl for rl, _ in hits]
        med = int(round(median(rls)))
        spread = max(rls) - min(rls)
        if spread > tol_mm:
            flags.append(
                f"cross_pdf_level_disagreement: {name!r} spans {spread} mm "
                f"across {len({src for _, src in hits})} PDF(s) (>{int(tol_mm)} mm tol)"
            )
        out.append({
            "name":         name,
            "rl_mm":        med,
            "rl_spread_mm": int(spread),
            "n_pdfs":       len({src for _, src in hits}),
            "source":       "extracted",
        })
    out.sort(key=lambda r: r["rl_mm"])
    return out, flags


def _apply_meta_level_overrides(
    extracted_levels: list[dict],
    meta:             MetaYaml | None,
) -> tuple[list[dict], list[str]]:
    """Apply meta.yaml.levels overrides on top of extracted levels.

    Override precedence (PLAN §7): a level present in both sources wins
    via meta.yaml; meta-only levels are added; extracted-only levels
    pass through unchanged.
    """
    if meta is None or not meta.levels:
        return extracted_levels, []

    by_name = {l["name"].upper(): l for l in extracted_levels}
    flags:   list[str] = []
    for raw_name, lm in meta.levels.items():
        name = raw_name.upper()
        if name in by_name:
            old_rl = by_name[name]["rl_mm"]
            if abs(old_rl - lm.rl_mm) > LEVEL_AGREEMENT_TOL_MM:
                flags.append(
                    f"meta_override_diverges: {name!r} extracted={old_rl} "
                    f"meta={int(lm.rl_mm)} (>{int(LEVEL_AGREEMENT_TOL_MM)} mm)"
                )
            by_name[name].update({
                "rl_mm":  int(lm.rl_mm),
                "source": "meta.yaml",
            })
        else:
            by_name[name] = {
                "name":         name,
                "rl_mm":        int(lm.rl_mm),
                "rl_spread_mm": 0,
                "n_pdfs":       0,
                "source":       "meta.yaml",
            }
    out = sorted(by_name.values(), key=lambda r: r["rl_mm"])
    return out, flags


def _build_slab_map(
    section_paths: list[Path],
    meta:          MetaYaml | None,
) -> dict:
    """Collect section_ids and tag every slab as meta.yaml fallback (PLAN §17)."""
    section_ids: list[str] = []
    section_pdfs: list[str] = []
    for sp in section_paths:
        d = json.loads(sp.read_text())
        section_ids.extend(d.get("section_ids", []))
        section_pdfs.append(d.get("source_pdf", sp.name))
    default_thk = float(meta.slabs.default_thickness_mm) if meta else 200.0
    zones = (
        {name: {"thickness_mm": float(z.thickness_mm), "source": "meta.yaml"}
         for name, z in meta.slabs.zones.items()}
        if meta else {}
    )
    return {
        "section_ids":              sorted(set(section_ids)),
        "section_pdfs":             section_pdfs,
        "default_thickness_mm":     default_thk,
        "default_source":           "meta.yaml",
        "zones":                    zones,
        "all_slabs_use_fallback":   True,                # PLAN §17 v5.3 behaviour
    }


def reconcile_project(
    elevation_paths: list[Path],
    section_paths:   list[Path],
    out_dir:         Path,
    meta:            MetaYaml | None = None,
) -> ProjectReconcileResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    flags: list[str] = []

    extracted_levels, level_flags = _merge_elevation_levels(elevation_paths)
    flags.extend(level_flags)
    levels, override_flags = _apply_meta_level_overrides(extracted_levels, meta)
    flags.extend(override_flags)

    floor_to_floor_mm = [
        levels[i + 1]["rl_mm"] - levels[i]["rl_mm"]
        for i in range(len(levels) - 1)
    ]

    slabs = _build_slab_map(section_paths, meta)

    payload = {
        "levels":             levels,
        "floor_to_floor_mm":  floor_to_floor_mm,
        "slabs":              slabs,
        "summary": {
            "level_count":       len(levels),
            "elevation_pdfs":    len(elevation_paths),
            "section_pdfs":      len(section_paths),
            "section_id_count":  len(slabs["section_ids"]),
        },
        "flags": flags,
    }
    payload_path = out_dir / "_project.json"
    with open(payload_path, "w") as f:
        json.dump(payload, f, indent=2)

    logger.info(
        f"  project: levels={len(levels)} "
        f"section_ids={len(slabs['section_ids'])} flags={len(flags)}"
    )

    return ProjectReconcileResult(
        levels       = levels,
        slabs        = slabs,
        payload_path = payload_path,
        flags        = flags,
    )
