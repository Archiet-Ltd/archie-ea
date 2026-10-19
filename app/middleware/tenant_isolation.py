"""
Tenant isolation middleware — automatic query filtering + write protection.

Layer 1: SQLAlchemy do_orm_execute event adds WHERE organization_id = X
          to every ORM SELECT on TenantMixin models.

Layer 3: before_flush event auto-sets organization_id on new TenantMixin
          records when the caller didn't set it explicitly.

Both layers are NO-OPs when g.current_org_id is None (CLI, migrations,
background tasks, unauthenticated requests).
"""

import contextlib
import logging
import os
import sys
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from flask import current_app, g, has_app_context
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, with_loader_criteria
from sqlalchemy.pool import NullPool, Pool

from app.extensions import db
from app.models.mixins.core import TenantMixin

logger = logging.getLogger(__name__)
# The disagreement lines of ``log`` mode. Their own logger name, so they can be
# found; the lines carry none of the words a deploy log scan rolls back on.
guard_logger = logging.getLogger("app.tenant_context_guard")


# --------------------------------------------------------------------------- #
# ADR 0013: the tenant context of every statement on every connection
#
# This module is the one writer of ``archie.organization_id`` and
# ``archie.platform_scope``. The ambient tenant context is resolved from
# ``bind_tenant`` (explicit) or ``g`` (the request, tenant_scope, platform_scope),
# never from the database; an engine-level hook makes each PostgreSQL connection
# carry it before every statement.
# --------------------------------------------------------------------------- #


class TenantBindingConflict(RuntimeError):
    """An organisation was named that differs from the signed-in one."""


class NoTenantContext(RuntimeError):
    """An independent session or connection was opened with no tenant context."""


class TenantContextUnsupported(RuntimeError):
    """A tenant context cannot be carried on this connection (AUTOCOMMIT)."""


class DatabaseRoleCheckFailed(RuntimeError):
    """The runtime database role could bypass row-level security."""


@dataclass(frozen=True)
class TenantContext:
    organization_id: int | None
    platform: bool


NO_TENANT = TenantContext(None, False)
_explicit: ContextVar[TenantContext | None] = ContextVar(
    "archie_tenant_binding", default=None
)


def _as_org_id(value, *, allow_none=False):
    """A positive ``int`` or an error. ``bool``, strings, zero and negatives are
    never an organisation id."""
    if value is None and allow_none:
        return None
    if type(value) is not int or value <= 0:
        raise ValueError(
            f"{value!r} is not an organisation id (a positive integer is required)"
        )
    return value


def ambient_tenant_context() -> TenantContext:
    """The tenant context in force right now, resolved without the database."""
    bound = _explicit.get()
    if bound is not None:
        return bound
    if has_app_context():
        return TenantContext(
            _as_org_id(getattr(g, "current_org_id", None), allow_none=True),
            bool(getattr(g, "_platform_scope", None)),
        )
    return NO_TENANT


@contextmanager
def bind_tenant(organization_id):
    """Name the organisation explicitly where ``g`` does not carry it.

    An explicit organisation always narrows, never widens: platform scope is off
    inside the block.
    """
    organization_id = _as_org_id(organization_id)
    if has_app_context():
        signed_in = getattr(g, "current_org_id", None)
        if signed_in is not None and signed_in != organization_id:
            raise TenantBindingConflict(
                f"cannot bind organisation {organization_id}: this context is "
                f"signed in to organisation {signed_in}"
            )
    token = _explicit.set(TenantContext(organization_id, False))
    try:
        yield organization_id
    finally:
        _explicit.reset(token)


def run_with_tenant(fn, *args, **kwargs):
    """Capture the caller's tenant context and return a callable that runs
    ``fn(*args, **kwargs)`` under it, for a thread or an executor::

        threading.Thread(target=run_with_tenant(work, item)).start()

    A thread given a bare callable starts with no application context and
    resolves to ``NO_TENANT``, which reads nothing.
    """
    captured = ambient_tenant_context()

    def run():
        if captured == NO_TENANT:
            return fn(*args, **kwargs)
        token = _explicit.set(captured)
        try:
            return fn(*args, **kwargs)
        finally:
            _explicit.reset(token)

    return run


