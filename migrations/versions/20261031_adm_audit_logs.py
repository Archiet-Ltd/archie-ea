"""adm_audit_logs for a database built from migrations

Revision ID: 20261031_adm_audit_logs
Revises: 20261031_fw_adopt_source
Create Date: 2026-10-31 00:00:00

The table behind app/models/adm_audit_log.py was only ever created by a
migration archived before the baseline, and the baseline builds from the models
the app imports at boot, which never include it. A database built from
migrations (a new environment, a restore drill, a test database) therefore had
no table for the audit service to write to. Guarded, so production, which has
the table, is unchanged.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261031_adm_audit_logs"
down_revision = "20261031_fw_adopt_source"
branch_labels = None
depends_on = None

_INDEXES = (
    ("idx_adm_audit_logs_timestamp", ["timestamp"]),
    ("idx_adm_audit_logs_action", ["action"]),
    ("idx_adm_audit_logs_entity", ["entity_type", "entity_id"]),
    ("idx_adm_audit_logs_board", ["board_id"]),
    ("idx_adm_audit_logs_card", ["card_id"]),
    ("idx_adm_audit_logs_actor", ["actor_id"]),
    ("idx_adm_audit_logs_phase_transition", ["source_phase_id", "target_phase_id"]),
)


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("adm_audit_logs"):
        op.create_table(
            "adm_audit_logs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("audit_id", sa.String(length=100), nullable=False),
            sa.Column("entity_type", sa.String(length=50), nullable=False),
            sa.Column("entity_id", sa.Integer(), nullable=False),
            sa.Column("entity_reference", sa.String(length=255), nullable=True),
            sa.Column("action", sa.String(length=50), nullable=False),
            sa.Column("action_description", sa.Text(), nullable=True),
            sa.Column("old_values", postgresql.JSON(astext_type=sa.Text()), nullable=True),
            sa.Column("new_values", postgresql.JSON(astext_type=sa.Text()), nullable=True),
            sa.Column("changed_fields", postgresql.JSON(astext_type=sa.Text()), nullable=True),
            sa.Column("board_id", sa.Integer(), nullable=True),
            sa.Column("card_id", sa.Integer(), nullable=True),
            sa.Column("approval_id", sa.Integer(), nullable=True),
            sa.Column("phase_id", sa.Integer(), nullable=True),
            sa.Column("source_phase_id", sa.Integer(), nullable=True),
            sa.Column("target_phase_id", sa.Integer(), nullable=True),
            sa.Column("source_phase_code", sa.String(length=10), nullable=True),
            sa.Column("target_phase_code", sa.String(length=10), nullable=True),
            sa.Column("actor_id", sa.Integer(), nullable=False),
            sa.Column("actor_email", sa.String(length=255), nullable=True),
            sa.Column("actor_role", sa.String(length=100), nullable=True),
            sa.Column("ip_address", sa.String(length=100), nullable=True),
            sa.Column("user_agent", sa.String(length=500), nullable=True),
            sa.Column("request_id", sa.String(length=100), nullable=True),
            sa.Column("session_id", sa.String(length=100), nullable=True),
            sa.Column("justification", sa.Text(), nullable=True),
            sa.Column("approval_chain", postgresql.JSON(astext_type=sa.Text()), nullable=True),
            sa.Column("timestamp", sa.DateTime(), nullable=False),
            sa.Column("recorded_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["approval_id"], ["adm_phase_approvals.id"]),
            sa.ForeignKeyConstraint(["board_id"], ["kanban_boards.id"]),
            sa.ForeignKeyConstraint(["card_id"], ["kanban_cards.id"]),
            sa.ForeignKeyConstraint(["phase_id"], ["adm_phases.id"]),
            sa.ForeignKeyConstraint(["source_phase_id"], ["adm_phases.id"]),
            sa.ForeignKeyConstraint(["target_phase_id"], ["adm_phases.id"]),
            sa.UniqueConstraint("audit_id"),
        )
    for name, columns in _INDEXES:
        cols = ", ".join(columns)
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON adm_audit_logs ({cols})")


def downgrade():
    # Deliberately a no-op: dropping the table would destroy audit rows on any
    # database that has them, production included.
    pass
