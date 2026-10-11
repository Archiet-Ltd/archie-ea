"""One trace id through a request, a scheduled job or an assistant run.

A slow answer is followed in the logs by one id: every log record made while
the work runs carries ``request_id`` (the trace id) and ``organization_id`` as
record attributes, and the ``app.trace`` logger writes one ``key=value`` line
per slow query, per model call and per finished request, job or run. The trace
id is returned to the caller in the ``X-Request-ID`` response header.

This is Python's own ``logging``: a log record factory adds the two
attributes to every record, and ``TraceContextFilter`` does the same for a
handler that wants them explicitly. No monitoring service or new dependency;
nothing leaves the process. When the optional OpenTelemetry export is switched
on (``_init_opentelemetry`` in ``app/_bootstrap/services.py``) the request id
is the active span's trace id, so a log line and an exported span join on one
value rather than two.

No personal data: trace lines carry ids, the route pattern (``/users/<id>``,
never the filled-in path or the query string), durations, counts, the model
provider and model name, and the first words of a slow SQL statement (its
parameters are never logged).
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator, Optional

trace_logger = logging.getLogger("app.trace")

REQUEST_ID_HEADER = "X-Request-ID"
_INBOUND_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
DEFAULT_SLOW_QUERY_MS = 200.0


@dataclass
class Trace:
    """The unit of work being traced, and what it has spent so far."""

    trace_id: str
    kind: str                     # request | job | agent-run
    name: str
    started: float = field(default_factory=time.perf_counter)
    queries: int = 0
    query_ms: float = 0.0
    model_calls: int = 0
    model_ms: float = 0.0


_current: ContextVar[Optional[Trace]] = ContextVar("archie_trace", default=None)


def new_trace_id() -> str:
    return uuid.uuid4().hex


def current_trace() -> Optional[Trace]:
    return _current.get()


def current_trace_id() -> Optional[str]:
    trace = _current.get()
    return trace.trace_id if trace is not None else None


def current_organization_id():
    """The tenant of the running work, read from the one place it is kept."""
    try:
        from flask import g, has_app_context

        if has_app_context():
            return getattr(g, "current_org_id", None)
    except Exception:  # noqa: BLE001 - logging must never raise
        pass
    return None


def _enrich(record: logging.LogRecord) -> None:
    if not hasattr(record, "request_id"):
        record.request_id = current_trace_id() or "-"
    if not hasattr(record, "organization_id"):
        org = current_organization_id()
        record.organization_id = org if org is not None else "-"


class TraceContextFilter(logging.Filter):
    """Adds ``request_id`` and ``organization_id`` to a record; never drops one."""

    def filter(self, record: logging.LogRecord) -> bool:
        _enrich(record)
        return True


def _fmt(value) -> str:
    """One token per value: a value with spaces or quotes is double-quoted."""
    if value is None:
        return "-"
    text = str(value)
    if not text or any(ch.isspace() or ch in "\"'=" for ch in text):
        return json.dumps(text)
    return text


def _emit(event: str, trace: Optional[Trace], level: int = logging.INFO, **fields) -> None:
    parts = [
        f"trace={event}",
        f"request_id={_fmt(trace.trace_id if trace else None)}",
        f"org={_fmt(current_organization_id())}",
    ]
    if trace is not None:
        parts.append(f"kind={trace.kind}")
    parts.extend(f"{key}={_fmt(value)}" for key, value in fields.items())
    trace_logger.log(level, " ".join(parts))


@contextmanager
def trace_scope(kind: str, name: str, *, trace_id: Optional[str] = None) -> Iterator[Trace]:
    """Trace a job or an assistant run.

    Inside a request (or another traced unit) the id is inherited, so a job
    run or assistant run started by a request is followed by the request's id.
    A finished line with the counts is written when the block ends.
    """
    parent = _current.get()
    trace = Trace(
        trace_id=trace_id or (parent.trace_id if parent else None) or new_trace_id(),
        kind=kind,
        name=name,
    )
    token = _current.set(trace)
    outcome = "error"
    try:
        yield trace
        outcome = "ok"
    finally:
        _current.reset(token)
        _finish(trace, outcome)
        if parent is not None:
            parent.queries += trace.queries
            parent.query_ms += trace.query_ms
            parent.model_calls += trace.model_calls
            parent.model_ms += trace.model_ms


def traced(kind: str, name: str):
    """Decorator form of ``trace_scope``; a no-op inside a unit of the same kind."""
    import functools

    def _decorate(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            current = _current.get()
            if current is not None and current.kind == kind:
                return func(*args, **kwargs)
            with trace_scope(kind, name):
                return func(*args, **kwargs)

        return wrapper

    return _decorate


def _finish(trace: Trace, outcome, **extra) -> None:
    duration_ms = (time.perf_counter() - trace.started) * 1000
    _emit(
        "finished",
        trace,
        name=trace.name,
        outcome=outcome,
        duration_ms=f"{duration_ms:.1f}",
        queries=trace.queries,
        query_ms=f"{trace.query_ms:.1f}",
        model_calls=trace.model_calls,
        model_ms=f"{trace.model_ms:.1f}",
        **extra,
    )


@contextmanager
def model_call(provider, model) -> Iterator[None]:
    """Trace one call to a language model: provider, model, duration, outcome."""
    trace = _current.get()
    started = time.perf_counter()
    outcome = "error"
    try:
        yield
        outcome = "ok"
    finally:
        elapsed = (time.perf_counter() - started) * 1000
        if trace is not None:
            trace.model_calls += 1
            trace.model_ms += elapsed
        _emit(
            "model-call",
            trace,
            provider=provider,
            model=model,
            outcome=outcome,
            duration_ms=f"{elapsed:.1f}",
        )


def traced_model_call(func):
    """Decorator for the model service's single call entry point."""
    import functools

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        provider = kwargs.get("provider", args[2] if len(args) > 2 else "openai")
        model = kwargs.get("model", args[1] if len(args) > 1 else None)
        with model_call(provider, model):
            return func(*args, **kwargs)

    return wrapper


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #


