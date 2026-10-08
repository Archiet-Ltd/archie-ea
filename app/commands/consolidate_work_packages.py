"""
Work package consolidation: backfill-work-package-org, merge-work-package-stores.

`unified_work_packages` (app/models/unified_work_package.py) predates
TenantMixin, so it has no organisation column at all -- every row is currently
unreadable through the tenant filter used by every other model.  Three other
stores answer the same "work package" question with no tenant column either:
`technology_roadmap_initiatives`, `roadmap_work_packages` and
`implementation_work_packages`.  A fourth, `work_packages`, already carries
TenantMixin and is left alone here except for gaining the retirement marker.

This module gives `unified_work_packages` an organisation the same way
`app/commands/backfill_principle_org.py` and the `EnterpriseInitiative`
override in app/models/vendor/vendor_organization.py already do it for their
own tables: reconcile-schema adds the column as plain nullable (ADR 0002),
and a row that cannot be attributed an owning organisation from anything it
links to is left NULL -- the tenant filter's `=` comparison then matches no
organisation, which is quarantine, not a guess.

Attribution rule: the organisation of the work package's linked
programme or element, else its creator, else quarantine.

Run order (deploy-schema.sh runs exactly this, and every step is idempotent):

    flask --app manage reconcile-schema
    flask --app manage backfill-work-package-org
    flask --app manage merge-work-package-stores       # copy rows, remap
                                                       # dependencies, fill
                                                       # fields, tombstone
    flask --app manage backfill-work-package-org       # second pass: a merged
                                                       # row may resolve through
                                                       # a link its source table
                                                       # copied in that its own
                                                       # attribution chain did not
                                                       # try (e.g. an
                                                       # application_component_id
                                                       # carried over from
                                                       # implementation_work_packages).
    flask --app manage merge-work-package-stores --verify   # exits non-zero if
                                                       # any retired store holds a
                                                       # row not yet copied

A copied source row carries `retired_into_id` (its unified copy) and
`retired_at`. Deleting the unified copy nulls `retired_into_id` (ON DELETE SET
NULL) but leaves `retired_at`, so the row stays deleted: the merge and the
bridge only ever select rows where both are NULL.

Until R1-B04 PR 3 retires the remaining writers of the old stores, the same
per-row copy (`sync_source_rows`) is called by the session bridge in
app/services/work_package_bridge.py for every row such a writer creates or
edits, so the one store never lags the old ones. The bridge is deleted by PR 3.

All commands are idempotent: `--dry-run` reports counts and changes nothing;
a second run changes nothing and reports zero.
"""

import json
import logging
import re
from datetime import datetime

import click
from flask.cli import with_appcontext
from sqlalchemy import text

from app import db

logger = logging.getLogger(__name__)

# (label, organization-owning parent table, join column on unified_work_packages)
_BACKFILL_STEPS = (
    ("enterprise_initiative_id -> enterprise_initiatives", "enterprise_initiatives", "enterprise_initiative_id"),
    ("archimate_element_id -> archimate_elements", "archimate_elements", "archimate_element_id"),
    ("application_component_id -> application_components", "application_components", "application_component_id"),
)


def _count(conn, sql, **params):
    return conn.execute(text(sql), params).scalar()


def _attribute_org(conn, dry_run=False, unified_ids=None, emit=True):
    """Attribute unified_work_packages.organization_id (linked programme or
    element, else creator). `unified_ids` restricts it to those rows (the
    bridge); None means every row (the deploy command). Returns rows changed."""
    only = "" if unified_ids is None else " AND t.id = ANY(:uids)"
    params = {} if unified_ids is None else {"uids": list(unified_ids)}
    changed = 0
    for label, parent_table, fk_column in _BACKFILL_STEPS:
        eligible = _count(
            conn,
            f'SELECT count(*) FROM unified_work_packages t '
            f'JOIN "{parent_table}" p ON p.id = t.{fk_column} '
            f'WHERE t.organization_id IS NULL AND p.organization_id IS NOT NULL{only}',
            **params,
        )
        if not eligible:
            continue
        if dry_run:
            if emit:
                click.echo(f"  - would attribute {eligible} row(s) via {label}")
            continue
        conn.execute(
            text(
                f'UPDATE unified_work_packages AS t SET organization_id = p.organization_id '
                f'FROM "{parent_table}" AS p '
                f'WHERE p.id = t.{fk_column} AND t.organization_id IS NULL '
                f'AND p.organization_id IS NOT NULL{only}'
            ),
            params,
        )
        changed += eligible
        if emit:
            click.echo(f"  + attributed {eligible} row(s) via {label}")

    eligible_creator = _count(
        conn,
        "SELECT count(*) FROM unified_work_packages t "
        "JOIN users u ON u.id = t.created_by "
        f"WHERE t.organization_id IS NULL AND u.organization_id IS NOT NULL{only}",
        **params,
    )
    if eligible_creator:
        if dry_run:
            if emit:
                click.echo(f"  - would attribute {eligible_creator} row(s) via created_by -> users")
        else:
            conn.execute(
                text(
                    "UPDATE unified_work_packages AS t SET organization_id = u.organization_id "
                    "FROM users AS u "
                    "WHERE u.id = t.created_by AND t.organization_id IS NULL "
                    f"AND u.organization_id IS NOT NULL{only}"
                ),
                params,
            )
            changed += eligible_creator
            if emit:
                click.echo(f"  + attributed {eligible_creator} row(s) via created_by -> users")
    return changed


@click.command("backfill-work-package-org")
@click.option("--dry-run", is_flag=True, help="Report what would change; change nothing.")
@with_appcontext
def backfill_work_package_org(dry_run):
    """Attribute unified_work_packages.organization_id: linked programme or
    element, else creator, else quarantine (left NULL)."""
    conn = db.session.connection()

    if not dry_run and _ensure_business_capability_nullable(conn):
        click.echo("  + unified_work_packages.business_capability: dropped NOT NULL")

    before = _count(conn, "SELECT count(*) FROM unified_work_packages WHERE organization_id IS NULL")
    click.echo(f"unified_work_packages: {before} row(s) with no organisation")

    _attribute_org(conn, dry_run=dry_run)

    # A row placed by this run (or any earlier one) that still holds plateau or gap
    # values is migrated now, so a later deploy cannot bring a stale link back.
    links = _Stats()
    _link_columns_to_relationships(links, dry_run)
    for key, n in links.items():
        click.echo(f"  {'-' if dry_run else '+'} {key}: {n}")

    if dry_run:
        click.echo("dry-run: no changes committed.")
        db.session.rollback()
        return

    remaining = _count(conn, "SELECT count(*) FROM unified_work_packages WHERE organization_id IS NULL")
    db.session.commit()
    click.echo(f"backfill-work-package-org: done. {remaining} row(s) quarantined (unattributable).")


