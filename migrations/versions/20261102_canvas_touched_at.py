"""business_model_canvases.touched_at for a database built from migrations

Revision ID: 20261102_canvas_touched_at
Revises: 20261101_risk_loss_assessments
Create Date: 2026-11-02 00:00:00

The canvas model and its writers record in touched_at that a person added, edited, moved or removed an entry, or
changed the name, description or operating model. A database upgraded by migrations alone lacked the column, so
every canvas write failed. Nullable: NULL means nobody has changed the canvas.

Canvases made before this release get NULL, except where the stored rows show a person worked on them: a canvas
whose row was saved more than a minute after it was created (sign-up fills and links a canvas within seconds) gets
touched_at from its last save. Wrongly marking a canvas as worked on only keeps it; wrongly leaving it untouched
could remove a canvas somebody changed, so the doubtful cases are marked. Imports nothing from app.

Idempotent: the column is added only when absent; the backfill runs only when the column was added here.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261102_canvas_touched_at"
down_revision = "20261101_risk_loss_assessments"
branch_labels = None
depends_on = None

TABLE = "business_model_canvases"


def _columns():
    return {c["name"] for c in inspect(op.get_bind()).get_columns(TABLE)}


def upgrade():
    if "touched_at" in _columns():
        return
    op.add_column(TABLE, sa.Column("touched_at", sa.DateTime(), nullable=True))
    op.execute(
        f"UPDATE {TABLE} SET touched_at = updated_at "
        "WHERE updated_at IS NOT NULL AND created_at IS NOT NULL "
        "AND updated_at > created_at + interval '1 minute'"
    )


def downgrade():
    if "touched_at" in _columns():
        op.drop_column(TABLE, "touched_at")
