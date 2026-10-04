"""api_settings: support multiple keys per provider via key_label

Revision ID: 20260312_api_settings_multi_key
Revises: 20260311_work_packages_missing_columns
Create Date: 2026-03-12

Changes:
- Add key_label VARCHAR(100) NOT NULL DEFAULT '' to api_settings
- Populate existing rows with key_label = ''
- Drop old unique constraint on provider
- Add composite unique constraint on (provider, key_label)
"""

from alembic import op
import sqlalchemy as sa

revision = "20260312_api_settings_multi_key"
down_revision = "20260311_work_packages_missing_columns"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Add key_label column (nullable first so existing rows are unaffected)
    op.add_column(
        "api_settings",
        sa.Column("key_label", sa.String(100), nullable=True),
    )

    # 2. Populate existing rows with empty string (= "default key" sentinel)
    op.execute("UPDATE api_settings SET key_label = '' WHERE key_label IS NULL")

    # 3. Now make it NOT NULL
    op.alter_column("api_settings", "key_label", nullable=False, server_default="")

    # 4. Drop the old single-column unique constraint on provider.
    #    The constraint name varies by DB; try the standard SQLAlchemy-generated name
    #    and fall back gracefully.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    uq_names = [
        uc["name"]
        for uc in inspector.get_unique_constraints("api_settings")
        if uc.get("column_names") == ["provider"]
    ]
    for name in uq_names:
        op.drop_constraint(name, "api_settings", type_="unique")

    # 5. Add composite unique constraint (provider, key_label)
    op.create_unique_constraint(
        "uq_api_settings_provider_label",
        "api_settings",
        ["provider", "key_label"],
    )


def downgrade():
    op.drop_constraint("uq_api_settings_provider_label", "api_settings", type_="unique")

    # Restore single unique on provider (keep only the first row per provider)
    op.execute("""
        DELETE FROM api_settings
        WHERE id NOT IN (
            SELECT MIN(id) FROM api_settings GROUP BY provider
        )
    """)
    op.create_unique_constraint("uq_api_settings_provider", "api_settings", ["provider"])

    op.drop_column("api_settings", "key_label")
