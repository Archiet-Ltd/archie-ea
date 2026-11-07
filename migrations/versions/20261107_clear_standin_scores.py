"""Clear the score inputs the old column defaults wrote for requirements nobody scored.

Before inputs were left empty, a new requirement stored 1 for each of the four WSJF inputs and
0 / 1 / 100 for RICE reach / impact / confidence. Those rows now read as "recorded", so they are
set back to empty here, once. After this revision a stored 1 or a reach of 0 is a person's entry.

Data only: no column changes. The downgrade does nothing, because the cleared values cannot be told
apart from a person's entry any more.

Revision ID: 20261107_clear_standin_scores
Revises: 20261107_stage_duration
Create Date: 2026-11-07
"""
from alembic import op
from sqlalchemy import inspect, text

revision = "20261107_clear_standin_scores"
down_revision = "20261107_stage_duration"
branch_labels = None
depends_on = None

_WSJF = """
UPDATE requirements
   SET business_value = NULL, time_criticality = NULL, risk_reduction = NULL, job_size = NULL
 WHERE business_value = 1 AND time_criticality = 1 AND risk_reduction = 1 AND job_size = 1
"""
_RICE = """
UPDATE requirements
   SET reach = NULL, impact = NULL, confidence = NULL
 WHERE reach = 0 AND impact = 1 AND confidence = 100
"""


def upgrade():
    bind = op.get_bind()
    if not inspect(bind).has_table("requirements"):
        return
    for statement in (_WSJF, _RICE):
        bind.execute(text(statement))


def downgrade():
    pass
