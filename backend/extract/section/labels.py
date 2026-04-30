"""Section helpers — filename → section_ids and best-effort text scanning.

PROBE §3C established that this fixture's sections expose section IDs only
in the *filename* (e.g. ``TD-A-120-0101_SECTION A_B.pdf`` carries sections
A and B). The page body has no ``SECTION X-Y`` text and no slab/beam
thickness annotations. We still scan for thickness keywords on every page
so a future fixture with annotated sections feeds back into the regex set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import fitz  # type: ignore[import-untyped]


# Capture the section-letter cluster after `_SECTION ` in a filename.
# Matches `A`, `A_B`, `A_B_C`, `A1`, etc. — split on `_` into individual ids.
SECTION_FILENAME_RE = re.compile(r"_SECTION\s+([A-Z]+(?:_[A-Z]+)*)", re.IGNORECASE)

# Keyword anchor for slab/beam thickness annotations. Matches the patterns
# v4 expected (`150 SLAB`, `T=200`, `600 DEEP`, `200 THK`). PROBE §3C
# returned 2 fixture hits — we keep the regex so future fixtures feed back.
THICKNESS_KEYWORD_RE = re.compile(
    r"(?:^|\s)(?:T\s*=\s*)?\d{2,4}\s*(SLAB|THK|THICK|DEEP|MM|mm)\b"
    r"|^T\s*=\s*\d{2,4}$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ThicknessHint:
    text:    str
    bbox_pt: tuple[float, float, float, float]
    page:    int


def parse_section_ids(filename: str) -> list[str]:
    """Extract the section-ID list from a filename like ``..._SECTION A_B.pdf``.

    Returns ``[]`` when no match — the caller flags the file for manual
    section-ID assignment (Stage 5A reviewer queue).
    """
    m = SECTION_FILENAME_RE.search(filename)
    if not m:
        return []
    return [tok for tok in m.group(1).upper().split("_") if tok]


def scan_thickness_hints(pdf_path: Path) -> list[ThicknessHint]:
    """Walk every page, return spans matching the thickness-keyword regex.

    Best-effort. PROBE §3C returned 0–2 hits per fixture PDF; the result
    is captured in the section payload so Stage 5B / future probes can see
    what the consultant's text actually looks like before refining the
    regex.
    """
    out: list[ThicknessHint] = []
    with fitz.open(pdf_path) as doc:
        for pi, page in enumerate(doc):
            d = page.get_text("dict") or {}
            for block in d.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        t = (span.get("text") or "").strip()
                        if not t:
                            continue
                        if not THICKNESS_KEYWORD_RE.search(t):
                            continue
                        bb = span.get("bbox")
                        if not bb or len(bb) < 4:
                            continue
                        out.append(ThicknessHint(
                            text    = t,
                            bbox_pt = (float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])),
                            page    = pi,
                        ))
    return out
