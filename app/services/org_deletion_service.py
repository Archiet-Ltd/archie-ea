"""Deleting an organisation, with every row that belongs to it.

Deleting an organisation never depends on another organisation existing:
  - a person who belongs to other organisations too (as their home organisation or through an
    organisation role) is detached from this one and keeps the others; their home organisation
    moves to the one where they have held a role longest (named in the message; the move is recorded
    in the platform record of the deletion, never in that organisation). An Administrator of this
    organisation does not carry that right across: role and administrator flag are set from the
    person's existing role in the new home, and the before and after are recorded;
  - a person who belongs only to this organisation is removed with it, with the rows that are theirs
    alone (sessions, tokens). If any row that is not this organisation's names such a person, in any
    column of any table (another organisation's, a platform row, or a row of a table with no
    organisation column that hangs from either), the whole deletion is refused before anything is
    written;
  - a row outside this organisation that points at the organisation row itself through a column
    other than ``organization_id`` (such as the organisation that named a vendor) is deleted with it
    when nothing outside the organisation uses it, and otherwise keeps its row with the pointer
    cleared by this service, not by the database's ON DELETE action; each cleared pointer (table,
    id, column, old value) is recorded in the platform record of the deletion;
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

The delete-refusing guards admit a DELETE only while this transaction has written a
deletion-in-progress row for the organisation as well as the marker; the row is removed before the
commit. Nothing is disabled on the way: no trigger is switched off, no ``session_replication_role`` is set,
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

# Every foreign key in the schema, composite ones included, whatever its ON DELETE action.
_ALL_FOREIGN_KEYS = text(
    """
    SELECT child.relname AS child_table, parent.relname AS parent_table,
           (SELECT array_agg(a.attname ORDER BY k.ord)
              FROM unnest(c.conkey) WITH ORDINALITY k(attnum, ord)
              JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum) AS child_columns,
           (SELECT array_agg(a.attname ORDER BY k.ord)
              FROM unnest(c.confkey) WITH ORDINALITY k(attnum, ord)
              JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.attnum) AS parent_columns,
           (SELECT bool_and(a.attnotnull)
              FROM unnest(c.conkey) k(attnum)
              JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum) AS not_null
    FROM pg_constraint c
    JOIN pg_class child ON child.oid = c.conrelid
    JOIN pg_namespace ns ON ns.oid = child.relnamespace
    JOIN pg_class parent ON parent.oid = c.confrelid
    WHERE c.contype = 'f' AND ns.nspname = current_schema() AND c.conparentid = 0
    """
)

# Every foreign key to organizations other than on a column named organization_id (such as
# vendor_organizations.named_by_organization_id), whatever its ON DELETE action: the database would
# otherwise clear or cascade these silently, so each is handled and recorded by the service.
_ORGANISATION_POINTERS = text(
    """
    SELECT child.relname AS child_table, child_col.attname AS child_column,
           child_col.attnotnull AS not_null, c.confdeltype AS on_delete,
           (SELECT pk_col.attname FROM pg_index i
              JOIN pg_attribute pk_col ON pk_col.attrelid = i.indrelid AND pk_col.attnum = i.indkey[0]
             WHERE i.indrelid = child.oid AND i.indisprimary AND i.indnatts = 1) AS pk_column
    FROM pg_constraint c
    JOIN pg_class child ON child.oid = c.conrelid
    JOIN pg_namespace ns ON ns.oid = child.relnamespace
    JOIN pg_attribute child_col ON child_col.attrelid = c.conrelid AND child_col.attnum = c.conkey[1]
    WHERE c.contype = 'f' AND ns.nspname = current_schema() AND array_length(c.conkey, 1) = 1
      AND c.confrelid = 'organizations'::regclass AND child_col.attname <> 'organization_id'
    ORDER BY 1, 2
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
        self.parents = defaultdict(list)  # child table -> [(child column, parent table, parent column)]
        for parent, rows in self.referrers.items():
            for child, child_col, parent_col, _, _ in rows:
                self.parents[child].append((child_col, parent, parent_col))
        for (table,) in sess.execute(_ORGANIZATION_COLUMNS):
            if table != "organizations":
                self.org_columns[table].append("organization_id")
        # child table -> [(parent table, child columns, parent columns, every child column is NOT NULL)]
        self.parent_keys = defaultdict(list)
        self.all_keys = [tuple(row) for row in sess.execute(_ALL_FOREIGN_KEYS)]
        for child, parent, child_cols, parent_cols, not_null in self.all_keys:
            self.parent_keys[child].append((parent, child_cols, parent_cols, bool(not_null)))
        # (table, column, column is NOT NULL, ON DELETE action, primary key column)
        self.organisation_pointers = [tuple(row) for row in sess.execute(_ORGANISATION_POINTERS)]
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