def _statement_head(statement: str) -> str:
    return " ".join(str(statement).split())[:80]


def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    if _current.get() is not None:
        conn.info.setdefault("archie_trace_started", []).append(time.perf_counter())


def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    trace = _current.get()
    if trace is None:
        return
    stack = conn.info.get("archie_trace_started")
    if not stack:
        return
    elapsed = (time.perf_counter() - stack.pop()) * 1000
    trace.queries += 1
    trace.query_ms += elapsed
    if elapsed >= _slow_query_ms():
        _emit("slow-query", trace, duration_ms=f"{elapsed:.1f}", sql=_statement_head(statement))


def _slow_query_ms() -> float:
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            return float(current_app.config.get("TRACE_SLOW_QUERY_MS", DEFAULT_SLOW_QUERY_MS))
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_SLOW_QUERY_MS


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #


def _inbound_or_new_id() -> str:
    from flask import request

    try:
        from opentelemetry import trace as otel_trace

        context = otel_trace.get_current_span().get_span_context()
        if context.is_valid:
            return format(context.trace_id, "032x")
    except Exception:  # noqa: BLE001 - optional export, absent by default
        pass
    supplied = request.headers.get(REQUEST_ID_HEADER, "")
    return supplied if _INBOUND_ID.match(supplied) else new_trace_id()


def _before_request():
    from flask import g, request

    rule = request.url_rule.rule if request.url_rule is not None else "unmatched"
    trace = Trace(trace_id=_inbound_or_new_id(), kind="request", name=f"{request.method} {rule}")
    g.request_id = trace.trace_id
    g._archie_trace = trace
    g._archie_trace_token = _current.set(trace)


def _after_request(response):
    from flask import g

    trace = getattr(g, "_archie_trace", None)
    if trace is not None:
        response.headers[REQUEST_ID_HEADER] = trace.trace_id
        _finish(trace, "ok" if response.status_code < 500 else "error", status=response.status_code)
        g._archie_trace_finished = True
    return response


def _teardown_request(exc):
    from flask import g

    trace = getattr(g, "_archie_trace", None)
    if trace is not None and not getattr(g, "_archie_trace_finished", False):
        _finish(trace, "error", status=500)
    token = getattr(g, "_archie_trace_token", None)
    if token is not None:
        try:
            _current.reset(token)
        except ValueError:  # reset from another context; clear instead
            _current.set(None)
    for name in ("_archie_trace", "_archie_trace_token", "_archie_trace_finished"):
        g.pop(name, None)


_installed_listeners = False


def install_tracing(app) -> None:
    """Wire request tracing, query timing and log record enrichment."""
    global _installed_listeners

    app.before_request(_before_request)
    app.after_request(_after_request)
    app.teardown_request(_teardown_request)

    if not _installed_listeners:
        from sqlalchemy import event
        from sqlalchemy.engine import Engine

        event.listen(Engine, "before_cursor_execute", _before_cursor_execute)
        event.listen(Engine, "after_cursor_execute", _after_cursor_execute)

        previous_factory = logging.getLogRecordFactory()

        def _record_factory(*args, **kwargs):
            record = previous_factory(*args, **kwargs)
            try:
                _enrich(record)
            except Exception:  # noqa: BLE001 - a log call must never fail
                pass
            return record

        logging.setLogRecordFactory(_record_factory)
        _installed_listeners = True


__all__ = [
    "REQUEST_ID_HEADER",
    "Trace",
    "TraceContextFilter",
    "current_organization_id",
    "current_trace",
    "current_trace_id",
    "install_tracing",
    "model_call",
    "new_trace_id",
    "trace_scope",
    "traced",
    "traced_model_call",
]
