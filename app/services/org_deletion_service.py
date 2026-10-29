"""Deleting an organisation, with every row that belongs to it.

The organisation's people move to the Default organisation (an Administrator there becomes an
ordinary member). Every other row that belongs to the organisation is removed first, in the order
the database's own foreign keys require: a row is removed only after every row that points at it.

What belongs to the organisation is read from the database, not from a hand-kept list:
  - a row of any table with a foreign key to ``organizations`` that names this organisation;
  - a row of a table with no such key that points (through its own foreign keys, at any depth) at
    one of those rows, such as a sync log of the organisation's connector.

Nothing is disabled on the way: no trigger is switched off, no ``session_replication_role`` is set,
and a statement the database refuses is not skipped. The whole deletion is one transaction; if any
statement fails, it is rolled back and the error is raised to the caller with the database's reason.
"""

from collections import defaultdict

from sqlalchemy import text

from app.extensions import db

# The people of a deleted organisation are moved, not deleted.
MOVED_NOT_DELETED = "users"

_FOREIGN_KEYS = text(
    """
    SELECT child.relname AS child_table, child_col.attname AS child_column,
           parent.relname AS parent_table, parent_col.attname AS parent_column
    FROM pg_constraint c
    JOIN pg_class child ON child.oid = c.conrelid
    JOIN pg_namespace ns ON ns.oid = child.relnamespace
    JOIN pg_class parent ON parent.oid = c.confrelid
    JOIN pg_attribute child_col ON child_col.attrelid = c.conrelid AND child_col.attnum = c.conkey[1]
    JOIN pg_attribute parent_col ON parent_col.attrelid = c.confrelid AND parent_col.attnum = c.confkey[1]
    WHERE c.contype = 'f' AND ns.nspname = current_schema() AND array_length(c.conkey, 1) = 1
    """
)


class OrganizationDeletionRefused(ValueError):
    """The organisation may not be deleted (the Default organisation, or no Default to move people to)."""


def _q(name):
    return '"%s"' % name.replace('"', '""')


class _Plan:
    """The foreign-key graph of the current schema, read once per deletion."""

    def __init__(self, sess):
        self.referrers = defaultdict(list)  # parent table -> [(child table, child column, parent column)]
        self.org_columns = defaultdict(list)  # table -> columns that point at organizations.id
        for child, child_col, parent, parent_col in sess.execute(_FOREIGN_KEYS):
            self.referrers[parent].append((child, child_col, parent_col))
            if parent == "organizations" and parent_col == "id" and child != "organizations":
                self.org_columns[child].append(child_col)

    def order(self):
        """Tables that name an organisation, each after every such table that points at it."""
        tables = set(self.org_columns) - {MOVED_NOT_DELETED}
        children_of = {t: {c for c, _, _ in self.referrers[t] if c in tables and c != t} for t in tables}
        remaining = {t: set(children) for t, children in children_of.items()}
        order = []
        while remaining:
            ready = sorted(t for t, children in remaining.items() if not children)
            if not ready:
                # A cycle: take the rest in name order; the database says plainly if one is refused.
                ready = sorted(remaining)
            for table in ready:
                order.append(table)
                remaining.pop(table)
            for children in remaining.values():
                children.difference_update(ready)
        return order


def _delete(sess, table, where, params):
    """Delete the rows of ``table`` in ``where``, only when there are any.

    An append-only table refuses every delete statement, even one that matches nothing; asking
    first keeps those tables out of a deletion that has no rows in them. When the organisation does
    hold such rows, the delete is sent and the database's refusal is raised, not skipped.
    """
    if sess.execute(text("SELECT 1 FROM %s WHERE %s LIMIT 1" % (_q(table), where)), params).first() is not None:
        sess.execute(text("DELETE FROM %s WHERE %s" % (_q(table), where)), params)


def _remove_pointing_rows(sess, plan, table, where, params, path):
    """Remove the rows of tables with no organisation column that point at ``table``'s rows in ``where``."""
    for child, child_col, parent_col in plan.referrers[table]:
        if child == table or child in path or child == MOVED_NOT_DELETED or child in plan.org_columns:
            continue
        child_where = "%s IN (SELECT %s FROM %s WHERE %s)" % (_q(child_col), _q(parent_col), _q(table), where)
        _remove_pointing_rows(sess, plan, child, child_where, params, path | {child})
        _delete(sess, child, child_where, params)


class OrganizationDeletionService:
    """Delete an organisation and every row that belongs to it."""

    @classmethod
    def delete_organization(cls, org_id, requester_id=None, session=None):
        """Delete organisation ``org_id``; return the sentence to show the person who asked.

        Raises OrganizationDeletionRefused when the organisation may not be deleted, and the
        database's own error (after rolling back) when a row cannot be removed.
        """
        from app.models.organization import Organization
        from app.models.user import User

        sess = session or db.session
        org = sess.get(Organization, org_id)
        if org is None:
            raise OrganizationDeletionRefused("That organisation no longer exists.")
        if org.slug == "default":
            raise OrganizationDeletionRefused("The Default organisation cannot be deleted.")
        default_org = sess.query(Organization).filter_by(slug="default").first()
        if default_org is None:
            raise OrganizationDeletionRefused("There is no Default organisation to move its people to.")

        org_name = org.name
        try:
            # The people move first; an Administrator of this organisation is not one of Default.
            moved = 0
            for user in sess.query(User).filter_by(organization_id=org_id).all():
                user.organization_id = default_org.id
                if user.is_admin():
                    user.revoke_org_admin()
                moved += 1
            sess.flush()

            plan = _Plan(sess)
            params = {"org_id": org_id}
            for table in plan.order():
                where = " OR ".join("%s = :org_id" % _q(col) for col in plan.org_columns[table])
                _remove_pointing_rows(sess, plan, table, where, params, {table})
                _delete(sess, table, where, params)

            sess.expunge(org)
            sess.execute(text("DELETE FROM organizations WHERE id = :org_id"), params)
            sess.commit()
        except Exception:
            sess.rollback()
            raise

        return 'Organisation "%s" deleted. %d %s moved to Default.' % (
            org_name, moved, "person" if moved == 1 else "people")
