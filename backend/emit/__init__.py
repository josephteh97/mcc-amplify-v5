"""Stage 5B — Geometry Emitter (PLAN.md §9).

Per storey: gates → GLTF preview → v4-compatible transaction.json →
optional Windows Revit build (HTTP or file-drop). Stage 5B never
generates ``.rvt`` files itself — the Windows-side
``RevitModelBuilderAddin`` (running inside Revit 2023) does, off the
emitted recipe. Server unreachable doesn't fail the job; the recipe
remains on disk for manual rerun.
"""

from backend.emit.gates              import GateResult, StoreyGates, validate_storey_gates
from backend.emit.gltf               import emit_storey_gltf
from backend.emit.revit_client       import RevitClient, RvtBuildResult
from backend.emit.revit_transaction  import (
    TransactionEmitResult,
    emit_revit_transaction,
)
from backend.emit.runner             import EmitResult, emit_storey


__all__ = [
    "GateResult",
    "StoreyGates",
    "validate_storey_gates",
    "emit_storey_gltf",
    "RevitClient",
    "RvtBuildResult",
    "TransactionEmitResult",
    "emit_revit_transaction",
    "EmitResult",
    "emit_storey",
]
