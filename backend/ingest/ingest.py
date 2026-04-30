"""Stage 1 — Ingest (PLAN.md §4).

Walk the uploaded list (loose multi-file, no zip handling), page-fingerprint
each PDF page so re-uploads of the same sheet dedupe automatically.

The fingerprint is `sha256(page content stream + media box repr)`. Excluding
the page's surrounding PDF metadata keeps the hash deterministic across
re-saves; including media-box keeps geometrically-different pages with
identical drawing operators distinct.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # type: ignore[import-untyped]


@dataclass(frozen=True)
class IngestedFile:
    pdf_path:    Path
    n_pages:     int
    page_hashes: tuple[str, ...] = field(default_factory=tuple)


def _fingerprint_pages(pdf_path: Path) -> list[str]:
    hashes: list[str] = []
    with fitz.open(pdf_path) as src:
        for i in range(src.page_count):
            page = src[i]
            h = hashlib.sha256()
            h.update(page.read_contents() or b"")
            h.update(repr(tuple(page.mediabox)).encode())
            hashes.append(h.hexdigest())
    return hashes


def walk_uploads(root: Path) -> list[Path]:
    """Flatten a directory (or accept a single PDF) into a sorted PDF list.

    PLAN.md §4: the build agent must not rely on folder structure. Real uploads
    may be flat or nested; both must produce identical output downstream.
    """
    if root.is_file() and root.suffix.lower() == ".pdf":
        return [root]
    return sorted(p for p in root.rglob("*.pdf") if p.is_file())


def ingest(pdf_paths: list[Path]) -> list[IngestedFile]:
    out: list[IngestedFile] = []
    for p in pdf_paths:
        hashes = _fingerprint_pages(p)
        out.append(IngestedFile(
            pdf_path    = p.resolve(),
            n_pages     = len(hashes),
            page_hashes = tuple(hashes),
        ))
    return out
