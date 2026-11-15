"""Where a change to the model came from, and which changes belong together.

Every audited write carries a source and a correlation id (see
``app/middleware/audit_middleware.py``). The source is one of ``SOURCES``:
``screen`` for a person using the product, ``import`` for a file or workbook
import, ``connector`` for a sync from an outside system, ``ai_proposal`` for a
proposal a person accepted, and ``system`` for a command or job. Inside a web
request the source defaults to ``screen``; anything that writes the model on a
person's behalf by another route declares itself with ``records_changes_as``
or ``change_source``.

The correlation id groups the rows one action wrote: one per web request, or
one per ``change_source`` block (an import run is one id however many rows it
writes).
"""

from __future__ import annotations

import functools
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

SOURCES = ("screen", "import", "connector", "ai_proposal", "system")

_source: ContextVar[str | None] = ContextVar("change_source", default=None)
_correlation: ContextVar[str | None] = ContextVar("change_correlation", default=None)
_actor: ContextVar[int | None] = ContextVar("change_actor", default=None)
_dating: ContextVar[object | None] = ContextVar("change_dating", default=None)


@contextmanager
def change_source(source: str, *, user_id: int | None = None, correlation_id: str | None = None):
    """Mark every audited write inside the block as coming from ``source``."""
    if source not in SOURCES:
        raise ValueError(f"unknown change source {source!r}")
    outer_correlation = _correlation.get()
    tokens = (
        _source.set(source),
        # Nested blocks stay inside the outer run's correlation id.
        _correlation.set(correlation_id or outer_correlation or uuid.uuid4().hex),
        _actor.set(user_id if user_id is not None else _actor.get()),
    )
    try:
        yield
    finally:
        _actor.reset(tokens[2])
        _correlation.reset(tokens[1])
        _source.reset(tokens[0])


@contextmanager
def dated_when_written(dating):
    """Let ``dating`` give each new audited record the date its first version is written with.

    For the demonstration and sample loads only: they are built in one sitting, so the audit writer asks
    ``dating.date_new_record(table_label, record)`` as it writes a record's first version, and the row is
    sealed once with that date. Nothing already sealed is ever touched.
    """
    token = _dating.set(dating)
    try:
        yield dating
    finally:
        _dating.reset(token)


def records_changes_as(source: str):
    """Decorator form of ``change_source`` for a service entry point."""

    def wrap(func):
        @functools.wraps(func)
        def inner(*args, **kwargs):
            with change_source(source):
                return func(*args, **kwargs)

        inner.records_changes_as = source
        return inner

    return wrap


def current_change_context() -> dict:
    """``{"source", "correlation_id", "user_id", "dating"}`` for the write happening now."""
    source = _source.get()
    correlation = _correlation.get()
    user_id = _actor.get()
    try:
        from flask import g, has_request_context

        if has_request_context():
            source = source or "screen"
            if correlation is None:
                correlation = getattr(g, "_change_correlation_id", None)
                if correlation is None:
                    correlation = g._change_correlation_id = uuid.uuid4().hex
    except Exception:  # noqa: BLE001 - never block a write on bookkeeping
        pass
    return {
        "source": source or "system",
        "correlation_id": correlation or uuid.uuid4().hex,
        "user_id": user_id,
        "dating": _dating.get(),
    }


def declared_source() -> tuple[str, int | None] | None:
    """The (source, user id) of the write happening now, or None when nothing declared one.

    A web request counts as ``screen``; a block opened with ``change_source`` names its own. A bare
    script, test or seed that declares nothing returns None, so a record written there is never given
    an origin it did not have.
    """
    source = _source.get()
    try:
        from flask import has_request_context

        if source is None and has_request_context():
            source = "screen"
    except Exception:  # noqa: BLE001 - never block a write on bookkeeping
        pass
    if source is None:
        return None
    user_id = _actor.get()
    if user_id is None:
        try:
            from flask_login import current_user

            if current_user and current_user.is_authenticated:
                user_id = current_user.id
        except Exception:  # noqa: BLE001
            user_id = None
    return source, user_id
