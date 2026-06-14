"""WebSocket connection manager — singleton used by FastAPI."""
from __future__ import annotations

import json
import logging
from collections import defaultdict

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections grouped by room ID.

    Room IDs are arbitrary strings — typically a scenario_id or machine_order_id.

    Standard broadcast message shapes
    ----------------------------------
    RESCHEDULE_COMPLETE  : {"type": "RESCHEDULE_COMPLETE",  "scenario_id": str,  "makespan_days": float}
    AI_SUGGESTION_NEW    : {"type": "AI_SUGGESTION_NEW",    "count": int,         "scenario_id": str}
    SCHEDULE_INFEASIBLE  : {"type": "SCHEDULE_INFEASIBLE",  "conflicts": list[str]}
    """

    def __init__(self) -> None:
        # room_id → list of active WebSocket connections
        self.active_connections: dict[str, list[WebSocket]] = defaultdict(list)

    async def connect(self, websocket: WebSocket, room: str) -> None:
        """Accept and register a WebSocket in *room*."""
        await websocket.accept()
        self.active_connections[room].append(websocket)
        logger.info("WebSocket connected — room=%s total=%d", room, len(self.active_connections[room]))

    async def disconnect(self, websocket: WebSocket, room: str) -> None:
        """Remove *websocket* from *room* (safe to call even if already removed)."""
        conns = self.active_connections.get(room, [])
        if websocket in conns:
            conns.remove(websocket)
        logger.info("WebSocket disconnected — room=%s remaining=%d", room, len(conns))

    async def broadcast(self, room: str, message: dict) -> None:
        """Send *message* as JSON text to all connections in *room*.

        Dead connections are silently removed.
        """
        dead: list[WebSocket] = []
        for ws in list(self.active_connections.get(room, [])):
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                dead.append(ws)

        for ws in dead:
            await self.disconnect(ws, room)


# Module-level singleton — imported by main.py and reschedule_engine.py
manager = ConnectionManager()
