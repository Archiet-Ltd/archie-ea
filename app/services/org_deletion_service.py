"""Deleting an organisation, with every row that belongs to it.

Deleting an organisation never depends on another organisation existing:
  - a person who belongs to other organisations too (as their home organisation or through an
    organisation role) is detached from this one and keeps the others; their home organisation
    moves to one of them, and an Administrator of this organisation is no longer one;
  - a person who belongs only to this organisation is removed with it, with the rows that are theirs
    alone (sessions, tokens). A person that a row of another organisation names is kept, like one who
    belongs elsewhere: no row of another organisation is deleted for it;
  - an organisation that is the home of a platform administrator is not deleted at all; the
    administrator is moved first.

Every other row that belongs to the organisation is removed first, in the order the database's own
foreign keys and the models' declared relationships require: a row is removed only after every row that points at it. What belongs to the
organisation is read from the database, not from a hand-kept list:
  - a row of any table with an ``organization_id`` column that names this organisation, whether or
    not the database holds a foreign key on that column (a database whose column was added later
    has none);
  - a row of a table with no such key that points (through its own foreign keys, at any depth) at
    one of those rows, such as a sync log of the organisation's connector.

Nothing is disabled on the way: no trigger is switched off, no ``session_replication_role`` is set,
and a statement the database refuses is not skipped. The whole deletion is one transaction; if any
statement fails, it is rolled back and the error is raised to the caller with the database's reason.
"""

from collections import defaultdict

import networkx as nx
from sqlalchemy import text

from app.extensions import db

# People are handled on their own: detached when they belong elsewhere, removed when they do not.
PEOPLE = "users"

# Delete-refusing append-only tables (re-listed from pg_trigger): a cycle-breaking UPDATE must never target one of these —
# their guard admits a DELETE, never an UPDATE, no matter what the marker says.
# A row here resolves only by being deleted, in order; never by nulling a column.
_APPEND_ONLY_TABLES = frozenset({
    "transformation_outbox_events", "operation_results", "command_materialisations",
    "candidate_signals", "candidate_overlap_dispositions", "evidence_records",
    "evidence_head_events", "transformation_option_versions", "decision_brief_versions",
    "decision_brief_option_citations", "decision_brief_evidence_citations", "decision_events",
    "arb_review_cycles", "arb_review_items", "arb_decision_events", "arb_canonical_conditions",
    "arb_subject_evidence_snapshots", "arb_condition_events", "arb_condition_evidence_records",
    "arb_submission_events", "arb_submission_evidence_snapshots", "workbench_artifact_evidence",
    "evidence_claim_heads", "command_idempotency_records", "delivery_export_attempts",
    "outcome_measurements",
})

_FOREIGN_KEYS = text(
    """
    SELECT child.relname AS child_table, child_col.attname AS child_column,
           parent.relname AS parent_table, parent_col.attname AS parent_column,
           child_col.attnotnull AS not_null, (c.condeferrable AND c.condeferred) AS deferred
    FROM pg_constraint c
    JOIN pg_class child ON child.oid = c.conrelid
    JOIN pg_namespace ns ON ns.oid = child.relnamespace
    JOIN pg_class parent ON parent.oid = c.confrelid
    JOIN pg_attribute child_col ON child_col.attrelid = c.conrelid AND child_col.attnum = c.conkey[1]
    JOIN pg_attribute parent_col ON parent_col.attrelid = c.confrelid AND parent_col.attnum = c.confkey[1]
    WHERE c.contype = 'f' AND ns.nspname = current_schema() AND array_length(c.conkey, 1) = 1
    """
)

# Every table that carries an organization_id column, with or without a key on it.
_ORGANIZATION_COLUMNS = text(
    """
    SELECT c.relname
    FROM pg_attribute a
    JOIN pg_class c ON c.oid = a.attrelid
    JOIN pg_namespace ns ON ns.oid = c.relnamespace
    WHERE a.attname = 'organization_id' AND a.attnum > 0 AND NOT a.attisdropped
      AND ns.nspname = current_schema() AND c.relkind IN ('r', 'p')
    """
)


class OrganizationDeletionRefused(ValueError):
    """The organisation may not be deleted (the Default organisation, or one that no longer exists)."""


def _q(name):
    return '"%s"' % name.replace('"', '""')


