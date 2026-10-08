"""
Tenant isolation middleware — automatic query filtering + write protection.

Layer 1: SQLAlchemy do_orm_execute event adds WHERE organization_id = X
          to every ORM SELECT on TenantMixin models.

Layer 3: before_flush event auto-sets organization_id on new TenantMixin
          records when the caller didn't set it explicitly.

Both layers are NO-OPs when g.current_org_id is None (CLI, migrations,
background tasks, unauthenticated requests).
"""

import logging

from flask import g
from sqlalchemy import text
from sqlalchemy.orm import with_loader_criteria

from app.extensions import db
from app.models.mixins.core import TenantMixin

logger = logging.getLogger(__name__)


def set_database_tenant_context(connection, organization_id):
    """Set the trigger-visible tenant for this transaction only.

    ``set_config(..., true)`` is PostgreSQL's transaction-local equivalent of
    ``SET LOCAL``.  Commit/rollback clears it before a pooled connection can be
    reused by another request.
    """

    if organization_id is None:
        return
    connection.execute(
        text("SELECT set_config('archie.organization_id', :organization_id, true)"),
        {"organization_id": str(organization_id)},
    )


def install_tenant_filter(app):
    """Wire SQLAlchemy event listeners for automatic tenant scoping."""

    @db.event.listens_for(db.session, "after_begin")
    def _set_database_tenant_after_begin(session, transaction, connection):
        if hasattr(g, "current_org_id") and g.current_org_id is not None:
            set_database_tenant_context(connection, g.current_org_id)

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
