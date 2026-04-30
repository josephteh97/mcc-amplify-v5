"""Step 1a verification CLI.

Walks an upload root (file or directory), runs Stage 1 ingest via the
orchestrator, and prints the manifest as JSON.

Usage:
    python scripts/ingest_cli.py [upload_root] [workspace_root]

Defaults walk the symlinked reference fixture and write to data/jobs/_step1a.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Run from repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.orchestrator import run  # noqa: E402
from backend.core.workspace import Workspace  # noqa: E402


def main(
    upload_root:    str = "tests/fixtures/sample_uploaded_documents",
    workspace_root: str = "data/jobs/_step1a",
) -> None:
    ws = Workspace.fresh(Path(workspace_root))
    result = run(workspace=ws, walk_root=Path(upload_root))

    manifest_path = ws.output / "manifest.json"
    with open(manifest_path) as f:
        payload = json.load(f)
    payload["workspace"] = str(result.workspace.root)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main(*sys.argv[1:])