class _Plan:
    """The foreign-key graph of the current schema, read once per deletion."""

    def __init__(self, sess):
        # parent table -> [(child table, child column, parent column, child column is NOT NULL,
        #                    the constraint is INITIALLY DEFERRED)]
        self.referrers = defaultdict(list)
        self.org_columns = defaultdict(list)  # table -> columns that name an organisation
        for child, child_col, parent, parent_col, not_null, deferred in sess.execute(_FOREIGN_KEYS):
            self.referrers[parent].append((child, child_col, parent_col, not_null, deferred))
        for (table,) in sess.execute(_ORGANIZATION_COLUMNS):
            if table != "organizations":
                self.org_columns[table].append("organization_id")
        # Foreign keys the models declare that this database does not hold. They add no cycle-breaking
        # NULL (the database has no constraint to satisfy); they only order the deletes.
        self.declared = defaultdict(set)  # parent table -> {child table}
        held = {(child, parent) for parent, rows in self.referrers.items() for child, *_ in rows}
        for table in db.metadata.tables.values():
            for fk in table.foreign_keys:
                pair = (table.name, fk.column.table.name)
                if pair not in held and pair[0] != pair[1]:
                    self.declared[pair[1]].add(pair[0])

    def _build(self):
        """Compute the deletion order and the cycle-breaking NULLs it needs, once.

        A real foreign-key cycle (two tables each pointing at the other, such as an
        ArchiMate element and the application component it mirrors) has no row order
        that satisfies every constraint at once. Each such cycle is resolved, preferring
        in order:

        1. An edge whose constraint is INITIALLY DEFERRED (such as
           arb_submission_evidence_snapshots.review_item_id -> arb_review_items.id):
           Postgres checks it only at commit, and by commit every row on both sides of
           the cycle is gone, so that edge's ordering requirement can simply be dropped
           -- no UPDATE needed at all, and this is the only way to resolve a cycle where
           every table is append-only (archie_guard_arb_cycle_history and
           reject_archie_evidence_mutation admit the DELETE; nothing ever needs to
           admit an UPDATE here).
        2. An edge whose child side may still be deleted on its own (the table isn't
           append-only) and whose column is nullable: clearing it, never a column on
           one of the delete-refusing append-only tables, whose guard admits a DELETE
           but never an UPDATE.

        A cycle with neither kind of edge is left for the database's own refusal to name.
        """
        tables = set(self.org_columns) - {PEOPLE}
        graph = nx.DiGraph()
        graph.add_nodes_from(tables)
        for parent in tables:
            for child, _, _, _, _ in self.referrers[parent]:
                if child in tables and child != parent:
                    # child must be deleted before parent: an edge in deletion order.
                    graph.add_edge(child, parent)
        for parent in tables:
            for child in self.declared[parent]:
                # Only where it cannot make a cycle the database has no constraint to resolve.
                if child in tables and not nx.has_path(graph, parent, child):
                    graph.add_edge(child, parent)
        breaks = []
        for members in list(nx.strongly_connected_components(graph)):
            if len(members) < 2:
                continue
            sub = graph.subgraph(members).copy()
            while not nx.is_directed_acyclic_graph(sub):
                cycle_edges = [(u, v) for u, v in nx.find_cycle(sub)]
                removed = False
                # First pass: an edge whose constraint is itself deferred to commit
                # needs no NULL at all -- drop its ordering requirement outright.
                for u, v in cycle_edges:
                    if any(
                        child == u and deferred
                        for child, _, _, _, deferred in self.referrers[v]
                    ):
                        sub.remove_edge(u, v)
                        graph.remove_edge(u, v)
                        removed = True
                        break
                if removed:
                    continue
                # Second pass: an edge whose child side may still be deleted on its own
                # (the table isn't append-only) and whose column is nullable.
                for u, v in cycle_edges:
                    # A DiGraph collapses every (u, v) pair to one edge even when several
                    # of u's columns point at v (as arb_canonical_conditions does, with
                    # both submitted_evidence_id and fulfilment_evidence_id naming
                    # arb_condition_evidence_records): every one of them must be cleared,
                    # not just the first found, or the edge removed here is still a live
                    # reference under the other column.
                    if u in _APPEND_ONLY_TABLES:
                        continue
                    columns = [
                        child_col
                        for child, child_col, _, not_null, _ in self.referrers[v]
                        if child == u and not not_null
                    ]
                    if not columns:
                        continue
                    sub.remove_edge(u, v)
                    graph.remove_edge(u, v)
                    breaks.extend((u, child_col) for child_col in columns)
                    removed = True
                    break
                if not removed:
                    # No safe edge in this cycle: leave it for the database to refuse.
                    break
        condensation = nx.condensation(graph)
        order = []
        for scc_index in nx.topological_sort(condensation):
            members = condensation.nodes[scc_index]["members"]
            sub = graph.subgraph(members)
            order.extend(nx.topological_sort(sub) if nx.is_directed_acyclic_graph(sub) else sorted(members))
        self._order = order
        self._breaks = breaks

    def order(self):
        """Tables that name an organisation, each after every such table that points at it."""
        if not hasattr(self, "_order"):
            self._build()
        return self._order

    def cycle_breaks(self):
        """(table, column) pairs to NULL for the organisation's own rows before deleting."""
        if not hasattr(self, "_breaks"):
            self._build()
        return self._breaks


