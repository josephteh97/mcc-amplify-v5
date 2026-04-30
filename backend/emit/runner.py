"""Per-storey Stage 5B runner — gates → GLTF → transaction.json → optional RVT build.

Sequence per storey:

  1. ``validate_storey_gates`` (PLAN §9). Hard failures abort emission.
  2. ``emit_storey_gltf`` — always runs (browser-side preview).
  3. ``emit_revit_transaction`` — always runs; recipe sits on disk
     for the Windows-side add-in to consume now or later.
  4. ``RevitClient.build`` — *optional*. Skipped if no client is
     supplied OR the server is unhealthy. Failure is logged and
     surfaced in the result, never raised.

Strict-mode (PLAN §11) applies upstream — the Windows hand-off is
treated as a deployment integration, not a hard gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from backend.emit.gates              import StoreyGates, validate_storey_gates
from backend.emit.gltf               import GltfEmitResult, emit_storey_gltf
from backend.emit.revit_client       import RevitClient, RvtBuildResult
from backend.emit.revit_transaction  import (
    TransactionEmitResult,
    emit_revit_transaction,
)


@dataclass
class EmitResult:
    storey_id:       str
    gates:           StoreyGates
    gltf:            GltfEmitResult         | None = None
    transaction:     TransactionEmitResult  | None = None
    rvt_build:       RvtBuildResult         | None = None
    skipped_reason:  str | None                    = None
    flags:           list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        """All HARD gates passed and the local artefacts emitted.

        RVT build success is *not* part of the success predicate — the
        Windows add-in is an external integration that may legitimately
        be unreachable; the recipe + GLTF on disk is enough to call
        Stage 5B done.
        """
        return (
            self.gates.all_passed
            and self.gltf is not None
            and self.transaction is not None
        )


def _resolve_level_names(
    storey_id:      str,
    base_rl_mm:     int,
    top_rl_mm:      int,
    project_levels: list[dict],
) -> tuple[str, str]:
    """Find the detected level names matching this storey's base + top RLs.

    Falls back to the storey_id when the project levels list doesn't
    contain a matching name (e.g. structural ``L3`` not aliased to
    architectural ``3RD STOREY``); a synthetic ``<storey>_TOP`` is used
    as the upper anchor in that case so per-column refs stay consistent
    with the recipe's ``levels`` array.
    """
    by_name = {l["name"].upper(): l for l in project_levels}
    if storey_id.upper() in by_name:
        base_name = by_name[storey_id.upper()]["name"]
    else:
        # Match by RL — useful when the storey id is structural and the
        # detected level names are architectural with the same elevation.
        match = next(
            (l for l in project_levels if int(l["rl_mm"]) == int(base_rl_mm)),
            None,
        )
        base_name = match["name"] if match else storey_id

    above = sorted(
        [l for l in project_levels if int(l["rl_mm"]) > int(base_rl_mm)],
        key=lambda l: int(l["rl_mm"]),
    )
    if above and int(above[0]["rl_mm"]) == int(top_rl_mm):
        top_name = above[0]["name"]
    elif above:
        top_name = above[0]["name"]
    else:
        top_name = f"{base_name}_TOP"
    return base_name, top_name


def emit_storey(
    storey_id:           str,
    overall_payload:     dict | None,
    reconciled_payload:  dict | None,
    typing_payload:      dict | None,
    project_levels:      list[dict],
    slab_default_mm:     float | None,
    slab_zones:          dict,
    inventory_payload:   dict,
    out_dir:             Path,
    revit_client:        RevitClient | None = None,
    pdf_filename:        str = "",
) -> EmitResult:
    """Run gates and (if all pass) emit gltf + transaction.json [+ optional RVT]."""
    inventory_shapes = {f.get("shape") for f in inventory_payload.get("families", [])
                        if f.get("shape")}
    gates = validate_storey_gates(
        storey_id          = storey_id,
        overall_payload    = overall_payload,
        reconciled_payload = reconciled_payload,
        typing_payload     = typing_payload,
        project_levels     = project_levels,
        inventory_shapes   = inventory_shapes,
        slab_default_mm    = slab_default_mm,
    )

    if not gates.all_passed:
        msg = f"{storey_id}: hard gates failed → {[g.name for g in gates.hard_failures]}"
        logger.warning(msg)
        return EmitResult(storey_id=storey_id, gates=gates, skipped_reason=msg)

    assert gates.base_rl_mm is not None and gates.top_rl_mm is not None
    assert gates.slab_thickness_mm is not None
    assert typing_payload is not None
    base_rl = gates.base_rl_mm
    top_rl  = gates.top_rl_mm

    base_level_name, top_level_name = _resolve_level_names(
        storey_id      = storey_id,
        base_rl_mm     = base_rl,
        top_rl_mm      = top_rl,
        project_levels = project_levels,
    )

    gltf = emit_storey_gltf(
        storey_id        = storey_id,
        typing_payload   = typing_payload,
        base_rl_mm       = base_rl,
        top_rl_mm        = top_rl,
        out_dir          = out_dir,
        slab_thickness_mm = gates.slab_thickness_mm,
    )
    transaction = emit_revit_transaction(
        storey_id          = storey_id,
        typing_payload     = typing_payload,
        base_rl_mm         = base_rl,
        top_rl_mm          = top_rl,
        base_level_name    = base_level_name,
        top_level_name     = top_level_name,
        project_levels     = project_levels,
        slab_thickness_mm  = gates.slab_thickness_mm,
        slab_zones         = slab_zones or {},
        out_dir            = out_dir,
    )

    rvt_build: RvtBuildResult | None = None
    if revit_client is not None and revit_client.is_healthy():
        rvt_build = revit_client.build(
            transaction_path = transaction.transaction_path,
            job_id           = storey_id,
            out_dir          = out_dir,
            pdf_filename     = pdf_filename,
        )
    elif revit_client is not None:
        logger.warning(
            f"{storey_id}: Revit client present but server unreachable "
            f"(mode={revit_client.mode}); recipe written for manual rerun"
        )

    return EmitResult(
        storey_id   = storey_id,
        gates       = gates,
        gltf        = gltf,
        transaction = transaction,
        rvt_build   = rvt_build,
    )
