"""Scoped api tokens: service users, service clients, and no org-less token.

The /api/v1 read family authenticates with a scoped organisation token issued
by a client-credentials grant to a service user. This migration:

* adds the add-only ``users.is_service_user`` flag (no non-human marker
  existed before);
* adds the add-only ``oauth_clients.service_user_id`` and
  ``oauth_clients.scope`` columns for service clients (that a client may only
  be issued the scopes it was registered with is enforced at grant time);
* backfills ``oauth_tokens.organization_id`` from the token's user, and
  revokes any token that still has none, so no active token is ever
  organisation-less.

Every statement is guarded so the revision runs twice (and runs on a schema
built fresh from the models) without error.

Revision ID: 20261012_api_token_scopes
Revises: 20261010_oauth_token_grants
Create Date: 2026-10-12
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261012_api_token_scopes"
down_revision = "20261010_oauth_token_grants"
branch_labels = None
depends_on = None


def _has_table(bind, name: str) -> bool:
    return inspect(bind).has_table(name)


def _has_column(bind, table: str, column: str) -> bool:
    return column in {col["name"] for col in inspect(bind).get_columns(table)}


def _has_index(bind, table: str, index: str) -> bool:
    return index in {idx["name"] for idx in inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()

    if _has_table(bind, "users") and not _has_column(bind, "users", "is_service_user"):
        op.add_column("users", sa.Column("is_service_user", sa.Boolean(), nullable=True))

    if _has_table(bind, "oauth_clients"):
        if not _has_column(bind, "oauth_clients", "service_user_id"):
            op.add_column(
                "oauth_clients", sa.Column("service_user_id", sa.Integer(), nullable=True)
            )
        if not _has_column(bind, "oauth_clients", "scope"):
            op.add_column("oauth_clients", sa.Column("scope", sa.String(length=256), nullable=True))
        if not _has_index(bind, "oauth_clients", "ix_oauth_clients_service_user_id"):
            op.create_index(
                "ix_oauth_clients_service_user_id", "oauth_clients", ["service_user_id"]
            )

    if _has_table(bind, "oauth_tokens"):
        # The organisation comes from the token, not from any request, and the
        # token's organisation is its issuing user's at backfill time. A token
        # whose user can no longer be resolved is revoked rather than left
        # active without an organisation.
        op.execute(
            sa.text(
                "UPDATE oauth_tokens SET organization_id = u.organization_id "
                "FROM users u "
                "WHERE oauth_tokens.user_id = u.id AND oauth_tokens.organization_id IS NULL"
            )
        )
        op.execute(
            sa.text(
                "UPDATE oauth_tokens SET revoked_at = now() "
                "WHERE organization_id IS NULL AND revoked_at IS NULL"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()

    if _has_table(bind, "oauth_clients"):
        if _has_index(bind, "oauth_clients", "ix_oauth_clients_service_user_id"):
            op.drop_index("ix_oauth_clients_service_user_id", table_name="oauth_clients")
        if _has_column(bind, "oauth_clients", "service_user_id"):
            op.drop_column("oauth_clients", "service_user_id")
        if _has_column(bind, "oauth_clients", "scope"):
            op.drop_column("oauth_clients", "scope")

    if _has_table(bind, "users") and _has_column(bind, "users", "is_service_user"):
        op.drop_column("users", "is_service_user")

    # The backfill is deliberately not reversed: rows revoked by the forward
    # pass stay revoked rather than becoming active without an organisation.