def _exists(sess, table, where, params):
    return sess.execute(text("SELECT 1 FROM %s WHERE %s LIMIT 1" % (_q(table), where)), params).first() is not None


def _delete(sess, table, where, params, counts=None):
    """Delete the rows of ``table`` in ``where``, only when there are any.

    An append-only table refuses every delete statement, even one that matches nothing; asking
    first keeps those tables out of a deletion that has no rows in them. When the organisation does
    hold such rows, the delete is sent and the database's refusal is raised, not skipped.
    """
    if _exists(sess, table, where, params):
        result = sess.execute(text("DELETE FROM %s WHERE %s" % (_q(table), where)), params)
        if counts is not None and result.rowcount:
            counts[table] = counts.get(table, 0) + result.rowcount


def _remove_pointing_rows(sess, plan, table, where, params, path, counts=None):
    """Remove the rows of tables with no organisation column that point at ``table``'s rows in ``where``.

    A row whose pointer column is nullable is kept and the pointer cleared; a row that must point
    is removed, after every row that points at it.
    """
    for child, child_col, parent_col, not_null, _ in plan.referrers[table]:
        if child == table or child in path or child == PEOPLE or child in plan.org_columns:
            continue
        child_where = "%s IN (SELECT %s FROM %s WHERE %s)" % (_q(child_col), _q(parent_col), _q(table), where)
        if not not_null and child not in _APPEND_ONLY_TABLES:
            # The row can outlive what it pointed at: clear the pointer and keep the row.
            if _exists(sess, child, child_where, params):
                sess.execute(
                    text("UPDATE %s SET %s = NULL WHERE %s" % (_q(child), _q(child_col), child_where)), params
                )
            continue
        _remove_pointing_rows(sess, plan, child, child_where, params, path | {child}, counts)
        _delete(sess, child, child_where, params, counts)


def _people_kept_by_other_rows(sess, plan, people_ids, org_id):
    """Map each person a tenant table's row must name to the organisation that row belongs to.

    The deleted organisation's own rows are gone by now, so any such row belongs to another
    organisation (or to none). The row cannot lose the name (the column is NOT NULL) and is not
    ours to delete, so the person is kept instead.
    """
    kept = {}
    if not people_ids:
        return kept
    params = {"people": list(people_ids)}
    for child, child_col, parent_col, not_null, _ in plan.referrers[PEOPLE]:
        if parent_col != "id" or child == PEOPLE or child not in plan.org_columns or not not_null:
            continue
        rows = sess.execute(text(
            "SELECT %s, min(organization_id) FROM %s WHERE %s = ANY(:people) GROUP BY %s"
            % (_q(child_col), _q(child), _q(child_col), _q(child_col))
        ), params).all()
        for person, home in rows:
            if person not in kept or (home is not None and (kept[person] is None or home < kept[person])):
                kept[person] = home
    return kept


def _remove_people(sess, plan, people_ids):
    """Remove people who belonged only to the deleted organisation, and the rows that are theirs alone.

    The organisation's own rows are gone by now, and no row of another organisation must name one of
    these people (those people are kept, see ``_people_kept_by_other_rows``). A row still naming one
    keeps its place and loses the name where the column allows it; a row that must name a person
    belongs to no organisation (a session, a token) and is removed with them.

    A nullable column on an append-only table (``arb_canonical_conditions.responsible_id`` and the
    like) is neither: any row still naming this person belongs to a different organisation, and that
    organisation's own append-only guard admits neither this UPDATE nor a DELETE -- only a delete of
    its own organisation, under its own marker, which this is not. The row keeps the name; nothing
    here is allowed to touch it.
    """
    if not people_ids:
        return
    params = {"people": list(people_ids)}
    for child, child_col, parent_col, not_null, _ in plan.referrers[PEOPLE]:
        if parent_col != "id" or child == PEOPLE:
            continue
        where = "%s = ANY(:people)" % _q(child_col)
        if not _exists(sess, child, where, params):
            continue
        if not_null:
            _remove_pointing_rows(sess, plan, child, where, params, {child, PEOPLE})
            sess.execute(text("DELETE FROM %s WHERE %s" % (_q(child), where)), params)
        elif child not in _APPEND_ONLY_TABLES:
            sess.execute(text("UPDATE %s SET %s = NULL WHERE %s" % (_q(child), _q(child_col), where)), params)
    sess.execute(text("DELETE FROM users WHERE id = ANY(:people)"), params)


