"""Grade existing capability support rows from the older mapping's primary-enabler flag.

A support row used to be graded "primary" for every realization link, so an application
linked to several capabilities had no way to say which one it is the primary enabler of.
The grade now comes from one source, the older mapping record's primary-enabler flag.

For every support row whose application and capability also have an older mapping record
in the same organisation: a flagged record makes the row "primary", and an unflagged one
turns a "primary" row into "linked". Rows with no older mapping record are not touched,
and neither are rows already at the right grade, so running it twice changes nothing the
second time. The number of rows changed is printed.

The downgrade does nothing: the earlier grade (every realization link primary) was the
defect, and it cannot be told apart from a grade a person set, so there is nothing safe
to put back.

Revision ID: 20261116_regrade_primary_enabler
Revises: 20261115_motivation_retype
Create Date: 2026-11-16
"""
from alembic import op

revision = "20261116_regrade_primary_enabler"
down_revision = "20261115_motivation_retype"
branch_labels = None
depends_on = None

_REGRADE = """
WITH graded AS (
    SELECT u.id, bool_or(m.is_primary_enabler IS TRUE) AS flagged
      FROM unified_application_capability_mapping u
      JOIN archimate_relationships rel
        ON rel.id = u.archimate_relationship_id AND rel.organization_id = u.organization_id
      JOIN business_capability bc
        ON bc.archimate_element_id = rel.target_id AND bc.organization_id = u.organization_id
      JOIN application_capability_mapping m
        ON m.business_capability_id = bc.id
       AND m.application_component_id = u.application_component_id
       AND m.organization_id = u.organization_id
     GROUP BY u.id
)
UPDATE unified_application_capability_mapping u
   SET support_level = CASE WHEN g.flagged THEN 'primary' ELSE 'linked' END
  FROM graded g
 WHERE u.id = g.id
   AND ((g.flagged AND u.support_level IS DISTINCT FROM 'primary')
        OR (NOT g.flagged AND u.support_level = 'primary'))
"""


def regrade(bind) -> int:
    """Re-grade the rows and return how many changed."""
    return bind.exec_driver_sql(_REGRADE).rowcount


def upgrade():
    changed = regrade(op.get_bind())
    print(f"regrade_primary_enabler: {changed} support rows changed")


def downgrade():
    # Nothing to restore: see the note at the top of this file.
    pass
