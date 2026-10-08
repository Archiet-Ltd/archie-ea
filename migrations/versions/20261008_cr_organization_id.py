"""Add organization_id to arb_change_requests (ChangeRequest).

PR 297 defects 2-3 (note 970): ArchitectureChangeImpactService and the
Phase H change-management routes have no column to tenant-fence
ChangeRequest on at all. Backfills from arb_review_item_id's own
organization_id where a change request links to a review item (the
only existing path from a ChangeRequest to a tenant); a request with no
review item has no derivable tenant and is left NULL rather than
guessed.

The column is only tightened to NOT NULL when the backfill actually
reached every row -- on a database with rows that have no review item,
it stays nullable and the application-side fencing treats a NULL
organization_id the same way the rest of the tenant-isolation layer
treats "no tenant context": excluded from a tenant-scoped query, never
returned to the wrong tenant.

Idempotent: guards the column add, the backfill and the NOT NULL step
so a second run is a no-op.

Revision ID: 20261008_cr_organization_id
Revises: 20261007_public_visitor_events
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "20261008_cr_organization_id"
down_revision = "20261007_public_visitor_events"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()

    # Guard against create_all() having created this table already, same
    # to_regclass idiom used elsewhere in this directory.
    if not bind.execute(text("SELECT to_regclass('arb_change_requests')")).scalar():
        # Nothing to migrate on a database where this table does not exist
        # yet -- the model's own create_all/next migration brings it up
        # with the column already on it.
        return

    existing_columns = {
        row[0]
        for row in bind.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'arb_change_requests'"
            )
        )
    }

    if "organization_id" not in existing_columns:
        op.add_column(
            "arb_change_requests",
            sa.Column("organization_id", sa.Integer(), nullable=True),
        )

    if not bind.execute(
        text("SELECT to_regclass('arb_review_items')")
    ).scalar():
        # No review-item table to backfill from on this database -- leave
        # every row NULL rather than guess.
        pass
    else:
        bind.execute(
            text(
                "UPDATE arb_change_requests AS cr "
                "SET organization_id = ri.organization_id "
                "FROM arb_review_items AS ri "
                "WHERE cr.arb_review_item_id = ri.id "
                "AND cr.organization_id IS NULL"
            )
        )

    remaining_null = bind.execute(
        text("SELECT COUNT(*) FROM arb_change_requests WHERE organization_id IS NULL")
    ).scalar()

    if remaining_null == 0:
        # Every row now has a tenant -- safe to enforce it going forward.
        # idempotent: ALTER ... SET NOT NULL is a no-op if already set.
        op.alter_column(
            "arb_change_requests",
            "organization_id",
            existing_type=sa.Integer(),
            nullable=False,
        )

    op.create_index(
        "ix_arb_change_requests_organization_id",
        "arb_change_requests",
        ["organization_id"],
        if_not_exists=True,
    )

    # FK to organizations, matching TenantMixin's own column definition.
    # Guarded: Postgres has no IF NOT EXISTS for ADD CONSTRAINT, so check
    # pg_constraint first.
    fk_exists = bind.execute(
        text(
            "SELECT 1 FROM pg_constraint WHERE conname = "
            "'fk_arb_change_requests_organization_id'"
        )
    ).scalar()
    if not fk_exists:
        op.create_foreign_key(
            "fk_arb_change_requests_organization_id",
            "arb_change_requests",
            "organizations",
            ["organization_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade():
    bind = op.get_bind()
    if not bind.execute(text("SELECT to_regclass('arb_change_requests')")).scalar():
        return

    fk_exists = bind.execute(
        text(
            "SELECT 1 FROM pg_constraint WHERE conname = "
            "'fk_arb_change_requests_organization_id'"
        )
    ).scalar()
    if fk_exists:
        op.drop_constraint(
            "fk_arb_change_requests_organization_id",
            "arb_change_requests",
            type_="foreignkey",
        )
    op.drop_index(
        "ix_arb_change_requests_organization_id",
        table_name="arb_change_requests",
        if_exists=True,
    )
    op.drop_column("arb_change_requests", "organization_id")
