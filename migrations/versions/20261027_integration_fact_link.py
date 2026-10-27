"""The link an applied integration fact wrote.

Adds integration_facts.applied_relationship_id (nullable, indexed): an applied
relationship or team-owner fact records the relationship it created, so the
one citation builder can cite that link by the system and item it was read
from (ADR-002).

Expand step only: one nullable column and its index, both added only when
absent, so a second run is a no-op. The table's row-level security policy is
unchanged.

Revision ID: 20261027_integration_fact_link
Revises: 20261023_integration_facts_rls
Create Date: 2026-10-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261027_integration_fact_link"
down_revision = "20261023_integration_facts_rls"
branch_labels = None
depends_on = None

_INDEX = "ix_integration_facts_applied_relationship_id"


def upgrade():
    inspector = inspect(op.get_bind())
    if "applied_relationship_id" not in {c["name"] for c in inspector.get_columns("integration_facts")}:
        op.add_column("integration_facts", sa.Column("applied_relationship_id", sa.Integer(), nullable=True))
    if _INDEX not in {i["name"] for i in inspector.get_indexes("integration_facts")}:
        op.create_index(_INDEX, "integration_facts", ["applied_relationship_id"])


def downgrade():
    inspector = inspect(op.get_bind())
    if _INDEX in {i["name"] for i in inspector.get_indexes("integration_facts")}:
        op.drop_index(_INDEX, table_name="integration_facts")
    if "applied_relationship_id" in {c["name"] for c in inspector.get_columns("integration_facts")}:
        op.drop_column("integration_facts", "applied_relationship_id")
