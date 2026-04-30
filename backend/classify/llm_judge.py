"""Tier 4 — primary VLM + checker (PLAN.md §5.4).

Two-VLM design:
  - Primary  (default `aisingapore/Gemma-SEA-LION-v4-4B-VL:latest`) makes
    the classification call.
  - Checker  (default `qwen3-vl:latest`) independently classifies the same
    image. Different model architecture so the two don't share blind spots.

Combination rule:
  - both decide same class → accept, confidence = max(primary, checker),
    tier = LLM, signals.checker_agreed = True
  - disagree                → UNRESOLVED, both verdicts preserved in signals
                              for the UI prompt to surface
  - primary fails (Ollama down / unparseable / unknown class) → fall through
                              to UNRESOLVED with whatever the checker said,
                              flagged so the user can override
  - checker fails  → accept primary alone if primary conf ≥ CONF_MIN,
                     else UNRESOLVED

`format=json` mode is intentionally NOT used: VLMs that emit a hidden
"thinking" channel starve the JSON output budget. Plain text + regex on
`CLASS:/CONFIDENCE:/REASON:` is robust across model families.

Each model's verdict is cached independently in `data/classifier_cache.sqlite`
keyed by `(page_hash, model)`, so swapping primary↔checker reuses prior work.
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path

import fitz  # type: ignore[import-untyped]
import httpx
from loguru import logger

from backend.classify.cache  import JudgeCache
from backend.classify.types  import (
    ClassificationResult,
    ClassifierTier,
    DrawingClass,
)
from backend.core.grid_mm    import (
    CLASSIFIER_LLM_CHECKER_MODEL,
    CLASSIFIER_LLM_CONF_MIN,
    CLASSIFIER_LLM_PRIMARY_MODEL,
    CLASSIFIER_LLM_THUMBPX,
)


OLLAMA_HOST     = os.getenv("OLLAMA_HOST", "http://localhost:11434")
LLM_TIMEOUT     = float(os.getenv("CLASSIFIER_LLM_TIMEOUT", "240"))
LLM_RETRIES     = int(os.getenv("CLASSIFIER_LLM_RETRIES", "2"))
LLM_DISABLED    = os.getenv("CLASSIFIER_LLM_DISABLED",         "false").lower() == "true"
CHECKER_DISABLED = os.getenv("CLASSIFIER_LLM_CHECKER_DISABLED", "false").lower() == "true"

DEFAULT_CACHE_PATH = Path(os.getenv("CLASSIFIER_CACHE_PATH", "data/classifier_cache.sqlite"))


PROMPT = """You are classifying construction drawings. Look carefully at the image and classify it.

CRITICAL RULE: Grid bubbles (lettered/numbered circles around the page perimeter) appear in BOTH structural and architectural plans — do NOT use them as the deciding factor.

DISCRIMINATORS:
• STRUCTURAL plans show: filled small rectangles at grid intersections (columns), thin lines between them (beams). They are SPARSE — almost no other content. No walls, no doors, no room names, no furniture.
• ARCHITECTURAL plans show: walls (often hatched or double-line), doors (arc symbols), room labels in CAPS (OFFICE, TOILET, LIFT LOBBY, BEDROOM, KITCHEN, CORRIDOR, STAIR), sometimes furniture or fixtures.
• ELEVATION: external view, horizontal level lines across the page with names like L1/L2/B1/RF and reduced level numbers.
• SECTION: vertical cut, hatched slab cross-sections at level lines, beam profiles below slabs. Side view of building structure.
• PERSPECTIVE: 3D rendering of the building, photographic look, no measurements.
• SCHEDULE: tabular layout, columns of numbers, no plan/elevation drawing.

CLASSES:
- STRUCT_PLAN_OVERALL: structural plan covering whole storey
- STRUCT_PLAN_ENLARGED: structural plan covering one quadrant of a storey
- ELEVATION: external building elevation
- SECTION: building cross-section
- DISCARD: architectural plans, perspectives, schedules, MEP, details, foundations, anything else

If you see ANY room labels (OFFICE/TOILET/LIFT/STAIR/BEDROOM/etc.) in a plan view → DISCARD.
If the plan is sparse (only columns + beams + grid) → STRUCT_PLAN_*.