def _ensure_nullable(conn, table, column):
    row = conn.execute(
        text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).first()
    if row and row[0] == "NO":
        conn.execute(text(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" DROP NOT NULL'))
        return True
    return False


def _ensure_business_capability_nullable(conn):
    """Drop the historical NOT NULL: a row merged in from a store with no
    capability link legitimately has none, and fabricating one would violate
    the no-invented-data rule. reconcile-schema only ever ADDs columns, so
    this constraint change has to happen here."""
    return _ensure_nullable(conn, "unified_work_packages", "business_capability")


def _ensure_deliverable_legacy_key_nullable(conn):
    """A deliverable can be added to any work package, including one with no
    work_packages row. deliverables.unified_work_package_id is the key every
    reader uses; the legacy key is only filled when a legacy row exists."""
    return _ensure_nullable(conn, "deliverables", "work_package_id")


# Each entry: source table, the columns copied straight across (same name on
# both sides), and how organization_id is derived for that source's own rows.
# `select_extra` is (expression over s, target column) for a column the source
# stores under another name or type. `fills` are columns carried from the
# source that earlier merges did not copy: they are inserted with the row and,
# for a row already merged, filled afterwards only where the target is NULL or
# empty. `org_expr` / `org_joins` are raw SQL fragments deliberately -- this is
# a data migration, not a query the ORM's tenant filter ever runs.
_MERGE_SOURCES = (
    {
        "table": "work_packages",
        "columns": (
            "name", "description", "status", "priority", "togaf_phase",
            "owner_id", "estimated_cost", "actual_cost",
            "capability_id", "element_type", "archimate_element_id",
            "application_component_id", "enterprise_initiative_id", "goal_id",
            "triggering_business_event_id", "created_at", "updated_at",
        ),
        "select_extra": (
            ("s.percent_complete", "progress_percentage"),
            ("s.start_date::timestamp", "start_date"),
            ("s.target_date::timestamp", "end_date"),
            ("s.dependencies", "work_dependencies"),
        ),
        "fills": (),
        "org_joins": "",
        "org_expr": "s.organization_id",
        "remap_dependencies": True,
    },
    {
        "table": "technology_roadmap_initiatives",
        "columns": ("name", "description", "status", "created_at", "updated_at"),
        "select_extra": (("s.investment_budget::float", "estimated_cost"),),
        "fills": (),
        "org_joins": (
            "LEFT JOIN solutions sol ON sol.id = s.solution_id "
            "LEFT JOIN architecture_models am ON am.id = s.architecture_id"
        ),
        "org_expr": "COALESCE(sol.organization_id, am.organization_id)",
    },
    {
        "table": "roadmap_work_packages",
        "columns": (
            "name", "description", "status", "business_capability", "assigned_to",
            "start_date", "end_date", "progress_percentage", "estimated_cost",
            "actual_cost", "priority", "risk_level", "created_at", "updated_at",
        ),
        "select_extra": (),
        "fills": (
            ("created_by", "s.created_by"),
            ("source_type", "s.source_type"),
            ("auto_generated", "s.auto_generated"),
            ("confidence_score", "s.confidence_score"),
        ),
        "org_joins": "LEFT JOIN users u ON u.id = s.created_by",
        "org_expr": "u.organization_id",
        "union_links": True,
    },
    {
        "table": "implementation_work_packages",
        "columns": (
            "name", "description", "documentation", "element_type", "layer",
            "status", "priority", "risk_level", "risk_mitigation", "start_date",
            "end_date", "progress_percentage", "assigned_to", "estimated_cost",
            "actual_cost", "required_resources", "work_dependencies",
            "prerequisites", "application_component_id", "created_at", "updated_at",
        ),
        "select_extra": (),
        # created_by is free text here; only a value that is a real user id counts.
        "fills": (("created_by", "(SELECT us.id FROM users us WHERE us.id::text = s.created_by::text)"),),
        "org_joins": (
            "LEFT JOIN architecture_models am ON am.id = s.architecture_id "
            "LEFT JOIN application_components ac ON ac.id = s.application_component_id"
        ),
        "org_expr": "COALESCE(am.organization_id, ac.organization_id)",
        "remap_dependencies": True,
    },
)

_SPEC_BY_TABLE = {spec["table"]: spec for spec in _MERGE_SOURCES}
RETIRED_TABLES = tuple(_SPEC_BY_TABLE)

_UNMERGED = "retired_into_id IS NULL AND retired_at IS NULL"


class _Stats(dict):
    def add(self, key, n):
        if n and n > 0:
            self[key] = self.get(key, 0) + n

    def merge(self, other):
        for key, n in other.items():
            self.add(key, n)

    def total(self):
        return sum(self.values())


def _only_ids(ids, alias="s"):
    return "" if ids is None else f" AND {alias}.id = ANY(:ids)"


def _table_exists(conn, name):
    return bool(conn.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": name}).scalar())


def _insert_missing(conn, spec, ids, stats, fallback_org_id=None):
    """Copy the rows not yet copied. Returns [(unified id, source id)] of the rows
    copied by this call."""
    table = spec["table"]
    params = {"source_table": table, "fallback_org": fallback_org_id}
    if ids is not None:
        params["ids"] = list(ids)
    # The element column is not copied by the INSERT: the shared rule (_update_existing) takes it below.
    columns = [c for c in spec["columns"] if c != "archimate_element_id"]
    insert_columns = (
        columns + [t for _, t in spec["select_extra"]] + [t for t, _ in spec["fills"]]
        + ["context", "scope", "organization_id", "source_table", "source_id"]
    )
    select_columns = (
        [f"s.{c}" for c in columns]
        + [e for e, _ in spec["select_extra"]]
        + [e for _, e in spec["fills"]]
        # context/scope have only a Python-side ORM default (no
        # server_default), so a raw INSERT bypassing the ORM must supply
        # both explicitly or trip their NOT NULL constraint.
        # A row the attribution chain cannot place, written from a request,
        # belongs to the organisation that made the request (bridge only).
        + ["'architecture'", "'enterprise'",
           f"COALESCE({spec['org_expr']}, CAST(:fallback_org AS integer))",
           f"'{table}'", "s.id"]
    )
    sql = (
        "WITH inserted AS ("
        f"INSERT INTO unified_work_packages ({', '.join(insert_columns)}) "
        f"SELECT {', '.join(select_columns)} "
        f'FROM "{table}" s {spec["org_joins"]} '
        f"WHERE s.retired_into_id IS NULL AND s.retired_at IS NULL{_only_ids(ids)} "
        "AND NOT EXISTS ("
        "SELECT 1 FROM unified_work_packages uwp "
        "WHERE uwp.source_table = :source_table AND uwp.source_id = s.id"
        ") "
        # A second writer racing this statement (the deploy merge against the
        # bridge) hits the unique index on (source_table, source_id) and skips the
        # row; the relink below then points the source at the winner's copy.
        "ON CONFLICT DO NOTHING "
        "RETURNING id AS new_id, source_id AS old_id"
        ") "
        f'UPDATE "{table}" SET retired_into_id = inserted.new_id, retired_at = CURRENT_TIMESTAMP '
        f'FROM inserted WHERE "{table}".id = inserted.old_id '
        "RETURNING inserted.new_id, inserted.old_id"
    )
    inserted = conn.execute(text(sql), params).fetchall()
    stats.add(f"{table}: copied", len(inserted))
    if inserted and "archimate_element_id" in spec["columns"]:
        _update_existing(conn, spec, {src: {"archimate_element_id"} for _, src in inserted}, stats)
    # A unified copy that already exists but whose source row was never marked
    # (an interrupted run): link it, do not copy again.
    relink = (
        f'UPDATE "{table}" AS s SET retired_into_id = uwp.id, retired_at = CURRENT_TIMESTAMP '
        "FROM unified_work_packages uwp "
        "WHERE uwp.source_table = :source_table AND uwp.source_id = s.id "
        f"AND s.retired_into_id IS NULL AND s.retired_at IS NULL{_only_ids(ids)}"
    )
    stats.add(f"{table}: linked", conn.execute(text(relink), params).rowcount)
    return [(int(new_id), int(old_id)) for new_id, old_id in inserted]


# The column a retired store keeps its dependency ids in. A change to it is applied
# to the copy as a diff of ids (added, removed), never as a replacement.
_DEP_SOURCE_COLUMN = {
    "work_packages": "dependencies",
    "implementation_work_packages": "work_dependencies",
}
# Relationships of the roadmap store (association tables) whose edits on an old
# screen are applied to the copy as the ids added and removed.
_LINK_RELATIONSHIPS = ("dependencies", "capabilities")
# Columns of a retired store that hold a plateau or gap link. They are not copied:
# a change is applied to the ArchiMate relationships (see apply_link_changes).
_LINK_SOURCE_COLUMNS = {"work_packages": ("plateau_id",)}
# The old store's association tables (WorkPackage.gaps / .plateaus): relationship
# attribute -> (association table, link key). Their rows become relationships too;
# the rows themselves stay, because old screens still read them.
_ASSOCIATIONS = {
    "plateaus": ("work_package_plateaus", "plateau_id"),
    "gaps": ("gap_work_packages", "gap_id"),
}
_ASSOCIATION_SOURCE = "work_packages"
# Special keys of a link-change dict, beside "plateau_id" and "gap_id".
_ASSOC_MIGRATE = "_associations"          # migrate the association rows, then mark the row
_ASSOC_CHANGES = "_association_changes"   # {"gaps"|"plateaus": (ids added, ids removed)}
_AUDIT_COLUMNS = ("created_at", "updated_at")
# association_links_migrated_at values come from _utcnow(), taken before the association rows are read.
# SQLSTATEs of a lock conflict: deadlock detected, serialization failure, lock not available.
_LOCK_CONFLICTS = ("40P01", "40001", "55P03")


def _utcnow():
    """The one clock for every association_links_migrated_at value (tests patch this)."""
    return datetime.utcnow()


def _is_lock_conflict(exc):
    """True for a deadlock, serialization failure or lock timeout (the SQLSTATE is on the
    driver's error, ``orig``, of the SQLAlchemy exception, or on the exception itself)."""
    for err in (exc, getattr(exc, "orig", None)):
        code = getattr(err, "sqlstate", None) or getattr(err, "pgcode", None)
        if code in _LOCK_CONFLICTS:
            return True
    return False


def _column_targets(spec):
    """{source column: [(target column, select expression over s)]}: the copy
    spec read per column, so a copy can be written column by column."""
    out = {}
    for column in spec["columns"]:
        out.setdefault(column, []).append((column, f"s.{column}"))
    for expr, target in spec["select_extra"]:
        out.setdefault(re.match(r"s\.(\w+)", expr).group(1), []).append((target, expr))
    out.pop(_DEP_SOURCE_COLUMN.get(spec["table"]), None)
    return out


def _element_taken_sql(element, org, copy="NULL"):
    """SQL for the one rule: a copy of `org` takes source element `element` (joined as ae) only if
    it is that organisation's, a WorkPackage, and used by no other copy."""
    return (f"(ae.organization_id = {org} AND ae.type = 'WorkPackage' AND NOT EXISTS (SELECT 1 FROM "  # tenancy-ok: same
            f"unified_work_packages ow WHERE ow.archimate_element_id = {element} AND ow.id IS DISTINCT FROM {copy}))")


def _element_refused(stats, table, uid, element, element_org, org_id):
    logger.warning("element not taken: work package %s, element %s of organisation %s, copy of "
                   "organisation %s", uid, element, element_org, org_id)
    stats.add(f"{table}: source element of another organisation not taken", 1)


def _move_links_with_element(conn, table, source_ids, stats):
    """A copy takes its source's element only by _element_taken_sql (else it keeps its own, logged);
    a taken element gets the copy's plateau and gap relationships. Returns the source ids refused."""
    from app.services import work_package_service as svc

    rows = conn.execute(text(
        "SELECT u.id, u.organization_id, u.archimate_element_id, s.id, s.archimate_element_id, "  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
        f"ae.organization_id, {_element_taken_sql('s.archimate_element_id', 'u.organization_id', 'u.id')} "
        "FROM unified_work_packages u "  # tenancy-ok: same
        f'JOIN "{table}" s ON s.id = u.source_id '  # tenancy-ok: same
        "LEFT JOIN archimate_elements ae ON ae.id = s.archimate_element_id "  # tenancy-ok: same
        "WHERE u.source_table = :t AND s.id = ANY(:ids) AND s.archimate_element_id IS NOT NULL "
        "AND u.archimate_element_id IS DISTINCT FROM s.archimate_element_id"),
        {"t": table, "ids": list(source_ids)}).fetchall()
    refused, used = set(), set()
    for uid, org_id, old, source_id, new, element_org, taken in rows:
        if not taken or new in used:
            _element_refused(stats, table, uid, new, element_org, org_id)
            refused.add(source_id)
        else:
            used.add(new)
        if source_id not in refused and old is not None:
            rel_ids = sorted({r[1] for r in svc._link_rows(org_id, wp_ids=[uid], connection=conn)})
            if rel_ids:
                conn.execute(text(
                    "UPDATE archimate_relationships SET source_id = :new "  # tenancy-ok: restricted to the copy's organisation below
                    "WHERE id = ANY(:rel_ids) AND organization_id = :org"),
                    {"new": new, "rel_ids": rel_ids, "org": org_id})
    return refused


def _update_existing(conn, spec, changed, stats):
    """Bridge only: a source row edited after its copy was made updates the
    copy's *changed* columns, and nothing else. `changed` is
    {source id: {source column, ...}}; a row whose changed columns map to
    nothing writes nothing, so edits made on the one store's own screens
    survive an old screen saving a different field. An element column is taken only
    from the copy's own organisation, and the links move with it."""
    table = spec["table"]
    targets = _column_targets(spec)
    groups = {}
    for source_id, columns in changed.items():
        columns = set(columns)
        wanted = {c for c in columns if c in targets and c not in _AUDIT_COLUMNS}
        if not wanted:
            continue
        wanted |= {c for c in _AUDIT_COLUMNS if c in targets}
        groups.setdefault(tuple(sorted(wanted)), []).append(int(source_id))
    for columns, ids in groups.items():
        refused = _move_links_with_element(conn, table, ids, stats) if "archimate_element_id" in columns else set()
        rest = tuple(c for c in columns if c != "archimate_element_id")
        batches = [(columns, [i for i in ids if i not in refused]), (rest, sorted(refused))]
        for cols, batch in batches:
            if not batch:
                continue
            assigns = [f"{t} = {e}" for c in cols for t, e in targets[c]]
            sql = (
                f"UPDATE unified_work_packages AS u SET {', '.join(assigns)} "
                f'FROM "{table}" s '
                "WHERE u.source_table = :source_table AND u.source_id = s.id AND s.id = ANY(:ids) "
                "AND s.retired_into_id IS NOT NULL"
            )
            stats.add(f"{table}: updated", conn.execute(
                text(sql), {"source_table": table, "ids": batch}).rowcount)


def _apply_dependency_changes(conn, table, dependency_changes, stats):
    """Apply a change of an old store's dependency list to the copy as a diff.
    `dependency_changes` is {source id: (old ids or None, new ids)}. The ids added
    and removed are remapped to unified ids; ids the copy already holds from the
    one store's own screens are left alone."""
    wanted = {}
    referenced = set()
    for source_id, (old, new) in dependency_changes.items():
        old_ids = _int_list(old)
        new_ids = _int_list(new)
        added = [i for i in new_ids if i not in old_ids]
        removed = [i for i in old_ids if i not in new_ids]
        if added or removed:
            wanted[int(source_id)] = (added, removed)
            referenced.update(added)
            referenced.update(removed)
    if not wanted:
        return
    mapping = {
        old: new for old, new in conn.execute(
            text("SELECT source_id, id FROM unified_work_packages "  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
                 "WHERE source_table = :t AND source_id = ANY(:ids)"),
            {"t": table, "ids": sorted(referenced)},
        )
    }
    rows = conn.execute(
        text("SELECT id, source_id, work_dependencies FROM unified_work_packages "  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
             "WHERE source_table = :t AND source_id = ANY(:ids)"),
        {"t": table, "ids": sorted(wanted)},
    ).fetchall()
    for uid, source_id, deps in rows:
        added, removed = wanted[source_id]
        current = _int_list(deps)
        gone = {int(mapping[i]) for i in removed if i in mapping}
        merged = [d for d in current if d not in gone]
        for old in added:
            new = mapping.get(old)
            if new is None:
                stats.add(f"{table}: dependencies dropped (no copy)", 1)
            elif int(new) not in merged:
                merged.append(int(new))
        if merged != current:
            conn.execute(
                text("UPDATE unified_work_packages SET work_dependencies = CAST(:v AS json) "  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
                     "WHERE id = :id"),
                {"v": json.dumps(merged), "id": uid},
            )
            stats.add(f"{table}: dependencies changed", 1)


def _apply_relation_changes(conn, table, relation_changes, stats):
    """Apply the edits of the roadmap store's association tables to the copy as a
    diff. `relation_changes` is {source id: {"dependencies": (added ids, removed ids),
    "capabilities": (added ids, removed ids)}}. Dependencies are the same diff the
    other stores use; capability ids are added and removed in capability_ids."""
    dependency_changes = {
        sid: (rem, add) for sid, ch in relation_changes.items()
        for add, rem in [ch.get("dependencies", ((), ()))] if add or rem
    }
    if dependency_changes:
        _apply_dependency_changes(conn, table, dependency_changes, stats)
    wanted = {sid: ch["capabilities"] for sid, ch in relation_changes.items()
              if ch.get("capabilities") and any(ch["capabilities"])}
    if not wanted:
        return
    rows = conn.execute(
        text("SELECT id, source_id, capability_ids FROM unified_work_packages "  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
             "WHERE source_table = :t AND source_id = ANY(:ids)"),
        {"t": table, "ids": sorted(wanted)},
    ).fetchall()
    for uid, source_id, caps in rows:
        added, removed = wanted[source_id]
        current = _int_list(caps)
        merged = [c for c in current if c not in set(removed)]
        merged += [c for c in added if c not in merged]
        if merged != current:
            conn.execute(
                text("UPDATE unified_work_packages SET capability_ids = CAST(:v AS json) "  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
                     "WHERE id = :id"),
                {"v": json.dumps(merged), "id": uid},
            )
            stats.add(f"{table}: capability links changed", 1)


def _fill_missing(conn, spec, ids, stats):
    """Fill, on rows already merged, only the targets that are NULL or empty."""
    table = spec["table"]
    params = {"source_table": table}
    if ids is not None:
        params["ids"] = list(ids)
    for target, expr in spec["fills"]:
        sql = (
            f"UPDATE unified_work_packages AS u SET {target} = {expr} "
            f'FROM "{table}" s '
            "WHERE u.source_table = :source_table AND u.source_id = s.id "
            f"AND (u.{target} IS NULL OR u.{target}::text = '') "
            f"AND NULLIF(({expr})::text, '') IS NOT NULL{_only_ids(ids)}"
        )
        stats.add(f"{table}: filled {target}", conn.execute(text(sql), params).rowcount)

    if table == "work_packages":
        sql = (
            "UPDATE unified_work_packages AS u SET parent_id = pu.id "
            "FROM work_packages s "
            "JOIN unified_work_packages pu ON pu.source_table = 'work_packages' AND pu.source_id = s.parent_id "
            "WHERE u.source_table = 'work_packages' AND u.source_id = s.id "
            f"AND u.parent_id IS NULL AND s.parent_id IS NOT NULL{_only_ids(ids)}"
        )
        stats.add(f"{table}: filled parent_id", conn.execute(text(sql), params).rowcount)

    if table == "roadmap_work_packages":
        _fill_roadmap_source_data(conn, ids, stats)


def _fill_roadmap_source_data(conn, ids, stats):
    """source_data is a JSON string; the roadmap row's own source_id goes into
    it as origin_source_id (the unified source_id keeps meaning the retired
    row's id). Written only where the target is empty."""
    sql = (
        "SELECT u.id, s.source_data, s.source_id FROM unified_work_packages u "
        "JOIN roadmap_work_packages s ON s.id = u.source_id "
        "WHERE u.source_table = 'roadmap_work_packages' "
        "AND (u.source_data IS NULL OR u.source_data = '')"
        + _only_ids(ids)
    )
    params = {} if ids is None else {"ids": list(ids)}
    for uid, raw, origin in conn.execute(text(sql), params).fetchall():
        data = None
        if raw not in (None, ""):
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                data = None
            if not isinstance(data, dict):
                data = {"raw": raw}
        if origin is not None:
            data = dict(data or {})
            data["origin_source_id"] = origin
        if data is None:
            continue
        conn.execute(
            text("UPDATE unified_work_packages SET source_data = :v WHERE id = :id"),  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
            {"v": json.dumps(data), "id": uid},
        )
        stats.add("roadmap_work_packages: filled source_data", 1)


def _int_list(raw):
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    out = []
    for item in raw if isinstance(raw, (list, tuple)) else []:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def _remap_dependencies(conn, table, ids, stats):
    """Rewrite each id in work_dependencies from the source store's id to the
    unified id of (source_table, id). An id with no mapping is dropped and
    counted. Runs once per row (dependencies_remapped_at)."""
    params = {"t": table}
    only = ""
    if ids is not None:
        only = " AND source_id = ANY(:ids)"
        params["ids"] = list(ids)
    rows = conn.execute(
        text(  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
            "SELECT id, work_dependencies FROM unified_work_packages "
            f"WHERE source_table = :t AND dependencies_remapped_at IS NULL{only}"
        ),
        params,
    ).fetchall()
    if not rows:
        return
    mapping = {
        old: new for old, new in conn.execute(
            text("SELECT source_id, id FROM unified_work_packages WHERE source_table = :t"),  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
            {"t": table},
        )
    }
    for uid, deps in rows:
        old_ids = _int_list(deps)
        new_ids = []
        for old in old_ids:
            new = mapping.get(old)
            if new is None:
                stats.add(f"{table}: dependencies dropped (no copy)", 1)
            elif int(new) not in new_ids:
                new_ids.append(int(new))
        if old_ids:
            conn.execute(
                text(  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
                    "UPDATE unified_work_packages SET work_dependencies = CAST(:v AS json), "
                    "dependencies_remapped_at = CURRENT_TIMESTAMP WHERE id = :id"
                ),
                {"v": json.dumps(new_ids), "id": uid},
            )
            stats.add(f"{table}: dependencies remapped", 1)
        else:
            conn.execute(
                text("UPDATE unified_work_packages SET dependencies_remapped_at = CURRENT_TIMESTAMP "  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
                     "WHERE id = :id"),
                {"id": uid},
            )


def _union_roadmap_links(conn, ids, stats):
    """work_package_dependencies pairs and work_package_capabilities links of
    the roadmap store become part of the unified row: a set-union into
    work_dependencies and capability_ids, once per row."""
    t = "roadmap_work_packages"
    params = {"t": t}
    only = ""
    if ids is not None:
        only = " AND source_id = ANY(:ids)"
        params["ids"] = list(ids)
    rows = conn.execute(
        text(  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
            "SELECT id, source_id, work_dependencies, capability_ids FROM unified_work_packages "
            f"WHERE source_table = :t AND dependencies_remapped_at IS NULL{only}"
        ),
        params,
    ).fetchall()
    if not rows:
        return
    mapping = {
        old: new for old, new in conn.execute(
            text("SELECT source_id, id FROM unified_work_packages WHERE source_table = :t"),  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
            {"t": t},
        )
    }
    pairs = {}
    if _table_exists(conn, "work_package_dependencies"):
        for wp_id, dep_id in conn.execute(
            text("SELECT work_package_id, dependency_id FROM work_package_dependencies")
        ):
            pairs.setdefault(wp_id, []).append(dep_id)
    caps = {}
    if _table_exists(conn, "work_package_capabilities"):
        for wp_id, cap_id in conn.execute(
            text("SELECT work_package_id, capability_id FROM work_package_capabilities")
        ):
            caps.setdefault(wp_id, []).append(cap_id)
    for uid, source_id, deps, cap_ids in rows:
        deps = _int_list(deps)
        cap_ids = _int_list(cap_ids)
        new_deps = list(deps)
        for dep in pairs.get(source_id, []):
            unified = mapping.get(dep)
            if unified is not None and int(unified) not in new_deps:
                new_deps.append(int(unified))
        new_caps = list(cap_ids)
        for cap in caps.get(source_id, []):
            if int(cap) not in new_caps:
                new_caps.append(int(cap))
        sets = ["dependencies_remapped_at = CURRENT_TIMESTAMP"]
        values = {"id": uid}
        if new_deps != deps:
            sets.append("work_dependencies = CAST(:deps AS json)")
            values["deps"] = json.dumps(new_deps)
            stats.add(f"{t}: dependency links added", len(new_deps) - len(deps))
        if new_caps != cap_ids:
            sets.append("capability_ids = CAST(:caps AS json)")
            values["caps"] = json.dumps(new_caps)
            stats.add(f"{t}: capability links added", len(new_caps) - len(cap_ids))
        conn.execute(text(f"UPDATE unified_work_packages SET {', '.join(sets)} WHERE id = :id"), values)  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store


def sync_source_rows(conn, table, ids=None, *, update_existing=False, fallback_org_id=None,
                     changed=None, dependency_changes=None, defer_links=None,
                     link_changes=None, relation_changes=None):
    """Copy rows of a retired store into unified_work_packages.

    The one per-row copy path. `ids` restricts it to those source ids (the
    bridge); None means every eligible row (the deploy command). A row whose
    `retired_into_id` or `retired_at` is set is never copied again, so a
    deleted copy stays deleted. With `update_existing` a row already copied
    updates only the columns named for it in `changed` ({source id: {source
    column, ...}}); `dependency_changes` ({source id: (old ids, new ids)}) is
    applied as a diff; `relation_changes` ({source id: {"dependencies": (added,
    removed), "capabilities": (added, removed)}}) is the roadmap store's association
    edits, applied as a diff too. A new row is always copied whole.

    Applies the same organisation attribution as backfill-work-package-org to
    the rows it touches, remaps dependencies, and fills the fields earlier
    merges did not copy. The plateau and gap values copied with a row become
    ArchiMate relationships in the same transaction: a new row's plateau is read
    from its source, an edited row's `link_changes` ({source id: {"plateau_id":
    new value or None}}) replace the link of that kind, and a row whose link
    attributes did not change is not touched. Applied at once, or, when the caller
    is inside a session flush and cannot flush again, through `defer_links({unified
    id: {"plateau_id": value or None}})`.
    Returns a dict of counts (empty when nothing changed).
    """
    stats = _Stats()
    spec = _SPEC_BY_TABLE[table]
    if ids is not None:
        ids = [int(i) for i in ids]
        if not ids:
            return stats

    inserted = _insert_missing(conn, spec, ids, stats, fallback_org_id)
    if update_existing:
        if changed:
            _update_existing(conn, spec, changed, stats)
        if dependency_changes and table in _DEP_SOURCE_COLUMN:
            _apply_dependency_changes(conn, table, dependency_changes, stats)
        if relation_changes and spec.get("union_links"):
            _apply_relation_changes(conn, table, relation_changes, stats)
    _fill_missing(conn, spec, ids, stats)
    pending_links = _links_to_apply(conn, table, inserted, link_changes, relation_changes)

    if ids is not None:
        # Only a copy with no organisation yet needs placing (and warning about); a
        # bulk edit of rows that have one reads them once and goes no further.
        unplaced = [
            row[0] for row in conn.execute(
                text("SELECT id FROM unified_work_packages "  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
                     "WHERE source_table = :t AND source_id = ANY(:ids) AND organization_id IS NULL"),
                {"t": table, "ids": ids},
            )
        ]
        if unplaced:
            stats.add(f"{table}: attributed", _attribute_org(conn, unified_ids=unplaced, emit=False))
            for source_id in [
                row[0] for row in conn.execute(
                    text("SELECT source_id FROM unified_work_packages "  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
                         "WHERE id = ANY(:uids) AND organization_id IS NULL"),
                    {"uids": unplaced},
                )
            ]:
                logger.warning(
                    "work package copy has no organisation: %s id %s (written outside a request "
                    "with no tenant context)", table, source_id)

    if pending_links:
        if ids is None:
            # The deploy merge: place the rows it just copied before linking them.
            _attribute_org(conn, unified_ids=sorted(pending_links), emit=False)
        if defer_links is not None:
            defer_links(pending_links)
        else:
            apply_link_changes(pending_links, stats=stats)

    if spec.get("remap_dependencies"):
        _remap_dependencies(conn, table, ids, stats)
    if spec.get("union_links"):
        _union_roadmap_links(conn, ids, stats)
    return stats


def _unified_ids(conn, table, source_ids):
    return {
        old: new for old, new in conn.execute(
            text("SELECT source_id, id FROM unified_work_packages "  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
                 "WHERE source_table = :t AND source_id = ANY(:ids)"),
            {"t": table, "ids": sorted(int(i) for i in source_ids)},
        )
    }


def _rows_with_associations(conn, source_ids):
    """The ids among these work_packages rows that hold a plateau or gap association."""
    found = set()
    for assoc_table, _key in _ASSOCIATIONS.values():
        found.update(r[0] for r in conn.execute(
            text(f"SELECT DISTINCT work_package_id FROM {assoc_table} "  # tenancy-ok: keyed by the work package's own id
                 "WHERE work_package_id = ANY(:ids)"), {"ids": list(source_ids)}))
    return found


def _advance_marker(executor, unified_ids, at):
    """The one writer of association_links_migrated_at: `at` is an _utcnow() taken before the association
    rows were read; the marker only moves forward. `executor` is a connection or a session."""
    unified_ids = list(unified_ids)
    if unified_ids:
        executor.execute(
            text("UPDATE unified_work_packages "  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
                 "SET association_links_migrated_at = "
                 "GREATEST(COALESCE(association_links_migrated_at, :at), :at) WHERE id = ANY(:ids)"),
            {"ids": unified_ids, "at": at})


def _links_to_apply(conn, table, inserted, link_changes, relation_changes=None):
    """{unified id: {"plateau_id": value or None}}: the link values to turn into
    relationships after this copy. A row copied by this call contributes the values
    its source holds (a NULL adds nothing); a row edited since contributes the value
    it was changed to, a NULL clearing the link. A row whose link attributes did not
    change is not listed."""
    columns = _LINK_SOURCE_COLUMNS.get(table)
    if not columns:
        return {}
    out = {}
    if inserted:
        by_source = {old: new for new, old in inserted}
        for row in conn.execute(
            text(f'SELECT id, {", ".join(columns)} FROM "{table}" WHERE id = ANY(:ids)'),
            {"ids": sorted(by_source)},
        ):
            values = {c: v for c, v in zip(columns, row[1:]) if v is not None}
            if values:
                out[by_source[row[0]]] = values
        if table == _ASSOCIATION_SOURCE:
            # A new row's association rows are written in the same flush, so they are
            # visible here. A row that holds some migrates them and is marked by the
            # link step; one that holds none is marked now.
            clock = _utcnow()
            holding = _rows_with_associations(conn, sorted(by_source))
            for source_id, uid in by_source.items():
                if source_id in holding:
                    out.setdefault(uid, {})[_ASSOC_MIGRATE] = True
            _advance_marker(conn, [u for s_, u in by_source.items() if s_ not in holding], clock)
    if relation_changes and table == _ASSOCIATION_SOURCE:
        mapping = _unified_ids(conn, table, relation_changes)
        for source_id, change in relation_changes.items():
            diff = {k: change[k] for k in _ASSOCIATIONS if change.get(k) and any(change[k])}
            uid = mapping.get(int(source_id))
            if diff and uid is not None:
                out.setdefault(int(uid), {})[_ASSOC_CHANGES] = diff
    if link_changes:
        mapping = {
            old: new for old, new in conn.execute(
                text("SELECT source_id, id FROM unified_work_packages "  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
                     "WHERE source_table = :t AND source_id = ANY(:ids)"),
                {"t": table, "ids": sorted(int(i) for i in link_changes)},
            )
        }
        for source_id, values in link_changes.items():
            uid = mapping.get(int(source_id))
            if uid is not None:
                out.setdefault(int(uid), {}).update(values)
    return out


def _backfill_retired_at(conn, dry_run, stats):
    for table in RETIRED_TABLES:
        n = _count(conn, f'SELECT count(*) FROM "{table}" WHERE retired_into_id IS NOT NULL AND retired_at IS NULL')
        if not n:
            continue
        if not dry_run:
            conn.execute(text(
                f'UPDATE "{table}" SET retired_at = CURRENT_TIMESTAMP '
                "WHERE retired_into_id IS NOT NULL AND retired_at IS NULL"))
        stats.add(f"{table}: retired_at set", n)


def _backfill_links(conn, stats):
    """Point child rows that still key on a retired store at the unified row."""
    if _table_exists(conn, "kanban_cards"):
        stats.add("kanban_cards: unified_work_package_id set", conn.execute(text(  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
            "UPDATE kanban_cards c SET unified_work_package_id = r.retired_into_id "
            "FROM roadmap_work_packages r WHERE r.id = c.work_package_id "
            "AND c.unified_work_package_id IS NULL AND r.retired_into_id IS NOT NULL")).rowcount)
    if _table_exists(conn, "deliverables"):
        stats.add("deliverables: unified_work_package_id set", conn.execute(text(  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
            "UPDATE deliverables d SET unified_work_package_id = w.retired_into_id "
            "FROM work_packages w WHERE w.id = d.work_package_id "
            "AND d.unified_work_package_id IS NULL AND w.retired_into_id IS NOT NULL")).rowcount)


DELIVERABLE_TABLE = "roadmap_deliverables"


def unmerged_counts(conn):
    tables = RETIRED_TABLES + ((DELIVERABLE_TABLE,) if _table_exists(conn, DELIVERABLE_TABLE) else ())
    return {
        table: _count(conn, f'SELECT count(*) FROM "{table}" WHERE {_UNMERGED}')
        for table in tables
    }


def _attributable_null_copies(conn):
    """Copied rows (source_table set) with no organisation although the chain of
    backfill-work-package-org would place them."""
    chain = " OR ".join(
        f'EXISTS (SELECT 1 FROM "{parent}" p WHERE p.id = t.{column} AND p.organization_id IS NOT NULL)'
        for _label, parent, column in _BACKFILL_STEPS
    ) + " OR EXISTS (SELECT 1 FROM users u WHERE u.id = t.created_by AND u.organization_id IS NOT NULL)"
    return _count(
        conn,
        "SELECT count(*) FROM unified_work_packages t "  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
        f"WHERE t.source_table IS NOT NULL AND t.organization_id IS NULL AND ({chain})",
    )


def _quarantined_copies(conn):
    return _count(
        conn,
        "SELECT count(*) FROM unified_work_packages "  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
        "WHERE source_table IS NOT NULL AND organization_id IS NULL",
    ) - _attributable_null_copies(conn)


# --- schema the reconcile step cannot do ----------------------------------------
# reconcile-schema only ADDs plain nullable columns (ADR 0002): no foreign key, no
# constraint change, no index. Each step below checks the catalogue first and is a
# no-op when the work is already done.

_COPY_INDEX = "uq_unified_wp_source_copy"

# (table, column, referenced table, ON DELETE rule)
_FOREIGN_KEYS = (
    ("unified_work_packages", "parent_id", "unified_work_packages", "SET NULL"),
    ("deliverables", "unified_work_package_id", "unified_work_packages", "CASCADE"),
    ("kanban_cards", "unified_work_package_id", "unified_work_packages", "SET NULL"),
    ("work_packages", "retired_into_id", "unified_work_packages", "SET NULL"),
    ("roadmap_work_packages", "retired_into_id", "unified_work_packages", "SET NULL"),
    ("implementation_work_packages", "retired_into_id", "unified_work_packages", "SET NULL"),
    ("technology_roadmap_initiatives", "retired_into_id", "unified_work_packages", "SET NULL"),
    ("roadmap_deliverables", "retired_into_id", "deliverables", "SET NULL"),
)

# (table, column, width the column must have)
_WIDE_COLUMNS = (
    ("unified_work_packages", "status", 50),
    ("deliverables", "delivery_status", 50),
)


def _column_exists(conn, table, column):
    return bool(conn.execute(
        text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
        {"t": table, "c": column},
    ).first())


def _ensure_column_width(conn, table, column, width, dry_run=False):
    """Widen a varchar column that is shorter than `width`. True when changed."""
    row = conn.execute(
        text("SELECT character_maximum_length FROM information_schema.columns "
             "WHERE table_name = :t AND column_name = :c"),
        {"t": table, "c": column},
    ).first()
    if row is None or row[0] is None or row[0] >= width:
        return False
    if not dry_run:
        conn.execute(text(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" TYPE varchar({width})'))
    return True


def _duplicate_copies(conn):
    return conn.execute(text(  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
        "SELECT source_table, source_id, count(*), array_agg(id ORDER BY id) "
        "FROM unified_work_packages WHERE source_table IS NOT NULL "
        "GROUP BY source_table, source_id HAVING count(*) > 1 ORDER BY 1, 2"
    )).fetchall()


def _ensure_copy_index(conn, dry_run=False):
    """A unique partial index on (source_table, source_id): one copy per source row.
    Refuses, printing the duplicates, rather than guess which copy to keep."""
    duplicates = _duplicate_copies(conn)
    if duplicates:
        click.echo("unified_work_packages holds more than one copy of a source row; "
                   "resolve these by hand (the command will not choose):")
        for source_table, source_id, n, ids in duplicates:
            click.echo(f"  {source_table} id {source_id}: {n} copies, unified ids {list(ids)}")
        raise SystemExit(1)
    exists = conn.execute(
        text("SELECT 1 FROM pg_indexes WHERE tablename = 'unified_work_packages' AND indexname = :n"),
        {"n": _COPY_INDEX},
    ).first()
    if exists:
        return False
    if not dry_run:
        conn.execute(text(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {_COPY_INDEX} "
            "ON unified_work_packages (source_table, source_id) WHERE source_table IS NOT NULL"
        ))
    return True


def _has_foreign_key(conn, table, column, target):
    return bool(conn.execute(
        text(
            "SELECT 1 FROM pg_constraint c WHERE c.contype = 'f' "
            "AND c.conrelid = to_regclass(:t) AND c.confrelid = to_regclass(:target) "
            "AND c.conkey = ARRAY[(SELECT a.attnum FROM pg_attribute a "
            "WHERE a.attrelid = c.conrelid AND a.attname = :c)]::smallint[]"
        ),
        {"t": table, "target": target, "c": column},
    ).first())


def _ensure_foreign_keys(conn, stats, dry_run=False):
    """Create each foreign key the models declare but a reconcile-added column
    never got. Values that point at nothing are set to NULL (and counted) first,
    as the constraint would refuse them."""
    for table, column, target, rule in _FOREIGN_KEYS:
        if not (_table_exists(conn, table) and _column_exists(conn, table, column)):
            continue
        if _has_foreign_key(conn, table, column, target):
            continue
        orphans = _count(
            conn,
            f'SELECT count(*) FROM "{table}" t WHERE t."{column}" IS NOT NULL '
            f'AND NOT EXISTS (SELECT 1 FROM "{target}" p WHERE p.id = t."{column}")',
        )
        stats.add(f"{table}.{column}: orphan values set to NULL", orphans)
        stats.add(f"{table}.{column}: foreign key added", 1)
        if dry_run:
            continue
        if orphans:
            conn.execute(text(
                f'UPDATE "{table}" t SET "{column}" = NULL WHERE t."{column}" IS NOT NULL '  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
                f'AND NOT EXISTS (SELECT 1 FROM "{target}" p WHERE p.id = t."{column}")'
            ))
        conn.execute(text(
            f'ALTER TABLE "{table}" ADD CONSTRAINT "fk_{table}_{column}" '
            f'FOREIGN KEY ("{column}") REFERENCES "{target}" (id) ON DELETE {rule}'
        ))


# --- the one deliverable store --------------------------------------------------
def _merge_roadmap_deliverables(conn, stats, dry_run=False):
    """Copy every roadmap_deliverables row into deliverables (the one deliverable
    store). The copy belongs to the unified work package the row points at, or else
    the one its roadmap work package was copied to. The source row is marked with
    retired_into_id and retired_at and is never copied again. A row with no work
    package left to belong to is marked retired and not copied."""
    if not _table_exists(conn, DELIVERABLE_TABLE) or not _column_exists(
            conn, DELIVERABLE_TABLE, "retired_at"):
        return
    rows = conn.execute(text(
        "SELECT rd.id, rd.name, rd.description, rd.status, rd.deliverable_type, "
        "rd.due_date, rd.delivered_date, rd.review_date, rd.approval_criteria, "
        "rd.quality_score, rd.approval_status, rd.related_task_ids, rd.created_at, "
        "rd.updated_at, COALESCE(u.id, r.retired_into_id), ac.id, "
        "rd.auto_generated, rd.generation_method, rd.created_by, rd.updated_by "
        'FROM roadmap_deliverables rd '
        "LEFT JOIN unified_work_packages u ON u.id = rd.unified_work_package_id "  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
        "LEFT JOIN roadmap_work_packages r ON r.id = rd.work_package_id "
        "LEFT JOIN application_components ac ON ac.id = rd.source_application_id "  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
        "WHERE rd.retired_into_id IS NULL AND rd.retired_at IS NULL ORDER BY rd.id"
    )).fetchall()
    for (source_id, name, description, status, dtype, due, delivered, review, criteria,
         quality, approval, tasks, created, updated, unified_id, application_id,
         auto_generated, generation_method, created_by, updated_by) in rows:
        if unified_id is None:
            stats.add("roadmap_deliverables: retired without a work package", 1)
            if not dry_run:
                conn.execute(text(
                    "UPDATE roadmap_deliverables SET retired_at = CURRENT_TIMESTAMP WHERE id = :i"),
                    {"i": source_id})
            continue
        stats.add("roadmap_deliverables: copied", 1)
        if dry_run:
            continue
        # Provenance columns, copied where the target has them.
        provenance = {
            c: v for c, v in (
                ("auto_generated", auto_generated), ("generation_method", generation_method),
                ("created_by", created_by), ("updated_by", updated_by))
            if _column_exists(conn, "deliverables", c)
        }
        new_id = conn.execute(text(
            "INSERT INTO deliverables (name, description, unified_work_package_id, delivery_status, "
            "deliverable_type, target_date, delivered_date, review_date, approval_criteria, "
            "quality_score, approval_status, related_task_ids, application_component_id, "
            "created_at, updated_at" + "".join(", " + c for c in provenance) + ") "
            "VALUES (:name, :description, :uid, :status, :dtype, CAST(:due AS date), "
            "CAST(:delivered AS date), :review, :criteria, :quality, :approval, :tasks, :application, "
            "COALESCE(:created, CURRENT_TIMESTAMP), COALESCE(:updated, CURRENT_TIMESTAMP)"
            + "".join(", :p_" + c for c in provenance) + ") "
            "RETURNING id"
        ), {
            **{"p_" + c: v for c, v in provenance.items()},
            "name": name, "description": description, "uid": unified_id, "status": status,
            "dtype": dtype, "due": due, "delivered": delivered, "review": review,
            "criteria": criteria, "quality": quality, "approval": approval, "tasks": tasks,
            "application": application_id, "created": created, "updated": updated,
        }).scalar()
        conn.execute(text(  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
            "UPDATE roadmap_deliverables SET retired_into_id = :n, retired_at = CURRENT_TIMESTAMP "
            "WHERE id = :i"), {"n": new_id, "i": source_id})


# --- plateau and gap links ------------------------------------------------------
def apply_link_changes(changes, *, replace=True, stats=None):
    """Turn {unified id: {"plateau_id": value or None, "gap_id": ...}} into the
    ArchiMate relationships, one savepoint per work package. With `replace` the
    value replaces the relationship of its kind (None clears it), except for a target
    the old store's association tables still hold for that work package; without
    `replace`, a link is only added. Two more keys carry the old store's association
    tables: `_associations` adds a relationship for each association row and marks the
    unified row (once, never again); `_association_changes` applies the ids an old
    screen added and removed. A link whose plateau or gap is missing or belongs to
    another organisation is skipped and logged; a failure rolls back that work
    package's links only, is logged, and leaves the transaction usable. The unified
    row is locked (FOR UPDATE) before its links are read, so concurrent edits run one
    after the other. Returns the unified ids whose links were handled (all of them
    but the ones that failed).

    A lock conflict (deadlock detected, serialization failure, lock not available) is not
    contained: it is re-raised after the savepoint has rolled back, so the flush and the
    request fail and roll back whole rather than leave the two stores apart. A call that
    touches several work packages takes every lock first, in ascending id order, before it
    reads or writes any link, so two calls cannot wait on each other in opposite orders."""
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.services import work_package_service as svc

    stats = stats if stats is not None else _Stats()
    done = []
    for uid in sorted(changes):
        try:
            with db.session.begin_nested():
                svc.lock_work_package(uid)
        except Exception as exc:  # noqa: BLE001
            if _is_lock_conflict(exc):
                raise
            logger.warning("work package %s not locked before its links: %s", uid, exc)
    for uid, values in sorted(changes.items()):
        org_id = None
        # The read of the row sits inside the savepoint too, so a failure anywhere in a
        # work package's link step leaves the outer transaction usable.
        try:
            with db.session.begin_nested():
                clock = _utcnow()  # before any association row is read
                row = db.session.execute(text(
                    "SELECT organization_id, source_table, source_id, association_links_migrated_at "
                    "FROM unified_work_packages "  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
                    "WHERE id = :i FOR UPDATE"),
                    {"i": uid}).first()
                org_id = row[0] if row is not None else None
                if row is None:
                    continue
                if org_id is None:
                    logger.warning("plateau/gap link not applied: work package %s has no organisation "
                                   "(values %s)", uid, values)
                    stats.add("unified_work_packages: link skipped (no organisation)", len(values))
                    continue
                source_id = row[2] if row[1] == _ASSOCIATION_SOURCE else None
                marker = row[3]
                wp = db.session.execute(  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
                    db.select(UnifiedWorkPackage).where(UnifiedWorkPackage.id == uid)).scalar_one()
                # The organisation may have just been written by SQL (placement).
                db.session.expire(wp, ["organization_id", "archimate_element_id"])
                for key, value in values.items():
                    if key in (_ASSOC_MIGRATE, _ASSOC_CHANGES):
                        continue
                    try:
                        links = svc._resolve_links({key: value}, org_id)
                    except svc.WorkPackageError as exc:
                        logger.warning(
                            "plateau/gap link skipped: work package %s, %s %s, organisation %s: %s "
                            "(missing, or it belongs to another organisation)",
                            uid, key[:-3], value, org_id, exc)
                        stats.add("unified_work_packages: link not migrated (missing or other organisation)", 1)
                        continue
                    present = [t for _rid, t in svc._link_targets(wp, key, org_id)]
                    keep = {key: set(_association_ids(source_id, key))} if source_id is not None else None
                    others = [t for t in present if t != value and t not in ((keep or {}).get(key) or ())]
                    if value is not None and value in present and not (replace and others):
                        continue
                    if value is not None:
                        stats.add(f"unified_work_packages: {key[:-3]} relationships created", 1)
                    svc._apply_links(wp, links, org_id, replace=replace, rollback=False,
                                     round_trip=False, keep=keep)
                if values.get(_ASSOC_CHANGES) and source_id is not None:
                    if marker is not None:
                        # Heal first: every association row newer than the marker (an unbridged
                        # Core insert too) is applied, add-only, before the marker moves past it.
                        _migrate_associations(wp, org_id, source_id, stats, since=marker)
                    _apply_association_changes(wp, org_id, values[_ASSOC_CHANGES], stats,
                                               source_id=source_id)
                    # A row not yet migrated keeps its empty marker: advancing it would skip
                    # the older rows the deploy has yet to migrate.
                    if marker is not None:
                        _advance_marker(db.session, [uid], clock)
                if values.get(_ASSOC_MIGRATE) and source_id is not None:
                    since = None if values[_ASSOC_MIGRATE] is True else values[_ASSOC_MIGRATE]
                    _migrate_associations(wp, org_id, source_id, stats, since=since)
                    _advance_marker(db.session, [uid], clock)
            done.append(uid)
        except Exception as exc:  # noqa: BLE001 - a link failure must not take the transaction down
            if _is_lock_conflict(exc):
                raise
            logger.warning(
                "plateau/gap links rolled back: work package %s, values %s, organisation %s: %s",
                uid, values, org_id, exc)
            stats.add("unified_work_packages: link step failed", 1)
    return done


def _association_ids(source_id, key, since=None):
    """Ids of the plateaus (key plateau_id) or gaps (gap_id) the old row `source_id`
    holds in its association table, in the order they were written. With `since`, only
    the rows written after that time (naive UTC, like created_at)."""
    for assoc_table, link_key in _ASSOCIATIONS.values():
        if link_key == key:
            later = " AND created_at > :since" if since is not None else ""
            params = {"i": source_id, **({"since": since} if since is not None else {})}
            return [r[0] for r in db.session.execute(text(
                f"SELECT {key} FROM {assoc_table} WHERE work_package_id = :i{later} ORDER BY id"),  # tenancy-ok: keyed by the work package's own id
                params)]
    return []


def _column_ids(source_id, key):
    """{the id} when the old row's own column of that name still holds one, else {}."""
    if key not in _LINK_SOURCE_COLUMNS.get(_ASSOCIATION_SOURCE, ()):
        return set()
    value = db.session.execute(text(
        f'SELECT "{key}" FROM "{_ASSOCIATION_SOURCE}" WHERE id = :i'), {"i": source_id}).scalar()  # tenancy-ok: keyed by the work package's own id
    return {value} if value is not None else set()


def _migrate_associations(wp, org_id, source_id, stats, since=None):
    """Add a relationship for each row of the old row's association tables (adding
    only; the rows stay), or only the rows written after `since`. A plateau or gap that
    is missing or of another organisation is counted and skipped."""
    from app.services import work_package_service as svc

    for _attr, (_table, key) in _ASSOCIATIONS.items():
        present = {t for _rid, t in svc._link_targets(wp, key, org_id)}
        for target_id in dict.fromkeys(_association_ids(source_id, key, since)):
            if target_id in present:
                continue
            try:
                links = svc._resolve_links({key: target_id}, org_id)
            except svc.WorkPackageError as exc:
                logger.warning(
                    "association link skipped: work package %s, %s %s, organisation %s: %s",
                    wp.id, key[:-3], target_id, org_id, exc)
                stats.add("unified_work_packages: association link not migrated (missing or other organisation)", 1)
                continue
            svc._apply_links(wp, links, org_id, replace=False, rollback=False, round_trip=False)
            present.add(target_id)
            stats.add(f"unified_work_packages: {key[:-3]} association links migrated", 1)


def _apply_association_changes(wp, org_id, change, stats, source_id=None):
    """An old screen's edit of WorkPackage.gaps / .plateaus: the ids added get a
    relationship, the ids removed lose theirs -- except an id the old row still holds,
    in its own plateau_id column or in a remaining association row of its own."""
    from app.services import work_package_service as svc

    for attr, (added, removed) in change.items():
        key = _ASSOCIATIONS[attr][1]
        if removed and source_id is not None:
            still = set(_association_ids(source_id, key)) | _column_ids(source_id, key)
            removed = [t for t in removed if t not in still]
        if removed:
            stats.add(f"unified_work_packages: {key[:-3]} relationships removed",
                      svc.remove_link_targets(wp, key, org_id, removed))
        present = {t for _rid, t in svc._link_targets(wp, key, org_id)}
        for target_id in added:
            if target_id in present:
                continue
            try:
                links = svc._resolve_links({key: target_id}, org_id)
            except svc.WorkPackageError as exc:
                logger.warning("association link skipped: work package %s, %s %s: %s",
                               wp.id, key[:-3], target_id, exc)
                stats.add("unified_work_packages: association link not migrated (missing or other organisation)", 1)
                continue
            svc._apply_links(wp, links, org_id, replace=False, rollback=False, round_trip=False)
            present.add(target_id)
            stats.add(f"unified_work_packages: {key[:-3]} relationships created", 1)


def _link_columns_to_relationships(stats, dry_run=False, unified_ids=None):
    """The one-time migration of a work package's plateau and gap links into the
    ArchiMate relationships (realization to the plateau's element, association to the
    gap's element), run at the end of merge-work-package-stores and of
    backfill-work-package-org, over every row that now has an organisation:
      * the unified plateau_id / gap_id columns: each value becomes a relationship,
        then BOTH columns are set to NULL in the same step, so a later deploy has
        nothing to migrate and a link moved or cleared on the new screens is never
        brought back;
      * the old store's association tables (gap_work_packages, work_package_plateaus):
        each row of a copied work_packages row becomes a relationship, adding only
        (the rows stay: old screens still read them), and the unified row gets
        association_links_migrated_at so it is never migrated again.
    A row with no organisation keeps its values until a run places it; the run that
    places it migrates it. A relationship that exists is kept; a plateau or gap of
    another organisation, or one that no longer exists, is counted and dropped with
    the column. A row whose links failed to write keeps its values for the next
    run. PR 3 drops the columns, the marker and the tables."""
    clock = _utcnow()  # before the association rows are read
    scope = "AND id = ANY(:uids) " if unified_ids is not None else ""
    params = {"uids": list(unified_ids)} if unified_ids is not None else {}
    # A marked row with an association row written after its marker (by a writer the bridge
    # did not see) is migrated again, adding only those rows.
    later = " OR ".join(
        f"EXISTS (SELECT 1 FROM {table} a WHERE a.work_package_id = source_id "
        "AND a.created_at > association_links_migrated_at)"
        for table, _key in _ASSOCIATIONS.values())
    has_later = (f"(source_table = :src AND association_links_migrated_at IS NOT NULL "
                 f"AND source_id IS NOT NULL AND ({later}))")
    rows = db.session.execute(text(
        "SELECT id, plateau_id, gap_id, source_table, source_id, association_links_migrated_at, "
        f"{has_later} "
        "FROM unified_work_packages "  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
        "WHERE organization_id IS NOT NULL AND (plateau_id IS NOT NULL OR gap_id IS NOT NULL "
        "OR (source_table = :src AND association_links_migrated_at IS NULL) "
        f"OR {has_later}) "
        + scope + "ORDER BY id"
    ), dict(params, src=_ASSOCIATION_SOURCE)).fetchall()
    holding = _rows_with_associations(
        db.session.connection(),
        [r[4] for r in rows if r[3] == _ASSOCIATION_SOURCE and r[5] is None and r[4] is not None])
    changes = {}
    empty_marks = []
    for wp_id, plateau_id, gap_id, source_table, source_id, marked, newer in rows:
        values = {}
        if plateau_id is not None:
            values["plateau_id"] = plateau_id
        if gap_id is not None:
            values["gap_id"] = gap_id
        if source_table == _ASSOCIATION_SOURCE and marked is None:
            if source_id in holding:
                values[_ASSOC_MIGRATE] = True
            else:
                empty_marks.append(wp_id)
        elif newer:
            values[_ASSOC_MIGRATE] = marked
        if values:
            changes[wp_id] = values
    stats.add("unified_work_packages: link columns to migrate",
              sum(1 for v in changes.values() if "plateau_id" in v or "gap_id" in v))
    stats.add("unified_work_packages: rows with association links to migrate",
              sum(1 for v in changes.values() if _ASSOC_MIGRATE in v))
    if dry_run:
        return
    if empty_marks:
        _advance_marker(db.session, empty_marks, clock)
    if not changes:
        return
    done = apply_link_changes(changes, replace=False, stats=stats)
    with_columns = [u for u in done if "plateau_id" in changes[u] or "gap_id" in changes[u]]
    if with_columns:
        res = db.session.execute(text(
            "UPDATE unified_work_packages SET plateau_id = NULL, gap_id = NULL "  # tenancy-ok: one-shot deploy data step run by the schema owner with no request context; rows are addressed by their own key or copied wholesale between the retired stores and the one store
            "WHERE id = ANY(:ids)"), {"ids": with_columns})
        stats.add("unified_work_packages: link columns set to NULL", res.rowcount)


def _merge_one(conn, spec, dry_run):
    """Merge every eligible row of one store (the deploy command's step)."""
    table = spec["table"]
    if dry_run:
        eligible = _count(conn, f'SELECT count(*) FROM "{table}" WHERE {_UNMERGED}')
        if eligible:
            click.echo(f"  - {table}: would merge {eligible} row(s) into unified_work_packages")
        else:
            click.echo(f"  {table}: nothing to merge")
        return _Stats()
    stats = sync_source_rows(conn, table)
    for key, n in stats.items():
        click.echo(f"  + {key}: {n}")
    if not stats:
        click.echo(f"  {table}: nothing to merge")
    return stats


@click.command("merge-work-package-stores")
@click.option("--dry-run", is_flag=True, help="Report what would change; change nothing.")
@click.option("--verify", is_flag=True,
              help="Change nothing; exit non-zero, with per-store counts, if any retired store "
                   "holds a row that has not been copied, or a copied row has no organisation "
                   "although the attribution chain would place it.")
@with_appcontext
def merge_work_package_stores(dry_run, verify):
    """Merge technology_roadmap_initiatives, roadmap_work_packages,
    implementation_work_packages and work_packages into unified_work_packages,
    and roadmap_deliverables into deliverables, recording provenance and marking
    each merged source row with retired_into_id and retired_at. Remaps
    dependencies, fills fields earlier merges did not copy, makes sure the
    constraints reconcile-schema cannot add exist, and records plateau and gap
    links as ArchiMate relationships. Never drops a source row or table."""
    conn = db.session.connection()

    if verify:
        counts = unmerged_counts(conn)
        for table, n in counts.items():
            click.echo(f"  {table}: {n} unmerged row(s)")
        attributable = _attributable_null_copies(conn)
        quarantined = _quarantined_copies(conn)
        click.echo(f"  unified_work_packages: {attributable} copied row(s) with no organisation "
                   f"that the attribution chain would place")
        click.echo(f"  unified_work_packages: {quarantined} copied row(s) unattributable "
                   f"(quarantined; information only)")
        db.session.rollback()
        if any(counts.values()) or attributable:
            click.echo("merge-work-package-stores --verify: FAILED, rows are not in the one store "
                       "or a copy has no organisation.")
            raise SystemExit(1)
        click.echo("merge-work-package-stores --verify: ok, every retired store row is copied.")
        return

    total = _Stats()
    try:
        _merge_all(conn, dry_run, total)
    except SystemExit:
        db.session.rollback()
        raise

    if dry_run:
        click.echo("dry-run: no changes committed.")
        db.session.rollback()
        return

    db.session.commit()
    click.echo(f"merge-work-package-stores: done. changed={total.total()}")


def _merge_all(conn, dry_run, total):
    if not dry_run:
        if _ensure_business_capability_nullable(conn):
            click.echo("  + unified_work_packages.business_capability: dropped NOT NULL")
            total.add("constraint", 1)
        if _table_exists(conn, "deliverables") and _ensure_deliverable_legacy_key_nullable(conn):
            click.echo("  + deliverables.work_package_id: dropped NOT NULL")
            total.add("constraint", 1)
    for table, column, width in _WIDE_COLUMNS:
        if _table_exists(conn, table) and _ensure_column_width(conn, table, column, width, dry_run):
            click.echo(f"  {'-' if dry_run else '+'} {table}.{column}: widened to {width}")
            total.add("column widened", 1)
    if _ensure_copy_index(conn, dry_run):
        click.echo(f"  {'-' if dry_run else '+'} unified_work_packages: unique index on the copy key")
        total.add("index created", 1)

    marked = _Stats()
    _backfill_retired_at(conn, dry_run, marked)
    for key, n in marked.items():
        click.echo(f"  {'-' if dry_run else '+'} {key}: {n}")
    total.merge(marked)

    constraints = _Stats()
    _ensure_foreign_keys(conn, constraints, dry_run)
    for key, n in constraints.items():
        click.echo(f"  {'-' if dry_run else '+'} {key}: {n}")
    total.merge(constraints)

    for spec in _MERGE_SOURCES:
        total.merge(_merge_one(conn, spec, dry_run))

    deliverables = _Stats()
    _merge_roadmap_deliverables(conn, deliverables, dry_run)
    for key, n in deliverables.items():
        click.echo(f"  {'-' if dry_run else '+'} {key}: {n}")
    total.merge(deliverables)

    if not dry_run:
        links = _Stats()
        _backfill_links(conn, links)
        for key, n in links.items():
            click.echo(f"  + {key}: {n}")
        total.merge(links)

    relationships = _Stats()
    _link_columns_to_relationships(relationships, dry_run)
    for key, n in relationships.items():
        click.echo(f"  {'-' if dry_run else '+'} {key}: {n}")
    total.merge(relationships)


def init_app(app):
    """Register the work package consolidation CLI commands."""
    app.cli.add_command(backfill_work_package_org)
    app.cli.add_command(merge_work_package_stores)
