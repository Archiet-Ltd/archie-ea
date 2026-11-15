"""Give back the motivation relationships an import had softened to association.

The relationship matrix used to draw realization backwards between goals, outcomes,
principles, requirements and constraints, and refused influence between several
motivation pairs. An import kept such a relationship as an association and wrote
"As modelled: <Type>." into its description. The matrix now allows those pairs, so a
relationship between two motivation elements whose note names a type that is now
allowed goes back to that type. The note stays, as the history of what happened.

Only rows whose two elements are motivation elements of the pair, in the same
organisation as the relationship, are changed; nothing moves across organisations.
Running it twice changes nothing the second time.

The downgrade turns the same rows (type and note agree, motivation pair) back into
associations, the form the old matrix forced.

Revision ID: 20261115_motivation_retype
Revises: 20261115_merge_lmoneypage01b
Create Date: 2026-11-15
"""
from alembic import op

revision = "20261115_motivation_retype"
down_revision = "20261115_merge_lmoneypage01b"
branch_labels = None
depends_on = None

_MOTIVATION = (
    "Stakeholder", "Driver", "Assessment", "Goal",
    "Outcome", "Principle", "Requirement", "Constraint",
)
_REALIZING = (
    ("Outcome", "Goal"), ("Principle", "Goal"), ("Principle", "Outcome"),
    ("Requirement", "Goal"), ("Requirement", "Outcome"), ("Requirement", "Principle"),
    ("Constraint", "Goal"), ("Constraint", "Outcome"), ("Constraint", "Principle"),
)


def _pairs(rel_type):
    if rel_type == "realization":
        return _REALIZING
    return tuple((a, b) for a in _MOTIVATION for b in _MOTIVATION)


def _move(bind, from_type, to_type, note_type):
    pairs = _pairs(note_type)
    pair_sql = ", ".join(f"('{a}', '{b}')" for a, b in pairs)
    bind.exec_driver_sql(
        f"""
        UPDATE archimate_relationships r
           SET type = '{to_type}'
          FROM archimate_elements s, archimate_elements t
         WHERE s.id = r.source_id AND t.id = r.target_id
           AND r.type = '{from_type}'
           AND r.description LIKE '%%As modelled: {note_type.capitalize()}.%%'
           AND (s.type, t.type) IN ({pair_sql})
           AND s.organization_id IS NOT DISTINCT FROM r.organization_id
           AND t.organization_id IS NOT DISTINCT FROM r.organization_id
        """
    )


def upgrade():
    bind = op.get_bind()
    for note_type in ("realization", "influence"):
        _move(bind, "association", note_type, note_type)


def downgrade():
    bind = op.get_bind()
    for note_type in ("realization", "influence"):
        _move(bind, note_type, "association", note_type)
