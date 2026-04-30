"""Vector-text label extraction for STRUCT_PLAN_ENLARGED (PLAN.md §3A-2).

Walks a fitz.Page's `get_text("dict")` output and classifies each text span
as one of:

  - TYPE      : column/beam type code, e.g. ``C2``, ``H-C9``, ``RCB2``, ``C1A``
  - RECT_DIM  : rectangular section dimension, e.g. ``800x800``, ``1150x800``
  - DIAMETER  : circular section diameter, e.g. ``Ø1000``, ``1130 Ø``, ``D1200``
  - OTHER     : anything else (kept so the associator can ignore it cleanly)

Regex notes (PROBE §3A-2):

  - TYPE_CODE_RE is widened from PLAN.md §13's ``^(H-)?[A-Z]{1,3}\d+$`` to
    accept the trailing-letter variant ``C1A`` (8 hits in the fixture).
  - RECT_DIM is unchanged — strict-mode caught all 4,792 fixture hits with
    zero loose-only escapees.
  - DIA accepts both ``Ø1000`` (prefix form) and ``1000 DIA``/``1000 Ø``
    (suffix form). The fixture has 200 strict + 33 loose-only mostly from
    embedded variants the strict anchor missed.

Rotation (PROBE §3A-2): ~95% of fixture labels are non-horizontal. PyMuPDF
exposes the writing direction as a unit vector in line["dir"]; we record it
as degrees so the downstream associator can do rotation-aware proximity.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

import fitz  # type: ignore[import-untyped]


TYPE_CODE_RE = re.compile(r"^(H-)?[A-Z]{1,3}\d+[A-Z]?$")
RECT_DIM_RE  = re.compile(r"^(\d{3,4})\s*[xX]\s*(\d{3,4})$")
DIA_RE       = re.compile(
    r"^[ØøD]\s*(\d{3,4})$|^(\d{3,4})\s*(?:DIA|dia|Ø|ø)$",
)


class LabelKind(str, Enum):
    TYPE     = "type"
    RECT_DIM = "rect_dim"
    DIAMETER = "diameter"
    OTHER    = "other"


@dataclass(frozen=True)
class Label:
    text:        str
    kind:        LabelKind
    bbox_pt:     tuple[float, float, float, float]   # natural pre-rotation page points
    centre_pt:   tuple[float, float]
    rotation_deg: float
    # parsed parts (populated for TYPE/RECT_DIM/DIAMETER, None otherwise)
    type_code:    str   | None = None
    is_steel:     bool  = False                # H-prefixed ⇒ steel column
    rect_a_mm:    int   | None = None          # first number in NxM
    rect_b_mm:    int   | None = None          # second number in NxM
    diameter_mm:  int   | None = None


def _dir_to_degrees(direction: tuple[float, float]) -> float:
    if not direction or len(direction) < 2:
        return 0.0
    dx, dy = direction[0], direction[1]
    return round(math.degrees(math.atan2(-dy, dx)), 1)


def _classify(text: str) -> tuple[LabelKind, dict]:
    """Return (kind, parsed-fields) for one stripped text span."""
    m = TYPE_CODE_RE.match(text)
    if m:
        return LabelKind.TYPE, {
            "type_code": text,
            "is_steel":  bool(m.group(1)),
        }
    m = RECT_DIM_RE.match(text)
    if m:
        return LabelKind.RECT_DIM, {
            "rect_a_mm": int(m.group(1)),
            "rect_b_mm": int(m.group(2)),
        }
    m = DIA_RE.match(text)
    if m:
        return LabelKind.DIAMETER, {
            "diameter_mm": int(m.group(1) or m.group(2)),
        }
    return LabelKind.OTHER, {}


def extract_labels(page: fitz.Page, *, include_other: bool = False) -> list[Label]:
    """Return every classified label span on the page.

    By default OTHER spans are dropped — the associator only ever pairs
    against TYPE / RECT_DIM / DIAMETER labels. Set ``include_other=True``
    to keep them (useful for probes/regression fixtures).
    """
    out: list[Label] = []
    d = page.get_text("dict") or {}
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            rot = _dir_to_degrees(tuple(line.get("dir") or (1.0, 0.0)))
            for span in line.get("spans", []):
                t = (span.get("text") or "").strip()
                if not t:
                    continue
                bb = span.get("bbox")
                if not bb or len(bb) < 4:
                    continue
                kind, parsed = _classify(t)
                if kind == LabelKind.OTHER and not include_other:
                    continue
                bbox = (float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
                centre = ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
                out.append(Label(
                    text         = t,
                    kind         = kind,
                    bbox_pt      = bbox,
                    centre_pt    = centre,
                    rotation_deg = rot,
                    **parsed,
                ))
    return out


def label_summary(labels: Iterable[Label]) -> dict:
    """Counter-style summary used by tests / probes."""
    from collections import Counter
    by_kind = Counter(l.kind.value for l in labels)
    return dict(by_kind)
