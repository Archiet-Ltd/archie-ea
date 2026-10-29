"""Deleting an organisation, with every row that belongs to it.

Deleting an organisation never depends on another organisation existing:
  - a person who belongs to other organisations too (as their home organisation or through an
    organisation role) is detached from this one and keeps the others; their home organisation
    moves to one of them, and an Administrator of this organisation is no longer one;
  - a person who belongs only to this organisation is removed with it, with the rows that are theirs
    alone (sessions, tokens). A row of another organisation that names them keeps the row and loses
    the name when the column allows it.

Every other row that belongs to the organisation is removed first, in the order the database's own
foreign keys require: a row is removed only after every row that points at it. What belongs to the
organisation is read from the database, not from a hand-kept list:
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

# People are handled on their own: detached when they belong elsewhere, removed when they do not.
PEOPLE = "users"

_FOREIGN_KEYS = text(
    """
    SELECT child.relname AS child_table, child_col.attname AS child_column,
           parent.relname AS parent_table, parent_col.attname AS parent_column,
           child_col.attnotnull AS not_null
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
    """The organisation may not be deleted (the Default organisation, or one that no longer exists)."""


def _q(name):
    return '"%s"' % name.replace('"', '""')


class _Plan:
    """The foreign-key graph of the current schema, read once per deletion."""

    def __init__(self, sess):
        # parent table -> [(child table, child column, parent column, child column is NOT NULL)]
        self.referrers = defaultdict(list)
        self.org_columns = defaultdict(list)  # table -> columns that point at organizations.id
        for child, child_col, parent, parent_col, not_null in sess.execute(_FOREIGN_KEYS):
            self.referrers[parent].append((child, child_col, parent_col, not_null))
            if parent == "organizations" and parent_col == "id" and child != "organizations":
                self.org_columns[child].append(child_col)

    def order(self):
        """Tables that name an organisation, each after every such table that points at it."""
        tables = set(self.org_columns) - {PEOPLE}
        children_of = {t: {c for c, _, _, _ in self.referrers[t] if c in tables and c != t} for t in tables}
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


def _exists(sess, table, where, params):
    return sess.execute(text("SELECT 1 FROM %s WHERE %s LIMIT 1" % (_q(table), where)), params).first() is not None


def _delete(sess, table, where, params):
    """Delete the rows of ``table`` in ``where``, only when there are any.

    An append-only table refuses every delete statement, even one that matches nothing; asking
    first keeps those tables out of a deletion that has no rows in them. When the organisation does
    hold such rows, the delete is sent and the database's refusal is raised, not skipped.
    """
    if _exists(sess, table, where, params):
        sess.execute(text("DELETE FROM %s WHERE %s" % (_q(table), where)), params)


def _remove_pointing_rows(sess, plan, table, where, params, path):
    """Remove the rows of tables with no organisation column that point at ``table``'s rows in ``where``."""
    for child, child_col, parent_col, _ in plan.referrers[table]:
        if child == table or child in path or child == PEOPLE or child in plan.org_columns:
            continue
        child_where = "%s IN (SELECT %s FROM %s WHERE %s)" % (_q(child_col), _q(parent_col), _q(table), where)
        _remove_pointing_rows(sess, plan, child, child_where, params, path | {child})
        _delete(sess, child, child_where, params)


def _remove_people(sess, plan, people_ids):
    """Remove people who belonged only to the deleted organisation, and the rows that are theirs alone.

    The organisation's own rows are gone by now, so a row still naming one of these people belongs
    to no organisation (a session, a token) or to another organisation. A row of another organisation
    keeps its place and loses the name where the column allows it; a row that must name a person is
    removed with them.
    """
    if not people_ids:
        return
    params = {"people": list(people_ids)}
    for child, child_col, parent_col, not_null in plan.referrers[PEOPLE]:
        if parent_col != "id" or child == PEOPLE:
            continue
        where = "%s = ANY(:people)" % _q(child_col)
        if not _exists(sess, child, where, params):
            continue
        if not_null:
            _remove_pointing_rows(sess, plan, child, where, params, {child, PEOPLE})
            sess.execute(text("DELETE FROM %s WHERE %s" % (_q(child), where)), params)
        else:
            sess.execute(text("UPDATE %s SET %s = NULL WHERE %s" % (_q(child), _q(child_col), where)), params)
    sess.execute(text("DELETE FROM users WHERE id = ANY(:people)"), params)


class OrganizationDeletionService:
    """Delete an organisation and every row that belongs to it."""

    @classmethod
    def delete_organization(cls, org_id, requester_id=None, session=None):
        """Delete organisation ``org_id``; return the sentence to show the person who asked.

        Raises OrganizationDeletionRefused when the organisation may not be deleted, and the
        database's own error (after rolling back) when a row cannot be removed.
        """
        from app.models.org_role import OrgRole
        from app.models.organization import Organization
        from app.models.user import User

        sess = session or db.session
        org = sess.get(Organization, org_id)
        if org is None:
            raise OrganizationDeletionRefused("That organisation no longer exists.")
        if org.slug == "default":
            raise OrganizationDeletionRefused("The Default organisation cannot be deleted.")

        org_name = org.name
        try:
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

            plan = _Plan(sess)
            params = {"org_id": org_id}
            for table in plan.order():
                where = " OR ".join("%s = :org_id" % _q(col) for col in plan.org_columns[table])
                _remove_pointing_rows(sess, plan, table, where, params, {table})
                _delete(sess, table, where, params)

            _remove_people(sess, plan, leaving)

            sess.expunge(org)
            sess.execute(text("DELETE FROM organizations WHERE id = :org_id"), params)
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
