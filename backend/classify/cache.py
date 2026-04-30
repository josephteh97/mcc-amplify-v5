"""SQLite-backed cache for LLM judgments (PLAN.md §5.4).

Keyed by `(page_hash, model)` — the same page judged by a different model is
not cached against the previous one's verdict. The cache lives at the project
root (`data/classifier_cache.sqlite`) so judgments persist across runs and
across jobs that re-upload the same sheet.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CachedJudgment:
    drawing_class: str
    confidence:    float
    reason:        str
    model:         str
    raw:           str
    created_at:    float


_SCHEMA = """
CREATE TABLE IF NOT EXISTS judgments (
    page_hash     TEXT NOT NULL,
    model         TEXT NOT NULL,
    drawing_class TEXT NOT NULL,
    confidence    REAL NOT NULL,
    reason        TEXT NOT NULL,
    raw           TEXT NOT NULL,
    created_at    REAL NOT NULL,
    PRIMARY KEY (page_hash, model)
)
"""


class JudgeCache:
    """Per-project judgment cache. Connections are short-lived per call."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as conn:
            conn.execute(_SCHEMA)

    def get(self, page_hash: str, model: str) -> CachedJudgment | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT drawing_class, confidence, reason, model, raw, created_at "
                "FROM judgments WHERE page_hash = ? AND model = ?",
                (page_hash, model),
            ).fetchone()
        if row is None:
            return None
        return CachedJudgment(*row)

    def put(
        self,
        page_hash:     str,
        model:         str,
        drawing_class: str,
        confidence:    float,
        reason:        str,
        raw:           str,
    ) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO judgments "
                "(page_hash, model, drawing_class, confidence, reason, raw, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (page_hash, model, drawing_class, confidence, reason, raw, time.time()),
            )

    def stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            n = conn.execute("SELECT COUNT(*) FROM judgments").fetchone()[0]
            by_class = dict(
                conn.execute(
                    "SELECT drawing_class, COUNT(*) FROM judgments GROUP BY drawing_class"
                ).fetchall()
            )
        return {"total": n, "by_class": by_class}