class OrganizationDeletionService:
    """Delete an organisation and every row that belongs to it."""

    @classmethod
    def delete_organization(cls, org_id, requester_id=None, session=None):
        """Delete organisation ``org_id``; return the sentence to show the person who asked.

        Raises OrganizationDeletionRefused when the organisation may not be deleted, and the
        database's own error (after rolling back) when a row cannot be removed.

        Every row the organisation held, including its append-only rows, is removed in the one
        transaction: a transaction-local marker (``archie.deleting_organisation``) tells each
        delete-refusing trigger this one delete is admitted. After every row is gone, one platform
        ``AuditLog`` row (``organization_id`` NULL) records the deletion, its row counts and the
        requesting administrator; it sits outside every organisation's hash chain.
        """
        from app.models.audit_log import AuditLog
        from app.models.org_role import OrgRole
        from app.models.organization import Organization
        from app.middleware.tenant_decorators import is_platform_admin
        from app.models.user import User

        sess = session or db.session
        org = sess.get(Organization, org_id)
        if org is None:
            raise OrganizationDeletionRefused("That organisation no longer exists.")
        if Organization.is_default(org):
            raise OrganizationDeletionRefused("The Default organisation cannot be deleted.")

        administrators = sum(
            1 for person in sess.query(User).filter_by(organization_id=org_id).all() if is_platform_admin(person)
        )
        if administrators:
            raise OrganizationDeletionRefused(
                "%d platform %s must be moved to another organisation before %s can be deleted. Nothing was deleted."
                % (administrators, "administrator" if administrators == 1 else "administrators", org.name)
            )

        org_name, org_slug = org.name, org.slug
        try:
            sess.execute(
                text("SELECT set_config('archie.deleting_organisation', :org_id, true)"),
                {"org_id": str(org_id)},
            )
            # People whose home is this organisation: detached when they belong elsewhere too,
            # removed with it when they do not.
            detached, leaving = 0, []
            for user in sess.query(User).filter_by(organization_id=org_id).order_by(User.id).all():
                elsewhere = sorted(
                    role.organization_id
                    for role in sess.query(OrgRole).filter(
                        OrgRole.user_id == user.id, OrgRole.organization_id != org_id
                    )
                )
                if elsewhere:
                    if user.is_admin():
                        user.revoke_org_admin()
                    user.organization_id = elsewhere[0]
                    detached += 1
                else:
                    leaving.append(user.id)
            sess.flush()
            for user in sess.query(User).filter(User.id.in_(leaving)).all() if leaving else []:
                sess.expunge(user)

            counts = {}
            plan = _Plan(sess)
            params = {"org_id": org_id}
            table_order = plan.order()
            for table, column in plan.cycle_breaks():
                where = " OR ".join("%s = :org_id" % _q(col) for col in plan.org_columns[table])
                sess.execute(text("UPDATE %s SET %s = NULL WHERE %s" % (_q(table), _q(column), where)), params)
            for table in table_order:
                where = " OR ".join("%s = :org_id" % _q(col) for col in plan.org_columns[table])
                _remove_pointing_rows(sess, plan, table, where, params, {table}, counts)
                _delete(sess, table, where, params, counts)

            kept = _people_kept_by_other_rows(sess, plan, leaving, org_id)
            if kept:
                fallback, rehomed = Organization.default_organization(), []
                for person_id, home in sorted(kept.items()):
                    home = home or (fallback.id if fallback is not None else None)
                    if home is None:
                        continue
                    person = sess.get(User, person_id)
                    if person.is_admin():
                        person.revoke_org_admin()
                    person.organization_id = home
                    detached += 1
                    leaving.remove(person_id)
                    rehomed.append(person)
                sess.flush()
                for person in rehomed:
                    sess.expunge(person)
            _remove_people(sess, plan, leaving)

            sess.expunge(org)
            sess.execute(text("DELETE FROM organizations WHERE id = :org_id"), params)

            sess.add(AuditLog(
                organization_id=None,
                user_id=requester_id,
                action="DELETE",
                table_name="organizations",
                record_id=org_id,
                new_value={
                    "name": org_name,
                    "slug": org_slug,
                    "row_counts": counts,
                    "people_removed": len(leaving),
                    "people_detached": detached,
                },
            ))
            sess.commit()
        except Exception:
            sess.rollback()
            raise

        parts = ['Organisation "%s" deleted.' % org_name]
        if leaving:
            parts.append("%d %s who belonged only to it %s removed." % (
                len(leaving), "person" if len(leaving) == 1 else "people", "was" if len(leaving) == 1 else "were"))
        if detached:
            parts.append("%d %s who also %s elsewhere %s detached." % (
                detached, "person" if detached == 1 else "people", "belongs" if detached == 1 else "belong",
                "was" if detached == 1 else "were"))
        return " ".join(parts)
