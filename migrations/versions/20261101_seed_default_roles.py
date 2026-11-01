"""default roles exist on a database built from migrations

Revision ID: 20261101_seed_default_roles
Revises: 20261031_provenance_index
Create Date: 2026-11-01 00:00:00

A database built from migrations alone had no rows in roles, so a first
self-serve owner signed up with no role and /account/welcome answered 403.
The roles were created only by a command run after the schema step, which a
new install, a restore drill or a new environment never ran. This inserts
the roles in app.models.user.DEFAULT_ROLES, the same list the command uses,
and only the ones that are missing: an existing role, its permissions and
every assignment are left as they are. The granular permissions table has no
seed list anywhere, so nothing is inserted into it.

Downgrade does nothing. A role may by then be assigned to users, and removing
it would leave them with none; the rows are harmless to keep.
"""
from alembic import op
from sqlalchemy import text

revision = "20261101_seed_default_roles"
down_revision = "20261031_provenance_index"
branch_labels = None
depends_on = None


def upgrade():
    from app.models.user import DEFAULT_ROLES

    connection = op.get_bind()
    if connection.execute(text("SELECT to_regclass('roles')")).scalar() is None:
        return
    for name, (permissions, index, is_default) in DEFAULT_ROLES.items():
        connection.execute(
            text(
                'INSERT INTO roles (name, "index", "default", permissions) '
                "SELECT CAST(:name AS varchar), :index, :is_default, :permissions "
                "WHERE NOT EXISTS (SELECT 1 FROM roles WHERE name = :name)"
            ),
            {"name": name, "index": index, "is_default": is_default, "permissions": permissions},
        )


def downgrade():
    pass
