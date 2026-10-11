"""``flask clear-foreign-assignees`` — null out stored assignee ids that name
another organisation's user.

``kanban_cards.assigned_to_id``, ``solution_issues.assigned_to_id`` and
``solution_issues.escalated_to_id`` were writable from a request with no organisation check before the tenant fence
went in (see the writers in ``adm_kanban_routes.py`` and
``solution_issue_service.py``). Every reader is now fenced, so a row still
holding a foreign id names nobody today — but the id itself is stale and
worth clearing rather than left in place.

One-off housekeeping command, not a scheduled job. Dry-run by default;
clears only with ``--apply``.
"""

import re

import click

_TABLES = (
    ("kanban_cards", "assigned_to_id"),
    ("solution_issues", "assigned_to_id"),
    ("solution_issues", "escalated_to_id"),
)

# _TABLES above is a fixed module-level constant, never request- or
# database-derived, so the table/column names interpolated below cannot
# carry attacker input. This check makes that trust explicit and executable
# (bandit B608 cannot know the source is trusted from a comment alone — see
# the identical guard and note in backfill_layer_tenancy.py) rather than
# leaving the interpolation looking unguarded.
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _checked(name):
    if not _SAFE_IDENTIFIER.match(name):
        raise RuntimeError(f"refusing to interpolate unexpected identifier {name!r}")
    return name


def _foreign_assignee_counts(conn):
    from sqlalchemy import text

    counts = {}
    for table, column in _TABLES:
        table, column = _checked(table), _checked(column)
        counts[f"{table}.{column}"] = conn.execute(
            text(
                f'SELECT count(*) FROM "{table}" t JOIN users u ON u.id = t."{column}" '
                f'WHERE t."{column}" IS NOT NULL '
                f'AND u.organization_id IS DISTINCT FROM t.organization_id'
            )
        ).scalar()
    return counts


def _clear_foreign_assignees(conn):
    """Null out each listed user-id column on every row whose stored id names
    a user of a different organisation. Returns nothing; call ``_foreign_assignee_counts``
    before and after to measure the effect."""
    from sqlalchemy import text

    for table, column in _TABLES:
        table, column = _checked(table), _checked(column)
        conn.execute(
            text(
                f'UPDATE "{table}" t SET "{column}" = NULL '
                f'FROM users u WHERE u.id = t."{column}" '
                f'AND u.organization_id IS DISTINCT FROM t.organization_id'
            )
        )


def init_app(app):
    @app.cli.command("clear-foreign-assignees")
    @click.option(
        "--apply",
        is_flag=True,
        default=False,
        help="Actually clear foreign assignee and escalation ids. Without this flag the command prints counts only.",
    )
    def clear_foreign_assignees(apply):
        """Null out assignee/escalation user ids that name a user of another organisation."""
        from app import db

        conn = db.session.connection()
        before = _foreign_assignee_counts(conn)
        for name, count in before.items():
            click.echo(f"{name}: {count} row(s) naming another organisation's user")

        if not apply:
            click.echo("Dry run — use --apply to clear.")
            return

        _clear_foreign_assignees(conn)
        db.session.commit()

        after = _foreign_assignee_counts(conn)
        for name, count in after.items():
            click.echo(f"{name}: {count} row(s) naming another organisation's user after clearing")
