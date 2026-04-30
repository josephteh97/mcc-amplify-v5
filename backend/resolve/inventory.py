"""Revit family inventory (PLAN.md §8).

A ``FamilyInventory`` is the headless mirror of the Revit document's
loaded families: per-shape family + each defined Type with its parsed
(shape, dims, label) and ``type_id``. The Stage 5A matcher consults the
inventory; Stage 5B's pyRevit script consumes the resulting placement
payload and runs Edit Type / Duplicate calls in the live document.

The inventory is mutable — auto-duplicate appends new ``FamilyType``
records — and round-trips to JSON so re-runs of Stage 5A are
deterministic given the same upload.

Schema (JSON):

    {
      "families": [
        {
          "family_name": "Concrete-Rectangular-Column",
          "shape": "rectangular",
          "types": [
            {"type_id": "abc",
             "type_name": "C2_R_800x800",
             "label": "C2",
             "dim_x_mm": 800, "dim_y_mm": 800}
          ]
        },
        {
          "family_name": "Concrete-Round-Column",
          "shape": "round",
          "types": [{"type_id": "def", "type_name": "RD1_RD_1130",
                     "label": "RD1", "diameter_mm": 1130}]
        }
      ]
    }
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger


# Family-name defaults per shape. Override in meta.yaml.families.column.
DEFAULT_FAMILY_NAMES: dict[str, str] = {
    "rectangular": "Concrete-Rectangular-Column",
    "square":      "Concrete-Rectangular-Column",   # square = rect with b=h
    "round":       "Concrete-Round-Column",
    "steel":       "Steel-H-Column",
}


@dataclass
class FamilyType:
    type_id:     str
    type_name:   str
    label:       str | None
    shape:       str                       # rectangular / square / round / steel
    dim_x_mm:    int | None = None
    dim_y_mm:    int | None = None
    diameter_mm: int | None = None
    is_synthetic: bool      = False        # True ⇒ created by auto-duplicate

    def to_dict(self) -> dict:
        return {
            "type_id":      self.type_id,
            "type_name":    self.type_name,
            "label":        self.label,
            "shape":        self.shape,
            "dim_x_mm":     self.dim_x_mm,
            "dim_y_mm":     self.dim_y_mm,
            "diameter_mm":  self.diameter_mm,
            "is_synthetic": self.is_synthetic,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FamilyType":
        return cls(
            type_id      = str(d["type_id"]),
            type_name    = str(d["type_name"]),
            label        = d.get("label"),
            shape        = d.get("shape", "unknown"),
            dim_x_mm     = d.get("dim_x_mm"),
            dim_y_mm     = d.get("dim_y_mm"),
            diameter_mm  = d.get("diameter_mm"),
            is_synthetic = bool(d.get("is_synthetic", False)),
        )


@dataclass
class Family:
    family_name: str
    shape:       str
    types:       list[FamilyType] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "family_name": self.family_name,
            "shape":       self.shape,
            "types":       [t.to_dict() for t in self.types],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Family":
        return cls(
            family_name = str(d["family_name"]),
            shape       = str(d["shape"]),
            types       = [FamilyType.from_dict(t) for t in d.get("types", [])],
        )


@dataclass
class FamilyInventory:
    families: list[Family] = field(default_factory=list)

    # ── Public lookup API ────────────────────────────────────────────────────

    def find_family_for_shape(self, shape: str) -> Family | None:
        """Return the canonical family for a shape, creating it lazily if absent.

        We don't auto-create here — the matcher decides whether to create.
        This is just the lookup half.
        """
        for f in self.families:
            if f.shape == shape:
                return f
        return None

    def lookup_by_dims(
        self,
        shape:        str,
        dim_x_mm:     int | None,
        dim_y_mm:     int | None,
        diameter_mm:  int | None,
        tol_mm:       float,
    ) -> FamilyType | None:
        """Find a type with matching shape AND dims within ±tol_mm."""
        f = self.find_family_for_shape(shape)
        if f is None:
            return None
        for t in f.types:
            if t.shape != shape:
                continue
            if shape == "round":
                if t.diameter_mm is None or diameter_mm is None:
                    continue
                if abs(t.diameter_mm - diameter_mm) <= tol_mm:
                    return t
            else:
                if t.dim_x_mm is None or t.dim_y_mm is None:
                    continue
                if dim_x_mm is None or dim_y_mm is None:
                    continue
                if (abs(t.dim_x_mm - dim_x_mm) <= tol_mm
                        and abs(t.dim_y_mm - dim_y_mm) <= tol_mm):
                    return t
        return None

    def lookup_by_label(
        self,
        shape:        str,
        label:        str,
        dim_x_mm:     int | None,
        dim_y_mm:     int | None,
        diameter_mm:  int | None,
        tol_mm:       float,
    ) -> tuple[FamilyType, int] | None:
        """Find a type whose label matches AND dims are within ±tol_mm.

        Returns (type, max_dim_delta_mm) so the matcher can record the
        delta in the audit trail. Label compare is case-insensitive +
        whitespace-stripped (PLAN §8 tolerance rules).
        """
        if not label:
            return None
        target = label.strip().upper()
        f = self.find_family_for_shape(shape)
        if f is None:
            return None
        for t in f.types:
            if t.shape != shape:
                continue
            if (t.label or "").strip().upper() != target:
                continue
            if shape == "round":
                if t.diameter_mm is None or diameter_mm is None:
                    continue
                d = abs(t.diameter_mm - diameter_mm)
                if d <= tol_mm:
                    return t, int(d)
            else:
                if t.dim_x_mm is None or t.dim_y_mm is None:
                    continue
                if dim_x_mm is None or dim_y_mm is None:
                    continue
                d = max(abs(t.dim_x_mm - dim_x_mm), abs(t.dim_y_mm - dim_y_mm))
                if d <= tol_mm:
                    return t, int(d)
        return None

    # ── Mutation: auto-duplicate ─────────────────────────────────────────────

    def add_type(
        self,
        shape:        str,
        type_name:    str,
        label:        str | None,
        dim_x_mm:     int | None     = None,
        dim_y_mm:     int | None     = None,
        diameter_mm:  int | None     = None,
        family_name:  str | None     = None,
    ) -> FamilyType:
        """Append a synthetic type. Used by the matcher's auto-duplicate path."""
        f = self.find_family_for_shape(shape)
        if f is None:
            f = Family(
                family_name = family_name or DEFAULT_FAMILY_NAMES.get(shape, f"Generic-{shape.title()}"),
                shape       = shape,
                types       = [],
            )
            self.families.append(f)
        new = FamilyType(
            type_id      = f"synthetic:{uuid.uuid4().hex[:12]}",
            type_name    = type_name,
            label        = label,
            shape        = shape,
            dim_x_mm     = dim_x_mm,
            dim_y_mm     = dim_y_mm,
            diameter_mm  = diameter_mm,
            is_synthetic = True,
        )
        f.types.append(new)
        return new

    # ── Round-trip ──────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {"families": [f.to_dict() for f in self.families]}

    @classmethod
    def from_dict(cls, d: dict) -> "FamilyInventory":
        return cls(families=[Family.from_dict(f) for f in d.get("families", [])])

    def types_count(self) -> int:
        return sum(len(f.types) for f in self.families)


