"""flask owners-from-commits — name the owner of connector-imported applications from their commits.

Runs, for applications the GitHub connector imported that have no primary owner, the same step the
connector's own read runs. Idempotent: an application with an owner is only reported, and a second run
changes nothing. One line per application. An organisation is read on its own; nothing crosses.

Usage:
    flask --app manage owners-from-commits --org 12
    flask --app manage owners-from-commits --all
"""

from __future__ import annotations

import click
from flask.cli import with_appcontext

from app import db


def run_for_organisation(org_id: int):
    """The lines for one organisation, committed; the tenant context is that organisation's alone."""
    from app.connectors.github import owners_from_commits
    from app.jobs.tenant_safe_job import tenant_scope

    with tenant_scope(org_id):
        lines = owners_from_commits(org_id)
        db.session.commit()
    return lines


@click.command("owners-from-commits")
@click.option("--org", "org_id", type=int, default=None, help="The organisation id to work on.")
@click.option("--all", "every", is_flag=True, default=False, help="Every organisation with a GitHub connection.")
@with_appcontext
def owners_from_commits_command(org_id, every):
    """Name the owner of imported applications from their recent commits, or record why none was."""
    if bool(org_id) == bool(every):
        raise click.UsageError("Give --org <id> or --all, not both and not neither.")
    if every:
        from app.models.connector_config import ConnectorConfig

        ids = sorted({
            row[0] for row in db.session.execute(
                db.select(ConnectorConfig.organization_id).where(ConnectorConfig.connector_type == "github")
            ).all() if row[0]
        })
        db.session.remove()
    else:
        ids = [org_id]
    changed = 0
    for current in ids:
        for line in run_for_organisation(current):
            changed += line["outcome"] in ("owner set", "reason recorded")
            click.echo(
                f"org {current}: {line['application']} ({line['repository']}): {line['outcome']}. {line['text']}"
            )
    click.echo(f"{changed} application(s) changed." if changed else "Nothing changed.")


def init_app(app):
    app.cli.add_command(owners_from_commits_command)
