"""Job-scoped, ephemeral workspace.

PLAN.md §2: one job = one upload = one output. Rerun = full reprocess; the
job's working directory is wiped on rerun.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Workspace:
    root: Path

    @property
    def uploads(self)   -> Path: return self.root / "uploads"
    @property
    def extracted(self) -> Path: return self.root / "extracted"
    @property
    def output(self)    -> Path: return self.root / "output"
    @property
    def meta_path(self) -> Path: return self.root / "meta.yaml"

    @classmethod
    def fresh(cls, root: Path) -> "Workspace":
        if root.exists():
            shutil.rmtree(root)
        ws = cls(root=root)
        for d in (ws.uploads, ws.extracted, ws.output):
            d.mkdir(parents=True)
        return ws