def _owners_outside(plan, table, alias, path):
    """SQL selecting the organisation of every row outside the deleted one that ``alias`` hangs from.

    One row per such ancestor: its organization_id, NULL for a platform row. A table with an
    organisation column answers for itself; one without hangs from whatever its own foreign keys
    point at, at any depth. Nothing is returned for an ancestor that is the deleted organisation's.
    """
    selects = []
    for number, (column, parent, parent_column) in enumerate(plan.parents[table]):
        if parent == table or parent in path:
            continue
        inner = "%s_%d" % (alias, number)
        link = "%s.%s = %s.%s" % (inner, _q(parent_column), alias, _q(column))
        if parent == "organizations":
            selects.append("SELECT %s.id FROM organizations %s WHERE %s AND %s.id <> :org_id" % (inner, inner, link, inner))
        elif parent in plan.org_columns:
            selects.append(
                "SELECT %s.organization_id FROM %s %s WHERE %s AND %s.organization_id IS DISTINCT FROM :org_id"
                % (inner, _q(parent), inner, link, inner)
            )
        else:
            deeper = _owners_outside(plan, parent, inner, path | {parent})
            if deeper:
                selects.append(
                    "SELECT o.organization_id FROM %s %s CROSS JOIN LATERAL (%s) AS o(organization_id) WHERE %s"
                    % (_q(parent), inner, deeper, link)
                )
    return " UNION ALL ".join(selects)


def _in_set(plan, table, alias, path=frozenset()):
    """SQL that is true for a row of ``table`` (as ``alias``) that the deletion removes, or None.

    The organisation's set is: its row; the people who leave with it; every row of a table with an
    organisation column that names it; and a row of any other table that must point (NOT NULL) at a
    row of the set, at any depth, such as a sync log of the organisation's connector.
    """
    if table == "organizations":
        return "%s.id = :org_id" % alias
    if table == PEOPLE:
        return "%s.id = ANY(CAST(:leaving AS integer[]))" % alias
    if table in plan.org_columns:
        return "%s.organization_id = :org_id" % alias
    parts = []
    for number, (parent, child_cols, parent_cols, not_null) in enumerate(plan.parent_keys[table]):
        if not not_null or parent in path or parent == table or parent == "organizations":
            continue
        inner = "%s_%d" % (alias, number)
        deeper = _in_set(plan, parent, inner, path | {table})
        if deeper is None:
            continue
        link = " AND ".join("%s.%s = %s.%s" % (inner, _q(p), alias, _q(c)) for c, p in zip(child_cols, parent_cols))
        parts.append("EXISTS (SELECT 1 FROM %s %s WHERE %s AND %s)" % (_q(parent), inner, link, deeper))
    if not parts:
        return None
    inside = "(" + " OR ".join(parts) + ")"
    # A row that also hangs from a row outside the set (another organisation's, or a platform row)
    # is not the deletion's to remove.
    outside = _owners_outside(plan, table, alias, frozenset({table}))
    if outside:
        inside += " AND NOT EXISTS (SELECT 1 FROM (%s) AS held_outside)" % outside
    return "(" + inside + ")"


# Columns that say what a row is, in the order tried, for the examples in a refusal.
_LABEL_COLUMNS = ("name", "title", "element_name", "email")


