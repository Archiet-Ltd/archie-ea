"""Record which organisation made each duplicate detection run.

Adds a nullable organisation column, its index and its foreign key to
unified_detection_runs. Existing runs stay NULL and are shown to nobody.
Safe to run twice: each piece is checked before it is added.

Revision ID: 20261115_run_organisation
Revises: 20261115_motivation_retype
Create Date: 2026-11-15
"""
import sqlalchemy as sa
from alembic import op

revision = "20261115_run_organisation"
down_revision = "20261115_motivation_retype"
branch_labels = None
depends_on = None

TABLE = "unified_detection_runs"
INDEX = "ix_unified_detection_runs_organization_id"
FK = "fk_unified_detection_runs_organization_id"


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "organization_id" not in {c["name"] for c in insp.get_columns(TABLE)}:
        op.add_column(TABLE, sa.Column("organization_id", sa.Integer(), nullable=True))
    insp = sa.inspect(bind)
    if INDEX not in {i["name"] for i in insp.get_indexes(TABLE)}:
        op.create_index(INDEX, TABLE, ["organization_id"])
    if not any(
        fk["constrained_columns"] == ["organization_id"] for fk in insp.get_foreign_keys(TABLE)
    ):
        op.create_foreign_key(
            FK, TABLE, "organizations", ["organization_id"], ["id"], ondelete="CASCADE"
        )


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for fk in insp.get_foreign_keys(TABLE):
        if fk["constrained_columns"] == ["organization_id"]:
            op.drop_constraint(fk["name"], TABLE, type_="foreignkey")
    if INDEX in {i["name"] for i in insp.get_indexes(TABLE)}:
        op.drop_index(INDEX, table_name=TABLE)
    if "organization_id" in {c["name"] for c in insp.get_columns(TABLE)}:
        op.drop_column(TABLE, "organization_id")
