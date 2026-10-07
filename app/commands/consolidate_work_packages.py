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

import click
from flask.cli import with_appcontext
from sqlalchemy import text

from app import db

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
            "owner_id", "estimated_cost", "actual_cost", "plateau_id",
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


def _insert_missing(conn, spec, ids, stats):
    table = spec["table"]
    params = {"source_table": table}
    if ids is not None:
        params["ids"] = list(ids)
    insert_columns = (
        list(spec["columns"]) + [t for _, t in spec["select_extra"]] + [t for t, _ in spec["fills"]]
        + ["context", "scope", "organization_id", "source_table", "source_id"]
    )
    select_columns = (
        [f"s.{c}" for c in spec["columns"]]
        + [e for e, _ in spec["select_extra"]]
        + [e for _, e in spec["fills"]]
        # context/scope have only a Python-side ORM default (no
        # server_default), so a raw INSERT bypassing the ORM must supply
        # both explicitly or trip their NOT NULL constraint.
        + ["'architecture'", "'enterprise'", spec["org_expr"], f"'{table}'", "s.id"]
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
        "RETURNING id AS new_id, source_id AS old_id"
        ") "
        f'UPDATE "{table}" SET retired_into_id = inserted.new_id, retired_at = CURRENT_TIMESTAMP '
        f'FROM inserted WHERE "{table}".id = inserted.old_id'
    )
    stats.add(f"{table}: copied", conn.execute(text(sql), params).rowcount)
    # A unified copy that already exists but whose source row was never marked
    # (an interrupted run): link it, do not copy again.
    relink = (
        f'UPDATE "{table}" AS s SET retired_into_id = uwp.id, retired_at = CURRENT_TIMESTAMP '
        "FROM unified_work_packages uwp "
        "WHERE uwp.source_table = :source_table AND uwp.source_id = s.id "
        f"AND s.retired_into_id IS NULL AND s.retired_at IS NULL{_only_ids(ids)}"
    )
    stats.add(f"{table}: linked", conn.execute(text(relink), params).rowcount)