def _plural(word):
    if word.endswith("y") and word[-2:-1] not in "aeiou":
        return word[:-1] + "ies"
    return word + ("es" if word.endswith(("s", "x", "ch", "sh")) else "s")


def _kind(table, number):
    """A plain label for a kind of record, from its table name: "business capability", "people"."""
    if table == PEOPLE:
        return "person" if number == 1 else "people"
    words = table.replace("_", " ")
    if words.endswith("ies"):
        words = words[:-3] + "y"
    elif words.endswith("s") and not words.endswith(("ss", "us")):
        words = words[:-1]
    return words if number == 1 else _plural(words)


class _Holder:
    """What one organisation (or the platform) still holds that refers into the deleted one."""

    def __init__(self):
        self.counts = defaultdict(int)  # table -> number of rows
        self.examples = []  # (table, label) at most _EXAMPLES

    def describe(self):
        kinds = ", ".join("%d %s" % (number, _kind(table, number)) for table, number in sorted(self.counts.items()))
        if self.examples:
            kinds += " (for example %s)" % ", ".join(
                '%s "%s"' % (_kind(table, 1), label) for table, label in self.examples)
        return kinds


_EXAMPLES = 3


def _example_select(sess, table):
    """SQL for the column that says what a row of ``table`` is (as ``c``), or NULL where it has none."""
    columns = {name for (name,) in sess.execute(text(
        "SELECT a.attname FROM pg_attribute a WHERE a.attrelid = CAST(:t AS regclass) "
        "AND a.attnum > 0 AND NOT a.attisdropped"), {"t": _q(table)})}
    label = next((c for c in _LABEL_COLUMNS if c in columns), None)
    return "c.%s" % _q(label) if label else "NULL"


def _references_into_set(sess, plan, org_id, leaving, homes):
    """Find the rows outside the deletion's set that refer to a row in it, per owning organisation.

    One reverse-reference query per foreign key in the database, whatever its ON DELETE action.
    Reads only. Returns {organisation id (None: a platform row): _Holder}. The two ruled
    kinds are not counted: a key to the organisation row itself, and a person who belongs elsewhere
    (their home is moved by the service, not by the database). A person is attributed to the
    organisation they belong to, which is the new home of a person being detached.
    """
    params = {"org_id": org_id, "leaving": list(leaving)}
    has_rows = {}
    found = defaultdict(_Holder)
    for child, parent, child_cols, parent_cols, _ in plan.all_keys:
        if parent == "organizations":
            continue
        inside_parent = _in_set(plan, parent, "p")
        if inside_parent is None:
            continue
        if parent not in has_rows:
            has_rows[parent] = sess.execute(
                text("SELECT 1 FROM %s p WHERE %s LIMIT 1" % (_q(parent), inside_parent)), params
            ).first() is not None
        if not has_rows[parent]:
            continue
        inside_child = _in_set(plan, child, "c") or "FALSE"
        link = " AND ".join("p.%s = c.%s" % (_q(p), _q(c)) for c, p in zip(child_cols, parent_cols))
        outside = "NOT COALESCE(%s, FALSE)" % inside_child
        label = _example_select(sess, child)
        if child == PEOPLE:
            rows = sess.execute(text(
                "SELECT c.id, c.organization_id, %s, COUNT(*) FROM users c JOIN %s p ON %s AND %s WHERE %s "
                "GROUP BY c.id, c.organization_id, 3 ORDER BY c.id"
                % (label, _q(parent), link, inside_parent, outside)), params).all()
            for person, home, name, number in rows:
                holder = found[homes.get(person, home)]
                holder.counts[child] += number
                if name and len(holder.examples) < _EXAMPLES:
                    holder.examples.append((child, name))
            continue
        if child in plan.org_columns:
            owner, lateral = "c.organization_id", ""
        else:
            owners = _owners_outside(plan, child, "c", {child})
            owner, lateral = ("o.organization_id", "LEFT JOIN LATERAL (%s) AS o(organization_id) ON TRUE" % owners) \
                if owners else ("NULL", "")
        rows = sess.execute(text(
            "SELECT %s, COUNT(DISTINCT c.ctid) FROM %s c JOIN %s p ON %s AND %s %s WHERE %s GROUP BY 1"
            % (owner, _q(child), _q(parent), link, inside_parent, lateral, outside)), params).all()
        if not rows:
            continue
        named = sess.execute(text(
            "SELECT DISTINCT %s, %s FROM %s c JOIN %s p ON %s AND %s %s WHERE %s ORDER BY 2 NULLS LAST LIMIT 10"
            % (owner, label, _q(child), _q(parent), link, inside_parent, lateral, outside)), params).all()
        for other, number in rows:
            found[other].counts[child] += number
        for other, name in named:
            if name and len(found[other].examples) < _EXAMPLES:
                found[other].examples.append((child, name))
    return found


