"""An owner record can name a person who has no account.

Adds a name, an optional email and a role to the one ownership record, and lets
its user be empty, so an engineering lead or a supplier contact outside the
workspace can be recorded as an owner. A row must name someone: a user, or a
name. Existing rows are untouched. The down step refuses, changing nothing,
while any row names a person without an account.

Imports nothing from ``app`` except the shared schema helpers.

Revision ID: 20261110_owner_named_person
Revises: 20261110_founder_charter
Create Date: 2026-11-10
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

from app.commands.schema_migrations import ContractBlocked, relax_not_null, tighten_not_null

revision = "20261110_owner_named_person"
down_revision = "20261110_founder_charter"
branch_labels = None
depends_on = None

TABLE = "application_owners"
COLUMNS = (
    ("person_name", sa.String(200)),
    ("person_email", sa.String(255)),
    ("person_role", sa.String(100)),
)
CHECK = "ck_application_owner_names_someone"


def upgrade():
    bind = op.get_bind()
    present = {c["name"] for c in inspect(bind).get_columns(TABLE)}
    for name, kind in COLUMNS:
        if name not in present:
            op.add_column(TABLE, sa.Column(name, kind, nullable=True))
    relax_not_null(bind, TABLE, "user_id")
    bind.execute(text(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {CHECK}"))
    bind.execute(text(
        f"ALTER TABLE {TABLE} ADD CONSTRAINT {CHECK} "
        "CHECK (user_id IS NOT NULL OR person_name IS NOT NULL)"
    ))


def downgrade():
    bind = op.get_bind()
    named = bind.execute(text(f"SELECT count(*) FROM {TABLE} WHERE user_id IS NULL")).scalar()
    if named:
        raise ContractBlocked(
            f"{TABLE}: {named} owner(s) are named people without an account, so the "
            "columns cannot be removed without discarding them."
        )
    bind.execute(text(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {CHECK}"))
    tighten_not_null(bind, TABLE, "user_id")
    for name, _kind in COLUMNS:
        op.drop_column(TABLE, name)
