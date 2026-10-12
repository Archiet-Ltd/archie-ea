"""Tenant-scope technical debt with an organisation column.

TechnicalDebt rows belong to exactly one organisation. This migration adds
``technical_debt.organization_id`` (idempotently), backfills it from the
application each row is linked to, tightens it to NOT NULL when no row is
left unattributable, and adds the index and foreign key the tenant mixin
expects.

Every statement is guarded so the revision runs twice (and runs on a schema
built fresh from the models, where the column already exists) without error.

Revision ID: 20261012_techdebt_orgid
Revises: 20261011_notif_rls
Create Date: 2026-10-12
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261012_techdebt_orgid"
down_revision = "20261011_notif_rls"
branch_labels = None
depends_on = None


def _has_table(bind, name: str) -> bool:
    return inspect(bind).has_table(name)


def _has_column(bind, table: str, column: str) -> bool:
    return column in {col["name"] for col in inspect(bind).get_columns(table)}


def _has_index(bind, table: str, index: str) -> bool:
    return index in {idx["name"] for idx in inspect(bind).get_indexes(table)}


def _has_constraint(bind, table: str, constraint: str) -> bool:
    return constraint in {
        c["name"] for c in inspect(bind).get_foreign_keys(table)
    }


def upgrade() -> None:
    bind = op.get_bind()

    if _has_table(bind, "technical_debt"):
        # 1. Add the column, nullable at first so existing rows can be backfilled.
        if not _has_column(bind, "technical_debt", "organization_id"):
            op.add_column(
                "technical_debt",
                sa.Column("organization_id", sa.Integer(), nullable=True),
            )

        # 2. Backfill every row from the organisation of the application it
        #    belongs to. Rows with no application link stay NULL here; the
        #    NOT NULL step below then can only run when nothing was left.
        op.execute(
            """
            UPDATE technical_debt td
            SET organization_id = ac.organization_id
            FROM application_components ac
            WHERE td.application_component_id = ac.id
              AND td.organization_id IS NULL
            """
        )

        # 3. Tighten to NOT NULL when every row now carries an organisation.
        has_null_org = bind.execute(
            sa.text("SELECT 1 FROM technical_debt WHERE organization_id IS NULL LIMIT 1")
        ).first()
        if has_null_org is None:
            op.alter_column(
                "technical_debt",
                "organization_id",
                existing_type=sa.Integer(),
                nullable=False,
            )

        # 4. Index + foreign key, matching the tenant mixin's declarations.
        if not _has_index(bind, "technical_debt", "ix_technical_debt_organization_id"):
            op.create_index(
                "ix_technical_debt_organization_id",
                "technical_debt",
                ["organization_id"],
            )
        if not _has_constraint(bind, "technical_debt", "fk_technical_debt_organization_id"):
            op.create_foreign_key(
                "fk_technical_debt_organization_id",
                "technical_debt",
                "organizations",
                ["organization_id"],
                ["id"],
            )

    # Optional downgrade is intentionally omitted: tenant scoping is the
    # target schema and reversing it would reopen the leak.


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "technical_debt") and _has_column(bind, "technical_debt", "organization_id"):
        with op.batch_alter_table("technical_debt") as batch_op:
            batch_op.drop_constraint(
                "fk_technical_debt_organization_id", type_="foreignkey"
            )
        op.drop_index(
            "ix_technical_debt_organization_id", table_name="technical_debt"
        )
        op.drop_column("technical_debt", "organization_id")