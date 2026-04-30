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


class MetaYaml(BaseModel):
    project:  ProjectMeta
    target:   TargetMeta   = TargetMeta()
    families: FamiliesMeta = FamiliesMeta()
    levels:   dict[str, LevelMeta] = {}
    slabs:    SlabsMeta    = SlabsMeta()
    review:   ReviewMeta   = ReviewMeta()

    @classmethod
    def load(cls, path: Path) -> "MetaYaml":
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls.model_validate(data)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.safe_dump(self.model_dump(by_alias=True), f, sort_keys=False)
