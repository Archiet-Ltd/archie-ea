"""Connector tokens: grant families, account-state binding, delete cascade.

One revision for the three schema changes the assistant connector's security
fixes need:

* ``oauth_tokens.grant_id`` -- the family a rotated refresh token belongs to,
  so reuse of a rotated-away token can revoke the whole grant, and a person can
  disconnect an assistant as one thing. Nullable: ``reconcile-schema`` would add
  it anyway; it is declared here so the index exists too.
* ``oauth_tokens.auth_state`` -- a digest of the account facts the token must
  not outlive (password, multi-factor, single sign-on, organisation). Nullable;
  a token without one is refused by the bearer loader.
* ``ON DELETE CASCADE`` on ``user_id`` of ``oauth_tokens`` and
  ``oauth_authorization_codes``, so deleting a user who ever connected an
  assistant succeeds instead of failing on a NOT NULL violation.

Every step is idempotent and skips a table that does not exist yet.

Revision ID: 20261010_oauth_token_grants
Revises: 20261010_arb_change_requests_rls
Create Date: 2026-10-10
"""
from alembic import op
from sqlalchemy import text

revision = "20261010_oauth_token_grants"
down_revision = "20261010_arb_change_requests_rls"
branch_labels = None
depends_on = None

_TABLES = ("oauth_tokens", "oauth_authorization_codes")


def _exists(bind, table: str) -> bool:
    return bool(bind.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}).scalar())


def _user_fk_names(bind, table: str) -> list[str]:
    rows = bind.execute(
        text(
            "SELECT c.conname FROM pg_constraint c "
            "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey) "
            "WHERE c.contype = 'f' AND c.conrelid = to_regclass(:t) "
            "AND c.confrelid = to_regclass('public.users') AND a.attname = 'user_id'"
        ),
        {"t": f"public.{table}"},
    )
    return [r[0] for r in rows]


def _set_user_fk(bind, table: str, on_delete: str) -> None:
    for name in _user_fk_names(bind, table):
        bind.execute(text(f'ALTER TABLE {table} DROP CONSTRAINT "{name}"'))
    bind.execute(
        text(
            f"ALTER TABLE {table} ADD CONSTRAINT {table}_user_id_fkey "
            f"FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE {on_delete}"
        )
    )


def upgrade():
    bind = op.get_bind()
    if _exists(bind, "oauth_tokens"):
        bind.execute(text("ALTER TABLE oauth_tokens ADD COLUMN IF NOT EXISTS grant_id VARCHAR(64)"))
        bind.execute(text("ALTER TABLE oauth_tokens ADD COLUMN IF NOT EXISTS auth_state VARCHAR(64)"))
        bind.execute(text("CREATE INDEX IF NOT EXISTS ix_oauth_tokens_grant_id ON oauth_tokens (grant_id)"))
    for table in _TABLES:
        if _exists(bind, table):
            _set_user_fk(bind, table, "CASCADE")


def downgrade():
    bind = op.get_bind()
    for table in _TABLES:
        if _exists(bind, table):
            _set_user_fk(bind, table, "NO ACTION")
    if _exists(bind, "oauth_tokens"):
        bind.execute(text("DROP INDEX IF EXISTS ix_oauth_tokens_grant_id"))
        bind.execute(text("ALTER TABLE oauth_tokens DROP COLUMN IF EXISTS auth_state"))
        bind.execute(text("ALTER TABLE oauth_tokens DROP COLUMN IF EXISTS grant_id"))
