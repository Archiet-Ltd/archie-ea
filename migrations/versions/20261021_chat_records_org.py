"""Chat threads and chat history carry the organisation they were made in.

``conversation_threads`` and ``chat_message_embeddings`` (plain chat history,
saved sessions and the page-guide history all live in the second) were keyed
only to the user, so a person who belongs to two organisations saw the records
made in one while the other was active. Each now carries ``organization_id``.
Messages of a thread (``conversation_messages``) are reached only through their
thread and keep no column of their own.

Backfill: a row takes its owner's home organisation (``users.organization_id``).
A row whose owner has no home organisation, or a history row with no owner,
has no derivable organisation: none is guessed, it stays NULL and matches no
organisation's filter. Both columns therefore stay nullable (the model declares
them nullable, and a user with no home organisation can still chat); the number
of unattributed rows is printed.

Row-level security is not enabled here; it follows the tenant-context work
(ADR 0013) so the policy lands with the connection-level setting it needs.

Idempotent: ``reconcile-schema`` may already have added the column by the time
this runs, so every step checks before it acts.

Revision ID: 20261021_chat_records_org
Revises: 20261020_change_feed_offsets
Create Date: 2026-10-21
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "20261021_chat_records_org"
down_revision = "20261020_change_feed_offsets"
branch_labels = None
depends_on = None

TABLES = ("conversation_threads", "chat_message_embeddings")


def _present(bind, table):
    return bool(bind.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}).scalar())


def _has_column(bind, table, column):
    return bool(
        bind.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = :t AND column_name = :c)"
            ),
            {"t": table, "c": column},
        ).scalar()
    )


def _fk_name(bind, table, column):
    return bind.execute(
        text(
            "SELECT con.conname FROM pg_constraint con "
            "JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey) "
            "WHERE con.contype = 'f' AND con.conrelid = to_regclass(:t) "
            "AND att.attname = :c"
        ),
        {"t": f"public.{table}", "c": column},
    ).scalar()


def upgrade():
    bind = op.get_bind()
    for table in TABLES:
        if not _present(bind, table):
            continue
        if not _has_column(bind, table, "organization_id"):
            op.add_column(table, sa.Column("organization_id", sa.Integer(), nullable=True))
        if _present(bind, "users") and _has_column(bind, table, "user_id"):
            bind.execute(
                text(
                    f"UPDATE {table} AS t SET organization_id = u.organization_id "  # nosec B608 - literal names
                    "FROM users AS u WHERE t.user_id = u.id "
                    "AND t.organization_id IS NULL AND u.organization_id IS NOT NULL"
                )
            )
        remaining = bind.execute(text(f"SELECT COUNT(*) FROM {table} WHERE organization_id IS NULL")).scalar()  # nosec B608 - literal names
        if remaining:
            print(f"chat org fence: {remaining} row(s) in {table} have no derivable organisation; left NULL")
        op.create_index(f"ix_{table}_organization_id", table, ["organization_id"], if_not_exists=True)
        if _fk_name(bind, table, "organization_id") is None:
            op.create_foreign_key(
                f"fk_{table}_organization_id", table, "organizations", ["organization_id"], ["id"], ondelete="CASCADE"
            )


def downgrade():
    bind = op.get_bind()
    for table in TABLES:
        if not _present(bind, table) or not _has_column(bind, table, "organization_id"):
            continue
        fk = _fk_name(bind, table, "organization_id")
        if fk:
            op.drop_constraint(fk, table, type_="foreignkey")
        op.drop_index(f"ix_{table}_organization_id", table_name=table, if_exists=True)
        op.drop_column(table, "organization_id")
