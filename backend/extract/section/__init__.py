"""Stage 3C — SECTION extractor (PLAN.md §3C, deferred per PROBE §3C).

This fixture's architectural sections carry NO machine-readable slab/beam
annotations (PROBE §3C: 0 dim pairs and 2 thickness-keyword hits across
6,107 text spans). For v5.3 we therefore:

  1. Parse `section_id` from the filename (`_SECTION\\s+([A-Z](?:_[A-Z])*)`).
  2. Best-effort scan for thickness-keyword spans (SLAB/THK/THICK/DEEP/T=)
     so a future fixture with annotated sections informs the regex.
  3. Emit a stub payload — Stage 5B reads
     `meta.yaml.slabs.default_thickness_mm` and flags every slab with
     `source: meta.yaml.fallback` for the review queue (PLAN §17).
"""

from backend.extract.section.extract import (
    SectionExtractResult,
    extract_section,
)
from backend.extract.section.labels  import (
    SECTION_FILENAME_RE,
    THICKNESS_KEYWORD_RE,
    parse_section_ids,
    scan_thickness_hints,
)


__all__ = [
    "SectionExtractResult",
    "extract_section",
    "SECTION_FILENAME_RE",
    "THICKNESS_KEYWORD_RE",
    "parse_section_ids",
    "scan_thickness_hints",
]