Reply EXACTLY in this format, nothing else:
CLASS: <NAME>
CONFIDENCE: <0.0-1.0>
REASON: <one sentence>"""


_CLASS_RE      = re.compile(r"CLASS:\s*([A-Z_]+)",     re.IGNORECASE)
_CONFIDENCE_RE = re.compile(r"CONFIDENCE:\s*([\d.]+)", re.IGNORECASE)
_REASON_RE     = re.compile(r"REASON:\s*(.+)",         re.IGNORECASE | re.DOTALL)


# ── Rendering ────────────────────────────────────────────────────────────────

def render_thumbnail_png(pdf_path: Path, page_index: int, max_px: int) -> bytes:
    with fitz.open(pdf_path) as doc:
        page = doc[page_index]
        max_dim = max(page.rect.width, page.rect.height)
        scale = max_px / max_dim if max_dim > 0 else 1.0
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
        return pix.tobytes("png")


# ── Parsing ──────────────────────────────────────────────────────────────────

def parse_response(raw: str) -> tuple[str | None, float | None, str | None]:
    cls_m  = _CLASS_RE.search(raw)
    conf_m = _CONFIDENCE_RE.search(raw)
    rea_m  = _REASON_RE.search(raw)
    cls    = cls_m.group(1).strip().upper() if cls_m else None
    try:
        conf = float(conf_m.group(1)) if conf_m else None
    except ValueError:
        conf = None
    reason = rea_m.group(1).strip().splitlines()[0] if rea_m else None
    return cls, conf, reason


# ── Ollama HTTP ──────────────────────────────────────────────────────────────

def is_ollama_reachable(host: str = OLLAMA_HOST, timeout: float = 2.0) -> bool:
    try:
        r = httpx.get(f"{host}/api/tags", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def _call_ollama(image_b64: str, model: str, host: str, timeout: float) -> str:
    r = httpx.post(
        f"{host}/api/generate",
        json={
            "model":   model,
            "prompt":  PROMPT,
            "images":  [image_b64],
            "stream":  False,
            "options": {"num_predict": 600, "temperature": 0.1},
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json().get("response", "")


# ── Single-model judgment with cache ─────────────────────────────────────────

def _judge_one(
    image_b64:   str,
    page_hash:   str,
    model:       str,
    cache:       JudgeCache,
    pdf_name:    str,
    host:        str   = OLLAMA_HOST,
    timeout:     float = LLM_TIMEOUT,
    retries:     int   = LLM_RETRIES,
) -> dict | None:
    """Run ONE model on the page; cached by (page_hash, model).

    Returns a dict {class, confidence, reason, model, raw, cached} or None
    if the model returned nothing parseable / produced an unknown class.
    """
    cached = cache.get(page_hash, model)
    if cached is not None:
        return {
            "class":      cached.drawing_class,
            "confidence": cached.confidence,
            "reason":     cached.reason,
            "model":      model,
            "raw":        cached.raw,
            "cached":     True,
        }

    raw = ""
    cls_str: str | None = None
    for attempt in range(max(1, retries + 1)):
        try:
            raw = _call_ollama(image_b64, model, host, timeout)
        except Exception as exc:
            logger.warning(f"{model}: HTTP error on {pdf_name} (attempt {attempt + 1}): {exc}")
            continue
        cls_str, _, _ = parse_response(raw)
        if cls_str is not None:
            break

    if cls_str is None:
        logger.warning(f"{model}: no parseable response for {pdf_name}")
        return None

    try:
        DrawingClass(cls_str)
    except ValueError:
        logger.warning(f"{model}: unknown class {cls_str!r} for {pdf_name}")
        return None

    _, conf, reason = parse_response(raw)
    confidence = conf if conf is not None else 0.0
    reason     = reason or "(no reason)"
    cache.put(page_hash, model, cls_str, confidence, reason, raw)
    return {
        "class":      cls_str,
        "confidence": confidence,
        "reason":     reason,
        "model":      model,
        "raw":        raw,
        "cached":     False,
    }


# ── Public entrypoint: primary + checker ─────────────────────────────────────

def classify_llm(
    pdf_path:      Path,
    page_index:    int,
    page_hash:     str,
    cache:         JudgeCache | None  = None,
    primary_model: str  = CLASSIFIER_LLM_PRIMARY_MODEL,
    checker_model: str  = CLASSIFIER_LLM_CHECKER_MODEL,
    host:          str  = OLLAMA_HOST,
) -> ClassificationResult | None:
    """Primary makes the call; checker validates. See module docstring for the
    combination rule.

    Returns None when the LLM tier is disabled, Ollama is unreachable, or
    nothing parseable came back from either model.
    """
    if LLM_DISABLED:
        return None
    if not is_ollama_reachable(host):
        logger.warning(f"Ollama unreachable at {host}; LLM judge skipped for {pdf_path.name}")
        return None

    cache = cache if cache is not None else JudgeCache(DEFAULT_CACHE_PATH)
    png = render_thumbnail_png(pdf_path, page_index, CLASSIFIER_LLM_THUMBPX)
    image_b64 = base64.b64encode(png).decode()

    primary = _judge_one(image_b64, page_hash, primary_model, cache, pdf_path.name, host=host)
    checker = (
        None if CHECKER_DISABLED or checker_model == primary_model
        else _judge_one(image_b64, page_hash, checker_model, cache, pdf_path.name, host=host)
    )
    return _combine(primary, checker, primary_model, checker_model)


def _combine(
    primary:       dict | None,
    checker:       dict | None,
    primary_model: str,
    checker_model: str,
) -> ClassificationResult | None:
    """Apply the primary+checker combination rule (see module docstring)."""

    if primary is None and checker is None:
        return None

    # Primary unavailable — fall back to checker, but flag as UNRESOLVED so
    # the UI confirms (we never had primary's vote).
    if primary is None:
        return ClassificationResult(
            drawing_class = DrawingClass.UNKNOWN,
            tier          = ClassifierTier.UNRESOLVED,
            confidence    = checker["confidence"],
            reason        = (f"primary {primary_model} failed; "
                             f"checker {checker_model} → {checker['class']} "
                             f"({checker['confidence']:.2f}): {checker['reason']}"),
            signals       = {
                "primary_failed": True,
                "checker":        _signals_for(checker),
            },
        )

    # Checker unavailable — accept primary alone if confident enough.
    if checker is None:
        if primary["confidence"] >= CLASSIFIER_LLM_CONF_MIN:
            return ClassificationResult(
                drawing_class = DrawingClass(primary["class"]),
                tier          = ClassifierTier.LLM,
                confidence    = primary["confidence"],
                reason        = primary["reason"],
                signals       = {
                    "primary":         _signals_for(primary),
                    "checker_skipped": True,
                },
            )
        return ClassificationResult(
            drawing_class = DrawingClass.UNKNOWN,
            tier          = ClassifierTier.UNRESOLVED,
            confidence    = primary["confidence"],
            reason        = (f"primary {primary_model} → {primary['class']} "
                             f"({primary['confidence']:.2f}) below threshold "
                             f"{CLASSIFIER_LLM_CONF_MIN:.2f}; no checker available"),
            signals       = {"primary": _signals_for(primary), "checker_skipped": True},
        )

    # Both ran. Agreement → high confidence.
    if primary["class"] == checker["class"]:
        confidence = max(primary["confidence"], checker["confidence"])
        return ClassificationResult(
            drawing_class = DrawingClass(primary["class"]),
            tier          = ClassifierTier.LLM,
            confidence    = confidence,
            reason        = primary["reason"],
            signals       = {
                "primary":          _signals_for(primary),
                "checker":          _signals_for(checker),
                "checker_agreed":   True,
            },
        )

    # Disagreement → UNRESOLVED with both verdicts surfaced.
    return ClassificationResult(
        drawing_class = DrawingClass.UNKNOWN,
        tier          = ClassifierTier.UNRESOLVED,
        confidence    = 0.0,
        reason        = (f"primary {primary_model} → {primary['class']} "
                         f"({primary['confidence']:.2f}); "
                         f"checker {checker_model} → {checker['class']} "
                         f"({checker['confidence']:.2f}); disagree"),
        signals       = {
            "primary":         _signals_for(primary),
            "checker":         _signals_for(checker),
            "checker_agreed":  False,
        },
    )


def _signals_for(j: dict) -> dict:
    """Compact view of one model's verdict for inclusion in the report."""
    return {
        "model":      j["model"],
        "class":      j["class"],
        "confidence": j["confidence"],
        "reason":     j["reason"],
        "cached":     j.get("cached", False),
    }