def _remove_people(sess, plan, people_ids):
    """Remove people who belonged only to the deleted organisation, and the rows that are theirs alone.

    The organisation's own rows are gone by now, and the deletion has already been refused if a row
    of another organisation names one of these people. A row still naming one belongs to no
    organisation: a nullable pointer is cleared, and a row that must point (a session, a token) is
    removed with the person.
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


def _rights(sess, user_id):
    """A person's home organisation, role and administrator flag, read straight from the row."""
    row = sess.execute(
        text("SELECT organization_id, role_id, is_org_admin FROM users WHERE id = :id"), {"id": user_id}
    ).one()
    return {"organization_id": row[0], "role_id": row[1], "is_org_admin": bool(row[2])}


# The role a person holds in an organisation (OrgRole) and the account role it corresponds to.
_ACCOUNT_ROLE = {"architect": "Architect", "viewer": "Viewer"}


def _take_role_of(sess, user, org_role):
    """Set ``user``'s account role and administrator flag from the role they hold in their new home."""
    from app.models.user import Role

    if org_role == "org_admin":
        user.grant_org_admin()
        return
    role = Role.query.filter_by(name=_ACCOUNT_ROLE.get(org_role, "Viewer")).first()
    if user.is_admin():
        user.revoke_org_admin(fallback_role=role)
    elif role is not None:
        user.role = role
        user._is_org_admin = False


def _release_organisation_pointers(sess, plan, org_id, counts):
    """Handle every row outside the deleted organisation that points at the organisation row itself.

    Runs after the organisation's own rows are gone, so a row's remaining referrers are exactly the
    rows that are not the organisation's. A row that nothing outside the organisation uses is the
    organisation's own and is deleted with it; any other row keeps existing and has its pointer
    cleared here, explicitly, rather than by the database's ON DELETE action. Returns one record per
    cleared pointer: table, id, column and old value. A row owned by another organisation is never
    deleted.
    """
    cleared = []
    params = {"org_id": org_id}
    for table, column, not_null, _on_delete, pk in plan.organisation_pointers:
        if pk is None:
            continue
        base = "SELECT %s FROM %s WHERE %s = :org_id" % (_q(pk), _q(table), _q(column))
        if table in plan.org_columns:
            # Another organisation's row is never deleted here; only a platform row (no organisation) can be.
            platform = [row_id for (row_id,) in sess.execute(text(base + " AND organization_id IS NULL"), params)]
            owned_elsewhere = [row_id for (row_id,) in sess.execute(
                text(base + " AND organization_id IS NOT NULL AND organization_id <> :org_id"), params)]
        else:
            platform = [row_id for (row_id,) in sess.execute(text(base), params)]
            owned_elsewhere = []
        for row_id, deletable in [(r, True) for r in platform] + [(r, False) for r in owned_elsewhere]:
            used_elsewhere = not deletable or any(
                _exists(sess, referrer, "%s = :row_id%s" % (
                    _q(referrer_col), " AND organization_id IS DISTINCT FROM :org_id"
                    if referrer in plan.org_columns else ""), {"row_id": row_id, "org_id": org_id})
                for referrer, referrer_col, _, _, _ in plan.referrers[table]
            )
            if not used_elsewhere:
                _remove_pointing_rows(sess, plan, table, "%s = :row_id" % _q(pk), {"row_id": row_id}, {table}, counts)
                _delete(sess, table, "%s = :row_id" % _q(pk), {"row_id": row_id}, counts)
                continue
            if not_null or table in _APPEND_ONLY_TABLES:
                raise OrganizationDeletionRefused(
                    "%s %s still uses this organisation and cannot be detached from it. Nothing was deleted."
                    % (table.replace("_", " ").capitalize(), row_id)
                )
            sess.execute(
                text("UPDATE %s SET %s = NULL WHERE %s = :row_id" % (_q(table), _q(column), _q(pk))),
                {"row_id": row_id},
            )
            cleared.append({"table": table, "id": row_id, "column": column, "old_value": org_id})
    return cleared


