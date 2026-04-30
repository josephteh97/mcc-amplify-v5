"""Project-wide constants per PLAN.md §13.

Coordinate-conversion helpers (pixel → grid-mm affine) land in Step 4 with the
STRUCT_PLAN_OVERALL extractor. This module is the canonical home for those
helpers when they arrive; for now it just exposes the constants every other
module references.
"""

PAGE_REGION_MAP: dict[int, str] = {
    1: "upper-left",
    2: "upper-right",
    3: "lower-left",
    4: "lower-right",
}

LABEL_SEARCH_BBOX_DIAGONAL_MULT: float = 2.0
PAIR_PROXIMITY_MM:               float = 50.0
# Reconcile (PLAN §7) tolerance for matching -00 canonical columns to
# -01..04 enlarged detections in the global grid-mm frame. PLAN guessed
# 50 mm; real fixture data shows a ~120 mm systematic Y offset between
# OVERALL and ENLARGED affines because the grid-bubble symbol's relative
# position to the actual grid line varies between page scales (4× zoom
# repositions the bubble glyph). 250 mm is generous enough to absorb
# this artifact while staying well under the 8400 mm minimum bay
# spacing, so neighbouring columns can never collide.
DEDUPE_TOL_MM:                   float = 250.0
ASPECT_TOL:                      float = 0.15
ROUND_ASPECT_LO:                 float = 0.85
ROUND_ASPECT_HI:                 float = 1.15
TYPE_DIM_TOL_MM:                 float = 5.0
LEVEL_AGREEMENT_TOL_MM:          float = 25.0

CLASSIFIER_LLM_PRIMARY_MODEL:    str   = "aisingapore/Gemma-SEA-LION-v4-4B-VL:latest"
CLASSIFIER_LLM_CHECKER_MODEL:    str   = "qwen3-vl:latest"
CLASSIFIER_LLM_THUMBPX:          int   = 512
CLASSIFIER_LLM_CONF_MIN:         float = 0.7

# Back-compat shim — some older imports still use the singular constant.
CLASSIFIER_LLM_MODEL:            str   = CLASSIFIER_LLM_PRIMARY_MODEL

TYPE_CODE_RE:  str = r"^(H-)?[A-Z]{1,3}\d+$"
SECTION_RE:    str = r"^\d{3,4}\s*[xX]\s*\d{3,4}$"
DIA_RE:        str = r"^[ØøD]\s*\d{3,4}$|^\d{3,4}\s*(?:DIA|dia|Ø|ø)$"
LEVEL_NAME_RE: str = r"^(B\d|L\d+|RF|UR|MEZZ|GF|GL)\b"
RL_RE:         str = r"[+\-]?\d+(?:\.\d+)?\s*(?:mm|m)?"

# Flag-string vocabulary (PLAN.md §11). Centralised so backend producers
# and the API aggregator share one truth — typos in either side would
# otherwise silently produce empty review queues.
FLAG_LABEL_MISSING:        str = "label_missing"
FLAG_LABEL_CONFLICT_PFX:   str = "label_conflict"
FLAG_LABEL_INFERRED_PFX:   str = "label_inferred_from_neighbour"
FLAG_ORIENTATION_AMBIG:    str = "orientation_ambiguous"
GATE_SEVERITY_HARD:        str = "hard"
GATE_SEVERITY_WARN:        str = "warn"
