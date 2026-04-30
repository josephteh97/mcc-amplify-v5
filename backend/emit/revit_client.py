"""Headless Windows-Revit client (v4 contract).

Two integration modes — selected via the ``REVIT_MODE`` env var.

  - ``http``  (default) — POST the recipe JSON to
    ``${WINDOWS_REVIT_SERVER}/build-model``. The
    ``RevitModelBuilderAddin.dll`` runs as ``IExternalApplication``
    inside an open Revit 2023 instance on the Windows host and replies
    with the binary ``.rvt``. Validates the response is a real Revit
    OLE container (header ``D0 CF 11 E0``) before saving.

  - ``file`` — write ``pending.json`` to ``${REVIT_SHARED_DIR}`` (a
    network share or SSH-mounted folder visible from both hosts), poll
    for ``{job_id}.done`` + ``{job_id}.rvt`` from the Revit Macro
    Manager script.

Best-effort policy: server unreachable / timeout / corrupt response is
logged as a warning and surfaced in the EmitResult, but doesn't fail
the upstream pipeline. The transaction.json + GLTF are always emitted
regardless of Windows reachability so an operator can rerun the build
later by hand-feeding the recipe to Revit.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from loguru import logger


# Revit OLE compound-document magic bytes — every legitimate .rvt starts here.
_RVT_MAGIC = b"\xd0\xcf\x11\xe0"

DEFAULT_HTTP_URL    = "http://localhost:5000"
DEFAULT_SHARED_DIR  = "/mnt/revit_output"
DEFAULT_TIMEOUT_S   = 300
POLL_INTERVAL_S     = 2


@dataclass(frozen=True)
class RvtBuildResult:
    job_id:        str
    rvt_path:      Path | None
    warnings:      list[str]
    mode:          str
    server_url:    str | None
    error:         str | None


class RevitClient:
    """Send transaction recipes to the Windows Revit add-in / macro."""

    def __init__(
        self,
        server_url:  str | None = None,
        api_key:     str | None = None,
        timeout_s:   int  | None = None,
        mode:        str  | None = None,
        shared_dir:  Path | None = None,
    ):
        self.server_url = server_url or os.getenv("WINDOWS_REVIT_SERVER", DEFAULT_HTTP_URL)
        self.api_key    = api_key    or os.getenv("REVIT_SERVER_API_KEY", "")
        self.timeout    = timeout_s  or int(os.getenv("REVIT_TIMEOUT", str(DEFAULT_TIMEOUT_S)))
        self.mode       = (mode or os.getenv("REVIT_MODE", "http")).lower()
        self.shared_dir = shared_dir or Path(os.getenv("REVIT_SHARED_DIR", DEFAULT_SHARED_DIR))

    # ── Health ────────────────────────────────────────────────────────────────

    def is_healthy(self) -> bool:
        if self.mode == "file":
            return self.shared_dir.exists()
        try:
            r = httpx.get(f"{self.server_url}/health", timeout=5.0)
            return r.status_code == 200
        except Exception as exc:                       # noqa: BLE001
            logger.debug(f"Revit health-check failed: {exc}")
            return False

    # ── Build ─────────────────────────────────────────────────────────────────

    def build(
        self,
        transaction_path: Path,
        job_id:           str,
        out_dir:          Path,
        pdf_filename:     str = "",
    ) -> RvtBuildResult:
        """Dispatch one recipe to the Windows add-in / macro.

        Always returns; never raises into the orchestrator. Failures
        surface as ``error != None``; ``rvt_path`` is None on failure.
        """
        try:
            if self.mode == "file":
                return self._build_file_drop(transaction_path, job_id, out_dir)
            return self._build_http(transaction_path, job_id, out_dir, pdf_filename)
        except Exception as exc:                       # noqa: BLE001
            logger.warning(f"Revit build failed for {job_id}: {exc}")
            return RvtBuildResult(
                job_id     = job_id,
                rvt_path   = None,
                warnings   = [],
                mode       = self.mode,
                server_url = self.server_url if self.mode == "http" else None,
                error      = f"{type(exc).__name__}: {exc}",
            )

    # ── HTTP mode ─────────────────────────────────────────────────────────────

    def _build_http(
        self,
        transaction_path: Path,
        job_id:           str,
        out_dir:          Path,
        pdf_filename:     str,
    ) -> RvtBuildResult:
        transaction_json = transaction_path.read_text()
        logger.info(f"Revit HTTP build → {self.server_url}/build-model  job={job_id}")
        with httpx.Client(timeout=self.timeout) as client:
            r = client.post(
                f"{self.server_url}/build-model",
                json    = {
                    "job_id":           job_id,
                    "pdf_filename":     pdf_filename,
                    "transaction_json": transaction_json,
                },
                headers = {"X-API-Key": self.api_key} if self.api_key else {},
            )
        if r.status_code != 200:
            raise RuntimeError(f"Revit server {r.status_code}: {r.text[:200]}")

        content = r.content
        if len(content) < 8 or content[:4] != _RVT_MAGIC:
            raise RuntimeError(
                f"server returned {len(content)} bytes that don't look like .rvt "
                f"(prefix={content[:8]!r})"
            )

        out_dir.mkdir(parents=True, exist_ok=True)
        rvt_path = out_dir / f"{job_id}.rvt"
        rvt_path.write_bytes(content)

        warnings = self._parse_warnings_header(r.headers)
        if warnings:
            logger.warning(f"Revit returned {len(warnings)} warning(s) for {job_id}")
            for w in warnings:
                logger.warning(f"  • {w}")
        else:
            logger.info(f"Revit build clean: {rvt_path} ({len(content):,} bytes)")
        return RvtBuildResult(
            job_id     = job_id,
            rvt_path   = rvt_path,
            warnings   = warnings,
            mode       = "http",
            server_url = self.server_url,
            error      = None,
        )

    @staticmethod
    def _parse_warnings_header(headers) -> list[str]:
        """Decode v1 (string list) or v2 (object list) X-Revit-Warnings header."""
        import json as _json
        raw = headers.get("x-revit-warnings", "[]")
        version = headers.get("x-revit-warnings-version", "1")
        try:
            parsed = _json.loads(raw)
        except Exception:                              # noqa: BLE001
            return []
        if not isinstance(parsed, list):
            return []
        if version == "2":
            return [str(w.get("text", "")) if isinstance(w, dict) else str(w)
                    for w in parsed]
        return [str(w) for w in parsed]

    # ── File-drop mode ───────────────────────────────────────────────────────

    def _build_file_drop(
        self,
        transaction_path: Path,
        job_id:           str,
        out_dir:          Path,
    ) -> RvtBuildResult:
        self.shared_dir.mkdir(parents=True, exist_ok=True)
        pending = self.shared_dir / "pending.json"
        done    = self.shared_dir / f"{job_id}.done"
        rvt_src = self.shared_dir / f"{job_id}.rvt"

        pending.write_text(transaction_path.read_text(), encoding="utf-8")
        logger.info(f"Revit file-drop → {pending}  waiting for {rvt_src}")

        elapsed = 0
        while elapsed < self.timeout:
            time.sleep(POLL_INTERVAL_S)
            elapsed += POLL_INTERVAL_S
            if done.exists() or rvt_src.exists():
                break
        else:
            raise TimeoutError(
                f"Revit macro did not produce {rvt_src.name} within {self.timeout}s"
            )

        try:
            done.unlink(missing_ok=True)
        except Exception:                              # noqa: BLE001
            pass

        out_dir.mkdir(parents=True, exist_ok=True)
        rvt_path = out_dir / f"{job_id}.rvt"
        rvt_path.write_bytes(rvt_src.read_bytes())
        logger.info(f"Revit file-drop completed: {rvt_path}")
        return RvtBuildResult(
            job_id     = job_id,
            rvt_path   = rvt_path,
            warnings   = [],
            mode       = "file",
            server_url = None,
            error      = None,
        )
