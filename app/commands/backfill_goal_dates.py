"""Move a goal's target date from an element property into the goal's own record.

    flask --app manage backfill-goal-dates --dry-run
    flask --app manage backfill-goal-dates

A Goal element that holds exactly one date-like property, which parses as an unambiguous date, and whose goal
record has no target date yet, gets that date through the goal store. Anything else is listed with the reason and
left as it is; no date is guessed. Safe to run again. The property is kept, so a goal can hold the date in both
places until the property is retired.
"""
from datetime import datetime

import click
from flask.cli import with_appcontext

from app import db

_FORMATS = ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y")


def parse_date(value):
    """A date from a plain, unambiguous spelling, or None. Day/month/year with slashes is never guessed."""
    text = str(value or "").strip()
    for fmt in _FORMATS:
        try:
            return datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text, fmt).date()
        except ValueError:
            continue
    return None


def plan_for(org_id):
    """(to write, to list) for one organisation: dicts of element id, name and what was found."""
    from app.commands.chat_records_report import date_keys_of
    from app.models.archimate_core import ArchiMateElement
    from app.services import goal_store

    write, listed = [], []
    elements = db.session.execute(db.select(ArchiMateElement).where(
        ArchiMateElement.organization_id == org_id, ArchiMateElement.type == "Goal")
        .order_by(ArchiMateElement.id)).scalars()
    for element in elements:
        keys = date_keys_of(element.custom_properties)
        if not keys:
            continue
        item = {"element_id": element.id, "name": element.name, "keys": keys}
        row = goal_store.goal_for_element(org_id, element.id)
        if row is not None and row.target_date is not None:
            listed.append({**item, "why": "the goal already has a target date (%s)" % row.target_date.isoformat()})
        elif len(keys) != 1:
            listed.append({**item, "why": "more than one date-like property: %s" % ", ".join(keys)})
        else:
            parsed = parse_date(element.custom_properties.get(keys[0]))
            if parsed is None:
                listed.append({**item, "why": "%s = %r is not a date it can read" % (
                    keys[0], element.custom_properties.get(keys[0]))})
            else:
                write.append({**item, "date": parsed})
    return write, listed


@click.command("backfill-goal-dates")
@click.option("--dry-run", is_flag=True, help="Show what would be written and what is left; change nothing.")
@click.option("--organization-id", type=int, default=None, help="One organisation only.")
@with_appcontext
def backfill_goal_dates(dry_run, organization_id):
    """Write each goal's property date into its goal record; list what it cannot read."""
    from app.models.organization import Organization
    from app.services import goal_store

    query = db.select(Organization.id, Organization.name).order_by(Organization.id)
    if organization_id is not None:
        query = query.where(Organization.id == organization_id)
    written = left = 0
    for org_id, name in db.session.execute(query).all():
        db.session.expunge_all()
        write, listed = plan_for(org_id)
        if not write and not listed:
            continue
        click.echo("Organisation %d (%s)" % (org_id, name))
        for item in write:
            click.echo("  %s goal %d %r: target date %s (from %s)" % (
                "would write" if dry_run else "wrote", item["element_id"], item["name"],
                item["date"].isoformat(), item["keys"][0]))
            if not dry_run:
                goal_store.update_goal(org_id, item["element_id"], target_date=item["date"])
            written += 1
        for item in listed:
            click.echo("  left goal %d %r: %s" % (item["element_id"], item["name"], item["why"]))
            left += 1
    click.echo("%s %d, left %d%s" % ("Would write" if dry_run else "Wrote", written, left,
                                     " (dry run: nothing changed)" if dry_run else ""))


def init_app(app):
    app.cli.add_command(backfill_goal_dates)
