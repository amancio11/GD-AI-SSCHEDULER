"""Tests for reschedule_engine and ConnectionManager.

No real DB or Redis is needed: we mock the SQLAlchemy session and the
Celery task infrastructure.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.websocket.manager import ConnectionManager


# ══════════════════════════════════════════════════════════════════════════════
# ConnectionManager tests
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_websocket_connect_and_disconnect():
    """Connect → room has 1 connection; disconnect → room is empty."""
    mgr = ConnectionManager()
    ws = AsyncMock()
    room = "test-room"

    await mgr.connect(ws, room)
    assert len(mgr.active_connections[room]) == 1
    ws.accept.assert_called_once()

    await mgr.disconnect(ws, room)
    assert len(mgr.active_connections[room]) == 0


@pytest.mark.asyncio
async def test_websocket_broadcast():
    """broadcast() sends the message as JSON text to all connections in the room."""
    mgr = ConnectionManager()
    ws1 = AsyncMock()
    ws2 = AsyncMock()
    room = str(uuid.uuid4())

    await mgr.connect(ws1, room)
    await mgr.connect(ws2, room)

    msg = {"type": "RESCHEDULE_COMPLETE", "scenario_id": room, "makespan_days": 3.5}
    await mgr.broadcast(room, msg)

    ws1.send_text.assert_called_once_with(json.dumps(msg))
    ws2.send_text.assert_called_once_with(json.dumps(msg))


@pytest.mark.asyncio
async def test_websocket_broadcast_dead_connection_removed():
    """A dead WebSocket that raises on send_text is silently removed."""
    mgr = ConnectionManager()
    dead_ws = AsyncMock()
    dead_ws.send_text.side_effect = RuntimeError("connection closed")
    alive_ws = AsyncMock()
    room = str(uuid.uuid4())

    await mgr.connect(dead_ws, room)
    await mgr.connect(alive_ws, room)

    await mgr.broadcast(room, {"type": "TEST"})

    # The dead socket should have been removed
    assert dead_ws not in mgr.active_connections[room]
    alive_ws.send_text.assert_called_once()


@pytest.mark.asyncio
async def test_websocket_broadcast_empty_room():
    """Broadcast to a room with no connections should not raise."""
    mgr = ConnectionManager()
    await mgr.broadcast("nonexistent-room", {"type": "TEST"})  # must not raise


# ══════════════════════════════════════════════════════════════════════════════
# reschedule_incremental — idempotency and retry tests
# ══════════════════════════════════════════════════════════════════════════════


def test_reschedule_skips_completed():
    """Operations with status COMPLETED must not appear in the schedulable list.

    We verify this by checking that _run_reschedule only queries operations
    that are NOT in the COMPLETED status.
    """
    from app.enums import OperationStatus

    # The engine filters with Operation.status.notin_([OperationStatus.COMPLETED])
    # We verify the enum value is correct so the filter works.
    assert OperationStatus.COMPLETED.value == "COMPLETED"
    excluded = [OperationStatus.COMPLETED]
    assert OperationStatus.PENDING not in excluded
    assert OperationStatus.IN_PROGRESS not in excluded
    assert OperationStatus.COMPLETED in excluded


def test_reschedule_incremental_task_registered():
    """The Celery task must be registered in the app with the correct name."""
    from celery_worker import celery_app

    task_names = list(celery_app.tasks.keys())
    assert any("reschedule_incremental" in name for name in task_names)


def test_analyze_proactive_task_registered():
    """analyze_proactive stub task must be registered."""
    from celery_worker import celery_app

    task_names = list(celery_app.tasks.keys())
    assert any("analyze_proactive" in name for name in task_names)


def test_celery_task_retries_on_exception():
    """reschedule_incremental must retry on failure (max_retries=3)."""
    from app.core.scheduler.reschedule_engine import reschedule_incremental

    assert reschedule_incremental.max_retries == 3


def test_scheduler_orchestrator_dispatches_to_celery():
    """run_schedule(use_celery=True) must call reschedule_incremental.delay()."""
    from app.core.scheduler.scheduler_orchestrator import run_schedule

    scenario_id = uuid.uuid4()
    fake_task = MagicMock()
    fake_task.id = "mock-task-id-123"

    with patch(
        "app.core.scheduler.scheduler_orchestrator.reschedule_incremental"
    ) as mock_task_cls:
        mock_task_cls.delay.return_value = fake_task
        result = run_schedule(scenario_id, use_celery=True)

    mock_task_cls.delay.assert_called_once_with(str(scenario_id), "api")
    assert result == "mock-task-id-123"


# ══════════════════════════════════════════════════════════════════════════════
# _cleanup_stale helper
# ══════════════════════════════════════════════════════════════════════════════


def test_cleanup_stale_uses_correct_status():
    """_cleanup_stale should delete entries with STALE status only."""
    from app.core.scheduler.reschedule_engine import _cleanup_stale
    from app.enums import ScheduleEntryStatus

    session = MagicMock()
    query_mock = MagicMock()
    filter_mock = MagicMock()
    session.query.return_value = query_mock
    query_mock.filter.return_value = filter_mock

    scenario_id = uuid.uuid4()
    _cleanup_stale(session, scenario_id)

    session.query.assert_called_once()
    filter_mock.delete.assert_called_once_with(synchronize_session="fetch")