# --------------------------------------------------------------------------- #
# Modes and the engine hook
# --------------------------------------------------------------------------- #

_MODES = ("off", "log", "enforce")
_default_mode = "enforce"
_UNSET = object()
_STATE_KEY = "archie_tenant_applied"
_FACTORY_OPTION = "archie_tenant_factory"
_FOREIGN_OPTION = "archie_foreign_database"
_guard = threading.local()


def normalise_mode(value, default="enforce"):
    value = (value or "").strip().lower()
    return value if value in _MODES else default


def _mode() -> str:
    if has_app_context():
        return normalise_mode(
            current_app.config.get("ARCHIE_TENANT_GUC_MODE"), _default_mode
        )
    return _default_mode


def _selfcheck_enabled() -> bool:
    return has_app_context() and bool(current_app.config.get("ARCHIE_TENANT_GUC_SELFCHECK"))


def _desired_values(context: TenantContext):
    return (
        str(context.organization_id) if context.organization_id is not None else "",
        "on" if context.platform else "",
    )


_WRITE_SQL = (
    "SELECT set_config('archie.organization_id', %s, true), "
    "set_config('archie.platform_scope', %s, true)"
)
_READ_SQL = (
    "SELECT current_setting('archie.organization_id', true), "
    "current_setting('archie.platform_scope', true)"
)


def _dbapi(connection):
    return connection.connection.dbapi_connection


def _write_context(connection, desired: TenantContext) -> None:
    """The one place the tenant settings are written (transaction-local), on a
    separate cursor of the same DBAPI connection so SQLAlchemy's events are not
    re-entered."""
    organization, platform = _desired_values(desired)
    cursor = _dbapi(connection).cursor()
    try:
        cursor.execute(_WRITE_SQL, (organization, platform))
        if _selfcheck_enabled() and _mode() == "enforce":
            cursor.execute(_READ_SQL)
            actual = tuple(value or "" for value in cursor.fetchone())
            if actual != (organization, platform):
                raise AssertionError(
                    f"tenant context self-check: wrote {(organization, platform)!r} "
                    f"but the connection reads back {actual!r}"
                )
    finally:
        cursor.close()
    connection.info[_STATE_KEY] = desired


def _read_context(connection):
    cursor = _dbapi(connection).cursor()
    try:
        cursor.execute(_READ_SQL)
        organization, platform = (value or "" for value in cursor.fetchone())
    finally:
        cursor.close()
    return (int(organization) if organization else None, platform == "on")


def _is_autocommit(connection) -> bool:
    return bool(getattr(_dbapi(connection), "autocommit", False))


def _classify(desired: TenantContext, actual_org, actual_platform):
    """conflict | excess | missing | None (no disagreement)."""
    desired_org, desired_platform = desired.organization_id, desired.platform
    if desired_org == actual_org and desired_platform == actual_platform:
        return None
    if desired_org is not None and actual_org is not None and desired_org != actual_org:
        return "conflict"
    if (actual_org is not None and desired_org is None) or (
        actual_platform and not desired_platform
    ):
        return "excess"
    return "missing"


_log_lock = threading.Lock()
_log_seen: dict = {}
_LOG_INTERVAL_SECONDS = 600


def _caller_site() -> str:
    """The first ``app/`` frame outside this module."""
    frame = sys._getframe(1)
    here = os.path.normcase(__file__)
    while frame is not None:
        filename = frame.f_code.co_filename
        normalised = filename.replace("\\", "/")
        if os.path.normcase(filename) != here and "/app/" in normalised:
            return "app/" + normalised.rsplit("/app/", 1)[1] + f":{frame.f_lineno}"
        frame = frame.f_back
    return "-"


def _log_disagreement(kind, desired, actual_org, actual_platform, site=None):
    """One WARNING line per (class, site) per process per ten minutes, carrying
    the running count. The line never contains ERROR, CRITICAL or Traceback."""
    site = site or _caller_site()
    now = time.monotonic()
    with _log_lock:
        entry = _log_seen.setdefault((kind, site), [None, 0])
        entry[1] += 1
        if entry[0] is not None and now - entry[0] < _LOG_INTERVAL_SECONDS:
            return
        entry[0] = now
        count = entry[1]
    guard_logger.warning(
        "tenant context disagreement class=%s desired_org=%s desired_platform=%s "
        "actual_org=%s actual_platform=%s site=%s count=%d",
        kind,
        desired.organization_id if desired.organization_id is not None else "-",
        "on" if desired.platform else "off",
        actual_org if actual_org is not None else "-",
        "on" if actual_platform else "off",
        site,
        count,
    )


