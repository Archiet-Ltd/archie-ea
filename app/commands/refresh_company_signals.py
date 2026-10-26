"""``flask refresh-company-signals``: read the public sources now for one organisation (or every one that agreed)."""

import click
from flask.cli import with_appcontext


@click.command("refresh-company-signals")
@click.option("--organization-id", type=int, help="Only this organisation; by default every one that agreed.")
@click.option("--force", is_flag=True, help="Read again even though it was read within the day.")
@with_appcontext
def refresh_company_signals(organization_id, force):
    from flask import current_app

    from app.modules.organization.public_signals import SignalsRefused, refresh_all, refresh_organization

    if organization_id is None:
        for row in refresh_all(current_app):
            click.echo("organisation %s: %s" % (row["organization_id"], row.get("refused") or "%d signals" % row["stored"]))
        return
    try:
        report = refresh_organization(organization_id, force=force)
    except SignalsRefused as exc:
        raise click.ClickException(str(exc))
    for key, row in report["sources"].items():
        click.echo("%-14s %s" % (key, row.get("signals", row.get("skipped") or row.get("failed"))))


def init_app(app):
    app.cli.add_command(refresh_company_signals)