def _refuse_unremovable_rows(sess, plan, org, org_id, leaving):
    """Refuse, before any write, when a row would be left that the deletion cannot follow.

    The deletion removes a row that hangs from the organisation through a key of one column. A row
    that hangs from it only through a key of several columns cannot be followed that way, so the
    database would refuse in the middle of the deletion; say so up front instead.
    """
    params = {"org_id": org_id, "leaving": list(leaving)}
    for child, parent, child_cols, parent_cols, not_null in plan.all_keys:
        if len(child_cols) < 2 or not not_null or parent == "organizations":
            continue
        if child in plan.org_columns or child == PEOPLE:
            continue
        inside_parent = _in_set(plan, parent, "p")
        if inside_parent is None:
            continue
        link = " AND ".join("p.%s = c.%s" % (_q(p), _q(c)) for c, p in zip(child_cols, parent_cols))
        if sess.execute(text("SELECT 1 FROM %s c JOIN %s p ON %s AND %s LIMIT 1" % (
                _q(child), _q(parent), link, inside_parent)), params).first() is not None:
            raise OrganizationDeletionRefused(
                "%s cannot be deleted: some %s depend on it through a link of several columns that cannot be "
                "removed automatically. Remove or reassign those references first. Nothing was deleted."
                % (org.name, _kind(child, 2))
            )


