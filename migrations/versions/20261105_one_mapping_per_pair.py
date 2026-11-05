"""One capability-to-application mapping per pair: remove exact duplicates, then make them impossible.

``unified_application_capability_mapping`` is the one store for which applications support which
capability, and it had no unique constraint: the demonstration seed and the legacy-insert mirror each
wrote every pair, so organisations held the same pair twice. This revision

1. counts and logs the duplicate pairs,
2. deletes only the extra copies of a pair, keeping the most complete row (then the lowest id). A
   capability belongs to one organisation, so a pair never spans two organisations and no organisation's
   row is ever removed for another's,
3. adds a unique constraint on (unified capability, application component), which is also one row per
   organisation, capability and application.

No legacy table is touched and no legacy row is deleted. Idempotent: the constraint is added only when
absent, so a database built from the models (which carry it) passes through. It runs under the platform
scope that migrations/env.py sets, so row-level security does not hide the rows.

Revision ID: 20261105_one_mapping_per_pair
Revises: 20261104_force_rls (20261103_requirement_source on a tree that does not carry it)
Create Date: 2026-11-05
"""
import logging
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "20261105_one_mapping_per_pair"
# Chain after the forced-RLS revision when this tree has it, so a merge keeps one head; otherwise after
# the previous head.
down_revision = (
    "20261104_force_rls"
    if (Path(__file__).with_name("20261104_force_rls.py")).exists()
    else "20261103_requirement_source"
)
branch_labels = None
depends_on = None

TABLE = "unified_application_capability_mapping"
CONSTRAINT = "uq_unified_app_cap_mapping_pair"
log = logging.getLogger("alembic.runtime.migration")

_RANKED = f"""
SELECT id,
       ROW_NUMBER() OVER (
           PARTITION BY unified_capability_id, application_component_id
           ORDER BY ( (CASE WHEN support_level IS NOT NULL AND support_level <> '' THEN 1 ELSE 0 END)
                    + (CASE WHEN COALESCE(coverage_percentage, 0) <> 0 THEN 1 ELSE 0 END)
                    + (CASE WHEN notes IS NOT NULL AND notes <> '' THEN 1 ELSE 0 END)
                    + (CASE WHEN maturity_level IS NOT NULL THEN 1 ELSE 0 END)
                    + (CASE WHEN gap_description IS NOT NULL AND gap_description <> '' THEN 1 ELSE 0 END)
                    + (CASE WHEN assessor IS NOT NULL AND assessor <> '' THEN 1 ELSE 0 END)) DESC,
                    id ASC) AS rn
  FROM {TABLE}
"""


def upgrade():
    bind = op.get_bind()
    extra = bind.execute(text(f"SELECT COUNT(*) FROM ({_RANKED}) r WHERE rn > 1")).scalar() or 0
    log.info("%s: %s duplicate pair row(s) to remove", TABLE, extra)
    print(f"{TABLE}: duplicate pair rows removed: {extra}")
    if extra:
        bind.execute(text(f"DELETE FROM {TABLE} WHERE id IN (SELECT id FROM ({_RANKED}) r WHERE rn > 1)"))
    exists = bind.execute(
        text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": CONSTRAINT}
    ).first()
    if not exists:
        op.create_unique_constraint(CONSTRAINT, TABLE, ["unified_capability_id", "application_component_id"])


def downgrade():
    bind = op.get_bind()
    exists = bind.execute(
        text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": CONSTRAINT}
    ).first()
    if exists:
        op.drop_constraint(CONSTRAINT, TABLE, type_="unique")