def _reset_disagreement_log() -> None:
    with _log_lock:
        _log_seen.clear()


def _hook_applies(connection) -> bool:
    if connection.dialect.name != "postgresql":
        return False
    return not connection.get_execution_options().get(_FOREIGN_OPTION)


def _before_statement(connection, statement="") -> None:
    if getattr(_guard, "active", False) or not _hook_applies(connection):
        return
    if statement[:22].upper() == "ROLLBACK TO SAVEPOINT ":
        # The statement restores whatever the setting held when the savepoint
        # was taken, which may be an older organisation. Write nothing before
        # it (the write would be undone by it) and forget what was written, so
        # the next statement writes again.
        connection.info.pop(_STATE_KEY, None)
        return
    _guard.active = True
    try:
        factory = bool(connection.get_execution_options().get(_FACTORY_OPTION))
        mode = "enforce" if factory else _mode()
        if mode == "off":
            return
        if mode == "log" and "set_config('archie." in statement:
            # An older writer is about to set the context; compare on the
            # statement after it, when there is something to compare.
            return
        try:
            desired = ambient_tenant_context()
        except ValueError:
            if mode == "enforce":
                raise
            return
        info = connection.info
        if info.get(_STATE_KEY, _UNSET) == desired:
            return
        if _is_autocommit(connection):
            if desired == NO_TENANT:
                return
            if mode == "enforce":
                raise TenantContextUnsupported(
                    "a tenant context cannot be carried on an AUTOCOMMIT connection "
                    f"(context {desired})"
                )
            _log_disagreement("autocommit", desired, None, False)
            info[_STATE_KEY] = desired
            return
        if mode == "enforce":
            _write_context(connection, desired)
            return
        # log: compare what the old writers left, write nothing
        actual_org, actual_platform = _read_context(connection)
        kind = _classify(desired, actual_org, actual_platform)
        if kind is not None:
            _log_disagreement(kind, desired, actual_org, actual_platform)
        info[_STATE_KEY] = desired
    finally:
        _guard.active = False


def _forget_state(connection) -> None:
    if connection.dialect.name == "postgresql":
        connection.info.pop(_STATE_KEY, None)


_SCRUB_SQL = (
    "SELECT set_config('archie.organization_id', '', false), "
    "set_config('archie.platform_scope', '', false)"
)