def _refuse_missing_roles(detaching):
    """Refuse, before any write, when a person being detached needs an account role that does not exist."""
    from app.models.org_role import OrgRole
    from app.models.user import Role

    for user, home in detaching:
        org_role = OrgRole.get_role(home, user.id)
        if org_role == "org_admin":
            continue
        name = _ACCOUNT_ROLE.get(org_role, "Viewer")
        if Role.query.filter_by(name=name).first() is None:
            raise OrganizationDeletionRefused(
                "The deletion cannot go ahead: the account role \"%s\" that a person leaving it would take "
                "in their other organisation does not exist. Nothing was deleted." % name
            )


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

        # Read-only: who is detached (belongs elsewhere) and who leaves with the organisation.
        detaching, leaving = [], []
        for user in sess.query(User).filter_by(organization_id=org_id).order_by(User.id).all():
            oldest = (
                sess.query(OrgRole)
                .filter(OrgRole.user_id == user.id, OrgRole.organization_id != org_id)
                .order_by(OrgRole.created_at.asc().nullslast(), OrgRole.id.asc())
                .first()
            )
            if oldest is not None:
                detaching.append((user, oldest.organization_id))
            else:
                leaving.append(user.id)
        plan = _Plan(sess)
        homes = {user.id: home for user, home in detaching}
        referring = _references_into_set(sess, plan, org_id, leaving, homes)
        if referring:
            names = dict(sess.query(Organization.id, Organization.name).filter(
                Organization.id.in_([other for other in referring if other is not None])).all())
            holders = sorted(
                ((names.get(other, "Another organisation") if other is not None else None, holder)
                 for other, holder in referring.items()),
                key=lambda item: (item[0] is None, item[0] or ""))
            detail = "; ".join(
                "%s: %s" % (name or "Shared platform records", holder.describe()) for name, holder in holders)
            raise OrganizationDeletionRefused(
                "%s cannot be deleted: records outside it still refer to it. %s. "
                "Remove or reassign those references first. Nothing was deleted." % (org.name, detail)
            )
        _refuse_unremovable_rows(sess, plan, org, org_id, leaving)
        _refuse_missing_roles(detaching)

        org_name, org_slug = org.name, org.slug
        try:
            # Proof that this transaction is deleting this organisation: the guards on append-only
            # history admit a DELETE only with this row as well as the marker. It is removed again
            # before the commit, so no other transaction ever sees it.
            sess.execute(
                text("INSERT INTO organization_deletions_in_progress (target_organization_id) VALUES (:org_id)"),
                {"org_id": org_id},
            )
            sess.execute(
                text("SELECT set_config('archie.deleting_organisation', :org_id, true)"),
                {"org_id": str(org_id)},
            )
            # People whose home is this organisation and who belong elsewhere are detached.
            detached, moved_to, rights_changed = 0, {}, {}
            for user, home in detaching:
                moved_to[str(user.id)] = home
                before = _rights(sess, user.id)
                # Rights in this organisation do not travel: the person is what their existing
                # role in the new home says, and nothing more.
                _take_role_of(sess, user, OrgRole.get_role(home, user.id))
                sess.flush()  # a change of role is recorded against this organisation, which is going
                rights_changed[str(user.id)] = {"before": before, "after_role_change": _rights(sess, user.id)}
                # A statement, not an attribute change: the audit hook would file the move under the
                # new home organisation. The platform record of this deletion carries it instead.
                sess.execute(
                    text("UPDATE users SET organization_id = :home WHERE id = :id"), {"home": home, "id": user.id}
                )
                rights_changed[str(user.id)]["after"] = _rights(sess, user.id)
                del rights_changed[str(user.id)]["after_role_change"]
                sess.expire(user)
                detached += 1
            sess.flush()
            for user in sess.query(User).filter(User.id.in_(leaving)).all() if leaving else []:
                sess.expunge(user)

            counts = {}
            params = {"org_id": org_id}
            table_order = plan.order()
            for table, column in plan.cycle_breaks():
                where = " OR ".join("%s = :org_id" % _q(col) for col in plan.org_columns[table])
                sess.execute(text("UPDATE %s SET %s = NULL WHERE %s" % (_q(table), _q(column), where)), params)
            for table in table_order:
                where = " OR ".join("%s = :org_id" % _q(col) for col in plan.org_columns[table])
                _remove_pointing_rows(sess, plan, table, where, params, {table}, counts)
                _delete(sess, table, where, params, counts)

            _remove_people(sess, plan, leaving)
            pointers_cleared = _release_organisation_pointers(sess, plan, org_id, counts)

            sess.expunge(org)
            sess.execute(
                text("DELETE FROM organization_deletions_in_progress WHERE target_organization_id = :org_id"),
                params,
            )
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
                    "people_detached_to": moved_to,
                    "detached_rights": rights_changed,
                    "pointers_cleared": pointers_cleared,
                },
            ))
            sess.commit()
        except Exception:
            sess.rollback()
            raise

        moved_to_ids = list(moved_to.values())
        parts = ['Organisation "%s" deleted.' % org_name]
        if leaving:
            parts.append("%d %s who belonged only to it %s removed." % (
                len(leaving), "person" if len(leaving) == 1 else "people", "was" if len(leaving) == 1 else "were"))
        if detached:
            parts.append("%d %s who also %s elsewhere %s detached." % (
                detached, "person" if detached == 1 else "people", "belongs" if detached == 1 else "belong",
                "was" if detached == 1 else "were"))
            homes = [name for (name,) in sess.query(Organization.name).filter(
                Organization.id.in_(set(moved_to_ids))).order_by(Organization.name)]
            parts[-1] = parts[-1][:-1] + ", and now %s to %s." % ("belongs" if detached == 1 else "belong", ", ".join(homes))
            demoted = sum(1 for rights in rights_changed.values()
                          if rights["before"]["is_org_admin"] and not rights["after"]["is_org_admin"])
            if demoted:
                parts.append("%d of them administered it and %s no longer %s." % (
                    demoted, "is" if demoted == 1 else "are", "an administrator" if demoted == 1 else "administrators"))
        return " ".join(parts)
