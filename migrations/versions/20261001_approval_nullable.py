"""Drop NOT NULL on ai_chat_crud_approvals.user_id to match the model.

``AIChatCRUDApproval.user_id`` (app/models/ai_chat_crud_approval.py) has been
nullable in the model since the approval-queue consolidation: a row backfilled
from a source with no live acting user (a background job, e.g. the
confidence-review pipeline) or created directly by one has no requester to
attribute the request to. ``reconcile-schema`` adds missing columns but never
relaxes a constraint on one that already exists, so any database whose table
predates that model change keeps the original NOT NULL until this runs.

Idempotent: PostgreSQL's ``DROP NOT NULL`` is a no-op on an already-nullable
column.

Revision ID: 20261001_approval_nullable
Revises: 20261001_adr_canonical_cols
Create Date: 2026-10-01
"""
from alembic import op
from sqlalchemy import text

from app.commands.schema_migrations import ContractBlocked

# alembic_version.version_num is VARCHAR(32) (set by the baseline migration
# system in migrations/env.py, not by this revision) -- keep this id at or
# under that length, the same constraint every revision after the baseline
# already satisfies.
revision = "20261001_approval_nullable"
down_revision = "20261001_adr_canonical_cols"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    bind.execute(text(
        "ALTER TABLE ai_chat_crud_approvals ALTER COLUMN user_id DROP NOT NULL"
    ))


def downgrade():
    bind = op.get_bind()
    orphaned = bind.execute(
        text("SELECT count(*) FROM ai_chat_crud_approvals WHERE user_id IS NULL")
    ).scalar()
    if orphaned:
        raise ContractBlocked(
            f"ai_chat_crud_approvals.user_id: {orphaned} row(s) have no "
            "requester; re-adding NOT NULL would break them. Forward-fix instead."
        )
    bind.execute(text(
        "ALTER TABLE ai_chat_crud_approvals ALTER COLUMN user_id SET NOT NULL"
    ))
