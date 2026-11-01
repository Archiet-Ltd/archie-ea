"""adm_audit_logs for a database built from migrations

Revision ID: 20261031_adm_audit_logs
Revises: 20261031_provenance_index
Create Date: 2026-10-31 00:00:00

The table behind app/models/adm_audit_log.py was only ever created by a
migration archived before the baseline, and the baseline builds from the models
the app imports at boot, which never include it. A database built from
migrations (a new environment, a restore drill, a test database) therefore had
no table for the audit service to write to. Guarded, so production, which has
the table, is unchanged.

The foreign keys carry the rules production has: removing a user removes their
audit rows, removing a board, card, approval or phase leaves the row with that
key empty.

A database that already has the table may carry the older no-action keys. This
revision drops and re-creates any of the seven keys whose rule differs, so
removing a user, board, card, approval or phase behaves the same on every
database. Keys that already have the right rule are left alone, so running it
again changes nothing. The downgrade puts back no-action keys.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261031_adm_audit_logs"
down_revision = "20261031_provenance_index"
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


# column -> (referred table, ondelete rule)
_RULES = {
    "actor_id": ("users", "CASCADE"),
    "board_id": ("kanban_boards", "SET NULL"),
    "card_id": ("kanban_cards", "SET NULL"),
    "approval_id": ("adm_phase_approvals", "SET NULL"),
    "phase_id": ("adm_phases", "SET NULL"),
    "source_phase_id": ("adm_phases", "SET NULL"),
    "target_phase_id": ("adm_phases", "SET NULL"),
}


def _set_rules(bind, wanted):
    """Make each key on adm_audit_logs carry the wanted rule (None is no action)."""
    inspector = sa.inspect(bind)
    if not inspector.has_table("adm_audit_logs"):
        return
    existing = {}
    for fk in inspector.get_foreign_keys("adm_audit_logs"):
        if len(fk["constrained_columns"]) == 1:
            existing[fk["constrained_columns"][0]] = fk
    for column, rule in wanted.items():
        target = _RULES[column][0]
        current = existing.get(column)
        have = ((current or {}).get("options") or {}).get("ondelete")
        if current is not None and (have or "").upper() == (rule or ""):
            continue
        if current is not None:
            op.drop_constraint(current["name"], "adm_audit_logs", type_="foreignkey")
        op.create_foreign_key(
            f"adm_audit_logs_{column}_fkey", "adm_audit_logs", target, [column], ["id"], ondelete=rule,
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
            sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["approval_id"], ["adm_phase_approvals.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["board_id"], ["kanban_boards.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["card_id"], ["kanban_cards.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["phase_id"], ["adm_phases.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["source_phase_id"], ["adm_phases.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["target_phase_id"], ["adm_phases.id"], ondelete="SET NULL"),
            sa.UniqueConstraint("audit_id"),
        )
    _set_rules(bind, {column: rule for column, (_, rule) in _RULES.items()})
    for name, columns in _INDEXES:
        cols = ", ".join(columns)
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON adm_audit_logs ({cols})")


def downgrade():
    # Put back the older no-action keys. The table and its rows stay: dropping
    # them would destroy audit rows on any database that has them.
    _set_rules(op.get_bind(), {column: None for column in _RULES})