def _update_existing(conn, spec, ids, stats):
    """Bridge only: a source row edited after its copy was made updates the copy."""
    table = spec["table"]
    assigns = (
        [f"{c} = s.{c}" for c in spec["columns"]]
        + [f"{t} = {e}" for e, t in spec["select_extra"]]
    )
    reset = ", dependencies_remapped_at = NULL" if (
        spec.get("remap_dependencies") or spec.get("union_links")) else ""
    sql = (
        f"UPDATE unified_work_packages AS u SET {', '.join(assigns)}{reset} "
        f'FROM "{table}" s '
        "WHERE u.source_table = :source_table AND u.source_id = s.id AND s.id = ANY(:ids) "
        "AND s.retired_into_id IS NOT NULL"
    )
    stats.add(f"{table}: updated", conn.execute(
        text(sql), {"source_table": table, "ids": list(ids)}).rowcount)


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
            text("UPDATE unified_work_packages SET source_data = :v WHERE id = :id"),
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
        text(
            "SELECT id, work_dependencies FROM unified_work_packages "
            f"WHERE source_table = :t AND dependencies_remapped_at IS NULL{only}"
        ),
        params,
    ).fetchall()
    if not rows:
        return
    mapping = {
        old: new for old, new in conn.execute(
            text("SELECT source_id, id FROM unified_work_packages WHERE source_table = :t"),
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
                text(
                    "UPDATE unified_work_packages SET work_dependencies = CAST(:v AS json), "
                    "dependencies_remapped_at = CURRENT_TIMESTAMP WHERE id = :id"
                ),
                {"v": json.dumps(new_ids), "id": uid},
            )
            stats.add(f"{table}: dependencies remapped", 1)
        else:
            conn.execute(
                text("UPDATE unified_work_packages SET dependencies_remapped_at = CURRENT_TIMESTAMP "
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
        text(
            "SELECT id, source_id, work_dependencies, capability_ids FROM unified_work_packages "
            f"WHERE source_table = :t AND dependencies_remapped_at IS NULL{only}"
        ),
        params,
    ).fetchall()
    if not rows:
        return
    mapping = {
        old: new for old, new in conn.execute(
            text("SELECT source_id, id FROM unified_work_packages WHERE source_table = :t"),
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
        conn.execute(text(f"UPDATE unified_work_packages SET {', '.join(sets)} WHERE id = :id"), values)


def sync_source_rows(conn, table, ids=None, *, update_existing=False, fallback_org_id=None):
    """Copy rows of a retired store into unified_work_packages.

    The one per-row copy path. `ids` restricts it to those source ids (the
    bridge); None means every eligible row (the deploy command). A row whose
    `retired_into_id` or `retired_at` is set is never copied again, so a
    deleted copy stays deleted. With `update_existing` a row already copied
    updates its copy from the source (the bridge, for an edited row).

    Applies the same organisation attribution as backfill-work-package-org to
    the rows it touches, remaps dependencies, and fills the fields earlier
    merges did not copy. Returns a dict of counts (empty when nothing changed).
    """
    stats = _Stats()
    spec = _SPEC_BY_TABLE[table]
    if ids is not None:
        ids = [int(i) for i in ids]
        if not ids:
            return stats

    _insert_missing(conn, spec, ids, stats)
    if update_existing:
        _update_existing(conn, spec, ids, stats)
    _fill_missing(conn, spec, ids, stats)

    if ids is not None:
        uids = [
            row[0] for row in conn.execute(
                text("SELECT id FROM unified_work_packages WHERE source_table = :t AND source_id = ANY(:ids)"),
                {"t": table, "ids": ids},
            )
        ]
        if uids:
            stats.add(f"{table}: attributed", _attribute_org(conn, unified_ids=uids, emit=False))
            if fallback_org_id is not None:
                # A row the chain could not attribute, written from a request:
                # it belongs to the organisation that made the request.
                stats.add(
                    f"{table}: attributed to caller",
                    conn.execute(
                        text("UPDATE unified_work_packages SET organization_id = :org "
                             "WHERE id = ANY(:uids) AND organization_id IS NULL"),
                        {"org": fallback_org_id, "uids": uids},
                    ).rowcount,
                )

    if spec.get("remap_dependencies"):
        _remap_dependencies(conn, table, ids, stats)
    if spec.get("union_links"):
        _union_roadmap_links(conn, ids, stats)
    return stats


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
        stats.add("kanban_cards: unified_work_package_id set", conn.execute(text(
            "UPDATE kanban_cards c SET unified_work_package_id = r.retired_into_id "
            "FROM roadmap_work_packages r WHERE r.id = c.work_package_id "
            "AND c.unified_work_package_id IS NULL AND r.retired_into_id IS NOT NULL")).rowcount)
    if _table_exists(conn, "deliverables"):
        stats.add("deliverables: unified_work_package_id set", conn.execute(text(
            "UPDATE deliverables d SET unified_work_package_id = w.retired_into_id "
            "FROM work_packages w WHERE w.id = d.work_package_id "
            "AND d.unified_work_package_id IS NULL AND w.retired_into_id IS NOT NULL")).rowcount)


def unmerged_counts(conn):
    return {
        table: _count(conn, f'SELECT count(*) FROM "{table}" WHERE {_UNMERGED}')
        for table in RETIRED_TABLES
    }


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
                   "holds a row that has not been copied.")
@with_appcontext
def merge_work_package_stores(dry_run, verify):
    """Merge technology_roadmap_initiatives, roadmap_work_packages,
    implementation_work_packages and work_packages into unified_work_packages,
    recording provenance (source_table/source_id) and marking each merged
    source row with retired_into_id and retired_at. Remaps dependencies and
    fills fields earlier merges did not copy. Never drops a source row or table."""
    conn = db.session.connection()

    if verify:
        counts = unmerged_counts(conn)
        for table, n in counts.items():
            click.echo(f"  {table}: {n} unmerged row(s)")
        db.session.rollback()
        if any(counts.values()):
            click.echo("merge-work-package-stores --verify: FAILED, rows are not in unified_work_packages.")
            raise SystemExit(1)
        click.echo("merge-work-package-stores --verify: ok, every retired store row is copied.")
        return

    total = _Stats()
    if not dry_run:
        if _ensure_business_capability_nullable(conn):
            click.echo("  + unified_work_packages.business_capability: dropped NOT NULL")
            total.add("constraint", 1)
        if _table_exists(conn, "deliverables") and _ensure_deliverable_legacy_key_nullable(conn):
            click.echo("  + deliverables.work_package_id: dropped NOT NULL")
            total.add("constraint", 1)
    _backfill_retired_at(conn, dry_run, total)

    for spec in _MERGE_SOURCES:
        total.merge(_merge_one(conn, spec, dry_run))

    if not dry_run:
        links = _Stats()
        _backfill_links(conn, links)
        for key, n in links.items():
            click.echo(f"  + {key}: {n}")
        total.merge(links)

    if dry_run:
        click.echo("dry-run: no changes committed.")
        db.session.rollback()
        return

    db.session.commit()
    click.echo(f"merge-work-package-stores: done. changed={total.total()}")


def init_app(app):
    """Register the work package consolidation CLI commands."""
    app.cli.add_command(backfill_work_package_org)
    app.cli.add_command(merge_work_package_stores)
