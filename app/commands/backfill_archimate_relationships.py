"""
Flask CLI command for backfilling ArchiMate relationships from solution junction data.
Run with: flask backfill-archimate-relationships [--dry-run] [--solution-id N]

Infers relationships by finding pairs of ArchiMate elements that co-occur
in the same solution (via solution_archimate_elements junction) and have
matching types for a specific ArchiMate relationship pattern.

Phase 3 (transitive closure) walks the existing relationship graph and
infers A→C when A→B and B→C exist and A→C passes ArchiMate 3.2 validation.
"""

import click
from flask.cli import with_appcontext


# Each rule: (rule_name, relationship_type, source_element_type, target_element_type)
_INFERENCE_RULES = [
    ("R2_app_serves_process",          "serving",      "ApplicationComponent", "BusinessProcess"),
    ("R3_app_realizes_service",        "realization",  "ApplicationComponent", "ApplicationService"),
    ("R4_appservice_realizes_bizservice", "realization", "ApplicationService",  "BusinessService"),
    ("R5_stakeholder_driver",          "association",  "Stakeholder",          "Driver"),
    ("R6_driver_goal",                 "association",  "Driver",               "Goal"),
    ("R7_goal_requirement",            "realization",  "Goal",                 "Requirement"),
    ("R8_requirement_constraint",      "influence",    "Requirement",          "Constraint"),
    ("R1_app_on_node",                 "assignment",   "ApplicationComponent", "Node"),
    ("R9_workpackage_plateau",         "realization",  "WorkPackage",          "Plateau"),
    ("R11_node_systemsoftware",        "composition",  "Node",                 "SystemSoftware"),
    ("R12_app_accesses_data",          "access",       "ApplicationComponent", "DataObject"),
]

# Transitive inference rules: (rel_type_AB, rel_type_BC) → inferred rel_type_AC
# Only create transitive relationships for well-known composition patterns.
_TRANSITIVE_RULES = [
    # If App serves Process, and Process realizes Capability → App serves Capability
    ("serving", "realization", "serving"),
    # If App realizes AppService, and AppService realizes BizService → App realizes BizService
    ("realization", "realization", "realization"),
    # If Node hosts App, and App serves Process → Node serves Process (indirect)
    ("assignment", "serving", "serving"),
    # If Driver motivates Goal, and Goal realizes Requirement → Driver influences Requirement
    ("association", "realization", "association"),
]

# Default enterprise-wide pair limit (was 50, lifted per BPP-004)
DEFAULT_ENTERPRISE_LIMIT = 500

# Batch size for processing pairs
BATCH_SIZE = 100


def _crosses_organisations(db, org_sql, params, src, tgt, crossing):
    """True (and counted in crossing) when the row about to be written would
    join elements of different organisations. Uses the one same-organisation
    check the model guard uses, because these inserts are plain SQL and the
    model guard never sees them."""
    from app.models.relationship_guard import (
        CrossOrganisationRelationship,
        assert_relationship_same_organisation,
    )

    org_id = db.session.execute(db.text(org_sql), params).scalar()
    try:
        assert_relationship_same_organisation(
            db.session.connection(), org_id, src, tgt
        )
    except CrossOrganisationRelationship:
        crossing["count"] += 1
        return True
    return False


_ORG_OF_ELEMENT = "SELECT organization_id FROM archimate_elements WHERE id = :s"
_ORG_OF_SOLUTION = "SELECT organization_id FROM solutions WHERE id = :sol"


def _build_inference_sql(source_type, target_type, solution_id=None):
    """Build the co-occurrence SQL for a given element type pair.

    Tenancy: both elements are drawn from the same ``solution_archimate_elements``
    row set for a single ``solution_id``, and ``solutions`` is tenant-scoped, so a
    pair can never straddle two organisations even though this runs with no
    request context. That is a property of the join, not of a predicate — hence
    it is written down here rather than assumed.
    """
    sql = (
        "SELECT DISTINCT s1.element_id AS source_id, "
        "s2.element_id AS target_id, s1.solution_id "
        "FROM solution_archimate_elements s1 "
        "JOIN solution_archimate_elements s2 "
        "  ON s1.solution_id = s2.solution_id AND s1.element_id != s2.element_id "
        "JOIN archimate_elements e1 ON s1.element_id = e1.id "
        "JOIN archimate_elements e2 ON s2.element_id = e2.id "
        f"WHERE e1.type = '{source_type}' AND e2.type = '{target_type}'"
    )
    if solution_id is not None:
        sql += f" AND s1.solution_id = {int(solution_id)}"
    return sql


