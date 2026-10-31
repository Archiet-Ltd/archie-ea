"""unified_capabilities provenance unique index

Revision ID: 20261031_provenance_index
Revises: 20261031_fw_adopt_source
Create Date: 2026-10-31 00:00:00

A database built from migrations had no unique index on
unified_capabilities (source_table, source_id), so the capability projection
skipped every new capability and the pickers came back empty. This creates it
with the same clean-up and statement the apply-unified-capability-provenance-
migration command runs. Capabilities that share a source keep the older row's
source; the later rows lose only the source values and none is deleted. The
count is logged. Downgrade drops the index; cleared source values are not
restored.
"""
from alembic import op
from sqlalchemy import text

revision = "20261031_provenance_index"
down_revision = "20261031_fw_adopt_source"
branch_labels = None
depends_on = None


def upgrade():
    from app.commands.apply_unified_capability_provenance_migration import (
        ensure_provenance_index,
    )

    connection = op.get_bind()
    if connection.execute(text("SELECT to_regclass('unified_capabilities')")).scalar() is None:
        return
    ensure_provenance_index(connection)


def downgrade():
    op.execute("DROP INDEX IF EXISTS uq_unified_capabilities_provenance")
