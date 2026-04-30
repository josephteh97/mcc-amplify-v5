"""YOLO column detection on STRUCT_PLAN_OVERALL pages (PLAN.md §3A-1).

Renovation of v4's yolo_runner.py, narrowed to the column model only and
wired against the GridResult / Affine2D outputs of detector.py + affine.py.

Pipeline:
  1. Render the page to RGB at the same DPI used by detect_grid (default 150).
  2. CLAHE contrast enhancement (engineering drawings render very faintly).
  3. Sliding-window tiling at 1280×1280 with 200-px overlap (column model
     was trained at imgsz=1280 — running on the full ~7000×5000 page in a
     single pass would downscale it ~5× and tank recall).
  4. Global NMS across tile detections.
  5. For each detection, transform the bbox centre + extents from pixels to
     building grid-mm via the supplied affine, and emit a payload row.

The module degrades gracefully: if ultralytics or the weight file isn't
available, it logs a warning and returns []. The caller flags the page as
"yolo_columns_skipped" rather than failing the extraction.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz  # type: ignore[import-untyped]
import numpy as np
from loguru import logger

from backend.extract.plan_overall.affine import Affine2D


DEFAULT_WEIGHTS = Path("ml/weights/column-detect.pt")
TILE_SIZE       = 1280
TILE_OVERLAP    = 200
CONF_THRESHOLD  = 0.25
IOU_THRESHOLD   = 0.45
MIN_SIDE_PX     = 10
MAX_SIDE_PX     = 200    # @150 dpi: 200 px ≈ 33 mm at 1:1 scale, plenty for columns


@dataclass(frozen=True)
class ColumnDetection:
    bbox_grid_mm: tuple[float, float, float, float]   # (x_min, y_min, x_max, y_max)
    centre_grid_mm: tuple[float, float]
    aspect: float                                      # min/max side
    confidence: float
    bbox_px: tuple[float, float, float, float]


def _try_import_yolo():
    try:
        from ultralytics import YOLO    # type: ignore[import-untyped]
        return YOLO
    except Exception as exc:               # noqa: BLE001
        logger.warning(f"ultralytics import failed ({exc}) — YOLO step skipped")
        return None


def _try_import_torch_nms():
    try:
        import torch
        from torchvision.ops import nms
        return torch, nms
    except Exception as exc:               # noqa: BLE001
        logger.warning(f"torchvision NMS unavailable ({exc}) — falling back to per-tile boxes")
        return None, None


def _try_import_cv2():
    try:
        import cv2
        return cv2
    except Exception:
        return None


def _render_page(page: fitz.Page, dpi: float) -> np.ndarray:
    """Render the displayed page (post-rotation) to a uint8 RGB array."""
    pix = page.get_pixmap(dpi=int(dpi), alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        arr = arr[:, :, :3]
    return arr


def _clahe(rgb: np.ndarray) -> np.ndarray:
    cv2 = _try_import_cv2()
    if cv2 is None:
        return rgb
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    return cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2RGB)


def _run_tiles(
    model:    Any,
    img:      np.ndarray,
    tile:     int  = TILE_SIZE,
    overlap:  int  = TILE_OVERLAP,
    conf:     float = CONF_THRESHOLD,
    iou:      float = IOU_THRESHOLD,
) -> tuple[list[list[float]], list[float]]:
    H, W = img.shape[:2]
    step = tile - overlap
    boxes: list[list[float]] = []
    confs: list[float]       = []
    xs = list(range(0, max(W - overlap, 1), step)) or [0]
    ys = list(range(0, max(H - overlap, 1), step)) or [0]
    for y0 in ys:
        for x0 in xs:
            x1 = min(x0 + tile, W); xa = max(0, x1 - tile)
            y1 = min(y0 + tile, H); ya = max(0, y1 - tile)
            crop = img[ya:y1, xa:x1]
            res = model.predict(source=crop, imgsz=tile, conf=conf, iou=iou,
                                verbose=False)[0]
            xyxy = res.boxes.xyxy.cpu().numpy()
            cs   = res.boxes.conf.cpu().numpy()
            for (bx0, by0, bx1, by1), c in zip(xyxy, cs):
                boxes.append([float(bx0) + xa, float(by0) + ya,
                              float(bx1) + xa, float(by1) + ya])
                confs.append(float(c))
    return boxes, confs


def _global_nms(
    boxes: list[list[float]],
    confs: list[float],
    iou:   float = IOU_THRESHOLD,
) -> tuple[list[list[float]], list[float]]:
    if not boxes:
        return boxes, confs
    torch, nms = _try_import_torch_nms()
    if torch is None or nms is None:
        return boxes, confs
    bt = torch.tensor(boxes, dtype=torch.float32)
    ct = torch.tensor(confs, dtype=torch.float32)
    keep = nms(bt, ct, iou_threshold=iou).cpu().numpy()
    return [boxes[i] for i in keep], [confs[i] for i in keep]


def detect_columns(
    page:    fitz.Page,
    affine:  Affine2D,
    dpi:     float = 150.0,
    weights: Path  = DEFAULT_WEIGHTS,
) -> list[ColumnDetection]:
    """Run the column model + affine transform; return per-column payloads.

    Returns [] (and logs a warning) when ultralytics or the weight file is
    unavailable — Step 4d wiring keeps the rest of the pipeline running so
    Stage 3A-1 still ships a partial overall.json.
    """
    weights_path = weights if weights.is_absolute() else (Path.cwd() / weights)
    if not weights_path.exists():
        logger.warning(f"column-detect weights not found at {weights_path} — skipping YOLO")
        return []

    YOLO = _try_import_yolo()
    if YOLO is None:
        return []

    rgb = _render_page(page, dpi)
    rgb = _clahe(rgb)

    model  = YOLO(str(weights_path))
    boxes, confs = _run_tiles(model, rgb)
    boxes, confs = _global_nms(boxes, confs)
    logger.info(f"YOLO column detect: {len(boxes)} boxes after NMS")

    out: list[ColumnDetection] = []
    for bx, c in zip(boxes, confs):
        x0, y0, x1, y1 = bx
        side_min = min(x1 - x0, y1 - y0)
        side_max = max(x1 - x0, y1 - y0)
        if side_min < MIN_SIDE_PX or side_max > MAX_SIDE_PX:
            continue
        aspect = side_min / side_max if side_max > 0 else 0.0
        # bbox corners → grid-mm via the per-axis affine
        mm_x0, mm_y0 = affine.px_to_mm(x0, y0)
        mm_x1, mm_y1 = affine.px_to_mm(x1, y1)
        mm_xmin, mm_xmax = sorted((mm_x0, mm_x1))
        mm_ymin, mm_ymax = sorted((mm_y0, mm_y1))
        cx, cy = (mm_xmin + mm_xmax) / 2.0, (mm_ymin + mm_ymax) / 2.0
        out.append(ColumnDetection(
            bbox_grid_mm   = (mm_xmin, mm_ymin, mm_xmax, mm_ymax),
            centre_grid_mm = (cx, cy),
            aspect         = aspect,
            confidence     = c,
            bbox_px        = (x0, y0, x1, y1),
        ))
    return out
