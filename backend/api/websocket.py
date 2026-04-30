"""WebSocket connection manager + event broadcaster.

Each job_id has a set of subscriber sockets. Broadcasts replay every event the
job has produced so far, then live events as they happen.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import WebSocket
from loguru import logger


class EventBroadcaster:
    """Per-job WebSocket fan-out.

    A subscriber connecting mid-run gets the full event backlog first so it
    doesn't miss the early stages.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, set[WebSocket]] = {}

    async def subscribe(self, job_id: str, ws: WebSocket, backlog: list[dict[str, Any]]) -> None:
        await ws.accept()
        self._subscribers.setdefault(job_id, set()).add(ws)
        logger.info(f"WS subscribed: job={job_id} (subs={len(self._subscribers[job_id])})")
        for event in backlog:
            await ws.send_text(json.dumps(event))

    def unsubscribe(self, job_id: str, ws: WebSocket) -> None:
        subs = self._subscribers.get(job_id)
        if subs is None:
            return
        subs.discard(ws)
        if not subs:
            del self._subscribers[job_id]

    async def send(self, job_id: str, event: dict[str, Any]) -> None:
        subs = self._subscribers.get(job_id)
        if not subs:
            return
        msg = json.dumps(event, default=str)
        dead: list[WebSocket] = []
        for ws in subs:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            subs.discard(ws)


# Module-level singleton, paired with job_store.
broadcaster = EventBroadcaster()