def _check_duplicate_sql(db, source_id, target_id, rel_type):
    """Return True if a relationship already exists for this tuple.

    archimate_relationships has no solution_id column — a relationship is
    associated with a solution only indirectly, through its endpoint elements'
    rows in solution_archimate_elements. Every statement in this command used to
    name solution_id, so the duplicate checks raised UndefinedColumn and the
    INSERTs failed outright: this backfill has never successfully run. The
    identity of a relationship is therefore (source, target, type) alone.
    """
    # tenancy-ok: CLI backfill, deliberately global across organisations; no
    # request context exists. Keyed on a (source, target, type) triple that came
    # from one solution's own junction rows.
    row = db.session.execute(db.text(
        "SELECT 1 FROM archimate_relationships "
        "WHERE source_id = :s AND target_id = :t "
        "AND type = :r "
        "LIMIT 1"
    ), {"s": source_id, "t": target_id, "r": rel_type}).fetchone()
    return row is not None


def _build_enterprise_sql(source_type, target_type, limit=DEFAULT_ENTERPRISE_LIMIT):
    """Build SQL to pair elements by type across the entire catalog (enterprise-wide).

    "Enterprise-wide" means every organisation's catalogue, because there is no
    request context — but it must not mean *across* organisations. Without the
    organisation-equality predicate this cross join paired one tenant's
    ApplicationComponent with another tenant's BusinessProcess and Phase 2 then
    wrote a relationship row joining two tenants' element graphs. IS NOT DISTINCT
    FROM rather than = so that legacy rows with a NULL organization_id still pair
    with each other and only with each other.
    """
    return (
        "SELECT e1.id AS source_id, e2.id AS target_id, "
        "e1.organization_id AS organization_id "
        "FROM archimate_elements e1, archimate_elements e2 "
        f"WHERE e1.type = '{source_type}' AND e2.type = '{target_type}' "
        "AND e1.id != e2.id "
        "AND e1.organization_id IS NOT DISTINCT FROM e2.organization_id "
        f"LIMIT {int(limit)}"
    )


def _check_duplicate_enterprise_sql(db, source_id, target_id, rel_type):
    """Return True if an enterprise-wide relationship already exists for this tuple."""
    # tenancy-ok: CLI backfill, deliberately global across organisations; no
    # request context exists. Keyed on ids that _build_enterprise_sql has already
    # constrained to a single organisation.
    row = db.session.execute(db.text(
        "SELECT 1 FROM archimate_relationships "
        "WHERE source_id = :s AND target_id = :t "
        # No solution_id column exists on archimate_relationships, so the
        # former "AND solution_id IS NULL" predicate is dropped; (source,
        # target, type) is the whole key.
        "AND type = :r "
        "LIMIT 1"
    ), {"s": source_id, "t": target_id, "r": rel_type}).fetchone()
    return row is not None


def _load_adjacency_graph(db):
    """Load the full relationship graph as an adjacency structure.

    Returns:
        dict: {source_id: [(target_id, rel_type), ...]}
        dict: {element_id: organization_id}
    """
    # tenancy-ok: CLI backfill, deliberately global across organisations; no
    # request context exists. Transitive closure has to see the whole graph to
    # find A->B->C; the organisations are carried alongside so the inferred edge
    # can be refused when A and C belong to different tenants.
    rows = db.session.execute(db.text(
        "SELECT source_id, target_id, type FROM archimate_relationships"
    )).fetchall()

    graph = {}
    for row in rows:
        graph.setdefault(row.source_id, []).append((row.target_id, row.type))

    # Element -> owning organisation, used to reject cross-tenant inferences.
    # tenancy-ok: CLI backfill, deliberately global across organisations; no
    # request context exists. This is the lookup that makes the check possible.
    el_rows = db.session.execute(db.text(
        "SELECT id, organization_id FROM archimate_elements"
    )).fetchall()
    element_orgs = {r.id: r.organization_id for r in el_rows}

    return graph, element_orgs


def _get_existing_relationship_keys(db):
    """Load all existing (source, target, type) tuples for dedup."""
    # tenancy-ok: CLI backfill, deliberately global across organisations; no
    # request context exists. A dedup set that saw only one tenant's rows would
    # re-create every other tenant's relationships on each run.
    rows = db.session.execute(db.text(
        "SELECT source_id, target_id, type FROM archimate_relationships"
    )).fetchall()
    return {(r.source_id, r.target_id, r.type) for r in rows}


