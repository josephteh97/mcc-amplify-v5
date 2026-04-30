"""Shared types for the classifier (PLAN.md §5).

The five drawing classes are exhaustive: every uploaded page lands in exactly
one of them (UNKNOWN is the in-flight state for pages no tier could decide
yet — PLAN.md §5.5 resolves them via UI prompt).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DrawingClass(str, Enum):
    STRUCT_PLAN_OVERALL  = "STRUCT_PLAN_OVERALL"
    STRUCT_PLAN_ENLARGED = "STRUCT_PLAN_ENLARGED"
    ELEVATION            = "ELEVATION"
    SECTION              = "SECTION"
    DISCARD              = "DISCARD"
    UNKNOWN              = "UNKNOWN"


class ClassifierTier(str, Enum):
    FILENAME    = "filename"
    TITLE_BLOCK = "title_block"
    CONTENT     = "content"
    LLM         = "llm"
    MANUAL      = "manual"
    UNRESOLVED  = "unresolved"   # no tier decided; needs UI prompt


@dataclass
class ClassificationResult:
    drawing_class: DrawingClass
    tier:          ClassifierTier
    confidence:    float                  # 0.0 .. 1.0
    reason:        str
    signals:       dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "class":      self.drawing_class.value,
            "tier":       self.tier.value,
            "confidence": self.confidence,
            "reason":     self.reason,
            "signals":    self.signals,
        }
