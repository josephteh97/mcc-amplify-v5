"""Stage 2 orchestrator — runs all classifier tiers in order, first hit wins.

Tier 4 (LLM judge) lands in Step 2b; today the orchestrator stops after tier 3
and pages that no tier could decide are reported as UNKNOWN with the reason
"deferred to LLM/manual fallback (tier 4 not yet implemented)".
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from backend.classify.content    import classify_content
from backend.classify.rules      import DEFAULT_FILENAME_RULES, FilenameRule, classify_filename
from backend.classify.titleblock import classify_titleblock
from backend.classify.types      import (
    ClassificationResult,
    ClassifierTier,
    DrawingClass,
)
from backend.ingest.ingest       import IngestedFile


@dataclass
class ClassifiedItem:
    pdf_path:    Path
    page_index:  int
    page_hash:   str
    result:      ClassificationResult

    def to_dict(self) -> dict:
        return {
            "pdf":        str(self.pdf_path),
            "page_index": self.page_index,
            "page_hash":  self.page_hash,
            **self.result.to_dict(),
        }


def classify_page(
    pdf_path: Path,
    page_index: int,
    filename_rules: list[FilenameRule] | None = None,
) -> ClassificationResult:
    """Run tiers 1 → 3 against one page; first hit wins."""
    name = pdf_path.name

    r = classify_filename(name, filename_rules)
    if r is not None:
        return r

    r = classify_titleblock(pdf_path, page_index)
    if r is not None:
        return r

    r = classify_content(pdf_path, page_index)
    if r is not None:
        return r

    return ClassificationResult(
        drawing_class = DrawingClass.UNKNOWN,
        tier          = ClassifierTier.UNRESOLVED,
        confidence    = 0.0,
        reason        = "no tier 1–3 signal; deferred to LLM/manual (tier 4 not yet implemented)",
    )


def classify_manifest(
    manifest:       list[IngestedFile],
    filename_rules: list[FilenameRule] | None = None,
) -> list[ClassifiedItem]:
    items: list[ClassifiedItem] = []
    for f in manifest:
        for i, page_hash in enumerate(f.page_hashes):
            result = classify_page(f.pdf_path, i, filename_rules)
            items.append(ClassifiedItem(
                pdf_path   = f.pdf_path,
                page_index = i,
                page_hash  = page_hash,
                result     = result,
            ))
    return items


def summarise(items: list[ClassifiedItem]) -> dict:
    by_class = Counter(i.result.drawing_class.value for i in items)
    by_tier  = Counter(i.result.tier.value          for i in items)
    return {
        "total":    len(items),
        "by_class": dict(by_class),
        "by_tier":  dict(by_tier),
    }


def write_report(items: list[ClassifiedItem], path: Path) -> dict:
    report = {
        "summary": summarise(items),
        "items":   [i.to_dict() for i in items],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(report, f, indent=2)
    logger.info(
        f"Classification report → {path} | "
        + " ".join(f"{k}={v}" for k, v in report["summary"]["by_class"].items())
    )
    return report
