"""A connector sync works whether or not an event loop is already running in the thread.

A web worker, a job runner or a browser driver in the same process can hold a running
loop; asyncio.run refuses there and the sync used to record the connector as failed.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.test_entity_history_trigger import _entity_history_trigger  # noqa: F401
from tests.test_event_log import _install_guards  # noqa: F401
from tests.test_github_connector import (  # noqa: F401
    _proposals,
    connect,
    github,
    org_a,
    org_b,
    sync,
)

pytestmark = pytest.mark.usefixtures("db_session")


def in_a_running_loop(make_coroutine):
    """Call ``make_coroutine()`` as a coroutine on a loop that is running in the calling code's
    own thread of control, whatever else runs in this process (a browser driver may already hold
    a loop in the test thread, which is why asyncio.run is not used here)."""
    import contextvars
    from concurrent.futures import ThreadPoolExecutor

    async def host():
        asyncio.get_running_loop()
        return make_coroutine()

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(contextvars.copy_context().run, asyncio.run, host()).result()


def test_run_coroutine_sync_returns_the_result_with_and_without_a_running_loop():
    from app.services.connector_framework import run_coroutine_sync

    async def answer():
        return 42

    assert run_coroutine_sync(answer()) == 42

    assert in_a_running_loop(lambda: run_coroutine_sync(answer())) == 42


def test_the_github_sync_succeeds_inside_a_running_event_loop(db_session, org_a, github, connect, sync):
    cfg = connect(org_a)

    result = in_a_running_loop(lambda: sync(org_a, cfg))

    assert result["success"] is True, result
    assert result["records_processed"] == 12
    assert len(_proposals(org_a.id)) == 12
