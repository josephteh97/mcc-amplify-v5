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
DEDUPE_TOL_MM:                   float = 50.0
ASPECT_TOL:                      float = 0.15
ROUND_ASPECT_LO:                 float = 0.85
ROUND_ASPECT_HI:                 float = 1.15
TYPE_DIM_TOL_MM:                 float = 5.0
LEVEL_AGREEMENT_TOL_MM:          float = 25.0

CLASSIFIER_LLM_MODEL:            str   = "qwen3-vl:2b"
CLASSIFIER_LLM_THUMBPX:          int   = 512
CLASSIFIER_LLM_CONF_MIN:         float = 0.7

TYPE_CODE_RE:  str = r"^(H-)?[A-Z]{1,3}\d+$"
SECTION_RE:    str = r"^\d{3,4}\s*[xX]\s*\d{3,4}$"
DIA_RE:        str = r"^[ØøD]\s*\d{3,4}$|^\d{3,4}\s*(?:DIA|dia|Ø|ø)$"
LEVEL_NAME_RE: str = r"^(B\d|L\d+|RF|UR|MEZZ|GF|GL)\b"
RL_RE:         str = r"[+\-]?\d+(?:\.\d+)?\s*(?:mm|m)?"
