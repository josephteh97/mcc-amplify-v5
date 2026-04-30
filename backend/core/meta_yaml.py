"""Pydantic schema for meta.yaml (PLAN.md §10).

Single source of truth for human-overridable values. Auto-populated by
extractors and the classifier; user edits override per §11.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field


class ClassifierRule(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    pattern: str
    cls: str = Field(alias="class")


class ProjectMeta(BaseModel):
    id: str
    classifier_rules: list[ClassifierRule] = []


class TargetMeta(BaseModel):
    revit_version: int = 2023


class FamiliesMeta(BaseModel):
    column: dict[str, str] = {}
    beam:   dict[str, str] = {}


class LevelMeta(BaseModel):
    rl_mm:  float
    source: str = "manual"


class SlabZone(BaseModel):
    thickness_mm: float
    source:       str = "manual"


class SlabsMeta(BaseModel):
    default_thickness_mm: float = 200.0
    zones: dict[str, SlabZone] = {}


class ReviewMeta(BaseModel):
    unresolved_columns: list[Any] = []
    conflicts:          list[Any] = []


class AliasesMeta(BaseModel):
    """Project-level name normalisation map.

    The structural plan filenames give us short codes (`B1`, `L3`, `RF`),
    while architectural elevations carry full names (`BASEMENT 1`,
    `1ST STOREY`, `ROOF`). Without aliasing, the project reconciler
    would emit them as separate levels with the same RL. The user
    declares the mapping once in meta.yaml::

        aliases:
          levels:
            "BASEMENT 1": B1
            "BASEMENT 2": B2
            "1ST STOREY": L1
            "2ND STOREY": L2
            ROOF:        RF

    Both source-name match and target-name match are honoured (i.e.
    the value side is also recognised — useful when the user prefers
    architectural names as canonical).
    """
    levels: dict[str, str] = {}


class MetaYaml(BaseModel):
    project:  ProjectMeta
    target:   TargetMeta   = TargetMeta()
    families: FamiliesMeta = FamiliesMeta()
    levels:   dict[str, LevelMeta] = {}
    slabs:    SlabsMeta    = SlabsMeta()
    review:   ReviewMeta   = ReviewMeta()
    aliases:  AliasesMeta  = AliasesMeta()

    @classmethod
    def load(cls, path: Path) -> "MetaYaml":
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls.model_validate(data)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.safe_dump(self.model_dump(by_alias=True), f, sort_keys=False)