# ── Loaders ───────────────────────────────────────────────────────────────────

def load_inventory(path: Path | None) -> FamilyInventory:
    """Load an inventory JSON, falling back to a starter inventory on absence."""
    if path is None or not path.exists():
        if path is not None:
            logger.warning(
                f"family inventory not found at {path} — using starter inventory"
            )
        return starter_inventory()
    try:
        d = json.loads(Path(path).read_text())
        return FamilyInventory.from_dict(d)
    except Exception as exc:                     # noqa: BLE001
        logger.warning(f"failed to parse {path} ({exc}) — using starter inventory")
        return starter_inventory()


def save_inventory(inv: FamilyInventory, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(inv.to_dict(), indent=2))


def starter_inventory() -> FamilyInventory:
    """Minimal seed: one type per shape so auto-duplicate has a base to clone.

    Every type carries ``is_synthetic=False`` so the audit trail still
    distinguishes 'matched a starter type' from 'created in this run' —
    starter types are equivalent to the empty Revit-template state where
    no consultant-specific dimensions have been registered yet.
    """
    return FamilyInventory(families=[
        Family(
            family_name = DEFAULT_FAMILY_NAMES["rectangular"],
            shape       = "rectangular",
            types       = [FamilyType(
                type_id="starter:rect", type_name="STARTER_R_800x800",
                label=None, shape="rectangular",
                dim_x_mm=800, dim_y_mm=800,
            )],
        ),
        Family(
            family_name = DEFAULT_FAMILY_NAMES["square"],
            shape       = "square",
            types       = [FamilyType(
                type_id="starter:sq", type_name="STARTER_S_800",
                label=None, shape="square",
                dim_x_mm=800, dim_y_mm=800,
            )],
        ),
        Family(
            family_name = DEFAULT_FAMILY_NAMES["round"],
            shape       = "round",
            types       = [FamilyType(
                type_id="starter:rd", type_name="STARTER_RD_800",
                label=None, shape="round",
                diameter_mm=800,
            )],
        ),
        Family(
            family_name = DEFAULT_FAMILY_NAMES["steel"],
            shape       = "steel",
            types       = [FamilyType(
                type_id="starter:steel", type_name="STARTER_H_600x600",
                label=None, shape="steel",
                dim_x_mm=600, dim_y_mm=600,
            )],
        ),
    ])
