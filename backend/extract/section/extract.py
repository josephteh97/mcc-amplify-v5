"""Per-PDF section extractor (PLAN.md §3C, deferred per PROBE §3C).

Stage 3C is intentionally a stub for v5.3 — this fixture's architectural
sections do not annotate slab thickness or beam depth in machine-readable
text (PROBE §3C: 0 dim pairs / 2 thickness-keyword hits across 6,107
spans). The pipeline:

  1. Parse `section_id` list from the filename.
  2. Capture any thickness-keyword spans found on the page (best-effort,
     usually empty — kept so future fixtures feed back into the regex).
  3. Emit ``extracted/section/<pdf_stem>.section.json`` with empty
     ``joints`` lists. Stage 5B reads
     ``meta.yaml.slabs.default_thickness_mm`` and stamps every slab with
     ``source: meta.yaml.fallback`` (PLAN §17 review-queue path).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # type: ignore[import-untyped]
from loguru import logger

from backend.extract.section.labels import (
    ThicknessHint,
    parse_section_ids,
    scan_thickness_hints,
)


@dataclass
class SectionExtractResult:
    pdf_path:        Path
    pdf_stem:        str
    page_count:      int
    section_ids:     list[str]
    thickness_hits:  int
    payload_path:    Path | None
    error:           str | None    = None
    flags:           list[str]     = field(default_factory=list)


def _build_payload(
    pdf_path:    Path,
    page_count:  int,
    section_ids: list[str],
    hints:       list[ThicknessHint],
    flags:       list[str],
) -> dict:
    return {
        "source_pdf":   pdf_path.name,
        "pdf_stem":     pdf_path.stem,
        "page_count":   page_count,
        "section_ids":  section_ids,
        "sections": [
            {"section_id": sid, "joints": []}    # PLAN §3C joints schema
            for sid in section_ids
        ],
        "thickness_hints": [
            {"text": h.text, "page": h.page, "bbox_pt": list(h.bbox_pt)}
            for h in hints
        ],
        "flags":        flags,
    }


def extract_section(pdf_path: Path, out_dir: Path) -> SectionExtractResult:
    """Run the deferred-stub section extractor on one section PDF.

    Always writes a payload — empty joints + flags is the documented
    Stage 5B fallback pathway, not a failure.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    section_ids = parse_section_ids(pdf_path.name)
    flags: list[str] = [
        "thickness_extraction_deferred_v5_3",   # PLAN §17
        "stage_5b_falls_back_to_meta_default",
    ]
    if not section_ids:
        flags.append("section_ids_unparseable_from_filename")
        logger.warning(f"{pdf_path.name}: could not parse section IDs from filename")
    else:
        flags.append("section_ids_from_filename")

    page_count = 0
    hints: list[ThicknessHint] = []
    try:
        with fitz.open(pdf_path) as doc:
            page_count = doc.page_count
        hints = scan_thickness_hints(pdf_path)
    except Exception as exc:                       # noqa: BLE001
        flags.append(f"text_scan_crashed: {type(exc).__name__}")
        logger.exception(f"{pdf_path.name}: section text scan crashed: {exc}")

    if hints:
        flags.append(f"thickness_hints_present: {len(hints)}")

    payload = _build_payload(
        pdf_path    = pdf_path,
        page_count  = page_count,
        section_ids = section_ids,
        hints       = hints,
        flags       = flags,
    )
    payload_path = out_dir / f"{pdf_path.stem}.section.json"
    with open(payload_path, "w") as f:
        json.dump(payload, f, indent=2)

    return SectionExtractResult(
        pdf_path       = pdf_path,
        pdf_stem       = pdf_path.stem,
        page_count     = page_count,
        section_ids    = section_ids,
        thickness_hits = len(hints),
        payload_path   = payload_path,
        error          = None,
        flags          = flags,
    )
