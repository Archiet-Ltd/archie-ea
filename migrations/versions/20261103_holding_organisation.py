"""Unconfirmed sign-ups wait in an organisation of their own.

They used to wait in the ``default`` organisation, which is also where the platform administrator
lives, so every stranger became a row in the administrator's organisation and the administrator's
real members were counted as none. This creates the holding organisation (slug
``unconfirmed-sign-ups``) and moves into it every account in ``default`` that is unconfirmed, is not
a platform administrator and holds no role there.

Idempotent: the organisation is inserted only when absent and the move touches only accounts still
in ``default``. The downgrade moves the held accounts back and leaves the organisation row, which a
later upgrade reuses.

Revision ID: 20261103_holding_organisation
Revises: 20261102_user_disabled_at
Create Date: 2026-11-03
"""
from alembic import op
from sqlalchemy import text

revision = "20261103_holding_organisation"
down_revision = "20261102_user_disabled_at"
branch_labels = None
depends_on = None

SLUG = "unconfirmed-sign-ups"
NAME = "Unconfirmed sign-ups"


def ensure_holding_organisation(bind):
    bind.execute(
        text(
            "INSERT INTO organizations (name, slug, plan, is_active, reporting_currency, created_at, updated_at) "
            "VALUES (:name, :slug, 'free', true, 'GBP', now(), now()) ON CONFLICT (slug) DO NOTHING"
        ),
        {"name": NAME, "slug": SLUG},
    )


def move_held_users(bind):
    """Move held strangers out of ``default``: unconfirmed, no role, not an administrator, and created through
    the public sign-up form (it records a sign-up time; an account an administrator added has none). Returns how many moved."""
    moved = bind.execute(
        text(
            "UPDATE users SET organization_id = (SELECT id FROM organizations WHERE slug = :holding) "
            "WHERE organization_id = (SELECT id FROM organizations WHERE slug = 'default') "
            "AND confirmed IS NOT TRUE AND COALESCE(is_platform_admin, false) = false "
            "AND signup_at IS NOT NULL AND password_hash IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM org_roles r WHERE r.user_id = users.id)"
        ),
        {"holding": SLUG},
    ).rowcount
    print("holding organisation: {} held account(s) moved out of default".format(moved))
    return moved


def upgrade():
    bind = op.get_bind()
    ensure_holding_organisation(bind)
    move_held_users(bind)


def downgrade():
    bind = op.get_bind()
    bind.execute(
        text(
            "UPDATE users SET organization_id = (SELECT id FROM organizations WHERE slug = 'default') "
            "WHERE organization_id = (SELECT id FROM organizations WHERE slug = :holding) "
            "AND EXISTS (SELECT 1 FROM organizations WHERE slug = 'default')"
        ),
        {"holding": SLUG},
    )