def _run_transitive_closure(db, dry_run, max_depth=2, crossing=None):
    """Infer transitive relationships from the existing graph.

    For each pair of relationships A→B (type1) and B→C (type2) that
    matches a transitive rule, create A→C if it doesn't already exist
    and passes validation.

    Args:
        db: SQLAlchemy database instance
        dry_run: If True, count but don't create
        max_depth: Number of transitive hops (default 2 = one level of transitivity)

    Returns:
        int: Number of relationships created (or would be created)
    """
    crossing = crossing if crossing is not None else {"count": 0}
    total_created = 0

    for depth in range(1, max_depth):
        graph, element_orgs = _load_adjacency_graph(db)
        existing_keys = _get_existing_relationship_keys(db)
        created_this_pass = 0

        candidates = []

        # For each node A, walk A→B→C and check transitive rules
        for a_id, a_edges in graph.items():
            for b_id, ab_type in a_edges:
                b_edges = graph.get(b_id, [])
                for c_id, bc_type in b_edges:
                    if c_id == a_id:
                        continue  # skip cycles

                    # A→C must stay inside one organisation. The walk sees every
                    # tenant's edges at once, so without this an existing
                    # cross-tenant edge would breed more of them.
                    crosses = element_orgs.get(a_id) != element_orgs.get(c_id)

                    # Check if this matches a transitive rule
                    for rule_ab, rule_bc, inferred_ac in _TRANSITIVE_RULES:
                        if ab_type == rule_ab and bc_type == rule_bc:
                            key = (a_id, c_id, inferred_ac)
                            if crosses:
                                crossing["count"] += 1
                                continue
                            if key not in existing_keys:
                                candidates.append(key)
                                existing_keys.add(key)  # prevent duplicates within pass

        # Batch-create candidates
        for i in range(0, len(candidates), BATCH_SIZE):
            batch = candidates[i:i + BATCH_SIZE]
            for src, tgt, rel_type in batch:
                if _crosses_organisations(
                    db, _ORG_OF_ELEMENT, {"s": src}, src, tgt, crossing
                ):
                    continue
                if not dry_run:
                    try:
                        # organization_id inherited from the source element: this
                        # runs with no request context, so before_flush cannot
                        # stamp it and the row would otherwise be untenanted and
                        # invisible to every organisation's ORM queries.
                        db.session.execute(db.text(
                            # no solution_id column on archimate_relationships
                            "INSERT INTO archimate_relationships "
                            "(source_id, target_id, type, "
                            " organization_id, created_at) "
                            "SELECT :s, :t, :r, e.organization_id, "
                            "       CURRENT_TIMESTAMP "
                            "FROM archimate_elements e WHERE e.id = :s"
                        ), {"s": src, "t": tgt, "r": rel_type})
                    except Exception:
                        db.session.rollback()
                        continue
                created_this_pass += 1

            if not dry_run and batch:
                db.session.commit()

        total_created += created_this_pass
        click.echo(f"  Depth {depth + 1}: {created_this_pass} transitive relationships")

        if created_this_pass == 0:
            break  # No new relationships found, stop early

    return total_created


@click.command("backfill-archimate-relationships")
@click.option("--dry-run", is_flag=True, help="Show counts without modifying data")
@click.option("--solution-id", type=int, default=None,
              help="Restrict to a single solution ID")
@click.option("--enterprise", is_flag=True,
              help="Also create enterprise-wide relationships from element type pairs")
@click.option("--transitive", is_flag=True,
              help="Run transitive closure after direct inference")
@click.option("--max-depth", type=int, default=2,
              help="Maximum transitive closure depth (default 2)")
@click.option("--limit", type=int, default=DEFAULT_ENTERPRISE_LIMIT,
              help=f"Enterprise-wide pair limit (default {DEFAULT_ENTERPRISE_LIMIT})")
