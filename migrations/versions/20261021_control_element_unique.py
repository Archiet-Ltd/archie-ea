"""One model element per compliance control per organisation.

``control_to_element`` finds or makes the Requirement (or Constraint) element that stands for a
library control in an organisation's model, and records the control's id on the element as
``custom_properties.compliance_control_id``. Nothing stopped two concurrent first links from
both making it, so an organisation could hold two elements for one control and the links to
them split between the two.

This revision merges any such duplicates and then adds a partial unique index on
(organization_id, compliance_control_id) so the database refuses a second one.

The merge, per organisation and control: the element with the lowest id is kept. Every
relationship that ends at, or starts from, a duplicate is pointed at the kept element; a
relationship that then repeats another one (same organisation, type, source, target and
architecture) is deleted, as is one that would join the kept element to itself. The duplicate
elements are not deleted (other tables may refer to them): their control id property is renamed
``merged_into_element_id`` and set to the kept element's id, so they stop counting as the
control's element and stay on record.

Idempotent: with no duplicates the merge changes nothing, the index is created only when absent,
and a second run is a no-op. ``downgrade()`` drops the index and leaves the merged rows as they
are.

Revision ID: 20261021_control_element_unique
Revises: 20261018_tree_models_rls
Create Date: 2026-10-21
"""
import sqlalchemy as sa
from alembic import op

revision = "20261021_control_element_unique"
down_revision = "20261018_tree_models_rls"
branch_labels = None
depends_on = None

INDEX = "uq_archimate_elements_org_control"
_KEY = "custom_properties ->> 'compliance_control_id'"


def _has_table(bind, table: str) -> bool:
    return bind.execute(sa.text("SELECT to_regclass(:t)"), {"t": table}).scalar() is not None


def _has_column(bind, table: str, column: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
                "AND table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).first()
    )


def _is_jsonb(bind) -> bool:
    return (
        bind.execute(
            sa.text(
                "SELECT data_type FROM information_schema.columns WHERE table_schema = current_schema() "
                "AND table_name = 'archimate_elements' AND column_name = 'custom_properties'"
            )
        ).scalar()
        == "jsonb"
    )


def _merge_duplicates(bind) -> int:
    """Merge duplicate control elements. Returns how many duplicate elements were found."""
    pairs = bind.execute(
        sa.text(
            "SELECT e.id AS dup_id, k.keeper_id "  # nosec B608 - literal names
            "FROM archimate_elements e "
            "JOIN (SELECT organization_id, (" + _KEY + ") AS control_id, MIN(id) AS keeper_id "
            "      FROM archimate_elements WHERE (" + _KEY + ") IS NOT NULL "
            "      GROUP BY organization_id, (" + _KEY + ") HAVING COUNT(*) > 1) k "
            "  ON k.organization_id IS NOT DISTINCT FROM e.organization_id "
            " AND k.control_id = (e." + _KEY + ") "
            "WHERE e.id <> k.keeper_id"
        )
    ).all()
    if not pairs:
        return 0

    has_rel = _has_table(bind, "archimate_relationships")
    cast = "jsonb" if _is_jsonb(bind) else "json"
    for dup_id, keeper_id in pairs:
        if has_rel:
            bind.execute(
                sa.text("UPDATE archimate_relationships SET source_id = :k WHERE source_id = :d"),
                {"k": keeper_id, "d": dup_id},
            )
            bind.execute(
                sa.text("UPDATE archimate_relationships SET target_id = :k WHERE target_id = :d"),
                {"k": keeper_id, "d": dup_id},
            )
        bind.execute(
            sa.text(
                "UPDATE archimate_elements SET custom_properties = "
                "((custom_properties::jsonb - 'compliance_control_id') "
                "|| jsonb_build_object('merged_into_element_id', CAST(:k AS integer)))::" + cast + " "
                "WHERE id = :d"
            ),
            {"k": keeper_id, "d": dup_id},
        )

    if has_rel:
        keepers = sorted({keeper for _, keeper in pairs})
        # A relationship the merge made into a loop on the kept element.
        bind.execute(
            sa.text(
                "DELETE FROM archimate_relationships "
                "WHERE source_id = target_id AND source_id = ANY(:keepers)"
            ),
            {"keepers": keepers},
        )
        # A relationship the merge made identical to an earlier one.
        bind.execute(
            sa.text(
                "DELETE FROM archimate_relationships r USING archimate_relationships earlier "
                "WHERE r.id > earlier.id "
                "AND r.organization_id IS NOT DISTINCT FROM earlier.organization_id "
                "AND r.type IS NOT DISTINCT FROM earlier.type "
                "AND r.source_id = earlier.source_id AND r.target_id = earlier.target_id "
                "AND r.architecture_id IS NOT DISTINCT FROM earlier.architecture_id "
                "AND (r.target_id = ANY(:keepers) OR r.source_id = ANY(:keepers))"
            ),
            {"keepers": keepers},
        )
    return len(pairs)


def upgrade():
    bind = op.get_bind()
    if not _has_table(bind, "archimate_elements") or not _has_column(
        bind, "archimate_elements", "custom_properties"
    ):
        return
    merged = _merge_duplicates(bind)
    if merged:
        print(f"{revision}: merged {merged} duplicate compliance control element(s)")
    bind.execute(
        sa.text(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {INDEX} "  # nosec B608 - literal names
            f"ON archimate_elements (organization_id, ({_KEY})) "
            f"WHERE ({_KEY}) IS NOT NULL"
        )
    )


def downgrade():
    bind = op.get_bind()
    bind.execute(sa.text(f"DROP INDEX IF EXISTS {INDEX}"))  # nosec B608 - literal name
