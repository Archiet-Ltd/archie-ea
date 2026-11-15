"""Give every existing capability support row its relationship.

Support rows written before support became one relationship have no relationship, so the readers
that follow the relationship would show them as nothing. For each such row this finds or makes the
application's and the capability's elements and records the support through the one writer, which
takes the row over. A second row for a pair that already has one is removed after its facts are
copied across. The counts before and after are printed. Running it again changes nothing.

The downgrade leaves the relationships in place: they are the fact, and removing them would
remove the support.

Revision ID: 20261114_capsupport_backfill
Revises: 20261114_capsupport_projection
Create Date: 2026-11-14
"""
from alembic import op
from sqlalchemy import text
from sqlalchemy.orm import Session

revision = "20261114_capsupport_backfill"
down_revision = "20261114_capsupport_projection"
branch_labels = None
depends_on = None

_TABLE = "unified_application_capability_mapping"


def upgrade():
    from app import db
    from app.services.capability_support import backfill_unprojected_support

    bind = op.get_bind()
    present = bind.execute(text("SELECT to_regclass('public." + _TABLE + "') IS NOT NULL")).scalar()
    if not present:
        return
    if bind.dialect.name == "postgresql":
        bind.execute(text("SELECT set_config('archie.platform_scope', 'on', true)"))
    # The writer works through the application's session; point that session at this revision's connection.
    registry = db.session.registry
    previous = registry() if registry.has() else None
    own = Session(bind=bind)
    registry.set(own)
    try:
        counts = backfill_unprojected_support()
        own.flush()
    finally:
        registry.clear()
        if previous is not None:
            registry.set(previous)
    print(
        "capability support backfill: rows without a relationship before "
        f"{counts['before']}, linked {counts['linked']}, duplicates removed {counts['duplicates_removed']}, "
        f"left {counts['after']}"
    )


def downgrade():
    pass
