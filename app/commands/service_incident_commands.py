"""Open, resolve and list the platform incidents the service-status page shows.

    flask --app manage service-incident open "Slow answers" --impact degraded
    flask --app manage service-incident resolve 12
    flask --app manage service-incident list

An incident is platform-wide (``app/models/service_incident.py``), so these
commands run with no organisation and touch no organisation's data.
"""

from __future__ import annotations

import click
from flask.cli import AppGroup

from app.models.service_incident import IMPACTS

service_incident_cli = AppGroup("service-incident", help="Platform incidents on the service-status page.")


@service_incident_cli.command("open")
@click.argument("title")
@click.option("--impact", type=click.Choice(IMPACTS), default=IMPACTS[0], show_default=True)
@click.option("--summary", default=None, help="One or two sentences on what users will notice.")
def open_command(title, impact, summary):
    """Open an incident; the status page shows it until it is resolved."""
    from app.modules.monitoring.services.service_status import open_incident

    try:
        incident = open_incident(title, impact, summary)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Opened incident {incident.id} ({incident.impact}): {incident.title}")


@service_incident_cli.command("resolve")
@click.argument("incident_id", type=int)
def resolve_command(incident_id):
    """Mark an incident resolved."""
    from app.modules.monitoring.services.service_status import resolve_incident

    try:
        incident = resolve_incident(incident_id)
    except LookupError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Resolved incident {incident.id} at {incident.resolved_at:%Y-%m-%d %H:%M} UTC")


@service_incident_cli.command("list")
def list_command():
    """List the incidents of the last 90 days, newest first."""
    from app.modules.monitoring.services.service_status import incident_history

    rows = incident_history()
    if not rows:
        click.echo("No incidents in the last 90 days.")
        return
    for incident in rows:
        state = "open" if incident.resolved_at is None else f"resolved {incident.resolved_at:%Y-%m-%d %H:%M}"
        click.echo(f"{incident.id}\t{incident.impact}\t{incident.started_at:%Y-%m-%d %H:%M}\t{state}\t{incident.title}")


def init_app(app):
    app.cli.add_command(service_incident_cli)
