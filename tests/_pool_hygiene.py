"""Idle pooled connections must not outlive the test module that opened them.

On the pool-of-one run (ARCHIE_TEST_POOL=one) every ``create_app("testing")``
builds its own engine, and the many test modules that build a module-scoped app
each leave one idle pooled connection behind for the rest of the session. After
about a hundred modules PostgreSQL answers "sorry, too many clients already" and
unrelated tests (test_raid_log, test_query_budget, test_programme_wizard_gate)
fail with OperationalError. Under the default NullPool nothing is retained, so
the ordinary shards never show it.

``track_engines`` remembers every engine that opens a connection;
``dispose_tracked_engines`` replaces their pools, closing the idle connections.
A connection still checked out is not touched: it closes when it is returned.
"""

from __future__ import annotations

import threading
import weakref

from sqlalchemy import event
from sqlalchemy.engine import Engine

_engines: "weakref.WeakSet" = weakref.WeakSet()
_lock = threading.Lock()
_installed = False


def track_engines() -> None:
    """Start remembering engines that open connections. Idempotent."""
    global _installed
    with _lock:
        if _installed:
            return
        _installed = True

    @event.listens_for(Engine, "engine_connect")
    def _remember(connection):
        with _lock:
            _engines.add(connection.engine)


def dispose_tracked_engines() -> int:
    """Close the idle pooled connections of every remembered engine. Returns the
    number of engines disposed."""
    with _lock:
        engines = list(_engines)
        _engines.clear()
    for engine in engines:
        engine.dispose()
    return len(engines)