@with_appcontext
def backfill_archimate_relationships_command(dry_run, solution_id, enterprise,
                                             transitive, max_depth, limit):
    """Backfill ArchiMate relationships inferred from solution junction data."""
    from app import db

    if dry_run:
        click.echo("DRY RUN -- no data will be modified.\n")

    if solution_id is not None:
        click.echo(f"Scoped to solution_id={solution_id}\n")

    total_created = 0
    crossing = {"count": 0}

    # --- Phase 1: Solution-scoped inference via junction table ---
    click.echo("Phase 1: Solution-scoped inference (junction table)")
    for rule_name, rel_type, source_type, target_type in _INFERENCE_RULES:
        sql = _build_inference_sql(source_type, target_type, solution_id)

        try:
            rows = db.session.execute(db.text(sql)).fetchall()
        except Exception as exc:
            click.echo(f"  {rule_name:40s}: QUERY ERROR -- {exc}", err=True)
            db.session.rollback()
            continue

        created = 0
        skipped = 0

        for row in rows:
            src = row.source_id
            tgt = row.target_id
            sol = row.solution_id

            if _check_duplicate_sql(db, src, tgt, rel_type):
                skipped += 1
                continue

            if _crosses_organisations(
                db, _ORG_OF_SOLUTION, {"sol": sol}, src, tgt, crossing
            ):
                continue

            if not dry_run:
                try:
                    # organization_id inherited from the owning solution — see
                    # the note on the transitive INSERT below.
                    db.session.execute(db.text(
                        # solution_id is not a column on
                        # archimate_relationships; the solution is still used to
                        # derive organization_id, but cannot be stored on the edge.
                        "INSERT INTO archimate_relationships "
                        "(source_id, target_id, type, "
                        " organization_id, created_at) "
                        "SELECT :s, :t, :r, sol.organization_id, "
                        "       CURRENT_TIMESTAMP "
                        "FROM solutions sol WHERE sol.id = :sol"
                    ), {"s": src, "t": tgt, "r": rel_type, "sol": sol})
                except Exception as exc:
                    db.session.rollback()
                    click.echo(
                        f"  {rule_name:40s}: INSERT ERROR -- {exc}", err=True
                    )
                    continue

            created += 1

        if not dry_run and created > 0:
            db.session.commit()

        action = "Would create" if dry_run else "Created"
        click.echo(
            f"  {rule_name:40s} ({rel_type:12s}): "
            f"{action} {created:4d}  skip {skipped:4d}"
        )
        total_created += created

    # --- Phase 2: Enterprise-wide inference from element type pairs ---
    if enterprise:
        click.echo(f"\nPhase 2: Enterprise-wide inference (limit={limit})")
        for rule_name, rel_type, source_type, target_type in _INFERENCE_RULES:
            sql = _build_enterprise_sql(source_type, target_type, limit=limit)

            try:
                rows = db.session.execute(db.text(sql)).fetchall()
            except Exception as exc:
                click.echo(f"  {rule_name:40s}: QUERY ERROR -- {exc}", err=True)
                db.session.rollback()
                continue

            created = 0
            skipped = 0

            # Process in batches
            for batch_start in range(0, len(rows), BATCH_SIZE):
                batch = rows[batch_start:batch_start + BATCH_SIZE]
                batch_created = 0

                for row in batch:
                    src = row.source_id
                    tgt = row.target_id

                    if _check_duplicate_enterprise_sql(db, src, tgt, rel_type):
                        skipped += 1
                        continue

                    if _crosses_organisations(
                        db, _ORG_OF_ELEMENT, {"s": src}, src, tgt, crossing
                    ):
                        continue

                    if not dry_run:
                        try:
                            # organization_id inherited from the source element;
                            # _build_enterprise_sql has already guaranteed source
                            # and target share it.
                            db.session.execute(db.text(
                                "INSERT INTO archimate_relationships "
                                # no solution_id column on archimate_relationships
                                "(source_id, target_id, type, "
                                " organization_id, created_at) "
                                "SELECT :s, :t, :r, e.organization_id, "
                                "       CURRENT_TIMESTAMP "
                                "FROM archimate_elements e WHERE e.id = :s"
                            ), {"s": src, "t": tgt, "r": rel_type})
                        except Exception as exc:
                            db.session.rollback()
                            click.echo(
                                f"  {rule_name:40s}: INSERT ERROR -- {exc}", err=True
                            )
                            continue

                    batch_created += 1

                if not dry_run and batch_created > 0:
                    db.session.commit()
                created += batch_created

            action = "Would create" if dry_run else "Created"
            click.echo(
                f"  {rule_name:40s} ({rel_type:12s}): "
                f"{action} {created:4d}  skip {skipped:4d}"
            )
            total_created += created

    # --- Phase 3: Transitive closure ---
    if transitive:
        click.echo(f"\nPhase 3: Transitive closure (max_depth={max_depth})")
        transitive_created = _run_transitive_closure(
            db, dry_run, max_depth=max_depth, crossing=crossing
        )
        action = "Would create" if dry_run else "Created"
        click.echo(f"  {action} {transitive_created} transitive relationships")
        total_created += transitive_created

    click.echo(
        f"\nSkipped {crossing['count']} relationships that would join "
        "elements of different organisations"
    )
    action = "Would create" if dry_run else "Total created"
    click.echo(f"\n{action}: {total_created} relationships")


def init_app(app):
    """Register CLI command with app."""
    app.cli.add_command(backfill_archimate_relationships_command)
