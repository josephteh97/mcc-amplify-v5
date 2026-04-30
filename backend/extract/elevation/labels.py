"""Level-name + RL span extraction for elevations (PLAN.md §3B, PROBE §3B).

PROBE §3B exposed two facts that drove the regex set:

  - The structural ``LEVEL_NAME_RE`` from PLAN §13 (``^(B\\d|L\\d+|RF|UR|MEZZ|GF|GL)\\b``)
    matches **zero** spans on this fixture's architectural elevations. The
    consultant uses ``BASEMENT 1`` / ``BASEMENT 2`` and ``1ST STOREY`` /
    ``2ND STOREY`` etc. instead.
  - RLs are written as ``FFL+3.50`` / ``FFL-2.50`` — *signed meters with
    decimal*. The PLAN §13 ``RL_RE`` (``[+\\-]?\\d+(?:\\.\\d+)?\\s*(?:mm|m)?``)
    matches these but also matches the dimension annotations on the same
    drawing (``8400`` etc.). We use the FFL anchor for primary detection
    and treat the loose form as fallback.

Conversion: FFL meters → millimeters (× 1000). The reconciler in Step 8
accepts an explicit ``meta.yaml.aliases.levels`` map so ``BASEMENT 1``
↔ ``B1`` normalisation is project-controlled, not hard-coded here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import fitz  # type: ignore[import-untyped]


# Architectural full-name forms + structural short codes — both supported so
# the same extractor handles either disciplines on the same upload.
LEVEL_NAME_RE = re.compile(
    r"^(?:"
    r"BASEMENT\s+\d+"
    r"|\d+(?:ST|ND|RD|TH)\s+STOREY"
    r"|B\d+"
    r"|L\d+"
    r"|RF|UR|MEZZ|GF|GL"
    r"|ROOF|PARAPET"
    r")\b",
    re.IGNORECASE,
)

# Signed meters anchored on the FFL prefix.
# Matches: FFL+3.50, FFL-2.50, FFL +9.50, ffl+15.50, …
RL_FFL_RE = re.compile(
    r"FFL\s*([+\-])\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

# Whole-number / decimal mm or m form (fallback). Used only when the FFL
# anchor is absent — see PROBE §3B for why this is unreliable on its own.
RL_MM_RE = re.compile(
    r"^([+\-])?\s*(\d{2,5}(?:\.\d+)?)\s*(mm|m|MM|M)?$",
)


@dataclass(frozen=True)
class LevelSpan:
    text:        str            # canonical text after .strip().upper()
    name:        str            # the matched level name (group 0 of LEVEL_NAME_RE)
    bbox_pt:     tuple[float, float, float, float]
    centre_pt:   tuple[float, float]


@dataclass(frozen=True)
class RLSpan:
    text:        str
    rl_mm:       int
    source:      str            # "FFL" | "MM"
    bbox_pt:     tuple[float, float, float, float]
    centre_pt:   tuple[float, float]


def _ffl_to_mm(sign: str, meters: str) -> int:
    """Convert a captured FFL pair, e.g. ('+', '3.50') → 3500 mm."""
    s = -1 if sign == "-" else 1
    return int(round(s * float(meters) * 1000))


def _mm_to_mm(sign: str | None, value: str, units: str | None) -> int:
    """Convert a loose ``RL_MM_RE`` match into millimeters."""
    s = -1 if sign == "-" else 1
    v = float(value)
    if units in ("m", "M"):
        v *= 1000.0
    return int(round(s * v))


def _try_parse_rl(text: str) -> tuple[int, str] | None:
    """Return (rl_mm, source) or None if the text isn't a recognisable RL."""
    m = RL_FFL_RE.search(text)
    if m:
        return _ffl_to_mm(m.group(1), m.group(2)), "FFL"
    m = RL_MM_RE.match(text.strip())
    if m:
        # The fallback would over-match dimension annotations on the same
        # page (PROBE §3B). Only accept it when FFL hasn't been seen — the
        # caller decides; here we just gate on a sane-looking magnitude.
        rl = _mm_to_mm(m.group(1), m.group(2), m.group(3))
        if -50_000 <= rl <= 200_000:    # building RLs sit in this range
            return rl, "MM"
    return None


def extract_level_and_rl_spans(
    page: fitz.Page,
) -> tuple[list[LevelSpan], list[RLSpan]]:
    """Walk the page's text dict and split spans into level names and RLs.

    Both forms live on the same page in this fixture. We extract them
    once and let the per-page pairing logic in extract.py associate
    each level with its closest RL.
    """
    levels: list[LevelSpan] = []
    rls:    list[RLSpan]    = []
    d = page.get_text("dict") or {}
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                t = (span.get("text") or "").strip()
                if not t:
                    continue
                bb = span.get("bbox")
                if not bb or len(bb) < 4:
                    continue
                bbox = (float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
                centre = ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)

                tu = t.upper()
                lm = LEVEL_NAME_RE.match(tu)
                if lm:
                    levels.append(LevelSpan(
                        text      = tu,
                        name      = lm.group(0).strip(),
                        bbox_pt   = bbox,
                        centre_pt = centre,
                    ))
                    continue   # a level-name span is never simultaneously an RL

                rl = _try_parse_rl(t)
                if rl is not None:
                    rls.append(RLSpan(
                        text      = t,
                        rl_mm     = rl[0],
                        source    = rl[1],
                        bbox_pt   = bbox,
                        centre_pt = centre,
                    ))
    return levels, rls
