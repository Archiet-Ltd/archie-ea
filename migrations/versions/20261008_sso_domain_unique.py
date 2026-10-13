"""One organisation per enabled SSO email domain.

Existing enabled configs that share the same (lower-cased, trimmed) domain
string keep the earliest one (lowest id) and the later ones are disabled,
each one logged. Then a partial unique index stops it happening again.
Idempotent: the update only touches rows that still conflict and the index
uses IF NOT EXISTS (flask init-db may already have created it from the model).

Revision ID: 20261008_sso_domain_unique
Revises: 20261007_public_visitor_events
Create Date: 2026-10-08
"""
import logging

from alembic import op
from sqlalchemy import text

revision = "20261008_sso_domain_unique"
down_revision = "20261007_public_visitor_events"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")


def upgrade():
    bind = op.get_bind()
    if not bind.execute(text("SELECT to_regclass('sso_configs')")).scalar():
        return
    rows = bind.execute(text(
        "SELECT id, organization_id FROM sso_configs c WHERE enabled "
        "AND email_domain IS NOT NULL AND btrim(email_domain) <> '' "
        "AND EXISTS (SELECT 1 FROM sso_configs o WHERE o.enabled AND o.id < c.id "
        "AND lower(btrim(o.email_domain)) = lower(btrim(c.email_domain))) ORDER BY id"
    )).fetchall()
    for row in rows:
        log.warning(
            "Disabling duplicate SSO config id=%s organization_id=%s: its email "
            "domain is already held by an earlier enabled config", row[0], row[1],
        )
        bind.execute(text("UPDATE sso_configs SET enabled = false WHERE id = :i"), {"i": row[0]})
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_sso_configs_enabled_email_domain "
        "ON sso_configs (lower(btrim(email_domain))) "
        "WHERE enabled AND email_domain IS NOT NULL AND btrim(email_domain) <> ''"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS uq_sso_configs_enabled_email_domain")