def _scrub_connection(dbapi_connection) -> None:
    """Overwrite any session-level tenant setting with the deny value."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(_SCRUB_SQL)
    finally:
        cursor.close()
    dbapi_connection.commit()


def _is_postgres_driver(dbapi_connection) -> bool:
    return type(dbapi_connection).__module__.split(".")[0] in ("psycopg2", "psycopg")


_hooks_installed = False


def _install_engine_hooks() -> None:
    """Register the hook once per process on the ``Engine`` class, so it covers
    ``db.engine``, ``Session(db.engine)``, ``engine.connect()``, ``engine.begin()``
    and any engine created later."""
    global _hooks_installed
    if _hooks_installed:
        return
    _hooks_installed = True

    @event.listens_for(Engine, "begin")
    def _tenant_on_begin(connection):
        _forget_state(connection)

    @event.listens_for(Engine, "commit")
    def _tenant_on_commit(connection):
        _forget_state(connection)

    @event.listens_for(Engine, "rollback")
    def _tenant_on_rollback(connection):
        _forget_state(connection)

    @event.listens_for(Engine, "rollback_savepoint")
    def _tenant_on_rollback_savepoint(connection, name, context):
        # Rolling back a savepoint restores whatever the setting was before it,
        # which may be an older organisation.
        _forget_state(connection)

    @event.listens_for(Engine, "before_cursor_execute")
    def _tenant_before_statement(
        connection, cursor, statement, parameters, context, executemany
    ):
        _before_statement(connection, statement)

    @event.listens_for(Engine, "engine_connect")
    def _tenant_mark_foreign(connection):
        if connection.get_execution_options().get(_FOREIGN_OPTION):
            connection.info["archie_foreign"] = True

    @event.listens_for(Pool, "checkout")
    def _tenant_selfcheck_on_checkout(dbapi_connection, connection_record, proxy):
        if not (_selfcheck_enabled() and _mode() == "enforce"):
            return
        if connection_record.info.get("archie_foreign") or not _is_postgres_driver(
            dbapi_connection
        ):
            return
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(_READ_SQL)
            actual = tuple(value or "" for value in cursor.fetchone())
        finally:
            cursor.close()
        dbapi_connection.rollback()
        if actual != ("", ""):
            raise AssertionError(
                f"tenant context self-check: a connection was checked out carrying {actual!r}"
            )

    @event.listens_for(Pool, "checkin")
    def _tenant_scrub_on_checkin(dbapi_connection, connection_record):
        if dbapi_connection is None or connection_record is None:
            return
        if connection_record.info.get("archie_foreign") or not _is_postgres_driver(
            dbapi_connection
        ):
            return
        if _mode() == "off":
            return
        # A pool that closes the connection on return (NullPool) has nothing to
        # scrub: the connection ceases to exist.
        if isinstance(getattr(connection_record, "_ConnectionRecord__pool", None), NullPool):
            return
        connection_record.info.pop(_STATE_KEY, None)
        try:
            _scrub_connection(dbapi_connection)
        except Exception as error:  # a connection not proven clean is never reused
            connection_record.invalidate(error)
            # Not WARNING: the error-aggregation handler opens a connection, and
            # this one is still held by the pool.
            logger.info("tenant scrub failed; connection invalidated: %s", error)


# --------------------------------------------------------------------------- #
# Factories for independent sessions and connections
# --------------------------------------------------------------------------- #


def _require_context(caller: str) -> None:
    if ambient_tenant_context() == NO_TENANT:
        raise NoTenantContext(
            f"{caller} opened a database session with no tenant context: bind an "
            "organisation (organization_id=...) or open a platform_scope with a reason"
        )


@contextmanager
def independent_session(*, organization_id=None, session_cls=Session, **session_kwargs):
    """A session on its own connection that carries the tenant context.

    ``organization_id``, when given, is bound for the life of the block. When it
    is omitted the ambient context must be an organisation or a platform scope;
    otherwise ``NoTenantContext`` names the caller. Context-manager only: the
    session closes at the end of the block.
    """
    caller = _caller_site()
    with contextlib.ExitStack() as stack:
        if organization_id is not None:
            stack.enter_context(bind_tenant(organization_id))
        _require_context(caller)
        engine = db.engine.execution_options(**{_FACTORY_OPTION: True})
        session = session_cls(engine, **session_kwargs)
        try:
            yield session
        finally:
            session.close()


@contextmanager
def independent_connection():
    """A connection of its own that carries the ambient tenant context."""
    caller = _caller_site()
    _require_context(caller)
    engine = db.engine.execution_options(**{_FACTORY_OPTION: True})
    with engine.connect() as connection:
        yield connection


# --------------------------------------------------------------------------- #
# Boot role check
# --------------------------------------------------------------------------- #


def check_database_role(app) -> None:
    """Refuse (``refuse``) or shout (``alert``) when the runtime role could bypass
    row-level security. An inability to run the check logs CRITICAL and never
    refuses."""
    mode = str(app.config.get("ARCHIE_DB_ROLE_CHECK") or "off").strip().lower()
    if mode not in ("alert", "refuse"):
        return
    try:
        with app.app_context():
            engine = db.engine
            if engine.dialect.name != "postgresql":
                return
            with engine.connect() as connection:
                name, superuser, bypass = connection.execute(
                    text(
                        "SELECT current_user, rolsuper, rolbypassrls "
                        "FROM pg_roles WHERE rolname = current_user"
                    )
                ).one()
                owned = connection.execute(
                    text(
                        "SELECT count(*) FROM pg_class c "
                        "JOIN pg_roles r ON r.oid = c.relowner "
                        "WHERE r.rolname = current_user AND c.relkind IN ('r', 'p') "
                        "AND c.relrowsecurity"
                    )
                ).scalar_one()
            engine.dispose()
    except Exception as error:
        logger.critical("tenant role check could not run: %s", error)
        return
    findings = []
    if superuser:
        findings.append("rolsuper=true")
    if bypass:
        findings.append("rolbypassrls=true")
    if owned:
        findings.append(f"owns_fenced_tables={owned}")
    if not findings:
        logger.info(
            "tenant role check passed current_user=%s rolsuper=false "
            "rolbypassrls=false owns_fenced_tables=0",
            name,
        )
        return
    message = f"tenant role check failed current_user={name} " + " ".join(findings)
    logger.critical(message)
    if mode == "refuse":
        raise DatabaseRoleCheckFailed(message)


def set_database_tenant_context(connection, organization_id):
    """Set the trigger-visible tenant for this transaction only (compatibility shim).

    ``set_config(..., true)`` is PostgreSQL's transaction-local equivalent of
    ``SET LOCAL``.  Commit/rollback clears it before a pooled connection can be
    reused by another request.

    In ``enforce`` mode this routes through the engine hook's write function, so
    the hook's record of the connection stays truthful, and refuses a value that
    differs from the ambient tenant context. In ``log`` it writes as it always
    did and reports a difference; in ``off`` it writes as it always did. The old
    writers go once the engine hook has been observed in production.

    Inside ``app.jobs.tenant_safe_job.platform_scope`` the legacy write also sets
    ``archie.platform_scope`` for this transaction, which the row-level
    security policies admit (see ``migrations/versions/20261008_row_level_security.py``).
    """
    mode = _mode()
    if (
        mode == "enforce"
        and connection.dialect.name == "postgresql"
        and _hook_applies(connection)
    ):
        ambient = ambient_tenant_context()
        if organization_id is not None and organization_id != ambient.organization_id:
            raise TenantBindingConflict(
                f"set_database_tenant_context was given organisation {organization_id} "
                f"but the ambient tenant context is {ambient}"
            )
        if connection.info.get(_STATE_KEY, _UNSET) != ambient:
            if _is_autocommit(connection):
                if ambient != NO_TENANT:
                    raise TenantContextUnsupported(
                        "a tenant context cannot be carried on an AUTOCOMMIT connection"
                    )
                return
            _write_context(connection, ambient)
        return
    if mode == "log" and organization_id is not None:
        try:
            ambient = ambient_tenant_context()
        except ValueError:
            ambient = None
        if ambient is not None and organization_id != ambient.organization_id:
            _log_disagreement(
                "conflict", ambient, organization_id, False, site=_caller_site()
            )
    _legacy_write(connection, organization_id)


def _legacy_write(connection, organization_id):
    if _platform_scope_active():
        connection.execute(text("SELECT set_config('archie.platform_scope', 'on', true)"))
    if organization_id is None:
        return
    connection.execute(
        text("SELECT set_config('archie.organization_id', :organization_id, true)"),
        {"organization_id": str(organization_id)},
    )


def _platform_scope_active() -> bool:
    return has_app_context() and bool(getattr(g, "_platform_scope", None))


_engine_hook_installed = False


def _install_platform_scope_engine_hook() -> None:
    """Carry ``archie.platform_scope`` onto connections the ORM session never opens.

    A few runtime paths open ``db.engine.connect()`` or ``Session(db.engine)``
    directly (the capability projection job, the typed-ARB waiver expiry), so the
    session ``after_begin`` listener never sees them. Inside ``platform_scope``
    each transaction they begin gets the same transaction-local setting. Outside
    one this does nothing, and it never sets an organisation.
    """
    global _engine_hook_installed
    if _engine_hook_installed:
        return
    _engine_hook_installed = True

    @event.listens_for(Engine, "begin")
    def _platform_scope_on_begin(connection):
        if connection.dialect.name == "postgresql" and _platform_scope_active():
            connection.exec_driver_sql(
                "SELECT set_config('archie.platform_scope', 'on', true)"
            )


_session_hooks_installed = False


def install_tenant_filter(app):
    """Wire SQLAlchemy event listeners for automatic tenant scoping.

    The listeners are registered on the process-global ``db.session``, so they
    are registered once however many apps are created in this process (the test
    suite creates one per module). Registering them again stacked each hook, and
    every ORM statement then issued one ``set_config`` per copy.
    """
    global _session_hooks_installed

    _install_platform_scope_engine_hook()
    _install_engine_hooks()
    check_database_role(app)

    if _session_hooks_installed:
        return
    _session_hooks_installed = True

    @db.event.listens_for(db.session, "after_begin")
    def _set_database_tenant_after_begin(session, transaction, connection):
        current_org_id = getattr(g, "current_org_id", None)
        if current_org_id is not None or _platform_scope_active():
            set_database_tenant_context(connection, current_org_id)

    @db.event.listens_for(db.session, "do_orm_execute")
    def _add_soft_delete_filter(orm_execute_state):
        # KNOWN REGRESSION closure (see 9cda379): bulk-delete soft-deletes
        # ApplicationComponent via a nullable deleted_at column, but nothing
        # filtered it back out of read paths, so a "deleted" application kept
        # appearing in every list/detail/dashboard/count query. Rather than
        # patch the ~150 call sites individually (a fourth independent count
        # path per file, exactly what the register is asking us to stop
        # doing), filter it once here, the same mechanism the tenant
        # predicate already uses. Applies unconditionally — unlike the tenant
        # predicate below, a soft-deleted row should stay hidden from ORM
        # reads even outside a request context (CLI, scheduler). Recovery
        # (`UPDATE ... SET deleted_at = NULL`) is raw SQL and bypasses the
        # ORM entirely, so it is unaffected.
        if not orm_execute_state.is_select:
            return
        from app.models.application_portfolio import ApplicationComponent
        from app.models.archimate_core import ArchiMateElement

        orm_execute_state.statement = orm_execute_state.statement.options(
            with_loader_criteria(
                ApplicationComponent,
                lambda cls: cls.deleted_at.is_(None),
                include_aliases=True,
            ),
            # fix/qa-register-100: bulk-delete soft-deletes the application's
            # ArchiMate mirror element too (see deleted_at on ArchiMateElement
            # in app/models/models.py) — filter it the same unconditional way
            # so composer palette, relationship matrix, OEF export and AI
            # context all stop seeing it without per-call-site changes.
            with_loader_criteria(
                ArchiMateElement,
                lambda cls: cls.deleted_at.is_(None),
                include_aliases=True,
            ),
        )

    @db.event.listens_for(db.session, "do_orm_execute")
    def _add_tenant_filter(orm_execute_state):
        # Skip if no tenant context (CLI commands, migrations, system tasks)
        if not hasattr(g, "current_org_id") or g.current_org_id is None:
            return

        # The request middleware sets this eagerly.  Reasserting it here also
        # covers tests/workers that establish ``g.current_org_id`` directly
        # and sessions that move to a fresh transaction after a mid-request
        # commit.
        set_database_tenant_context(
            orm_execute_state.session.connection(), g.current_org_id
        )

        # SELECT plus ORM-enabled bulk UPDATE/DELETE. Inserts are handled by
        # before_flush below. This closes ADR-0003 gap 1: the early return for
        # non-SELECT statements meant Model.query.filter(...).update()/.delete()
        # ran with NO tenant predicate even inside an authenticated request, so
        # safety at all 35 bulk-write call sites rested on a scoped read having
        # happened first — an invariant held by convention, not mechanism.
        # with_loader_criteria is honoured by ORM-enabled UPDATE and DELETE
        # (SQLAlchemy 1.4+), so the same option covers all three.
        if not (
            orm_execute_state.is_select
            or orm_execute_state.is_update
            or orm_execute_state.is_delete
        ):
            return

        # Add WHERE organization_id = X to all TenantMixin models
        orm_execute_state.statement = orm_execute_state.statement.options(
            with_loader_criteria(
                TenantMixin,
                lambda cls: cls.organization_id == g.current_org_id,
                include_aliases=True,
            ),
        )

    @db.event.listens_for(db.session, "before_flush")
    def _set_tenant_on_new(session, flush_context, instances):
        if not hasattr(g, "current_org_id") or g.current_org_id is None:
            return
        set_database_tenant_context(session.connection(), g.current_org_id)
        for obj in session.new:
            if isinstance(obj, TenantMixin) and getattr(obj, "organization_id", None) is None:
                obj.organization_id = g.current_org_id

    app.logger.info("Tenant isolation filters installed (do_orm_execute + before_flush)")
