"""
Tenant isolation middleware — automatic query filtering + write protection.

Layer 1: SQLAlchemy do_orm_execute event adds WHERE organization_id = X
          to every ORM SELECT on TenantMixin models.

Layer 3: before_flush event auto-sets organization_id on new TenantMixin
          records when the caller didn't set it explicitly.

Both layers are NO-OPs when g.current_org_id is None (CLI, migrations,
background tasks, unauthenticated requests), except that a new row of a model that names
``__tenant_parent__`` takes its organisation from that parent (organization_from_parent), and is
refused (TenantParentMismatch) when a signed-in organisation differs from the parent's.
"""

import logging

from flask import g, has_app_context
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import with_loader_criteria

from app.extensions import db
from app.models.mixins.core import HybridTenantMixin, TenantMixin

logger = logging.getLogger(__name__)


_TENANT_CONTEXT_KEY = "archie_tenant_context_held"


class TenantParentMismatch(ValueError):
    """A new row would link to a parent record owned by a different organisation."""


def organization_from_parent(session, obj, include_actors=True, refuse_unseen=False):
    """The organisation that owns the record ``obj`` hangs from, or None.

    A model names its parents in ``__tenant_parent__``, a tuple of ``(attribute, parent_table)``
    pairs tried in order: ``(("solution_id", "solutions"),)`` says "my organisation is my
    solution's". Used when nothing signed in supplies one, so a background job or a seeder that
    creates a child row cannot leave it unowned (and, once the table is fenced, cannot fail on it).

    With ``refuse_unseen`` a parent id that names no row this session can read raises
    TenantParentMismatch. Under row-level security another organisation's parent is invisible, so
    "not found" is exactly what a foreign parent looks like and must never count as no evidence.
    """
    for attribute, parent_table in getattr(type(obj), "__tenant_parent__", ()):
        if parent_table == "users" and not include_actors:
            continue  # a creator is an actor, not a parent: it is not evidence of a cross-company link
        parent_id = getattr(obj, attribute, None)
        if parent_id is None:
            continue
        with session.no_autoflush:
            row = session.execute(
                text(f'SELECT organization_id FROM "{parent_table}" WHERE id = :id'),  # nosec B608 - literal table name from the model
                {"id": parent_id},
            ).first()
        if row is None:
            if refuse_unseen:
                raise TenantParentMismatch(
                    f"{type(obj).__name__} names {parent_table} record {parent_id}, "
                    "which this organisation cannot see"
                )
            continue
        if row[0] is not None:
            return row[0]
    return None



def _refuse_foreign_parent(obj, derived, owner):
    """Never let a signed-in or a named organisation win over a parent that belongs to another
    company: the child would link to a record that is not theirs. A parent with no organisation
    (shared, platform) is no evidence either way."""
    if derived is not None and owner is not None and derived != owner:
        raise TenantParentMismatch(
            f"{type(obj).__name__} hangs from a record of organisation {derived}, "
            f"but the row is for organisation {owner}"
        )


def set_database_tenant_context(connection, organization_id):
    """Set the trigger-visible tenant for this transaction only.

    ``set_config(..., true)`` is PostgreSQL's transaction-local equivalent of
    ``SET LOCAL``.  Commit/rollback clears it before a pooled connection can be
    reused by another request.

    Inside ``app.jobs.tenant_safe_job.platform_scope`` the same call also sets
    ``archie.platform_scope`` for this transaction, which the row-level
    security policies admit (see ``migrations/versions/20261008_row_level_security.py``).
    It does that even when there is no organisation, which is the point of a
    platform scope.
    """

    platform = _platform_scope_active()
    # The setting lasts for the whole transaction, so asserting it again on every
    # statement and every flush (hundreds of round trips while the sample company
    # loads) changes nothing. Skip it only when this same transaction already holds
    # exactly this setting and no savepoint is open: a savepoint that rolls back
    # takes a setting made inside it with it.
    transaction = connection.get_transaction()
    cacheable = transaction is not None and connection.get_nested_transaction() is None
    wanted = (platform, organization_id)
    held = connection.info.get(_TENANT_CONTEXT_KEY)
    if cacheable and held is not None and held[0] is transaction and held[1] == wanted:
        return
    if platform:
        connection.execute(text("SELECT set_config('archie.platform_scope', 'on', true)"))
    if organization_id is not None:
        connection.execute(
            text("SELECT set_config('archie.organization_id', :organization_id, true)"),
            {"organization_id": str(organization_id)},
        )
    if cacheable:
        connection.info[_TENANT_CONTEXT_KEY] = (transaction, wanted)


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
        org_id = getattr(g, "current_org_id", None) if has_app_context() else None
        if org_id is not None:
            set_database_tenant_context(session.connection(), org_id)
        for obj in session.new:
            if isinstance(obj, HybridTenantMixin) and getattr(obj, "organization_id", None) is None:
                # A shared-plus-override row never takes the signed-in organisation by default (NULL
                # means shared), but a child of a company's row belongs to that company.
                derived = organization_from_parent(
                    session, obj, include_actors=org_id is None, refuse_unseen=org_id is not None
                )
                _refuse_foreign_parent(obj, derived, org_id)
                if derived is not None:
                    obj.organization_id = derived
                continue
            if not isinstance(obj, TenantMixin):
                continue
            named = getattr(obj, "organization_id", None)
            # The row belongs to the organisation that owns the parent record it hangs from.
            owner = named if named is not None else org_id
            derived = organization_from_parent(
                session,
                obj,
                include_actors=(named or org_id) is None,
                refuse_unseen=owner is not None,
            )
            _refuse_foreign_parent(obj, derived, owner)
            if named is None:
                if org_id is not None:
                    obj.organization_id = org_id
                elif derived is not None:
                    # No signed-in organisation (a job, a seeder, a command).
                    obj.organization_id = derived

    app.logger.info("Tenant isolation filters installed (do_orm_execute + before_flush)")
