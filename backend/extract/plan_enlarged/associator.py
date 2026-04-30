"""Shape-aware label associator (PLAN.md §3A-2).

For each YOLO column bbox, find the closest type-code label and its
matching dimension/diameter label. Both bboxes and labels live in
different coordinate frames coming in:

  - YOLO bbox       → image-pixel space (post-rotation, what gets rendered)
  - Label bbox      → PDF natural-space (pre-rotation), in points

We project the label centres into image-pixel space using the same
rotation-aware transform the grid detector uses, and run all proximity
queries in pixels so the bbox-diagonal search radius scales naturally
with bbox size.

Once a type-label match is found, the dim/dia label is searched in a
small radius around the *type label*, not the YOLO bbox — because
consultants typically stack type-on-top-of-dim like:

    C2                C2
    800x800           Ø1000

so the type-to-dim distance is far smaller than the column-to-type one.

Shape decision (PLAN.md §3A-2):

  - DIAMETER label                  → round
  - RECT_DIM with a == b            → square
  - RECT_DIM with a ≠ b             → rectangular (orientation deferred to
                                      orientation.decide_orientation)
  - TYPE present, no dim/dia        → unknown, flag `dim_missing`
  - No TYPE, no dim                 → unknown, flag `unlabeled`
  - H-prefixed type code            → steel (geometric handling identical
                                      to rectangular for v5.3)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.extract.plan_enlarged.labels      import Label, LabelKind
from backend.extract.plan_enlarged.orientation import (
    OrientationDecision,
    OrientationVerdict,
    decide_orientation,
)


TYPE_SEARCH_MULT = 3.0     # × bbox diagonal — type labels can sit one column-width away
DIM_SEARCH_MULT  = 3.5     # × type-label diagonal — dim sits stacked under/next to type
SHAPES = ("rectangular", "square", "round", "unknown", "steel")


@dataclass(frozen=True)
class AssociatedColumn:
    bbox_px:        tuple[float, float, float, float]
    centre_px:      tuple[float, float]
    yolo_aspect:    float
    yolo_confidence: float

    label:          str | None
    is_steel:       bool
    shape:          str                                # one of SHAPES
    dim_along_x_mm: int | None
    dim_along_y_mm: int | None
    diameter_mm:    int | None
    orientation:    OrientationDecision | None

    type_label:     Label | None
    dim_label:      Label | None
    flags:          list[str] = field(default_factory=list)


def _euclid(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _bbox_diag(bbox: tuple[float, float, float, float]) -> float:
    return ((bbox[2] - bbox[0]) ** 2 + (bbox[3] - bbox[1]) ** 2) ** 0.5


def _label_centre_px(
    label:     Label,
    disp_w_pt: float,
    disp_h_pt: float,
    scale:     float,
    rotation:  int,
) -> tuple[float, float]:
    """PDF natural-space → rendered image pixels (rotation-aware).

    Mirrors detector._to_image_coords. Label.bbox_pt is in PDF natural
    coords; YOLO bboxes are in rendered image pixels — must reconcile.
    """
    cx, cy = label.centre_pt
    if rotation == 90:
        return (disp_w_pt - cy) * scale, cx * scale
    if rotation == 270:
        return cy * scale, (disp_h_pt - cx) * scale
    if rotation == 180:
        return (disp_w_pt - cx) * scale, (disp_h_pt - cy) * scale
    return cx * scale, cy * scale


def _nearest(
    target_px: tuple[float, float],
    candidates: list[tuple[Label, tuple[float, float]]],
    radius_px: float,
) -> tuple[Label, float] | None:
    best: tuple[Label, float] | None = None
    for lbl, c in candidates:
        d = _euclid(target_px, c)
        if d > radius_px:
            continue
        if best is None or d < best[1]:
            best = (lbl, d)
    return best


def associate_columns(
    yolo_columns:  list[tuple[float, float, float, float, float, float]],
    # ^ each: (x0, y0, x1, y1, aspect, confidence) in image-pixel space
    labels:        list[Label],
    disp_w_pt:     float,
    disp_h_pt:     float,
    scale:         float,         # dpi / 72
    rotation:      int,
) -> list[AssociatedColumn]:
    """Pair every YOLO column bbox with its type/dim labels.

    All proximity queries run in image-pixel space (matches YOLO bbox space
    natively and scales with bbox size). Caller is responsible for building
    the YOLO tuples — keeps this function decoupled from yolo_columns.py.
    """
    type_pts: list[tuple[Label, tuple[float, float]]] = []
    dim_pts:  list[tuple[Label, tuple[float, float]]] = []
    for l in labels:
        c = _label_centre_px(l, disp_w_pt, disp_h_pt, scale, rotation)
        if l.kind == LabelKind.TYPE:
            type_pts.append((l, c))
        elif l.kind in (LabelKind.RECT_DIM, LabelKind.DIAMETER):
            dim_pts.append((l, c))

    out: list[AssociatedColumn] = []
    for x0, y0, x1, y1, aspect, conf in yolo_columns:
        bbox = (x0, y0, x1, y1)
        diag = _bbox_diag(bbox)
        centre = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)

        # 1) closest type label within type-search radius
        type_hit = _nearest(centre, type_pts, TYPE_SEARCH_MULT * diag)
        type_label = type_hit[0] if type_hit else None

        # 2) closest dim/dia label — anchor on TYPE label centre when we have
        #    one (consultants stack type-over-dim), else on the bbox centre
        dim_hit: tuple[Label, float] | None = None
        if type_label is not None:
            anchor_px = _label_centre_px(type_label, disp_w_pt, disp_h_pt, scale, rotation)
            type_diag = _bbox_diag(type_label.bbox_pt) * scale
            dim_radius = max(DIM_SEARCH_MULT * type_diag, diag * 1.5)
            dim_hit = _nearest(anchor_px, dim_pts, dim_radius)
        if dim_hit is None:
            dim_hit = _nearest(centre, dim_pts, TYPE_SEARCH_MULT * diag)
        dim_label = dim_hit[0] if dim_hit else None

        # 3) shape + dimension extraction
        flags: list[str] = []
        shape = "unknown"
        dim_x = dim_y = dia_mm = None
        orientation: OrientationDecision | None = None
        is_steel = bool(type_label and type_label.is_steel)

        if dim_label is not None and dim_label.kind == LabelKind.DIAMETER:
            shape = "round"
            dia_mm = dim_label.diameter_mm
        elif dim_label is not None and dim_label.kind == LabelKind.RECT_DIM:
            a, b = dim_label.rect_a_mm, dim_label.rect_b_mm
            assert a is not None and b is not None
            if a == b:
                shape = "square"
                dim_x = dim_y = a
            else:
                shape = "rectangular"
                bbox_dx = max(x1 - x0, 1e-3)
                bbox_dy = max(y1 - y0, 1e-3)
                orientation = decide_orientation(bbox_dx, bbox_dy, a, b)
                if orientation.verdict == OrientationVerdict.AMBIGUOUS:
                    flags.append("orientation_ambiguous")
                else:
                    dim_x = orientation.dim_along_x_mm
                    dim_y = orientation.dim_along_y_mm
        else:
            if type_label is None:
                flags.append("unlabeled")
            else:
                flags.append("dim_missing")

        if is_steel and shape != "unknown":
            shape = "steel"

        out.append(AssociatedColumn(
            bbox_px         = bbox,
            centre_px       = centre,
            yolo_aspect     = aspect,
            yolo_confidence = conf,
            label           = type_label.text if type_label else None,
            is_steel        = is_steel,
            shape           = shape,
            dim_along_x_mm  = dim_x,
            dim_along_y_mm  = dim_y,
            diameter_mm     = dia_mm,
            orientation     = orientation,
            type_label      = type_label,
            dim_label       = dim_label,
            flags           = flags,
        ))
    return out
